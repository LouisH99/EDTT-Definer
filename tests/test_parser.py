from __future__ import annotations

from pathlib import Path

from edtt_definer.model import Position, SectionType
from edtt_definer.parser import load_document, parse_text, save_document, serialize_document
from edtt_definer.project import ProjectData, load_project, save_project


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
    project = ProjectData(parse_text(SAMPLE), {"abc": {"heading": "90"}}, "race.export.txt")
    path = tmp_path / "race.edttc.json"
    save_project(project, path)
    loaded = load_project(path)
    assert serialize_document(loaded.document) == SAMPLE
    assert loaded.capture_metadata["abc"]["heading"] == "90"
    assert loaded.last_export_path == "race.export.txt"


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
