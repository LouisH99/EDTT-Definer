from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edtt_definer.model import Position, SectionType
from edtt_definer.parser import load_document, parse_text, save_document, serialize_document
from edtt_definer.project import (
    AltitudeMode,
    CourseMap,
    ProjectData,
    StageMetadata,
    apply_maximum_altitude,
    inferred_altitude_mode,
    inferred_maximum_altitude,
    project_from_document,
    serialize_project_export,
    load_project,
    save_project,
)


SAMPLE = (
    "<!-- race comment\r\n"
    "[eventinfo]\r\n"
    "EventRef = TEST01\r\n"
    "EventState=AUTHOR\r\n"
    "Name=Test Race\r\n"
    "ShortDesc=A test\r\n"
    "MaxShipSize=S\r\n"
    "GameType=BOTH\r\n"
    "RaceType=SHIP\r\n"
    "FutureField=preserve me\r\n"
    "[\\eventinfo]\r\n"
    "\r\n"
    "[start]\r\n"
    "PlayerState=Ship\r\n"
    "System=Sol\r\n"
    "Body=Earth\r\n"
    "Position=1.000000,2.000000\r\n"
    "Proximity=100\r\n"
    "NextInstruction=Begin.\r\n"
    "[\\start]\r\n"
    "\r\n"
    "[waypoint]\r\n"
    "PlayerState=ShipPass\r\n"
    "System=Sol\r\n"
    "Body=Earth\r\n"
    "Position=1.100000,2.100000 to 1.200000,2.200000\r\n"
    "Condition=DirectionLimit=(90,180)\r\n"
    "NextInstruction=Terse:Turn left.\r\n"
    "[\\waypoint]\r\n"
    "\r\n"
    "[end]\r\n"
    "PlayerState=ShipPass\r\n"
    "System=Sol\r\n"
    "Body=Earth\r\n"
    "Position=1.000000,2.000000\r\n"
    "Proximity=100\r\n"
    "EndInstruction=Finished.\r\n"
    "[\\end]\r\n"
)

WRAPPED_SAMPLE = (
    "[event]\r\n"
    "\r\n"
    "[eventinfo]\r\n"
    "EventRef=Legacy Race     <!-- identifier note\r\n"
    "EventState=AUTHOR      <!-- state note\r\n"
    "Name=Legacy Race       <!-- name note\r\n"
    "ShortDesc=Wrapped sample      <!-- description note\r\n"
    "MaxShipSize=N/A       <!-- size note\r\n"
    "GameType=ODYSSEY      <!-- game note\r\n"
    "RaceType=SRV        <!-- race note\r\n"
    "[\\eventinfo]\r\n"
    "\r\n"
    "[start]\r\n"
    "PlayerState=SRV      <!-- state note\r\n"
    "System=Sol      <!-- system note\r\n"
    "Body=Earth      <!-- body note\r\n"
    "Position=1.0,2.0      <!-- position note\r\n"
    "Proximity=100      <!-- proximity note\r\n"
    "NextInstruction=Begin.      <!-- instruction note\r\n"
    "[\\start]\r\n"
    "\r\n"
    "[end]\r\n"
    "PlayerState=SRV\r\n"
    "System=Sol\r\n"
    "Body=Earth\r\n"
    "Position=1.1,2.1\r\n"
    "Proximity=100\r\n"
    "EndInstruction=Finished.\r\n"
    "[\\end]\r\n"
    "\r\n"
    "[\\event]\r\n"
)


def test_unchanged_round_trip_is_exact() -> None:
    document = parse_text(SAMPLE)
    assert serialize_document(document) == SAMPLE


def test_course_map_project_round_trip(tmp_path) -> None:
    project = ProjectData.new()
    project.course_maps.append(CourseMap(
        system="Sol", body="Earth", image_base64="aW1hZ2U=",
        reference_latitude=1.0, reference_longitude=2.0,
        corners=[(-100, 100), (100, 100), (100, -100), (-100, -100)],
        opacity=0.4, visible=False, locked=True,
    ))
    path = tmp_path / "map.edtt"
    save_project(project, path)
    assert load_project(path).course_maps == project.course_maps
    legacy = project.to_dict()
    legacy["schema_version"] = 4
    legacy.pop("course_maps")
    assert ProjectData.from_dict(legacy).course_maps == []


@pytest.mark.parametrize("corners", [[], [(float("nan"), 0)] * 4])
def test_course_map_rejects_invalid_corners(corners) -> None:
    data = CourseMap("Sol", "Earth", "", 0, 0, corners).to_dict()
    with pytest.raises(ValueError, match="alignment"):
        CourseMap.from_dict(data)


def test_course_map_without_scale_defaults_to_original_size() -> None:
    data = CourseMap("Sol", "Earth", "", 0, 0,
                     [(-100, 100), (100, 100), (100, -100), (-100, -100)]).to_dict()
    data.pop("scale_factor")
    assert CourseMap.from_dict(data).scale_factor == 1.0


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_course_map_rejects_invalid_saved_scale(scale) -> None:
    data = CourseMap("Sol", "Earth", "", 0, 0,
                     [(-100, 100), (100, 100), (100, -100), (-100, -100)]).to_dict()
    data["scale_factor"] = scale
    with pytest.raises(ValueError, match="alignment"):
        CourseMap.from_dict(data)


def test_new_project_defaults_max_ship_size_and_altitude_modes() -> None:
    project = ProjectData.new()

    assert project.document.event_info.get("MaxShipSize") == "N/A"
    stages = (
        project.document.sections(SectionType.START)
        + project.document.sections(SectionType.WAYPOINT)
        + project.document.sections(SectionType.END)
    )
    assert stages
    assert all(
        project.stage_metadata[section.node_id].altitude_mode is AltitudeMode.MAXIMUM
        for section in stages
    )
    assert all(project.stage_metadata[section.node_id].altitude_locked for section in stages)


def test_edit_only_changes_targeted_field() -> None:
    document = parse_text(SAMPLE)
    original_event_ref = next(
        entry.raw
        for entry in document.sections(SectionType.EVENTINFO)[0].entries
        if entry.key == "EventRef"
    )
    document.event_info.set("Name", "Changed Race")
    output = serialize_document(document)
    assert original_event_ref in output
    assert "Name=Changed Race" in output
    assert "FutureField=preserve me" in output


def test_utf8_bom_and_newlines_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "race.export.txt"
    source.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))
    document = load_document(source)
    destination = tmp_path / "copy.export.txt"
    save_document(document, destination)
    assert destination.read_bytes() == source.read_bytes()


def test_project_round_trip_preserves_ast_and_metadata(tmp_path: Path) -> None:
    document = parse_text(SAMPLE)
    waypoint_id = document.waypoints[0].section.node_id
    project = ProjectData(
        document,
        {"abc": {"heading": "90"}},
        "race.export.txt",
        {
            waypoint_id: StageMetadata(
                "Canyon Gate", AltitudeMode.MAXIMUM, "500", True, "Lap 1"
            )
        },
    )
    path = tmp_path / "race.edttc.json"
    save_project(project, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 5
    assert payload["time_trial_version"] == 1
    loaded = load_project(path)
    assert serialize_document(loaded.document) == SAMPLE
    assert loaded.capture_metadata["abc"]["heading"] == "90"
    assert loaded.last_export_path == "race.export.txt"
    assert loaded.stage_metadata[waypoint_id].display_name == "Canyon Gate"
    assert loaded.stage_metadata[waypoint_id].altitude_mode is AltitudeMode.MAXIMUM
    assert loaded.stage_metadata[waypoint_id].maximum_altitude == "500"
    assert loaded.stage_metadata[waypoint_id].altitude_locked
    assert loaded.stage_metadata[waypoint_id].separator_before == "Lap 1"
    assert loaded.time_trial_version == 1
    assert loaded.last_saved_at is not None


def test_version_one_project_migrates_with_altitude_locked(tmp_path: Path) -> None:
    project = ProjectData(parse_text(SAMPLE))
    payload = project.to_dict()
    payload["schema_version"] = 1
    payload.pop("stage_metadata")
    path = tmp_path / "legacy.edttc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(path)
    waypoint = loaded.document.waypoints[0].section

    assert loaded.stage_metadata == {}
    assert loaded.metadata_for(waypoint.node_id).altitude_locked
    assert loaded.metadata_for(waypoint.node_id).separator_before == ""


def test_version_two_project_migrates_without_separator_metadata(tmp_path: Path) -> None:
    project = ProjectData(parse_text(SAMPLE))
    waypoint = project.document.waypoints[0].section
    project.stage_metadata[waypoint.node_id] = StageMetadata(display_name="Legacy")
    payload = project.to_dict()
    payload["schema_version"] = 2
    payload["stage_metadata"][waypoint.node_id].pop("separator_before")
    path = tmp_path / "version-two.edttc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.stage_metadata[waypoint.node_id].display_name == "Legacy"
    assert loaded.stage_metadata[waypoint.node_id].separator_before == ""
    assert loaded.time_trial_version == 0
    assert loaded.last_saved_at is None


def test_version_three_project_migrates_without_revision_metadata(tmp_path: Path) -> None:
    project = ProjectData(parse_text(SAMPLE))
    payload = project.to_dict()
    payload["schema_version"] = 3
    payload.pop("time_trial_version")
    payload.pop("last_saved_at")
    path = tmp_path / "version-three.edttc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.time_trial_version == 0
    assert loaded.last_saved_at is None


def test_each_successful_project_save_increments_revision(tmp_path: Path) -> None:
    project = ProjectData.new()
    path = tmp_path / "revision.edttc.json"

    save_project(
        project,
        path,
        saved_at=datetime(2026, 9, 25, 9, 23, 32, tzinfo=timezone.utc),
    )
    assert project.time_trial_version == 1
    assert project.last_saved_at == "2026-09-25T09:23:32Z"

    save_project(
        project,
        path,
        saved_at=datetime(2026, 9, 25, 9, 24, 0, tzinfo=timezone.utc),
    )
    assert project.time_trial_version == 2
    assert load_project(path).time_trial_version == 2


def test_failed_project_save_does_not_increment_revision(monkeypatch, tmp_path: Path) -> None:
    project = ProjectData.new()

    def fail_write(_path, _payload) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("edtt_definer.project.atomic_write", fail_write)
    with pytest.raises(OSError, match="disk full"):
        save_project(project, tmp_path / "failed.edttc.json")

    assert project.time_trial_version == 0
    assert project.last_saved_at is None


def test_export_projection_adds_and_recovers_edttd_metadata_without_mutating_ast(
    tmp_path: Path,
) -> None:
    project = project_from_document(parse_text(WRAPPED_SAMPLE))
    start = project.document.start.section
    waypoint = project.document.add_waypoint("SRV")
    waypoint.set_value("System", "Sol")
    waypoint.set_value("Body", "Earth")
    waypoint.set_value("Position", "1.05,2.05")
    waypoint.set_value("Proximity", "50")
    unnamed_waypoint = project.document.add_waypoint("SRV")
    unnamed_waypoint.set_value("System", "Sol")
    unnamed_waypoint.set_value("Body", "Earth")
    unnamed_waypoint.set_value("Position", "1.10,2.10")
    unnamed_waypoint.set_value("Proximity", "50")
    end = project.document.end.section
    project.metadata_for(start.node_id).display_name = "Crater Entrance"
    project.metadata_for(waypoint.node_id).display_name = "Canyon Entrance"
    project.metadata_for(waypoint.node_id).separator_before = "Round 1"
    project.metadata_for(end.node_id).display_name = "Crater Finish"
    original = serialize_document(project.document)
    save_project(
        project,
        tmp_path / "race.edttc.json",
        saved_at=datetime(2026, 9, 25, 9, 23, 32, tzinfo=timezone.utc),
    )

    exported = serialize_project_export(project)

    assert serialize_document(project.document) == original
    assert "[event]\r\n<!-- EDTTD: Time-trial version 1 — Last saved " in exported
    assert (
        "[start]\r\n<!-- EDTTD: Start — Crater Entrance -->\r\nPlayerState=SRV"
        in exported
    )
    assert (
        "[waypoint]\r\n<!-- EDTTD: (Round 1) WP1 — Canyon Entrance -->\r\n"
        "PlayerState=SRV"
        in exported
    )
    assert (
        "[waypoint]\r\n<!-- EDTTD: (Round 1) WP2 -->\r\nPlayerState=SRV"
        in exported
    )
    assert (
        "[end]\r\n<!-- EDTTD: End — Crater Finish -->\r\nPlayerState=SRV"
        in exported
    )

    restored = project_from_document(parse_text(exported))
    assert restored.time_trial_version == 1
    assert restored.last_saved_at == "2026-09-25T09:23:32Z"
    assert restored.metadata_for(restored.document.start.section.node_id).display_name == "Crater Entrance"
    assert (
        restored.metadata_for(restored.document.waypoints[0].section.node_id).display_name
        == "Canyon Entrance"
    )
    assert restored.metadata_for(
        restored.document.waypoints[1].section.node_id
    ).display_name == ""
    assert restored.metadata_for(
        restored.document.waypoints[1].section.node_id
    ).separator_before == ""
    assert restored.metadata_for(restored.document.end.section.node_id).display_name == "Crater Finish"
    assert serialize_project_export(restored) == exported


def test_export_preview_marks_revision_as_pending_until_saved() -> None:
    project = ProjectData.new()

    preview = serialize_project_export(project, pending_save=True)

    assert "EDTTD: Unsaved changes — next time-trial version 1" in preview


def test_maximum_altitude_helpers_preserve_decimal_values() -> None:
    document = parse_text(SAMPLE)
    waypoint = document.waypoints[0].section
    metadata = StageMetadata()

    assert inferred_altitude_mode(waypoint, metadata) is AltitudeMode.NONE
    assert apply_maximum_altitude(waypoint, "501")
    assert waypoint.value("Altitude") == "250.5"
    assert waypoint.value("AltitudeProximity") == "250.5"
    assert inferred_altitude_mode(waypoint, metadata) is AltitudeMode.MAXIMUM
    assert inferred_maximum_altitude(waypoint, metadata) == "501.0"


def test_equal_imported_altitudes_are_detected_without_rewriting() -> None:
    source = SAMPLE.replace(
        "NextInstruction=Terse:Turn left.\r\n",
        "Altitude=250.0      <!-- target note\r\n"
        "AltitudeProximity=250.00      <!-- tolerance note\r\n"
        "NextInstruction=Terse:Turn left.\r\n",
    )
    document = parse_text(source)
    waypoint = document.waypoints[0].section

    assert inferred_altitude_mode(waypoint, StageMetadata()) is AltitudeMode.MAXIMUM
    assert inferred_maximum_altitude(waypoint, StageMetadata()) == "500.00"
    assert apply_maximum_altitude(waypoint, "500.00")
    assert serialize_document(document) == source


def test_position_point_and_gate_preserve_decimal_text() -> None:
    point = Position.parse("-12.340000,56.780000")
    gate = Position.parse("-12.340000,56.780000 to -12.350000,56.790000")
    assert point.format() == "-12.340000,56.780000"
    assert not point.is_gate
    assert gate.format() == "-12.340000,56.780000 to -12.350000,56.790000"
    assert gate.is_gate


def test_position_preserves_gate_endpoint_b_before_a() -> None:
    position = Position(
        latitude_b="-12.350000",
        longitude_b="56.790000",
        gate=True,
    )

    serialized = position.format()
    restored = Position.parse(serialized)

    assert serialized == ", to -12.350000,56.790000"
    assert restored.is_gate
    assert restored.latitude_a == ""
    assert restored.longitude_a == ""
    assert restored.latitude_b == "-12.350000"
    assert restored.longitude_b == "56.790000"


def test_waypoint_operations_keep_complete_blocks() -> None:
    document = parse_text(SAMPLE)
    first = document.waypoints[0].section
    duplicate = document.duplicate_waypoint(first.node_id)
    assert duplicate is not None
    duplicate.set_value("NextInstruction", "Second")
    assert document.move_waypoint(duplicate.node_id, -1)
    assert document.waypoints[0].get("NextInstruction") == "Second"
    assert document.remove_waypoint(first.node_id)
    assert len(document.waypoints) == 1


def test_waypoint_group_operations_preserve_route_order() -> None:
    document = parse_text(SAMPLE)
    first = document.waypoints[0].section
    first.set_value("NextInstruction", "First")
    second = document.add_waypoint("ShipPass")
    second.set_value("NextInstruction", "Second")
    third = document.add_waypoint("ShipPass")
    third.set_value("NextInstruction", "Third")

    duplicates = document.duplicate_waypoints([first.node_id, second.node_id])

    assert [
        waypoint.get("NextInstruction") for waypoint in document.waypoints
    ] == ["First", "Second", "First", "Second", "Third"]
    assert len({duplicate.node_id for duplicate in duplicates}) == 2
    assert all(
        duplicate.node_id not in {first.node_id, second.node_id}
        for duplicate in duplicates
    )

    duplicate_ids = [duplicate.node_id for duplicate in duplicates]
    assert document.move_waypoints(duplicate_ids, 1)
    assert [
        waypoint.get("NextInstruction") for waypoint in document.waypoints
    ] == ["First", "Second", "Third", "First", "Second"]
    assert document.move_waypoints(duplicate_ids, -1)
    assert [
        waypoint.get("NextInstruction") for waypoint in document.waypoints
    ] == ["First", "Second", "First", "Second", "Third"]


def test_add_waypoint_can_insert_after_a_selected_route_waypoint() -> None:
    document = parse_text(SAMPLE)
    first = document.waypoints[0].section
    first.set_value("NextInstruction", "First")
    second = document.add_waypoint("ShipPass")
    second.set_value("NextInstruction", "Second")
    third = document.add_waypoint("ShipPass")
    third.set_value("NextInstruction", "Third")

    inserted = document.add_waypoint(
        "ShipPass",
        after_node_id=second.node_id,
    )
    inserted.set_value("NextInstruction", "Inserted")

    assert [
        waypoint.get("NextInstruction") for waypoint in document.waypoints
    ] == ["First", "Second", "Inserted", "Third"]


def test_reverse_waypoints_requires_and_reverses_one_contiguous_range() -> None:
    document = parse_text(SAMPLE)
    first = document.waypoints[0].section
    first.set_value("NextInstruction", "First")
    second = document.add_waypoint("ShipPass")
    second.set_value("NextInstruction", "Second")
    third = document.add_waypoint("ShipPass")
    third.set_value("NextInstruction", "Third")

    assert not document.reverse_waypoints([first.node_id, third.node_id])
    assert document.reverse_waypoints([first.node_id, second.node_id, third.node_id])
    assert [
        waypoint.get("NextInstruction") for waypoint in document.waypoints
    ] == ["Third", "Second", "First"]


def test_event_wrapper_and_inline_comments_are_lossless() -> None:
    document = parse_text(WRAPPED_SAMPLE)
    assert [section.name for section in document.sections()] == ["eventinfo", "start", "end"]
    assert document.event_info.get("EventRef") == "Legacy Race"
    assert document.start.get("Position") == "1.0,2.0"
    assert serialize_document(document) == WRAPPED_SAMPLE


def test_editing_wrapped_value_preserves_inline_comment() -> None:
    document = parse_text(WRAPPED_SAMPLE)
    document.event_info.set("Name", "Updated Race")
    assert "Name=Updated Race       <!-- name note" in serialize_document(document)
    assert "EventRef=Legacy Race     <!-- identifier note" in serialize_document(document)
