from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from .model import EventDocument, SectionNode, SectionType
from .parser import atomic_write


PROJECT_SCHEMA_VERSION = 2


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "altitude_mode": self.altitude_mode.value if self.altitude_mode else None,
            "maximum_altitude": self.maximum_altitude,
            "altitude_locked": self.altitude_locked,
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectData:
        version = data.get("schema_version")
        if version not in {1, PROJECT_SCHEMA_VERSION}:
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
        )


def save_project(project: ProjectData, path: str | Path) -> None:
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    atomic_write(path, payload)


def load_project(path: str | Path) -> ProjectData:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProjectData.from_dict(data)
