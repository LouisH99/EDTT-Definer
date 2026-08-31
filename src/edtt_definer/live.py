from __future__ import annotations

import json
import os
import winreg
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any


SAVED_GAMES_GUID = "{4C5C32FF-BB9D-43B0-BF4C-2A725E4234A0}"
HAS_LAT_LONG_FLAG = 1 << 21


class LiveState(str, Enum):
    MISSING = "missing"
    INVALID = "invalid"
    STALE = "stale"
    NO_COORDINATES = "no_coordinates"
    LIVE = "live"


@dataclass
class JournalContext:
    system: str | None = None
    body: str | None = None
    station: str | None = None


@dataclass
class LocationSnapshot:
    timestamp: str | None = None
    system: str | None = None
    body: str | None = None
    station: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    altitude: Decimal | None = None
    heading: Decimal | None = None
    flags: int | None = None
    flag_has_lat_long: bool | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def capture_ready(self) -> bool:
        return self.has_coordinates and self.flag_has_lat_long is not False

    def to_metadata(self) -> dict[str, str | int | None]:
        def text(value: Decimal | None) -> str | None:
            return format(value, "f") if value is not None else None

        return {
            "timestamp": self.timestamp,
            "system": self.system,
            "body": self.body,
            "station": self.station,
            "latitude": text(self.latitude),
            "longitude": text(self.longitude),
            "altitude": text(self.altitude),
            "heading": text(self.heading),
            "flags": self.flags,
        }


@dataclass
class LiveResult:
    state: LiveState
    snapshot: LocationSnapshot | None
    message: str


def default_journal_directory() -> Path:
    saved_games: str | None = None
    if os.name == "nt":
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                saved_games, _ = winreg.QueryValueEx(key, SAVED_GAMES_GUID)
                saved_games = os.path.expandvars(saved_games)
        except OSError:
            saved_games = None
    base = Path(saved_games) if saved_games else Path.home() / "Saved Games"
    return base / "Frontier Developments" / "Elite Dangerous"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _timestamp_age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    except ValueError:
        return None


class LiveLocationReader:
    def __init__(self, journal_directory: str | Path | None = None, *, stale_after: float = 15.0):
        self.journal_directory = Path(journal_directory) if journal_directory else default_journal_directory()
        self.stale_after = stale_after
        self.last_snapshot: LocationSnapshot | None = None
        self.context = JournalContext()
        self._journal_path: Path | None = None
        self._journal_offset = 0
        self._journal_needs_alignment = False

    @property
    def status_path(self) -> Path:
        return self.journal_directory / "Status.json"

    def set_journal_directory(self, path: str | Path) -> None:
        self.journal_directory = Path(path)
        self.context = JournalContext()
        self._journal_path = None
        self._journal_offset = 0
        self._journal_needs_alignment = False

    def poll(self) -> LiveResult:
        self._poll_journal()
        path = self.status_path
        if not path.exists():
            return LiveResult(LiveState.MISSING, self.last_snapshot, f"Status.json not found in {self.journal_directory}")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
        except (OSError, json.JSONDecodeError, UnicodeError):
            return LiveResult(LiveState.INVALID, self.last_snapshot, "Status.json is being updated; retaining the last valid reading.")

        flags_value = data.get("Flags")
        try:
            flags = int(flags_value) if flags_value is not None else None
        except (TypeError, ValueError):
            flags = None
        flag_has_lat_long = bool(flags & HAS_LAT_LONG_FLAG) if flags is not None else None
        snapshot = LocationSnapshot(
            timestamp=data.get("timestamp"),
            system=self.context.system,
            body=data.get("BodyName") or self.context.body,
            station=self.context.station,
            latitude=_decimal(data.get("Latitude")),
            longitude=_decimal(data.get("Longitude")),
            altitude=_decimal(data.get("Altitude")),
            heading=_decimal(data.get("Heading")),
            flags=flags,
            flag_has_lat_long=flag_has_lat_long,
        )
        self.last_snapshot = snapshot
        age = _timestamp_age_seconds(snapshot.timestamp)
        if age is not None and age > self.stale_after:
            return LiveResult(LiveState.STALE, snapshot, f"Game data is stale ({int(age)} seconds old).")
        if not snapshot.has_coordinates:
            return LiveResult(LiveState.NO_COORDINATES, snapshot, "Game connected, but no planetary coordinates are available.")
        if snapshot.flag_has_lat_long is False:
            return LiveResult(LiveState.NO_COORDINATES, snapshot, "Coordinates are present, but the Has Lat/Long game flag is not set.")
        return LiveResult(LiveState.LIVE, snapshot, "Live planetary coordinates available.")

    def _latest_journal(self) -> Path | None:
        try:
            files = list(self.journal_directory.glob("Journal.*.log"))
        except OSError:
            return None
        return max(files, key=lambda item: item.stat().st_mtime, default=None)

    def _poll_journal(self) -> None:
        latest = self._latest_journal()
        if latest is None:
            return
        if latest != self._journal_path:
            self._journal_path = latest
            self._journal_offset = max(0, latest.stat().st_size - 8 * 1024 * 1024)
            self._journal_needs_alignment = self._journal_offset > 0
            self.context = JournalContext()
        try:
            size = latest.stat().st_size
            if size < self._journal_offset:
                self._journal_offset = 0
                self._journal_needs_alignment = False
                self.context = JournalContext()
            with latest.open("r", encoding="utf-8-sig", errors="replace") as handle:
                handle.seek(self._journal_offset)
                if self._journal_needs_alignment:
                    handle.readline()
                    self._journal_needs_alignment = False
                for line in handle:
                    self._consume_journal_line(line)
                self._journal_offset = handle.tell()
        except OSError:
            return

    def _consume_journal_line(self, line: str) -> None:
        try:
            event = json.loads(line, parse_float=Decimal)
        except json.JSONDecodeError:
            return
        event_name = event.get("event")
        system = event.get("StarSystem")
        if system:
            self.context.system = system
        if event_name in {"Location", "FSDJump", "CarrierJump", "ApproachBody", "Touchdown", "Liftoff", "SupercruiseExit"}:
            body = event.get("Body") or event.get("BodyName")
            if body:
                self.context.body = body
        if event_name == "LeaveBody":
            self.context.body = None
        if event_name == "Docked":
            self.context.station = event.get("StationName")
        elif event_name in {"Undocked", "FSDJump", "CarrierJump"}:
            self.context.station = None
