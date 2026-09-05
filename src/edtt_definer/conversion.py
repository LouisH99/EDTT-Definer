from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import EventDocument, SectionType


class RaceVehicle(str, Enum):
    SHIP = "SHIP"
    SLF = "FIGHTER"
    SRV = "SRV"
    ON_FOOT = "ONFOOT"

    @property
    def label(self) -> str:
        return {
            RaceVehicle.SHIP: "Ship",
            RaceVehicle.SLF: "SLF",
            RaceVehicle.SRV: "SRV",
            RaceVehicle.ON_FOOT: "On Foot",
        }[self]


@dataclass(frozen=True)
class FieldChange:
    section_id: str
    section_label: str
    field: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class VehicleConversionPlan:
    target: RaceVehicle
    changes: tuple[FieldChange, ...]
    preserves_flying_start: bool = False


FLYING_START_STATES = {"ShipFlyingStart", "FighterFlyingStart"}


def build_vehicle_conversion(
    document: EventDocument,
    target: RaceVehicle | str,
) -> VehicleConversionPlan:
    target = RaceVehicle(target)
    start = document.start
    flying_start = bool(
        target is RaceVehicle.SHIP
        and start
        and start.get("PlayerState") in FLYING_START_STATES
    )

    if target is RaceVehicle.SHIP:
        start_state = "ShipFlyingStart" if flying_start else "Ship"
        first_waypoint_state = "ShipFlyingStart" if flying_start else "ShipPass"
        waypoint_state = "ShipPass"
        end_state = "Ship"
    elif target is RaceVehicle.SLF:
        start_state = "FighterFlyingStart"
        first_waypoint_state = "FighterFlyingStart"
        waypoint_state = "Fighter"
        end_state = "Fighter"
    elif target is RaceVehicle.SRV:
        start_state = first_waypoint_state = waypoint_state = end_state = "SRV"
    else:
        start_state = first_waypoint_state = waypoint_state = end_state = "OnFoot"

    changes: list[FieldChange] = []

    def add_change(section, label: str, field: str, new_value: str) -> None:
        if section is None:
            return
        old_value = section.value(field)
        if old_value == new_value:
            return
        changes.append(
            FieldChange(
                section_id=section.node_id,
                section_label=label,
                field=field,
                old_value=old_value,
                new_value=new_value,
            )
        )

    event_sections = document.sections(SectionType.EVENTINFO)
    if event_sections:
        add_change(event_sections[0], "Event Info", "RaceType", target.value)

    start_sections = document.sections(SectionType.START)
    if start_sections:
        add_change(start_sections[0], "Start", "PlayerState", start_state)

    for index, waypoint in enumerate(document.sections(SectionType.WAYPOINT), start=1):
        state = first_waypoint_state if index == 1 else waypoint_state
        add_change(waypoint, f"Waypoint {index}", "PlayerState", state)

    end_sections = document.sections(SectionType.END)
    if end_sections:
        add_change(end_sections[0], "End", "PlayerState", end_state)

    return VehicleConversionPlan(target, tuple(changes), flying_start)


def apply_vehicle_conversion(
    document: EventDocument,
    plan: VehicleConversionPlan,
) -> None:
    for change in plan.changes:
        section = document.section_by_id(change.section_id)
        if section is not None:
            section.set_value(change.field, change.new_value)


def conversion_preview(plan: VehicleConversionPlan) -> str:
    if not plan.changes:
        return "No changes are needed."
    lines = []
    for change in plan.changes:
        old_value = change.old_value or "(missing)"
        lines.append(
            f"{change.section_label}: {change.field}  {old_value}  →  {change.new_value}"
        )
    return "\n".join(lines)
