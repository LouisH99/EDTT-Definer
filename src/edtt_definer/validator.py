from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from .model import EntryKind, EventDocument, Position, RawNode, SectionNode, SectionType
from .project import (
    AltitudeMode,
    ProjectData,
    StageMetadata,
    decimal_value,
    inferred_altitude_mode,
    inferred_maximum_altitude,
)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    message: str
    section_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class StartVehicleMismatch:
    race_type: str
    player_state: str
    player_state_race_type: str
    section_id: str


EVENT_STATES = {"AUTHOR", "ALPHA", "PROD", "DELETE"}
GAME_TYPES = {"ODYSSEY", "HORIZONS", "BOTH"}
RACE_TYPES = {"SHIP", "SRV", "FIGHTER", "ONFOOT"}
SHIP_SIZES = {"N/A", "S", "M", "L"}
START_STATES = {"ShipFlyingStart", "SRV", "OnFoot", "Ship", "FighterFlyingStart"}
STAGE_STATES = START_STATES | {"ShipPass", "Fighter"}
PLAYER_STATE_RACE_TYPES = {
    "Ship": "SHIP",
    "ShipFlyingStart": "SHIP",
    "ShipPass": "SHIP",
    "FighterFlyingStart": "FIGHTER",
    "Fighter": "FIGHTER",
    "SRV": "SRV",
    "OnFoot": "ONFOOT",
}
RACE_TYPE_VEHICLE_LABELS = {
    "SHIP": "Ship",
    "FIGHTER": "SLF",
    "SRV": "SRV",
    "ONFOOT": "On Foot",
}
REPEATABLE_KEYS = {
    "restriction", "condition", "nextinstruction", "startinstruction",
    "startorientationinstruction", "endinstruction",
}
COMMON_STAGE_FIELDS = {
    "playerstate", "system", "body", "bodyradius", "station", "position",
    "proximity", "altitude", "altitudeproximity", "condition",
    "entersysteminstruction", "approachbodyinstruction",
}
SPEED_FIELDS = {
    "maxspeed", "maxspeedwarningdistance", "maxspeedwarninginstruction",
    "maxspeedexceededinstruction", "maxspeedwarningtrigger",
}
KNOWN_FIELDS = {
    "eventinfo": {
        "eventref", "eventstate", "name", "shortdesc", "maxshipsize", "gametype",
        "racetype", "timepenalty", "maxpenalties", "restriction", "condition",
    },
    "start": COMMON_STAGE_FIELDS | {
        "startorientationinstruction", "startinstruction", "nextinstruction",
    },
    "waypoint": COMMON_STAGE_FIELDS | SPEED_FIELDS | {"nextinstruction"},
    "end": COMMON_STAGE_FIELDS | SPEED_FIELDS | {"lastlegmessage", "endinstruction"},
}


def _issue(
    issues: list[ValidationIssue], severity: Severity, message: str,
    section: SectionNode | None = None, field: str | None = None,
) -> None:
    issues.append(ValidationIssue(severity, message, section.node_id if section else None, field))


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip()) if value.strip() else None
    except InvalidOperation:
        return None


def _validate_position(section: SectionNode, issues: list[ValidationIssue]) -> None:
    raw = section.value("Position")
    conditions = section.values("Condition")
    if not raw:
        has_station = bool(section.value("Station"))
        has_condition = any(conditions)
        if not has_station and not has_condition:
            _issue(issues, Severity.ERROR, "A position is required.", section, "Position")
        return
    position = Position.parse(raw)
    try:
        lat_a, lon_a, lat_b, lon_b = position.numeric_values()
    except (InvalidOperation, ValueError):
        _issue(issues, Severity.ERROR, "Position must contain numeric latitude and longitude values.", section, "Position")
        return
    coordinates = [(lat_a, lon_a)]
    if lat_b is not None and lon_b is not None:
        coordinates.append((lat_b, lon_b))
    for latitude, longitude in coordinates:
        if not Decimal("-90") <= latitude <= Decimal("90"):
            _issue(issues, Severity.ERROR, "Latitude must be between -90 and 90.", section, "Position")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            _issue(issues, Severity.ERROR, "Longitude must be between -180 and 180.", section, "Position")
    if len(coordinates) == 2 and coordinates[0] == coordinates[1]:
        _issue(issues, Severity.ERROR, "Gate endpoints must be different.", section, "Position")

    proximity = section.value("Proximity")
    if position.is_gate:
        proximity_number = _decimal(proximity)
        if proximity and proximity_number not in {None, Decimal("0")}:
            _issue(issues, Severity.WARNING, "Gate waypoints normally omit Proximity.", section, "Proximity")
    elif not proximity:
        _issue(issues, Severity.ERROR, "A point position requires a Proximity radius.", section, "Proximity")

    if raw and (not section.value("System") or not section.value("Body")):
        _issue(issues, Severity.ERROR, "Surface coordinates require both System and Body.", section, "System")


def _validate_optional_number(
    section: SectionNode, key: str, issues: list[ValidationIssue], *, positive: bool = True,
) -> None:
    value = section.value(key)
    if not value:
        return
    number = _decimal(value)
    if number is None:
        _issue(issues, Severity.ERROR, f"{key} must be numeric.", section, key)
    elif positive and number <= 0:
        _issue(issues, Severity.ERROR, f"{key} must be greater than zero.", section, key)


def _validate_non_negative_number(
    section: SectionNode, key: str, issues: list[ValidationIssue],
) -> None:
    value = section.value(key)
    if not value:
        return
    number = _decimal(value)
    if number is None:
        _issue(issues, Severity.ERROR, f"{key} must be numeric.", section, key)
    elif number < 0:
        _issue(issues, Severity.ERROR, f"{key} cannot be negative.", section, key)


def _validate_number_list(
    section: SectionNode,
    key: str,
    issues: list[ValidationIssue],
    *,
    positive: bool,
    allow_blank_items: bool = False,
) -> None:
    value = section.value(key)
    if not value:
        return
    for item in value.split(","):
        item = item.strip()
        if not item and allow_blank_items:
            continue
        number = _decimal(item)
        if number is None:
            _issue(issues, Severity.ERROR, f"{key} must be a number or comma-separated list of numbers.", section, key)
            return
        if positive and number <= 0:
            _issue(issues, Severity.ERROR, f"Every {key} value must be greater than zero.", section, key)
            return
        if not positive and number < 0:
            _issue(issues, Severity.ERROR, f"{key} values cannot be negative.", section, key)
            return


def _validate_stage(section: SectionNode, issues: list[ValidationIssue]) -> None:
    state = section.value("PlayerState")
    allowed = START_STATES if section.section_type is SectionType.START else STAGE_STATES
    if not state:
        _issue(issues, Severity.ERROR, "PlayerState is required.", section, "PlayerState")
    elif state not in allowed:
        _issue(issues, Severity.ERROR, f"Unsupported PlayerState: {state}", section, "PlayerState")

    _validate_position(section, issues)
    position = Position.parse(section.value("Position"))
    if position.is_gate:
        _validate_optional_number(section, "Proximity", issues, positive=False)
        proximity = _decimal(section.value("Proximity"))
        if proximity is not None and proximity < 0:
            _issue(issues, Severity.ERROR, "Proximity cannot be negative.", section, "Proximity")
    else:
        _validate_optional_number(section, "Proximity", issues)
    _validate_optional_number(section, "Altitude", issues, positive=False)
    _validate_optional_number(section, "AltitudeProximity", issues)
    _validate_optional_number(section, "BodyRadius", issues)
    altitude_value = section.value("Altitude")
    altitude_number = _decimal(altitude_value)
    has_altitude = bool(altitude_value)
    has_tolerance = bool(section.value("AltitudeProximity"))
    if has_tolerance and not has_altitude:
        _issue(issues, Severity.ERROR, "Altitude and AltitudeProximity must be supplied together.", section, "Altitude")
    elif has_altitude and not has_tolerance:
        if altitude_number == Decimal("0"):
            _issue(
                issues,
                Severity.WARNING,
                "Legacy Altitude=0 without AltitudeProximity is treated as no altitude constraint.",
                section,
                "Altitude",
            )
        else:
            _issue(issues, Severity.ERROR, "Altitude and AltitudeProximity must be supplied together.", section, "Altitude")

    parameterless = {
        "OnlyStateMaxAltitudeOnMiss": None,
        "NoAltitudeSpikeFilter": {"Ship", "ShipPass", "ShipFlyingStart"},
        "NoCrossWaypointInstruction": {"ShipFlyingStart"},
        "HandBrakeOn": {"SRV"},
        "Salute": {"OnFoot"},
        "InsideBuilding": {"OnFoot"},
        "OnBuilding": {"OnFoot"},
    }
    for condition in section.values("Condition"):
        allowed_states = parameterless.get(condition)
        if allowed_states and state not in allowed_states:
            _issue(issues, Severity.WARNING, f"Condition {condition} is unusual for PlayerState {state}.", section, "Condition")

    if section.section_type is SectionType.START and not section.values("NextInstruction"):
        _issue(issues, Severity.ERROR, "The [start] block requires at least one NextInstruction.", section, "NextInstruction")
    if section.section_type is SectionType.END:
        if section.values("NextInstruction"):
            _issue(
                issues,
                Severity.ERROR,
                "The [end] block uses EndInstruction, not NextInstruction.",
                section,
                "NextInstruction",
            )
        if not section.values("EndInstruction"):
            _issue(issues, Severity.ERROR, "The [end] block requires an EndInstruction.", section, "EndInstruction")

    if section.section_type in {SectionType.WAYPOINT, SectionType.END}:
        _validate_non_negative_number(section, "MaxSpeed", issues)
        _validate_number_list(section, "MaxSpeedWarningDistance", issues, positive=True)
        _validate_number_list(
            section,
            "MaxSpeedWarningTrigger",
            issues,
            positive=False,
            allow_blank_items=True,
        )
        speed_dependents = (
            "MaxSpeedWarningDistance", "MaxSpeedWarningInstruction",
            "MaxSpeedExceededInstruction", "MaxSpeedWarningTrigger",
        )
        if not section.value("MaxSpeed") and any(section.value(key) for key in speed_dependents):
            _issue(
                issues,
                Severity.WARNING,
                "Max-speed warning settings are present but MaxSpeed is empty.",
                section,
                "MaxSpeed",
            )


def start_vehicle_mismatch(document: EventDocument) -> StartVehicleMismatch | None:
    """Return a RaceType/Start PlayerState mismatch when both values are known."""
    event_sections = document.sections(SectionType.EVENTINFO)
    start_sections = document.sections(SectionType.START)
    if not event_sections or not start_sections:
        return None
    race_type = event_sections[0].value("RaceType")
    player_state = start_sections[0].value("PlayerState")
    expected_race_type = PLAYER_STATE_RACE_TYPES.get(player_state)
    if not race_type or expected_race_type is None or race_type == expected_race_type:
        return None
    return StartVehicleMismatch(
        race_type=race_type,
        player_state=player_state,
        player_state_race_type=expected_race_type,
        section_id=start_sections[0].node_id,
    )


def _warn_for_waypoint_vehicle_outlier(
    document: EventDocument, issues: list[ValidationIssue],
) -> None:
    """Flag one waypoint whose vehicle differs from every other waypoint."""
    waypoints = document.sections(SectionType.WAYPOINT)
    if len(waypoints) < 3:
        return

    vehicles: list[str] = []
    for waypoint in waypoints:
        race_type = PLAYER_STATE_RACE_TYPES.get(waypoint.value("PlayerState"))
        if race_type is None:
            # Unknown PlayerState values already receive a validation error. Avoid
            # guessing whether one of them is a vehicle outlier as well.
            return
        vehicles.append(race_type)

    counts = {vehicle: vehicles.count(vehicle) for vehicle in set(vehicles)}
    majority = next(
        (vehicle for vehicle, count in counts.items() if count == len(waypoints) - 1),
        None,
    )
    outliers = [index for index, vehicle in enumerate(vehicles) if vehicle != majority]
    if majority is None or len(outliers) != 1:
        return

    outlier_index = outliers[0]
    outlier = vehicles[outlier_index]
    _issue(
        issues,
        Severity.WARNING,
        f"Waypoint {outlier_index + 1} uses {RACE_TYPE_VEHICLE_LABELS[outlier]} while "
        f"the other {len(waypoints) - 1} waypoints use "
        f"{RACE_TYPE_VEHICLE_LABELS[majority]}. This single vehicle-type difference "
        "may be accidental.",
        waypoints[outlier_index],
        "PlayerState",
    )


def validate_document(document: EventDocument) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in document.nodes:
        if isinstance(node, SectionNode) and not node.closer_raw:
            _issue(
                issues,
                Severity.ERROR,
                f"Section [{node.name}] is missing its matching [\\{node.name}] closing tag.",
                node,
            )
        elif isinstance(node, RawNode):
            stripped = node.raw.strip()
            if (
                stripped
                and stripped.casefold() not in {"[event]", "[\\event]"}
                and not stripped.startswith(("<!--", "<!–", "#", ";"))
            ):
                _issue(issues, Severity.WARNING, f"Unrecognized line outside a section is preserved: {node.raw}")
    known_sections = [section for section in document.sections() if section.section_type is not None]
    actual_order = [section.section_type for section in known_sections]
    expected_order = [SectionType.EVENTINFO, SectionType.START]

    for kind in (SectionType.EVENTINFO, SectionType.START, SectionType.END):
        count = len(document.sections(kind))
        if count != 1:
            _issue(issues, Severity.ERROR, f"Exactly one [{kind.value}] block is required (found {count}).")
    if actual_order:
        canonical = (
            actual_order[0] is SectionType.EVENTINFO
            and len(actual_order) >= 3
            and actual_order[1] is SectionType.START
            and actual_order[-1] is SectionType.END
            and all(item is SectionType.WAYPOINT for item in actual_order[2:-1])
        )
        if not canonical:
            _issue(issues, Severity.ERROR, "Blocks must be ordered [eventinfo], [start], [waypoint]..., [end].")

    event_sections = document.sections(SectionType.EVENTINFO)
    if event_sections:
        section = event_sections[0]
        required = ("EventRef", "EventState", "Name", "ShortDesc", "RaceType", "GameType")
        for key in required:
            if not section.value(key):
                _issue(issues, Severity.ERROR, f"{key} is required.", section, key)
        event_ref = section.value("EventRef")
        if event_ref and not re.fullmatch(r"[A-Za-z0-9]+", event_ref):
            if re.fullmatch(r"[A-Za-z0-9 _'-]+", event_ref):
                _issue(
                    issues,
                    Severity.WARNING,
                    "EventRef contains legacy spaces, underscores, or hyphens; EDCoPilot examples use these, but new references should prefer letters and numbers only.",
                    section,
                    "EventRef",
                )
            else:
                _issue(issues, Severity.ERROR, "EventRef contains unsupported characters.", section, "EventRef")
        enumerations = {
            "EventState": EVENT_STATES,
            "GameType": GAME_TYPES,
            "RaceType": RACE_TYPES,
        }
        for key, allowed in enumerations.items():
            value = section.value(key)
            if value and value not in allowed:
                _issue(issues, Severity.ERROR, f"Unsupported {key}: {value}", section, key)
        ship_size = section.value("MaxShipSize")
        if ship_size and ship_size not in SHIP_SIZES:
            _issue(
                issues,
                Severity.ERROR,
                f"Unsupported MaxShipSize: {ship_size}. Use N/A, S, M, or L.",
                section,
                "MaxShipSize",
            )
        race_type = section.value("RaceType")
        if race_type in {"SRV", "ONFOOT"} and ship_size and ship_size != "N/A":
            _issue(issues, Severity.WARNING, f"{race_type} races normally use MaxShipSize=N/A.", section, "MaxShipSize")
        state = section.value("EventState")
        if state == "PROD":
            _issue(issues, Severity.WARNING, "PROD events cannot be edited after publication.", section, "EventState")
        elif state == "DELETE":
            _issue(issues, Severity.WARNING, "Importing a DELETE event asks EDCoPilot to remove it.", section, "EventState")
        for key in ("TimePenalty", "MaxPenalties"):
            _validate_optional_number(section, key, issues)

    for section in document.sections(SectionType.START) + document.sections(SectionType.WAYPOINT) + document.sections(SectionType.END):
        _validate_stage(section, issues)

    starts = document.sections(SectionType.START)
    waypoints = document.sections(SectionType.WAYPOINT)
    if starts and waypoints:
        start_state = starts[0].value("PlayerState")
        if start_state in {"ShipFlyingStart", "FighterFlyingStart"} and waypoints[0].value("PlayerState") != start_state:
            _issue(
                issues, Severity.ERROR,
                f"The first waypoint must use PlayerState={start_state} for a flying start.",
                waypoints[0], "PlayerState",
            )

    mismatch = start_vehicle_mismatch(document)
    if mismatch is not None:
        start_section = document.section_by_id(mismatch.section_id)
        _issue(
            issues,
            Severity.WARNING,
            f"RaceType={mismatch.race_type} does not match Start "
            f"PlayerState={mismatch.player_state}, which belongs to "
            f"RaceType={mismatch.player_state_race_type}. Double-check the race vehicle before exporting.",
            start_section,
            "PlayerState",
        )

    _warn_for_waypoint_vehicle_outlier(document, issues)

    unknown_fields: dict[str, tuple[str, int, SectionNode]] = {}
    for section in document.sections():
        section_key = section.name.casefold()
        known = KNOWN_FIELDS.get(section_key)
        if known is None:
            _issue(issues, Severity.WARNING, f"Unknown section [{section.name}] is preserved.", section)
            continue
        counts: dict[str, int] = {}
        for entry in section.entries:
            if entry.kind is EntryKind.RAW:
                _issue(issues, Severity.WARNING, f"Unrecognized line is preserved: {entry.raw}", section)
            if entry.kind is EntryKind.KEY_VALUE and entry.key:
                folded = entry.key.casefold()
                counts[folded] = counts.get(folded, 0) + 1
                if folded not in known:
                    original, count, first_section = unknown_fields.get(
                        folded, (entry.key, 0, section)
                    )
                    unknown_fields[folded] = (original, count + 1, first_section)
        for key, count in counts.items():
            if count > 1 and key not in REPEATABLE_KEYS:
                _issue(issues, Severity.WARNING, f"Field {key} appears {count} times; editing it will consolidate the values.", section, key)

    for original, count, first_section in unknown_fields.values():
        suffix = f" in {count} sections" if count > 1 else ""
        _issue(
            issues,
            Severity.WARNING,
            f"Unknown field {original} appears{suffix} and is preserved in Advanced entries.",
            first_section,
            original,
        )

    return issues


def validate_project(project: ProjectData) -> list[ValidationIssue]:
    issues = validate_document(project.document)
    stages = (
        project.document.sections(SectionType.START)
        + project.document.sections(SectionType.WAYPOINT)
        + project.document.sections(SectionType.END)
    )
    for section in stages:
        metadata = project.stage_metadata.get(section.node_id, StageMetadata())
        if inferred_altitude_mode(section, metadata) is not AltitudeMode.MAXIMUM:
            continue
        maximum_text = inferred_maximum_altitude(section, metadata)
        if not maximum_text.strip():
            _issue(issues, Severity.ERROR, "Maximum height is required.", section, "MaximumAltitude")
            continue
        maximum = decimal_value(maximum_text)
        if maximum is None:
            _issue(issues, Severity.ERROR, "Maximum height must be numeric.", section, "MaximumAltitude")
            continue
        if maximum <= 0:
            _issue(issues, Severity.ERROR, "Maximum height must be greater than zero.", section, "MaximumAltitude")
            continue
        expected = maximum / Decimal("2")
        altitude = decimal_value(section.value("Altitude"))
        tolerance = decimal_value(section.value("AltitudeProximity"))
        if altitude != expected or tolerance != expected:
            _issue(
                issues,
                Severity.ERROR,
                "Maximum height is not synchronized with Altitude and AltitudeProximity.",
                section,
                "MaximumAltitude",
            )
    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity is Severity.ERROR for issue in issues)
