from __future__ import annotations

import json
from pathlib import Path

from edtt_definer.model import Position, SectionType
from edtt_definer.parser import load_document, parse_text, save_document, serialize_document
from edtt_definer.project import (
    AltitudeMode,
    ProjectData,
    StageMetadata,
    apply_maximum_altitude,
    inferred_altitude_mode,
    inferred_maximum_altitude,
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
                "Canyon Gate", AltitudeMode.MAXIMUM, "500", True
            )
        },
    )
    path = tmp_path / "race.edttc.json"
    save_project(project, path)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    loaded = load_project(path)
    assert serialize_document(loaded.document) == SAMPLE
    assert loaded.capture_metadata["abc"]["heading"] == "90"
    assert loaded.last_export_path == "race.export.txt"
    assert loaded.stage_metadata[waypoint_id].display_name == "Canyon Gate"
    assert loaded.stage_metadata[waypoint_id].altitude_mode is AltitudeMode.MAXIMUM
    assert loaded.stage_metadata[waypoint_id].maximum_altitude == "500"
    assert loaded.stage_metadata[waypoint_id].altitude_locked


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
