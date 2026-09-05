from __future__ import annotations

import pytest

from edtt_definer.conversion import (
    RaceVehicle,
    apply_vehicle_conversion,
    build_vehicle_conversion,
    conversion_preview,
)
from edtt_definer.model import EventDocument, SectionType
from edtt_definer.parser import parse_text, serialize_document

from .test_parser import WRAPPED_SAMPLE


def _states(document: EventDocument) -> tuple[str, list[str], str]:
    return (
        document.start.get("PlayerState"),
        [stage.get("PlayerState") for stage in document.waypoints],
        document.end.get("PlayerState"),
    )


@pytest.mark.parametrize(
    ("target", "race_type", "start", "waypoints", "end"),
    [
        (RaceVehicle.SLF, "FIGHTER", "FighterFlyingStart", ["FighterFlyingStart", "Fighter"], "Fighter"),
        (RaceVehicle.SRV, "SRV", "SRV", ["SRV", "SRV"], "SRV"),
        (RaceVehicle.ON_FOOT, "ONFOOT", "OnFoot", ["OnFoot", "OnFoot"], "OnFoot"),
    ],
)
def test_vehicle_conversion_mappings(target, race_type, start, waypoints, end) -> None:
    document = EventDocument.new()
    document.add_waypoint("ShipPass")

    plan = build_vehicle_conversion(document, target)
    apply_vehicle_conversion(document, plan)

    assert document.event_info.get("RaceType") == race_type
    assert _states(document) == (start, waypoints, end)
    assert target.label in {"SLF", "SRV", "On Foot"}


def test_ship_conversion_preserves_a_ship_or_slf_flying_start() -> None:
    document = EventDocument.new()
    document.start.set("PlayerState", "FighterFlyingStart")
    document.waypoints[0].set("PlayerState", "FighterFlyingStart")
    document.add_waypoint("Fighter")
    document.end.set("PlayerState", "Fighter")

    plan = build_vehicle_conversion(document, RaceVehicle.SHIP)
    apply_vehicle_conversion(document, plan)

    assert plan.preserves_flying_start
    assert document.event_info.get("RaceType") == "SHIP"
    assert _states(document) == (
        "ShipFlyingStart",
        ["ShipFlyingStart", "ShipPass"],
        "Ship",
    )


def test_ship_conversion_uses_landed_start_from_non_flying_race() -> None:
    document = EventDocument.new()
    document.event_info.set("RaceType", "SRV")
    document.start.set("PlayerState", "SRV")
    document.waypoints[0].set("PlayerState", "SRV")
    document.end.set("PlayerState", "SRV")

    plan = build_vehicle_conversion(document, RaceVehicle.SHIP)
    apply_vehicle_conversion(document, plan)

    assert not plan.preserves_flying_start
    assert _states(document) == ("Ship", ["ShipPass"], "Ship")


def test_conversion_changes_only_race_type_and_player_state_syntax_nodes() -> None:
    document = parse_text(WRAPPED_SAMPLE)
    event = document.sections(SectionType.EVENTINFO)[0]
    start = document.sections(SectionType.START)[0]
    event.set_value("CustomFuture", "keep")
    before_values = {
        (section.node_id, entry.key): entry.raw
        for section in document.sections()
        for entry in section.entries
        if entry.key not in {"RaceType", "PlayerState"}
    }

    plan = build_vehicle_conversion(document, RaceVehicle.SLF)
    preview = conversion_preview(plan)
    apply_vehicle_conversion(document, plan)

    after_values = {
        (section.node_id, entry.key): entry.raw
        for section in document.sections()
        for entry in section.entries
        if entry.key not in {"RaceType", "PlayerState"}
    }
    assert before_values == after_values
    assert "RaceType  SRV  →  FIGHTER" in preview
    assert "PlayerState=FighterFlyingStart      <!-- state note" in serialize_document(document)
    assert event.value("CustomFuture") == "keep"
    assert start.value("System") == "Sol"


def test_conversion_handles_missing_blocks_and_noop_documents() -> None:
    document = EventDocument(nodes=[])
    plan = build_vehicle_conversion(document, RaceVehicle.SRV)
    assert plan.changes == ()
    assert conversion_preview(plan) == "No changes are needed."
    apply_vehicle_conversion(document, plan)
