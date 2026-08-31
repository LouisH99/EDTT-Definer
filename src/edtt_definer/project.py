from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import EventDocument
from .parser import atomic_write


PROJECT_SCHEMA_VERSION = 1


@dataclass
class ProjectData:
    document: EventDocument = field(default_factory=EventDocument.new)
    capture_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_export_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "document": self.document.to_dict(),
            "capture_metadata": self.capture_metadata,
            "last_export_path": self.last_export_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectData:
        version = data.get("schema_version")
        if version != PROJECT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported project schema version: {version!r}")
        return cls(
            document=EventDocument.from_dict(data["document"]),
            capture_metadata=dict(data.get("capture_metadata", {})),
            last_export_path=data.get("last_export_path"),
        )


def save_project(project: ProjectData, path: str | Path) -> None:
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    atomic_write(path, payload)


def load_project(path: str | Path) -> ProjectData:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProjectData.from_dict(data)

