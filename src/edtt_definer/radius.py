from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def normalize_name(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def positive_decimal(value: Any) -> Decimal | None:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


@dataclass(frozen=True)
class RadiusRecord:
    radius_km: Decimal
    origin: str


class BodyRadiusCache:
    """Small persistent lookup keyed by game IDs and by normalized names."""

    def __init__(self, entries: dict[str, dict[str, str]] | None = None):
        self._entries = entries or {}
        self.dirty = False

    @staticmethod
    def _id_key(system_address: int | None, body_id: int | None) -> str | None:
        if system_address is None or body_id is None:
            return None
        return f"id:{system_address}:{body_id}"

    @staticmethod
    def _name_key(system: str | None, body: str | None) -> str | None:
        system_name = normalize_name(system)
        body_name = normalize_name(body)
        if not system_name or not body_name:
            return None
        return f"name:{system_name}:{body_name}"

    def put(
        self,
        radius_km: Decimal | str,
        *,
        origin: str,
        system: str | None = None,
        body: str | None = None,
        system_address: int | None = None,
        body_id: int | None = None,
    ) -> bool:
        radius = positive_decimal(radius_km)
        keys = {
            key
            for key in (
                self._id_key(system_address, body_id),
                self._name_key(system, body),
            )
            if key
        }
        if radius is None or not keys:
            return False
        value = {"radius_km": format(radius, "f"), "origin": origin}
        changed = False
        for key in keys:
            if self._entries.get(key) != value:
                self._entries[key] = value
                changed = True
        self.dirty = self.dirty or changed
        return changed

    def get(
        self,
        *,
        system: str | None = None,
        body: str | None = None,
        system_address: int | None = None,
        body_id: int | None = None,
    ) -> RadiusRecord | None:
        for key in (
            self._id_key(system_address, body_id),
            self._name_key(system, body),
        ):
            if not key:
                continue
            raw = self._entries.get(key)
            if not isinstance(raw, dict):
                continue
            radius = positive_decimal(raw.get("radius_km"))
            if radius is not None:
                return RadiusRecord(radius, str(raw.get("origin", "journal")))
        return None

    def to_json(self) -> str:
        return json.dumps(self._entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, value: str | None) -> BodyRadiusCache:
        if not value:
            return cls()
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        entries = {
            str(key): {str(item_key): str(item_value) for item_key, item_value in raw.items()}
            for key, raw in data.items()
            if isinstance(raw, dict)
        }
        return cls(entries)

    def mark_clean(self) -> None:
        self.dirty = False
