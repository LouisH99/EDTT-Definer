from __future__ import annotations

import csv
import io
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from .geometry import great_circle_distance_m
from .live import LocationSnapshot
from .parser import atomic_write
from .radius import normalize_name, positive_decimal


TRACE_SCHEMA_VERSION = 1
TRACE_JSON_SUFFIX = ".edtttrace.json"

RACEVIZ_V3_HEADER = (
    "Time",
    "UnixTime",
    "Latitude",
    "Longitude",
    "Radius",
    "Altitude",
    "Heading",
    "Speed",
    "CargoScoop",
    "LandingGear",
    "Lights",
    "TargetWaypointNum",
    "TargetWaypointInstruction",
    "DistanceFromWaypoint",
    "HitWaypoint",
    "CompletedWaypoint",
    "MissedReason",
    "TransportMode",
    "DirectionOfTravel",
)


class TraceState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"


def _decimal(value: Any, field_name: str, *, optional: bool = False) -> Decimal | None:
    if value in (None, "") and optional:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid {field_name} value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Invalid {field_name} value: {value!r}")
    return result


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _validate_coordinates(latitude: Decimal, longitude: Decimal) -> None:
    if not latitude.is_finite() or not longitude.is_finite():
        raise ValueError("Latitude and longitude must be finite numbers.")
    if not Decimal("-90") <= latitude <= Decimal("90"):
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not Decimal("-180") <= longitude <= Decimal("180"):
        raise ValueError("Longitude must be between -180 and 180 degrees.")


@dataclass(frozen=True)
class TracePoint:
    timestamp_utc: str
    unix_time: Decimal
    elapsed_ms: int
    latitude: Decimal
    longitude: Decimal
    altitude_m: Decimal
    heading_degrees: Decimal | None
    radius_km: Decimal
    speed_mps: Decimal | None = None
    target_waypoint: int = 0
    transport_mode: str = ""

    def __post_init__(self) -> None:
        _validate_coordinates(self.latitude, self.longitude)
        if not self.altitude_m.is_finite():
            raise ValueError("Trace altitude must be finite.")
        if self.heading_degrees is not None and not self.heading_degrees.is_finite():
            raise ValueError("Trace heading must be finite.")
        if self.speed_mps is not None and (
            not self.speed_mps.is_finite() or self.speed_mps < 0
        ):
            raise ValueError("Trace speed must be a finite non-negative value.")
        if not self.radius_km.is_finite() or self.radius_km <= 0:
            raise ValueError("Trace BodyRadius must be greater than zero.")
        if self.elapsed_ms < 0:
            raise ValueError("Trace elapsed time cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "unix_time": _decimal_text(self.unix_time),
            "elapsed_ms": self.elapsed_ms,
            "latitude": _decimal_text(self.latitude),
            "longitude": _decimal_text(self.longitude),
            "altitude_m": _decimal_text(self.altitude_m),
            "heading_degrees": _decimal_text(self.heading_degrees),
            "radius_km": _decimal_text(self.radius_km),
            "speed_mps": _decimal_text(self.speed_mps),
            "target_waypoint": self.target_waypoint,
            "transport_mode": self.transport_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TracePoint:
        try:
            point = cls(
                timestamp_utc=str(data.get("timestamp_utc", "")),
                unix_time=_decimal(data.get("unix_time"), "UnixTime"),
                elapsed_ms=int(data.get("elapsed_ms", 0)),
                latitude=_decimal(data.get("latitude"), "Latitude"),
                longitude=_decimal(data.get("longitude"), "Longitude"),
                altitude_m=_decimal(data.get("altitude_m", "0"), "Altitude"),
                heading_degrees=_decimal(
                    data.get("heading_degrees"), "Heading", optional=True
                ),
                radius_km=_decimal(data.get("radius_km"), "BodyRadius"),
                speed_mps=_decimal(data.get("speed_mps"), "Speed", optional=True),
                target_waypoint=int(data.get("target_waypoint", 0)),
                transport_mode=str(data.get("transport_mode", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("A trace point is malformed.") from exc
        return point


@dataclass
class TraceSegment:
    points: list[TracePoint] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceSegment:
        raw_points = data.get("points")
        if not isinstance(raw_points, list):
            raise ValueError("A trace segment has no point list.")
        return cls(
            points=[TracePoint.from_dict(item) for item in raw_points if isinstance(item, dict)],
            reason=str(data.get("reason", "")),
        )


@dataclass
class RaceTrace:
    system: str = ""
    body: str = ""
    radius_km: Decimal | None = None
    created_utc: str = ""
    segments: list[TraceSegment] = field(default_factory=list)
    source_format: str = "EDTT trace"

    @property
    def point_count(self) -> int:
        return sum(len(segment.points) for segment in self.segments)

    @property
    def max_speed_mps(self) -> Decimal | None:
        speeds = [
            point.speed_mps
            for segment in self.segments
            for point in segment.points
            if point.speed_mps is not None and point.speed_mps >= 0
        ]
        return max(speeds) if speeds else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "system": self.system,
            "body": self.body,
            "radius_km": _decimal_text(self.radius_km),
            "created_utc": self.created_utc,
            "source_format": self.source_format,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RaceTrace:
        if data.get("schema_version") != TRACE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported trace schema version: {data.get('schema_version')!r}"
            )
        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list):
            raise ValueError("Trace file has no segment list.")
        radius = _decimal(data.get("radius_km"), "BodyRadius", optional=True)
        if radius is not None and radius <= 0:
            raise ValueError("Trace BodyRadius must be greater than zero.")
        return cls(
            system=str(data.get("system", "")),
            body=str(data.get("body", "")),
            radius_km=radius,
            created_utc=str(data.get("created_utc", "")),
            source_format=str(data.get("source_format", "EDTT trace")),
            segments=[
                TraceSegment.from_dict(item)
                for item in raw_segments
                if isinstance(item, dict)
            ],
        )


def save_trace_json(trace: RaceTrace, path: str | Path) -> None:
    payload = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    atomic_write(path, payload)


def load_trace_json(path: str | Path) -> RaceTrace:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the trace JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Trace JSON must contain an object.")
    return RaceTrace.from_dict(data)


def _iso_from_unix(unix_time: Decimal) -> str:
    try:
        return datetime.fromtimestamp(float(unix_time), timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OverflowError, OSError, ValueError):
        return ""


def _parse_csv_point(
    row: list[str],
    version: int,
    elapsed_origin: Decimal,
    previous: TracePoint | None,
    fallback_radius_km: Decimal | None,
) -> TracePoint:
    indexes = (0, 1, 2, 3, 4) if version == 1 else (1, 2, 3, 5, 6, 4, 7, 11)
    if len(row) <= max(indexes):
        raise ValueError("Trace CSV row has too few columns.")
    unix_time = _decimal(row[indexes[0]], "UnixTime")
    latitude = _decimal(row[indexes[1]], "Latitude")
    longitude = _decimal(row[indexes[2]], "Longitude")
    altitude = _decimal(row[indexes[3]] or "0", "Altitude")
    heading = _decimal(row[indexes[4]], "Heading", optional=True)
    if version == 1:
        radius = previous.radius_km if previous is not None else fallback_radius_km
        speed = None
        waypoint = 0
    else:
        radius = positive_decimal(row[indexes[5]])
        speed = _decimal(row[indexes[6]], "Speed", optional=True)
        try:
            waypoint = int(row[indexes[7]] or 0)
        except ValueError as exc:
            raise ValueError("Invalid target waypoint number in trace CSV.") from exc
    if radius is None:
        raise ValueError(
            "Version 1 trace CSV has no body radius. Import a Version 2/3 CSV or use EDTT trace JSON."
        )
    elapsed_ms = max(0, int((unix_time - elapsed_origin) * Decimal("1000")))
    transport = row[17].strip() if version >= 3 and len(row) > 17 else ""
    return TracePoint(
        timestamp_utc=(row[0].strip() if version >= 2 else _iso_from_unix(unix_time)),
        unix_time=unix_time,
        elapsed_ms=elapsed_ms,
        latitude=latitude,
        longitude=longitude,
        altitude_m=altitude,
        heading_degrees=heading,
        radius_km=radius,
        speed_mps=speed,
        target_waypoint=waypoint,
        transport_mode=transport,
    )


def load_trace_csv(
    path: str | Path, *, fallback_radius_km: Decimal | str | None = None
) -> RaceTrace:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not read trace CSV: {exc}") from exc
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or not rows[0]:
        raise ValueError("Trace CSV is empty.")
    marker = "".join(rows[0]).replace(" ", "").casefold()
    if marker not in {"#version1", "#version2", "#version3"}:
        raise ValueError("Trace CSV must begin with #Version1, #Version2, or #Version3.")
    version = int(marker[-1])
    fallback_radius = positive_decimal(fallback_radius_km)
    data_rows = rows[1:]
    if version >= 2:
        if not data_rows:
            raise ValueError("Trace CSV is missing its column header.")
        normalized_header = tuple(value.strip() for value in data_rows.pop(0))
        if len(normalized_header) < 12 or normalized_header[:4] != RACEVIZ_V3_HEADER[:4]:
            raise ValueError("Trace CSV has an unsupported column header.")

    segments: list[TraceSegment] = []
    current = TraceSegment(reason="Imported CSV")
    origin: Decimal | None = None
    previous: TracePoint | None = None
    for line_number, row in enumerate(data_rows, start=3 if version >= 2 else 2):
        if not row or not any(value.strip() for value in row):
            if current.points:
                segments.append(current)
                current = TraceSegment(reason="CSV gap")
                previous = None
            continue
        try:
            row_unix = _decimal(row[0 if version == 1 else 1], "UnixTime")
            if origin is None:
                origin = row_unix
            point = _parse_csv_point(
                row, version, origin, previous, fallback_radius
            )
        except ValueError as exc:
            raise ValueError(f"Trace CSV line {line_number}: {exc}") from exc
        current.points.append(point)
        previous = point
    if current.points:
        segments.append(current)
    if not segments:
        raise ValueError("Trace CSV contains no data points.")
    first = segments[0].points[0]
    return RaceTrace(
        radius_km=first.radius_km,
        created_utc=first.timestamp_utc,
        segments=segments,
        source_format=f"RaceViz / EDCoPilot CSV v{version}",
    )


def save_trace_csv(trace: RaceTrace, path: str | Path) -> None:
    output = io.StringIO(newline="")
    output.write("#Version3\r\n")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(RACEVIZ_V3_HEADER)
    for segment_index, segment in enumerate(trace.segments):
        if segment_index:
            writer.writerow([])
        for point in segment.points:
            writer.writerow(
                (
                    point.timestamp_utc or _iso_from_unix(point.unix_time),
                    _decimal_text(point.unix_time),
                    _decimal_text(point.latitude),
                    _decimal_text(point.longitude),
                    _decimal_text(point.radius_km),
                    _decimal_text(point.altitude_m),
                    _decimal_text(point.heading_degrees) or "0",
                    _decimal_text(point.speed_mps) or "0",
                    "False",
                    "False",
                    "False",
                    point.target_waypoint,
                    "",
                    "",
                    "",
                    "",
                    "",
                    point.transport_mode,
                    _decimal_text(point.heading_degrees) or "",
                )
            )
    atomic_write(path, output.getvalue().encode("utf-8"))


class TraceRecorder:
    """Record valid live snapshots without coupling traces to project schema v2."""

    def __init__(self) -> None:
        self.trace = RaceTrace()
        self.state = TraceState.IDLE
        self.dirty = False
        self._started_monotonic = 0.0
        self._break_pending = False
        self._last_point: TracePoint | None = None
        self.status = "No trace loaded."

    def start(
        self,
        *,
        system: str,
        body: str,
        radius_km: Decimal | str,
        now_monotonic: float | None = None,
        now_utc: datetime | None = None,
    ) -> None:
        radius = positive_decimal(radius_km)
        if radius is None:
            raise ValueError("A positive body radius is required before recording.")
        current_utc = now_utc or datetime.now(timezone.utc)
        self.trace = RaceTrace(
            system=system.strip(),
            body=body.strip(),
            radius_km=radius,
            created_utc=current_utc.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            source_format="EDTT live recording",
        )
        self.state = TraceState.RECORDING
        self.dirty = False
        self._started_monotonic = (
            time.monotonic() if now_monotonic is None else now_monotonic
        )
        self._break_pending = True
        self._last_point = None
        self.status = "Recording; waiting for matching live coordinates."

    def pause(self) -> None:
        if self.state is TraceState.RECORDING:
            self.state = TraceState.PAUSED
            self._break_pending = True
            self.status = f"Recording paused at {self.trace.point_count} points."

    def resume(self) -> None:
        if self.state is TraceState.PAUSED:
            self.state = TraceState.RECORDING
            self._break_pending = True
            self._last_point = None
            self.status = "Recording resumed; waiting for live coordinates."

    def stop(self) -> None:
        if self.state is not TraceState.IDLE:
            self.state = TraceState.IDLE
            self._break_pending = True
            self.status = f"Recording stopped · {self.trace.point_count} points."

    def clear(self) -> None:
        self.trace = RaceTrace()
        self.state = TraceState.IDLE
        self.dirty = False
        self._break_pending = False
        self._last_point = None
        self.status = "No trace loaded."

    def replace(self, trace: RaceTrace, *, dirty: bool = False) -> None:
        self.trace = trace
        self.state = TraceState.IDLE
        self.dirty = dirty
        self._break_pending = False
        self._last_point = None
        self.status = (
            f"Loaded {trace.point_count} trace points · {len(trace.segments)} segment(s)."
        )

    def mark_saved(self) -> None:
        self.dirty = False

    def _snapshot_matches(self, snapshot: LocationSnapshot) -> bool:
        if self.trace.system and normalize_name(snapshot.system) != normalize_name(self.trace.system):
            return False
        if self.trace.body and normalize_name(snapshot.body) != normalize_name(self.trace.body):
            return False
        return True

    def consume(
        self,
        snapshot: LocationSnapshot | None,
        *,
        now_monotonic: float | None = None,
        now_utc: datetime | None = None,
    ) -> bool:
        if self.state is not TraceState.RECORDING:
            return False
        if snapshot is None or not snapshot.capture_ready:
            self._break_pending = True
            self._last_point = None
            self.status = "Recording; waiting for valid live coordinates."
            return False
        if not self._snapshot_matches(snapshot):
            self._break_pending = True
            self._last_point = None
            self.status = "Recording paused automatically: live System/Body does not match."
            return False
        latitude = snapshot.latitude
        longitude = snapshot.longitude
        if latitude is None or longitude is None:
            return False
        try:
            _validate_coordinates(latitude, longitude)
        except (ValueError, InvalidOperation):
            self._break_pending = True
            self._last_point = None
            self.status = "Recording; waiting for valid coordinate ranges."
            return False
        # Recording uses the radius chosen when Record was pressed so a later
        # conflicting live/cache value cannot distort speed or projection.
        radius = self.trace.radius_km
        if radius is None:
            self.status = "Recording; waiting for a body radius."
            return False
        altitude = snapshot.altitude or Decimal("0")
        heading = snapshot.heading
        if not altitude.is_finite() or (heading is not None and not heading.is_finite()):
            self._break_pending = True
            self._last_point = None
            self.status = "Recording; waiting for finite altitude and heading data."
            return False
        fingerprint = (latitude, longitude, altitude, heading)
        if self._last_point is not None:
            previous_fingerprint = (
                self._last_point.latitude,
                self._last_point.longitude,
                self._last_point.altitude_m,
                self._last_point.heading_degrees,
            )
            if fingerprint == previous_fingerprint:
                self.status = f"Recording · {self.trace.point_count} points (ship stationary)."
                return False

        monotonic_now = time.monotonic() if now_monotonic is None else now_monotonic
        utc_now = now_utc or datetime.now(timezone.utc)
        elapsed_ms = max(0, round((monotonic_now - self._started_monotonic) * 1000))
        speed: Decimal | None = None
        if self._last_point is not None:
            delta_seconds = Decimal(elapsed_ms - self._last_point.elapsed_ms) / Decimal("1000")
            if delta_seconds > 0:
                surface = great_circle_distance_m(
                    self._last_point.latitude,
                    self._last_point.longitude,
                    latitude,
                    longitude,
                    radius,
                )
                vertical = float(altitude - self._last_point.altitude_m)
                distance = math.hypot(surface, vertical)
                speed = (Decimal(str(distance)) / delta_seconds).quantize(Decimal("0.001"))
        unix_time = Decimal(str(utc_now.astimezone(timezone.utc).timestamp()))
        point = TracePoint(
            timestamp_utc=utc_now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            unix_time=unix_time,
            elapsed_ms=elapsed_ms,
            latitude=latitude,
            longitude=longitude,
            altitude_m=altitude,
            heading_degrees=heading,
            radius_km=radius,
            speed_mps=speed,
        )
        if self._break_pending or not self.trace.segments:
            self.trace.segments.append(TraceSegment(reason="Live recording"))
        self.trace.segments[-1].points.append(point)
        self._break_pending = False
        self._last_point = point
        self.dirty = True
        self.status = f"Recording · {self.trace.point_count} points."
        return True
