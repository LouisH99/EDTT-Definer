from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from .geometry import great_circle_distance_m, spherical_midpoint
from .live import LocationSnapshot
from .model import Position, SectionNode, SectionType
from .project import (
    AltitudeMode,
    ProjectData,
    StageMetadata,
    inferred_altitude_mode,
    inferred_maximum_altitude,
)
from .radius import BodyRadiusCache, normalize_name, positive_decimal
from .trace import RaceTrace
from .validator import Severity, validate_project


class SceneState(str, Enum):
    READY = "ready"
    NO_GROUPS = "no_groups"
    NO_RADIUS = "no_radius"
    NO_VALID_STAGES = "no_valid_stages"


@dataclass(frozen=True)
class BodyKey:
    system: str
    body: str

    @classmethod
    def from_names(cls, system: str | None, body: str | None) -> BodyKey:
        return cls(normalize_name(system), normalize_name(body))


@dataclass(frozen=True)
class BodyGroup:
    key: BodyKey
    system_name: str
    body_name: str
    section_ids: tuple[str, ...]

    @property
    def label(self) -> str:
        system = self.system_name or "Unspecified system"
        body = self.body_name or "Unspecified body"
        count = len(self.section_ids)
        return f"{system} — {body} ({count} stage{'s' if count != 1 else ''})"


@dataclass(frozen=True)
class Point3D:
    east_m: float
    north_m: float
    altitude_m: float


@dataclass(frozen=True)
class AltitudeBounds:
    minimum_m: float
    maximum_m: float

    @property
    def center_m(self) -> float:
        return (self.minimum_m + self.maximum_m) / 2.0


@dataclass(frozen=True)
class SceneValidationNote:
    severity: Severity
    message: str
    field: str | None


@dataclass(frozen=True)
class SceneStage:
    section_id: str
    section_type: SectionType
    route_index: int
    waypoint_number: int | None
    label: str
    player_state: str
    is_gate: bool
    center: Point3D
    endpoint_a: Point3D
    endpoint_b: Point3D | None
    proximity_m: float | None
    altitude_bounds: AltitudeBounds | None
    validation_notes: tuple[SceneValidationNote, ...]
    latitude: str
    longitude: str

    @property
    def has_error(self) -> bool:
        return any(note.severity is Severity.ERROR for note in self.validation_notes)


@dataclass(frozen=True)
class RouteSegment:
    from_section_id: str
    to_section_id: str
    start: Point3D
    end: Point3D


@dataclass(frozen=True)
class SceneTracePoint:
    position: Point3D
    speed_mps: float | None


@dataclass(frozen=True)
class SceneTraceSegment:
    points: tuple[SceneTracePoint, ...]


@dataclass(frozen=True)
class LiveMarker:
    position: Point3D
    heading_degrees: float | None


@dataclass(frozen=True)
class SkippedStage:
    section_id: str
    label: str
    reason: str


@dataclass(frozen=True)
class CourseScene:
    state: SceneState
    body_group: BodyGroup | None
    radius_km: Decimal | None
    radius_source: str
    radius_warning: str
    stages: tuple[SceneStage, ...]
    route_segments: tuple[RouteSegment, ...]
    skipped: tuple[SkippedStage, ...]
    live_marker: LiveMarker | None
    message: str
    trace_segments: tuple[SceneTraceSegment, ...] = ()
    trace_message: str = ""
    course_distance_m: float | None = None
    course_distance_message: str = ""
    reference_coordinate: tuple[float, float] | None = None


@dataclass(frozen=True)
class _StageSource:
    section: SectionNode
    section_type: SectionType
    route_index: int
    waypoint_number: int | None
    label: str
    body_key: BodyKey
    system_name: str
    body_name: str


@dataclass(frozen=True)
class _GeographicStage:
    source: _StageSource
    position: Position
    center_latitude: Decimal
    center_longitude: Decimal
    latitude_a: Decimal
    longitude_a: Decimal
    latitude_b: Decimal | None
    longitude_b: Decimal | None
    proximity_m: Decimal | None
    altitude_bounds: AltitudeBounds | None


def _finite_decimal(value: str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(value.strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return result if result.is_finite() else None


def _stage_sources(project: ProjectData) -> list[_StageSource]:
    result: list[_StageSource] = []
    waypoint_number = 0
    route_index = 0
    for section in project.document.sections():
        kind = section.section_type
        if kind not in {SectionType.START, SectionType.WAYPOINT, SectionType.END}:
            continue
        route_index += 1
        number: int | None = None
        if kind is SectionType.WAYPOINT:
            waypoint_number += 1
            number = waypoint_number
            name = project.stage_metadata.get(section.node_id, StageMetadata()).display_name.strip()
            label = f"Waypoint {number}" + (f" — {name}" if name else "")
        elif kind is SectionType.START:
            name = project.stage_metadata.get(section.node_id, StageMetadata()).display_name.strip()
            label = "Start" + (f" — {name}" if name else "")
        else:
            name = project.stage_metadata.get(section.node_id, StageMetadata()).display_name.strip()
            label = "End" + (f" — {name}" if name else "")
        system_name = section.value("System").strip()
        body_name = section.value("Body").strip()
        result.append(
            _StageSource(
                section=section,
                section_type=kind,
                route_index=route_index,
                waypoint_number=number,
                label=label,
                body_key=BodyKey.from_names(system_name, body_name),
                system_name=system_name,
                body_name=body_name,
            )
        )
    return result


def body_groups(project: ProjectData) -> tuple[BodyGroup, ...]:
    grouped: dict[BodyKey, list[_StageSource]] = {}
    for source in _stage_sources(project):
        grouped.setdefault(source.body_key, []).append(source)
    result: list[BodyGroup] = []
    for key, sources in grouped.items():
        system_name = next((item.system_name for item in sources if item.system_name), "")
        body_name = next((item.body_name for item in sources if item.body_name), "")
        result.append(
            BodyGroup(
                key=key,
                system_name=system_name,
                body_name=body_name,
                section_ids=tuple(item.section.node_id for item in sources),
            )
        )
    return tuple(result)


def body_key_for_section(project: ProjectData, section_id: str | None) -> BodyKey | None:
    if not section_id:
        return None
    section = project.document.section_by_id(section_id)
    if section is None or section.section_type not in {
        SectionType.START,
        SectionType.WAYPOINT,
        SectionType.END,
    }:
        return None
    return BodyKey.from_names(section.value("System"), section.value("Body"))


def _coordinate(value: Decimal | None, *, latitude: bool) -> Decimal:
    if value is None or not value.is_finite():
        raise ValueError("coordinate is missing or not numeric")
    lower, upper = (Decimal("-90"), Decimal("90")) if latitude else (
        Decimal("-180"),
        Decimal("180"),
    )
    if not lower <= value <= upper:
        raise ValueError("coordinate is outside its valid range")
    return value


def _altitude_bounds(section: SectionNode, metadata: StageMetadata) -> AltitudeBounds | None:
    mode = inferred_altitude_mode(section, metadata)
    if mode is AltitudeMode.NONE:
        return None
    if mode is AltitudeMode.MAXIMUM:
        maximum = _finite_decimal(inferred_maximum_altitude(section, metadata))
        if maximum is None or maximum <= 0:
            return None
        return AltitudeBounds(0.0, float(maximum))
    altitude = _finite_decimal(section.value("Altitude"))
    tolerance = _finite_decimal(section.value("AltitudeProximity"))
    if altitude is None or tolerance is None or tolerance < 0:
        return None
    return AltitudeBounds(float(altitude - tolerance), float(altitude + tolerance))


def _geographic_stage(source: _StageSource, project: ProjectData) -> _GeographicStage:
    position = Position.parse(source.section.value("Position"))
    try:
        raw_lat_a, raw_lon_a, raw_lat_b, raw_lon_b = position.numeric_values()
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("position is missing or malformed") from exc
    lat_a = _coordinate(raw_lat_a, latitude=True)
    lon_a = _coordinate(raw_lon_a, latitude=False)
    lat_b = _coordinate(raw_lat_b, latitude=True) if position.is_gate else None
    lon_b = _coordinate(raw_lon_b, latitude=False) if position.is_gate else None
    if position.is_gate:
        try:
            center_lat, center_lon = spherical_midpoint(lat_a, lon_a, lat_b, lon_b)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        center_latitude = Decimal(center_lat)
        center_longitude = Decimal(center_lon)
    else:
        center_latitude = lat_a
        center_longitude = lon_a
    proximity = positive_decimal(source.section.value("Proximity"))
    metadata = project.stage_metadata.get(source.section.node_id, StageMetadata())
    return _GeographicStage(
        source=source,
        position=position,
        center_latitude=center_latitude,
        center_longitude=center_longitude,
        latitude_a=lat_a,
        longitude_a=lon_a,
        latitude_b=lat_b,
        longitude_b=lon_b,
        proximity_m=proximity,
        altitude_bounds=_altitude_bounds(source.section, metadata),
    )


def _reference_coordinate(stages: list[_GeographicStage]) -> tuple[float, float]:
    vectors: list[tuple[float, float, float]] = []
    for stage in stages:
        latitude = math.radians(float(stage.center_latitude))
        longitude = math.radians(float(stage.center_longitude))
        vectors.append(
            (
                math.cos(latitude) * math.cos(longitude),
                math.cos(latitude) * math.sin(longitude),
                math.sin(latitude),
            )
        )
    x = sum(item[0] for item in vectors)
    y = sum(item[1] for item in vectors)
    z = sum(item[2] for item in vectors)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        first = stages[0]
        return float(first.center_latitude), float(first.center_longitude)
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def project_coordinate(
    latitude: Decimal | str | float,
    longitude: Decimal | str | float,
    reference_latitude: Decimal | str | float,
    reference_longitude: Decimal | str | float,
    radius_km: Decimal | str | float,
    altitude_m: float = 0.0,
) -> Point3D:
    """Project a surface coordinate into antimeridian-safe local east/north metres."""
    lat = math.radians(float(latitude))
    lon = math.radians(float(longitude))
    ref_lat = math.radians(float(reference_latitude))
    ref_lon = math.radians(float(reference_longitude))
    longitude_delta = (lon - ref_lon + math.pi) % (2 * math.pi) - math.pi
    haversine = (
        math.sin((lat - ref_lat) / 2) ** 2
        + math.cos(ref_lat) * math.cos(lat) * math.sin(longitude_delta / 2) ** 2
    )
    angular_distance = 2 * math.asin(min(1.0, math.sqrt(haversine)))
    if angular_distance < 1e-15:
        east = 0.0
        north = 0.0
    else:
        bearing = math.atan2(
            math.sin(longitude_delta) * math.cos(lat),
            math.cos(ref_lat) * math.sin(lat)
            - math.sin(ref_lat) * math.cos(lat) * math.cos(longitude_delta),
        )
        distance = angular_distance * float(radius_km) * 1000.0
        east = distance * math.sin(bearing)
        north = distance * math.cos(bearing)
    return Point3D(east, north, float(altitude_m))


def unproject_coordinate(
    east_m: float,
    north_m: float,
    reference_latitude: float,
    reference_longitude: float,
    radius_km: Decimal | float,
) -> tuple[float, float]:
    distance = math.hypot(east_m, north_m)
    if distance < 1e-12:
        return reference_latitude, reference_longitude
    angular = distance / (float(radius_km) * 1000.0)
    latitude = math.radians(reference_latitude)
    longitude = math.radians(reference_longitude)
    sine = math.sin(angular)
    cosine = math.cos(angular)
    result_latitude = math.asin(max(-1.0, min(1.0,
        cosine * math.sin(latitude)
        + north_m * sine * math.cos(latitude) / distance,
    )))
    result_longitude = longitude + math.atan2(
        east_m * sine,
        distance * math.cos(latitude) * cosine - north_m * math.sin(latitude) * sine,
    )
    return math.degrees(result_latitude), (math.degrees(result_longitude) + 180) % 360 - 180


def _resolve_radius(
    sources: list[_StageSource],
    group: BodyGroup,
    live_snapshot: LocationSnapshot | None,
    radius_cache: BodyRadiusCache | None,
) -> tuple[Decimal | None, str, str]:
    project_values: list[tuple[Decimal, str]] = []
    for source in sources:
        radius = positive_decimal(source.section.value("BodyRadius"))
        if radius is not None:
            project_values.append((radius, source.label))
    if project_values:
        radius, label = project_values[0]
        distinct = {value for value, _label in project_values}
        warning = ""
        if len(distinct) > 1:
            warning = (
                f"Conflicting BodyRadius values are present; using {format(radius, 'f')} km "
                f"from {label}."
            )
        return radius, f"Project ({label})", warning

    live_matches = (
        live_snapshot is not None
        and group.key == BodyKey.from_names(live_snapshot.system, live_snapshot.body)
    )
    if live_matches and live_snapshot is not None:
        radius = positive_decimal(live_snapshot.body_radius_km)
        if radius is not None:
            return radius, live_snapshot.body_radius_source or "Live game", ""

    if radius_cache is not None and group.key.system and group.key.body:
        record = radius_cache.get(system=group.system_name, body=group.body_name)
        if record is not None:
            source = "EDSM cache" if record.origin == "edsm" else "Local cache"
            return record.radius_km, source, ""
    return None, "", ""


def _scene_stage(
    stage: _GeographicStage,
    reference: tuple[float, float],
    radius_km: Decimal,
    validation_notes: tuple[SceneValidationNote, ...],
) -> SceneStage:
    bounds = stage.altitude_bounds
    altitude = bounds.center_m if bounds is not None else 0.0
    center = project_coordinate(
        stage.center_latitude,
        stage.center_longitude,
        reference[0],
        reference[1],
        radius_km,
        altitude,
    )
    point_a = project_coordinate(
        stage.latitude_a,
        stage.longitude_a,
        reference[0],
        reference[1],
        radius_km,
        0.0,
    )
    point_b = None
    if stage.position.is_gate and stage.latitude_b is not None and stage.longitude_b is not None:
        point_b = project_coordinate(
            stage.latitude_b,
            stage.longitude_b,
            reference[0],
            reference[1],
            radius_km,
            0.0,
        )
    return SceneStage(
        section_id=stage.source.section.node_id,
        section_type=stage.source.section_type,
        route_index=stage.source.route_index,
        waypoint_number=stage.source.waypoint_number,
        label=stage.source.label,
        player_state=stage.source.section.value("PlayerState"),
        is_gate=stage.position.is_gate,
        center=center,
        endpoint_a=point_a,
        endpoint_b=point_b,
        proximity_m=float(stage.proximity_m) if stage.proximity_m is not None else None,
        altitude_bounds=bounds,
        validation_notes=validation_notes,
        latitude=format(stage.center_latitude, "f"),
        longitude=format(stage.center_longitude, "f"),
    )


def _stage_validation_notes(project: ProjectData) -> dict[str, tuple[SceneValidationNote, ...]]:
    grouped: dict[str, list[SceneValidationNote]] = {}
    for issue in validate_project(project):
        if issue.section_id is None:
            continue
        grouped.setdefault(issue.section_id, []).append(
            SceneValidationNote(issue.severity, issue.message, issue.field)
        )
    return {section_id: tuple(notes) for section_id, notes in grouped.items()}


def _course_surface_distance(
    all_sources: list[_StageSource],
    group: BodyGroup,
    geographic: list[_GeographicStage],
    radius_km: Decimal,
) -> tuple[float | None, str]:
    if not group.key.system or not group.key.body:
        return None, "System and Body are required on every route stage"
    if any(source.body_key != group.key for source in all_sources):
        return None, "route spans multiple System/Body groups"
    if len(geographic) != len(all_sources):
        missing = len(all_sources) - len(geographic)
        return None, f"{missing} route stage{'s are' if missing != 1 else ' is'} missing valid coordinates"
    if len(geographic) < 2:
        return None, "at least Start and End coordinates are required"
    total = 0.0
    for first, second in zip(geographic, geographic[1:], strict=False):
        total += great_circle_distance_m(
            first.center_latitude,
            first.center_longitude,
            second.center_latitude,
            second.center_longitude,
            radius_km,
        )
    return total, ""


def build_course_scene(
    project: ProjectData,
    selected_body: BodyKey | None = None,
    *,
    live_snapshot: LocationSnapshot | None = None,
    radius_cache: BodyRadiusCache | None = None,
    trace: RaceTrace | None = None,
) -> CourseScene:
    all_sources = _stage_sources(project)
    groups = body_groups(project)
    if not groups:
        return CourseScene(
            SceneState.NO_GROUPS,
            None,
            None,
            "",
            "",
            (),
            (),
            (),
            None,
            "No Start, Waypoint, or End stages are available.",
        )
    group = next((item for item in groups if item.key == selected_body), groups[0])
    sources = [source for source in all_sources if source.body_key == group.key]
    radius, radius_source, radius_warning = _resolve_radius(
        sources, group, live_snapshot, radius_cache
    )
    if radius is None:
        return CourseScene(
            SceneState.NO_RADIUS,
            group,
            None,
            "",
            "",
            (),
            (),
            (),
            None,
            "Body radius is required to draw this course at an accurate scale.",
        )

    geographic: list[_GeographicStage] = []
    skipped: list[SkippedStage] = []
    for source in sources:
        try:
            geographic.append(_geographic_stage(source, project))
        except ValueError as exc:
            skipped.append(SkippedStage(source.section.node_id, source.label, str(exc)))
    if not geographic:
        return CourseScene(
            SceneState.NO_VALID_STAGES,
            group,
            radius,
            radius_source,
            radius_warning,
            (),
            (),
            tuple(skipped),
            None,
            "No stages on this body have complete, valid coordinates.",
        )

    reference = _reference_coordinate(geographic)
    validation_notes = _stage_validation_notes(project)
    stages = tuple(
        _scene_stage(
            stage,
            reference,
            radius,
            validation_notes.get(stage.source.section.node_id, ()),
        )
        for stage in geographic
    )
    stage_by_route = {stage.route_index: stage for stage in stages}
    segments: list[RouteSegment] = []
    for route_index in range(1, max(stage_by_route, default=0)):
        first = stage_by_route.get(route_index)
        second = stage_by_route.get(route_index + 1)
        if first is not None and second is not None:
            segments.append(
                RouteSegment(
                    first.section_id,
                    second.section_id,
                    first.center,
                    second.center,
                )
            )

    live_marker = None
    if (
        live_snapshot is not None
        and live_snapshot.capture_ready
        and group.key == BodyKey.from_names(live_snapshot.system, live_snapshot.body)
    ):
        try:
            latitude = _coordinate(live_snapshot.latitude, latitude=True)
            longitude = _coordinate(live_snapshot.longitude, latitude=False)
            altitude = float(live_snapshot.altitude or Decimal("0"))
            heading = (
                float(live_snapshot.heading % Decimal("360"))
                if live_snapshot.heading is not None and live_snapshot.heading.is_finite()
                else None
            )
            live_marker = LiveMarker(
                project_coordinate(
                    latitude,
                    longitude,
                    reference[0],
                    reference[1],
                    radius,
                    altitude,
                ),
                heading,
            )
        except (ValueError, InvalidOperation):
            live_marker = None

    scene_trace_segments: list[SceneTraceSegment] = []
    trace_message = ""
    if trace is not None and trace.point_count:
        context_matches = (
            (not trace.system or normalize_name(trace.system) == group.key.system)
            and (not trace.body or normalize_name(trace.body) == group.key.body)
        )
        if not context_matches:
            trace_message = "Loaded trace belongs to a different System/Body."
        else:
            skipped_trace_points = 0
            for trace_segment in trace.segments:
                projected_points: list[SceneTracePoint] = []
                for point in trace_segment.points:
                    try:
                        projected_points.append(
                            SceneTracePoint(
                                project_coordinate(
                                    point.latitude,
                                    point.longitude,
                                    reference[0],
                                    reference[1],
                                    radius,
                                    float(point.altitude_m),
                                ),
                                float(point.speed_mps)
                                if point.speed_mps is not None
                                else None,
                            )
                        )
                    except (ValueError, InvalidOperation, OverflowError):
                        skipped_trace_points += 1
                if projected_points:
                    scene_trace_segments.append(
                        SceneTraceSegment(tuple(projected_points))
                    )
            trace_message = (
                f"{sum(len(item.points) for item in scene_trace_segments)} trace points shown"
            )
            if skipped_trace_points:
                trace_message += f"; {skipped_trace_points} skipped"

    message = f"{len(stages)} stage{'s' if len(stages) != 1 else ''} shown"
    if skipped:
        message += f"; {len(skipped)} skipped"
    course_distance_m, course_distance_message = _course_surface_distance(
        all_sources, group, geographic, radius
    )
    return CourseScene(
        SceneState.READY,
        group,
        radius,
        radius_source,
        radius_warning,
        stages,
        tuple(segments),
        tuple(skipped),
        live_marker,
        message,
        tuple(scene_trace_segments),
        trace_message,
        course_distance_m,
        course_distance_message,
        reference,
    )
