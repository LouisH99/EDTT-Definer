from __future__ import annotations

from edtt_definer.model import EventDocument, SectionType
from edtt_definer.parser import parse_text
from edtt_definer.validator import Severity, validate_document

from .test_parser import SAMPLE, WRAPPED_SAMPLE


def errors(document: EventDocument) -> list[str]:
    return [issue.message for issue in validate_document(document) if issue.severity is Severity.ERROR]


def test_complete_sample_has_no_errors() -> None:
    assert errors(parse_text(SAMPLE)) == []


def test_new_document_is_a_saveable_draft_but_not_exportable() -> None:
    messages = errors(EventDocument.new())
    assert any("EventRef" in message for message in messages)
    assert any("position" in message.lower() for message in messages)


def test_flying_start_requires_matching_first_waypoint() -> None:
    document = parse_text(SAMPLE)
    document.start.set("PlayerState", "ShipFlyingStart")
    messages = errors(document)
    assert any("first waypoint" in message for message in messages)
    document.waypoints[0].set("PlayerState", "ShipFlyingStart")
    assert not any("first waypoint" in message for message in errors(document))


def test_gate_rejects_incomplete_second_endpoint() -> None:
    document = parse_text(SAMPLE)
    waypoint = document.sections(SectionType.WAYPOINT)[0]
    waypoint.set_value("Position", "1,2 to ,")
    assert any("numeric latitude" in message for message in errors(document))


def test_invalid_order_blocks_export() -> None:
    document = parse_text(SAMPLE)
    start = document.sections(SectionType.START)[0]
    end = document.sections(SectionType.END)[0]
    a = document.nodes.index(start)
    b = document.nodes.index(end)
    document.nodes[a], document.nodes[b] = document.nodes[b], document.nodes[a]
    assert any("ordered" in message for message in errors(document))


def test_unknown_field_is_warning_not_error() -> None:
    issues = validate_document(parse_text(SAMPLE))
    unknown = [issue for issue in issues if "FutureField" in issue.message]
    assert unknown and all(issue.severity is Severity.WARNING for issue in unknown)


def test_missing_closing_tag_blocks_export() -> None:
    document = parse_text(SAMPLE.replace("[\\end]\r\n", ""))
    assert any("closing tag" in message for message in errors(document))


def test_unknown_top_level_line_is_warning() -> None:
    document = parse_text("future-directive\r\n" + SAMPLE)
    issues = validate_document(document)
    matches = [issue for issue in issues if "outside a section" in issue.message]
    assert matches and matches[0].severity is Severity.WARNING


def test_wrapped_legacy_event_opens_without_errors() -> None:
    document = parse_text(WRAPPED_SAMPLE)
    assert errors(document) == []


def test_legacy_gate_zero_proximity_and_ground_altitude_are_allowed() -> None:
    document = parse_text(SAMPLE)
    waypoint = document.sections(SectionType.WAYPOINT)[0]
    waypoint.set_value("Proximity", "0")
    waypoint.set_value("Altitude", "0")
    waypoint.set_value("AltitudeProximity", "")
    assert errors(document) == []

    waypoint.set_value("AltitudeProximity", "10")
    assert errors(document) == []


def test_legacy_event_reference_may_contain_apostrophe() -> None:
    document = parse_text(SAMPLE)
    document.event_info.set("EventRef", "DR1 OVER THE CLOOKU'S NEST")
    assert errors(document) == []


def test_edcopilot_ship_size_codes_are_enforced() -> None:
    document = parse_text(SAMPLE)
    for value in ("N/A", "S", "M", "L"):
        document.event_info.set("MaxShipSize", value)
        assert not any("MaxShipSize" in message for message in errors(document))

    document.event_info.set("MaxShipSize", "Small")
    assert any("Use N/A, S, M, or L" in message for message in errors(document))


def test_end_uses_end_instruction_instead_of_next_instruction() -> None:
    document = parse_text(SAMPLE)
    end = document.sections(SectionType.END)[0]
    end.set_value("EndInstruction", "")
    end.set_value("NextInstruction", "You made it.")
    messages = errors(document)
    assert any("uses EndInstruction" in message for message in messages)
    assert any("requires an EndInstruction" in message for message in messages)


def test_speed_rule_fields_and_navigation_instructions_are_recognized() -> None:
    document = parse_text(SAMPLE)
    waypoint = document.sections(SectionType.WAYPOINT)[0]
    waypoint.set_value("BodyRadius", "6371.0")
    waypoint.set_value("EnterSystemInstruction", "Enter Sol")
    waypoint.set_value("ApproachBodyInstruction", "Approach Earth")
    waypoint.set_value("MaxSpeed", "100")
    waypoint.set_value("MaxSpeedWarningDistance", "500,250")
    waypoint.set_value("MaxSpeedWarningTrigger", "150,")
    waypoint.set_value("MaxSpeedWarningInstruction", "Slow to <maxSpeed>")
    waypoint.set_value("MaxSpeedExceededInstruction", "Too fast")
    issues = validate_document(document)
    assert not [issue for issue in issues if issue.severity is Severity.ERROR]
    newly_supported = {
        "BodyRadius", "EnterSystemInstruction", "ApproachBodyInstruction", "MaxSpeed",
        "MaxSpeedWarningDistance", "MaxSpeedWarningTrigger", "MaxSpeedWarningInstruction",
        "MaxSpeedExceededInstruction",
    }
    assert not [
        issue for issue in issues
        if "Unknown field" in issue.message and any(key in issue.message for key in newly_supported)
    ]

    waypoint.set_value("MaxSpeedWarningDistance", "far")
    assert any("comma-separated list" in message for message in errors(document))
