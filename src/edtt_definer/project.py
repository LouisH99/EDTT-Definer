from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from .model import (
    EntryKind,
    EntryNode,
    EventDocument,
    RawNode,
    SectionNode,
    SectionType,
)
from .parser import atomic_write, save_document, serialize_document


PROJECT_SCHEMA_VERSION = 4
_VERSION_COMMENT_RE = re.compile(
    r"^\s*<!--\s*EDTTD:\s*Time-trial version\s+(\d+)\s+—\s+"
    r"Last saved\s+(.+?)\s*-->\s*$",
    re.IGNORECASE,
)
_PENDING_VERSION_COMMENT_RE = re.compile(
    r"^\s*<!--\s*EDTTD:\s*Unsaved changes\s+—\s+next time-trial version\s+"
    r"\d+\s+will be assigned when saved\s*-->\s*$",
    re.IGNORECASE,
)
_STAGE_COMMENT_RE = re.compile(
    r"^\s*<!--\s*EDTTD:\s*(.*?)\s*-->\s*$",
    re.IGNORECASE,
)


class AltitudeMode(str, Enum):
    NONE = "none"
    MAXIMUM = "maximum"
    TARGET = "target"


@dataclass
class StageMetadata:
    display_name: str = ""
    altitude_mode: AltitudeMode | None = None
    maximum_altitude: str = ""
    altitude_locked: bool = True
    separator_before: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "altitude_mode": self.altitude_mode.value if self.altitude_mode else None,
            "maximum_altitude": self.maximum_altitude,
            "altitude_locked": self.altitude_locked,
            "separator_before": self.separator_before,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageMetadata:
        raw_mode = data.get("altitude_mode")
        try:
            mode = AltitudeMode(raw_mode) if raw_mode else None
        except ValueError:
            mode = None
        return cls(
            display_name=str(data.get("display_name", "")),
            altitude_mode=mode,
            maximum_altitude=str(data.get("maximum_altitude", "")),
            altitude_locked=bool(data.get("altitude_locked", True)),
            separator_before=str(data.get("separator_before", "")),
        )


def decimal_value(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip()) if value.strip() else None
    except InvalidOperation:
        return None


def inferred_altitude_mode(section: SectionNode, metadata: StageMetadata) -> AltitudeMode:
    if metadata.altitude_mode is not None:
        return metadata.altitude_mode
    altitude_text = section.value("Altitude")
    tolerance_text = section.value("AltitudeProximity")
    if not altitude_text and not tolerance_text:
        return AltitudeMode.NONE
    altitude = decimal_value(altitude_text)
    tolerance = decimal_value(tolerance_text)
    if altitude is not None and tolerance is not None and altitude == tolerance:
        return AltitudeMode.MAXIMUM
    return AltitudeMode.TARGET


def inferred_maximum_altitude(section: SectionNode, metadata: StageMetadata) -> str:
    if metadata.maximum_altitude.strip():
        return metadata.maximum_altitude.strip()
    altitude = decimal_value(section.value("Altitude"))
    tolerance = decimal_value(section.value("AltitudeProximity"))
    if altitude is None or tolerance is None:
        return ""
    return format(altitude + tolerance, "f")


def apply_maximum_altitude(section: SectionNode, maximum_text: str) -> bool:
    maximum = decimal_value(maximum_text)
    if maximum is None or maximum <= 0:
        section.set_value("Altitude", "")
        section.set_value("AltitudeProximity", "")
        return False
    half_value = maximum / Decimal("2")
    if (
        decimal_value(section.value("Altitude")) == half_value
        and decimal_value(section.value("AltitudeProximity")) == half_value
    ):
        return True
    half = format(half_value, "f")
    section.set_value("Altitude", half)
    section.set_value("AltitudeProximity", half)
    return True


@dataclass
class ProjectData:
    document: EventDocument = field(default_factory=EventDocument.new)
    capture_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_export_path: str | None = None
    stage_metadata: dict[str, StageMetadata] = field(default_factory=dict)
    time_trial_version: int = 0
    last_saved_at: str | None = None

    @classmethod
    def new(cls) -> ProjectData:
        document = EventDocument.new()
        project = cls(document=document)
        for section in (
            document.sections(SectionType.START)
            + document.sections(SectionType.WAYPOINT)
            + document.sections(SectionType.END)
        ):
            project.stage_metadata[section.node_id] = StageMetadata(
                altitude_mode=AltitudeMode.MAXIMUM,
            )
        return project

    def metadata_for(self, node_id: str) -> StageMetadata:
        return self.stage_metadata.setdefault(node_id, StageMetadata())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "document": self.document.to_dict(),
            "capture_metadata": self.capture_metadata,
            "last_export_path": self.last_export_path,
            "stage_metadata": {
                node_id: metadata.to_dict()
                for node_id, metadata in self.stage_metadata.items()
            },
            "time_trial_version": self.time_trial_version,
            "last_saved_at": self.last_saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectData:
        version = data.get("schema_version")
        if version not in {1, 2, 3, PROJECT_SCHEMA_VERSION}:
            raise ValueError(f"Unsupported project schema version: {version!r}")
        raw_stage_metadata = data.get("stage_metadata", {}) if version >= 2 else {}
        return cls(
            document=EventDocument.from_dict(data["document"]),
            capture_metadata=dict(data.get("capture_metadata", {})),
            last_export_path=data.get("last_export_path"),
            stage_metadata={
                str(node_id): StageMetadata.from_dict(metadata)
                for node_id, metadata in raw_stage_metadata.items()
                if isinstance(metadata, dict)
            },
            time_trial_version=max(0, int(data.get("time_trial_version", 0)))
            if version >= 4
            else 0,
            last_saved_at=(
                normalize_saved_at(data.get("last_saved_at")) if version >= 4 else None
            ),
        )


def normalize_saved_at(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def format_saved_at(value: str | None, *, include_offset: bool = False) -> str:
    normalized = normalize_saved_at(value)
    if normalized is None:
        return "Never"
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone()
    result = parsed.strftime("%Y-%m-%d %H:%M:%S")
    if include_offset:
        offset = parsed.strftime("%z")
        if offset:
            result += f" {offset[:3]}:{offset[3:]}"
    return result


def _saved_at_now(saved_at: datetime | None = None) -> str:
    current = saved_at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Saved timestamp must include a timezone.")
    return (
        current.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def save_project(
    project: ProjectData,
    path: str | Path,
    *,
    saved_at: datetime | None = None,
) -> None:
    next_version = max(0, int(project.time_trial_version)) + 1
    next_saved_at = _saved_at_now(saved_at)
    payload_data = project.to_dict()
    payload_data["time_trial_version"] = next_version
    payload_data["last_saved_at"] = next_saved_at
    payload = (
        json.dumps(payload_data, ensure_ascii=False, indent=2).encode("utf-8")
        + b"\n"
    )
    atomic_write(path, payload)
    project.time_trial_version = next_version
    project.last_saved_at = next_saved_at


def load_project(path: str | Path) -> ProjectData:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProjectData.from_dict(data)


def _comment_safe(value: str) -> str:
    return " ".join(value.replace("--", "—").split())


def _generated_version_comment(raw: str) -> bool:
    return bool(
        _VERSION_COMMENT_RE.fullmatch(raw)
        or _PENDING_VERSION_COMMENT_RE.fullmatch(raw)
    )


def _generated_stage_label(
    section_type: SectionType,
    waypoint_number: int | None,
    display_name: str,
    route_section: str,
) -> str:
    name = _comment_safe(display_name)
    if section_type is SectionType.START:
        return f"Start — {name}"
    if section_type is SectionType.END:
        return f"End — {name}"
    prefix = f"({_comment_safe(route_section)}) " if route_section.strip() else ""
    label = f"{prefix}WP{waypoint_number or 0}"
    return f"{label} — {name}" if name else label


def _stage_alias_from_comment(raw: str, section_type: SectionType) -> str | None:
    match = _STAGE_COMMENT_RE.fullmatch(raw)
    if match is None:
        return None
    label = match.group(1).strip()
    if label.casefold().startswith(("time-trial version", "unsaved changes")):
        return None
    if section_type is SectionType.START:
        prefix = "Start — "
        return (
            label[len(prefix):].strip()
            if label.casefold().startswith(prefix.casefold())
            else None
        )
    if section_type is SectionType.END:
        prefix = "End — "
        return (
            label[len(prefix):].strip()
            if label.casefold().startswith(prefix.casefold())
            else None
        )
    waypoint_match = re.fullmatch(
        r"(?:\(.*\)\s+)?WP\d+(?:\s+—\s+(.+))?",
        label,
        re.IGNORECASE,
    )
    if waypoint_match is None:
        return None
    return waypoint_match.group(1).strip() if waypoint_match.group(1) else ""


def _waypoint_route_section_from_comment(raw: str) -> str:
    match = _STAGE_COMMENT_RE.fullmatch(raw)
    if match is None:
        return ""
    waypoint_match = re.fullmatch(
        r"(?:\((.*)\)\s+)?WP\d+(?:\s+—\s+.+)?",
        match.group(1).strip(),
        re.IGNORECASE,
    )
    return (
        waypoint_match.group(1).strip()
        if waypoint_match and waypoint_match.group(1)
        else ""
    )


def project_from_document(
    document: EventDocument,
    *,
    last_export_path: str | None = None,
) -> ProjectData:
    """Create an editable project and recover EDTTD metadata from an export."""

    project = ProjectData(document=document, last_export_path=last_export_path)
    current_route_section = ""
    for node in document.nodes:
        if isinstance(node, RawNode):
            version_match = _VERSION_COMMENT_RE.fullmatch(node.raw)
            if version_match:
                project.time_trial_version = int(version_match.group(1))
                project.last_saved_at = normalize_saved_at(version_match.group(2))
            continue
        kind = node.section_type
        if kind not in {SectionType.START, SectionType.WAYPOINT, SectionType.END}:
            continue
        for entry in node.entries:
            alias = _stage_alias_from_comment(entry.raw, kind)
            if alias is not None:
                metadata = project.metadata_for(node.node_id)
                metadata.display_name = alias
                if kind is SectionType.WAYPOINT:
                    route_section = _waypoint_route_section_from_comment(entry.raw)
                    if route_section and route_section != current_route_section:
                        metadata.separator_before = route_section
                    current_route_section = route_section
                break
    return project


def build_export_document(
    project: ProjectData,
    *,
    pending_save: bool = False,
) -> EventDocument:
    """Return a syntax-tree copy enriched with editor metadata comments."""

    document = deepcopy(project.document)
    document.nodes = [
        node
        for node in document.nodes
        if not (isinstance(node, RawNode) and _generated_version_comment(node.raw))
    ]

    next_version = max(0, int(project.time_trial_version)) + 1
    version_comment = ""
    if pending_save:
        version_comment = (
            f"<!-- EDTTD: Unsaved changes — next time-trial version {next_version} "
            "will be assigned when saved -->"
        )
    elif project.time_trial_version > 0 and project.last_saved_at:
        version_comment = (
            f"<!-- EDTTD: Time-trial version {project.time_trial_version} — Last saved "
            f"{format_saved_at(project.last_saved_at, include_offset=True)} -->"
        )
    if version_comment:
        event_index = next(
            (
                index
                for index, node in enumerate(document.nodes)
                if isinstance(node, RawNode)
                and node.raw.strip().casefold() == "[event]"
            ),
            -1,
        )
        document.nodes.insert(
            event_index + 1 if event_index >= 0 else 0,
            RawNode(version_comment),
        )

    waypoint_number = 0
    route_section = ""
    for section in document.sections():
        kind = section.section_type
        if kind not in {SectionType.START, SectionType.WAYPOINT, SectionType.END}:
            continue
        section.entries = [
            entry
            for entry in section.entries
            if not (
                entry.kind is EntryKind.COMMENT
                and _stage_alias_from_comment(entry.raw, kind) is not None
            )
        ]
        metadata = project.stage_metadata.get(section.node_id, StageMetadata())
        if kind is SectionType.WAYPOINT:
            waypoint_number += 1
            if metadata.separator_before.strip():
                route_section = metadata.separator_before.strip()
        name = metadata.display_name.strip()
        if not name and kind is not SectionType.WAYPOINT:
            continue
        label = _generated_stage_label(kind, waypoint_number, name, route_section)
        section.entries.insert(
            0,
            EntryNode(
                raw=f"<!-- EDTTD: {label} -->",
                kind=EntryKind.COMMENT,
            ),
        )
    return document


def serialize_project_export(project: ProjectData, *, pending_save: bool = False) -> str:
    return serialize_document(build_export_document(project, pending_save=pending_save))


def save_project_export(project: ProjectData, path: str | Path) -> None:
    save_document(build_export_document(project), path)
