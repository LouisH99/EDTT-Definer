from __future__ import annotations

import math
from decimal import Decimal

import pytest

from edtt_definer.course_scene import (
    BodyKey,
    SceneState,
    body_groups,
    build_course_scene,
    project_coordinate,
)
from edtt_definer.live import LocationSnapshot
from edtt_definer.project import AltitudeMode, ProjectData, StageMetadata
from edtt_definer.radius import BodyRadiusCache
from edtt_definer.trace import RaceTrace, TracePoint, TraceSegment


def scene_project() -> ProjectData:
    project = ProjectData.new()
    start = project.document.start.section
    waypoint = project.document.waypoints[0].section
    end = project.document.end.section
    for stage in (start, waypoint, end):
        stage.set_value("System", "Demo System")
        stage.set_value("Body", "Demo A 1")
        stage.set_value("BodyRadius", "5411.771")
    start.set_value("Position", "0,0")
    start.set_value("Proximity", "100")
    waypoint.set_value("Position", "0.01,-0.01 to 0.01,0.01")
    end.set_value("Position", "0.02,0")
    end.set_value("Proximity", "80")
    project.stage_metadata[start.node_id] = StageMetadata(
        altitude_mode=AltitudeMode.MAXIMUM,
        maximum_altitude="500",
    )
    start.set_value("Altitude", "250")
    start.set_value("AltitudeProximity", "250")
    project.stage_metadata[waypoint.node_id] = StageMetadata(
        display_name="Canyon Gate",
        altitude_mode=AltitudeMode.TARGET,
    )
    waypoint.set_value("Altitude", "300")
    waypoint.set_value("AltitudeProximity", "50")
    project.stage_metadata[end.node_id] = StageMetadata(
        altitude_mode=AltitudeMode.NONE,
    )
    return project


def test_scene_builds_complete_route_and_true_altitude_shapes() -> None:
    project = scene_project()

    scene = build_course_scene(project)

    assert scene.state is SceneState.READY
    assert scene.radius_km == Decimal("5411.771")
    assert scene.radius_source == "Project (Start)"
    assert len(scene.stages) == 3
    assert len(scene.route_segments) == 2
    start, gate, end = scene.stages
    assert start.proximity_m == 100
    assert start.altitude_bounds is not None
    assert (start.altitude_bounds.minimum_m, start.altitude_bounds.maximum_m) == (0, 500)
    assert gate.is_gate and gate.endpoint_b is not None
    assert gate.label == "Waypoint 1 — Canyon Gate"
    assert gate.altitude_bounds is not None
    assert (gate.altitude_bounds.minimum_m, gate.altitude_bounds.maximum_m) == (250, 350)
    assert end.altitude_bounds is None
    gate_width = math.hypot(
        gate.endpoint_b.east_m - gate.endpoint_a.east_m,
        gate.endpoint_b.north_m - gate.endpoint_a.north_m,
    )
    assert gate_width == pytest.approx(1889.2, rel=0.002)


def test_visible_stages_carry_validation_notes_for_the_renderer() -> None:
    project = scene_project()
    start = project.document.start.section
    start.set_value("Proximity", "-5")

    scene = build_course_scene(project)
    scene_start = next(stage for stage in scene.stages if stage.section_id == start.node_id)

    assert scene_start.has_error
    assert any(note.field == "Proximity" for note in scene_start.validation_notes)


def test_projection_is_antimeridian_safe_and_preserves_north_east_orientation() -> None:
    west_of_dateline = project_coordinate("0", "-179.999", "0", "179.999", "5411.771")
    north = project_coordinate("0.01", "179.999", "0", "179.999", "5411.771")

    assert 100 < west_of_dateline.east_m < 300
    assert abs(west_of_dateline.north_m) < 0.01
    assert north.north_m > 900
    assert abs(north.east_m) < 0.01


def test_projection_stays_finite_and_local_at_high_latitudes() -> None:
    east = project_coordinate("89.9", "45.1", "89.9", "45", "5411.771")
    north = project_coordinate("89.91", "45", "89.9", "45", "5411.771")

    assert math.isfinite(east.east_m)
    assert math.isfinite(east.north_m)
    assert east.east_m > 0
    assert abs(east.north_m) < 1
    assert north.north_m > 900


def test_body_groups_are_separate_and_keep_global_waypoint_labels() -> None:
    project = scene_project()
    second = project.document.add_waypoint("ShipPass")
    second.set_value("System", "Other System")
    second.set_value("Body", "Other B 2")
    second.set_value("BodyRadius", "1000")
    second.set_value("Position", "1,2")
    second.set_value("Proximity", "50")
    project.stage_metadata[second.node_id] = StageMetadata(
        display_name="Remote",
        altitude_mode=AltitudeMode.NONE,
    )

    groups = body_groups(project)
    other = next(group for group in groups if group.body_name == "Other B 2")
    scene = build_course_scene(project, other.key)

    assert len(groups) == 2
    assert other.label.endswith("(1 stage)")
    assert [stage.label for stage in scene.stages] == ["Waypoint 2 — Remote"]
    assert scene.route_segments == ()


def test_radius_precedence_conflict_live_and_cache_fallbacks() -> None:
    project = scene_project()
    waypoint = project.document.waypoints[0].section
    waypoint.set_value("BodyRadius", "6000")

    conflict = build_course_scene(project)

    assert conflict.radius_km == Decimal("5411.771")
    assert "Conflicting BodyRadius" in conflict.radius_warning

    for stage in (
        project.document.start.section,
        waypoint,
        project.document.end.section,
    ):
        stage.set_value("BodyRadius", "")
    live = LocationSnapshot(
        system="Demo System",
        body="Demo A 1",
        body_radius_km=Decimal("5000"),
        body_radius_source="Journal",
        latitude=Decimal("0"),
        longitude=Decimal("0"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )
    live_scene = build_course_scene(project, live_snapshot=live)
    assert live_scene.radius_km == Decimal("5000")
    assert live_scene.radius_source == "Journal"

    cache = BodyRadiusCache()
    cache.put(
        "4500",
        origin="edsm",
        system="Demo System",
        body="Demo A 1",
    )
    cached_scene = build_course_scene(project, radius_cache=cache)
    assert cached_scene.radius_km == Decimal("4500")
    assert cached_scene.radius_source == "EDSM cache"


def test_missing_radius_waits_and_malformed_stages_are_skipped() -> None:
    project = scene_project()
    for stage in (
        project.document.start.section,
        project.document.waypoints[0].section,
        project.document.end.section,
    ):
        stage.set_value("BodyRadius", "")

    missing = build_course_scene(project)

    assert missing.state is SceneState.NO_RADIUS
    assert "required" in missing.message

    project.document.start.section.set_value("BodyRadius", "5411.771")
    project.document.end.section.set_value("Position", "broken")
    skipped = build_course_scene(project)
    assert skipped.state is SceneState.READY
    assert len(skipped.stages) == 2
    assert skipped.skipped[0].label == "End"
    assert len(skipped.route_segments) == 1

    for stage in (
        project.document.start.section,
        project.document.waypoints[0].section,
    ):
        stage.set_value("Position", "broken")
    no_valid = build_course_scene(project)
    assert no_valid.state is SceneState.NO_VALID_STAGES
    assert len(no_valid.skipped) == 3


def test_live_marker_requires_matching_body_and_valid_coordinates() -> None:
    project = scene_project()
    live = LocationSnapshot(
        system="demo system",
        body="DEMO A 1",
        latitude=Decimal("0.005"),
        longitude=Decimal("0.001"),
        altitude=Decimal("123.5"),
        heading=Decimal("370"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )

    scene = build_course_scene(project, live_snapshot=live)

    assert scene.live_marker is not None
    assert scene.live_marker.position.altitude_m == 123.5
    assert scene.live_marker.heading_degrees == 10

    live.body = "Another body"
    assert build_course_scene(project, live_snapshot=live).live_marker is None

    live.body = "Demo A 1"
    live.flag_has_lat_long = False
    assert build_course_scene(project, live_snapshot=live).live_marker is None


def test_trace_is_projected_with_speed_and_filtered_by_body() -> None:
    project = scene_project()
    trace = RaceTrace(
        system="Demo System",
        body="Demo A 1",
        radius_km=Decimal("5411.771"),
        segments=[
            TraceSegment(
                points=[
                    TracePoint(
                        timestamp_utc="2026-09-05T12:00:00Z",
                        unix_time=Decimal("1"),
                        elapsed_ms=0,
                        latitude=Decimal("0"),
                        longitude=Decimal("0"),
                        altitude_m=Decimal("100"),
                        heading_degrees=Decimal("90"),
                        radius_km=Decimal("5411.771"),
                    ),
                    TracePoint(
                        timestamp_utc="2026-09-05T12:00:01Z",
                        unix_time=Decimal("2"),
                        elapsed_ms=1000,
                        latitude=Decimal("0.001"),
                        longitude=Decimal("0.001"),
                        altitude_m=Decimal("120"),
                        heading_degrees=Decimal("91"),
                        radius_km=Decimal("5411.771"),
                        speed_mps=Decimal("123.456"),
                    ),
                ]
            )
        ],
    )

    scene = build_course_scene(project, trace=trace)

    assert len(scene.trace_segments) == 1
    assert len(scene.trace_segments[0].points) == 2
    assert scene.trace_segments[0].points[1].speed_mps == pytest.approx(123.456)
    assert scene.trace_segments[0].points[0].position.altitude_m == 100
    assert "2 trace points" in scene.trace_message

    trace.body = "Other Body"
    mismatched = build_course_scene(project, trace=trace)
    assert not mismatched.trace_segments
    assert "different System/Body" in mismatched.trace_message
