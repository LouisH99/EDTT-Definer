from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable
from uuid import uuid4


class SectionType(str, Enum):
    EVENTINFO = "eventinfo"
    START = "start"
    WAYPOINT = "waypoint"
    END = "end"


class EntryKind(str, Enum):
    KEY_VALUE = "key_value"
    COMMENT = "comment"
    BLANK = "blank"
    RAW = "raw"


@dataclass
class RawNode:
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return {"node": "raw", "raw": self.raw}


@dataclass
class EntryNode:
    raw: str
    kind: EntryKind = EntryKind.RAW
    key: str | None = None
    value: str | None = None
    suffix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "kind": self.kind.value,
            "key": self.key,
            "value": self.value,
            "suffix": self.suffix,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntryNode:
        return cls(
            raw=str(data.get("raw", "")),
            kind=EntryKind(data.get("kind", EntryKind.RAW.value)),
            key=data.get("key"),
            value=data.get("value"),
            suffix=str(data.get("suffix", "")),
        )


@dataclass
class SectionNode:
    name: str
    opener_raw: str
    closer_raw: str
    entries: list[EntryNode] = field(default_factory=list)
    node_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def section_type(self) -> SectionType | None:
        try:
            return SectionType(self.name.lower())
        except ValueError:
            return None

    def values(self, key: str) -> list[str]:
        wanted = key.casefold()
        return [
            entry.value or ""
            for entry in self.entries
            if entry.kind is EntryKind.KEY_VALUE
            and entry.key is not None
            and entry.key.casefold() == wanted
        ]

    def value(self, key: str, default: str = "") -> str:
        values = self.values(key)
        return values[0] if values else default

    def set_value(self, key: str, value: str | None) -> None:
        matches = self._matching_indexes(key)
        clean_value = "" if value is None else str(value).strip()
        if not clean_value:
            if not matches:
                return
            if all(not (self.entries[index].value or "") for index in matches):
                return
            for index in reversed(matches):
                del self.entries[index]
            return
        if matches and self.entries[matches[0]].value == clean_value:
            return
        suffix = self.entries[matches[0]].suffix if matches else ""
        replacement = EntryNode(
            raw=f"{key}={clean_value}{suffix}",
            kind=EntryKind.KEY_VALUE,
            key=key,
            value=clean_value,
            suffix=suffix,
        )
        if matches:
            first = matches[0]
            self.entries[first] = replacement
            for index in reversed(matches[1:]):
                del self.entries[index]
        else:
            self.entries.insert(self._insertion_index(), replacement)

    def set_values(self, key: str, values: Iterable[str]) -> None:
        clean_values = [str(value).strip() for value in values if str(value).strip()]
        matches = self._matching_indexes(key)
        current_clean_values = [value.strip() for value in self.values(key) if value.strip()]
        if current_clean_values == clean_values:
            return
        original_entries = [self.entries[index] for index in matches]
        insert_at = matches[0] if matches else self._insertion_index()
        for index in reversed(matches):
            del self.entries[index]
        for offset, value in enumerate(clean_values):
            suffix = original_entries[offset].suffix if offset < len(original_entries) else ""
            self.entries.insert(
                insert_at + offset,
                EntryNode(
                    raw=f"{key}={value}{suffix}",
                    kind=EntryKind.KEY_VALUE,
                    key=key,
                    value=value,
                    suffix=suffix,
                ),
            )

    def duplicate_keys(self) -> list[str]:
        counts: dict[str, tuple[str, int]] = {}
        for entry in self.entries:
            if entry.kind is EntryKind.KEY_VALUE and entry.key:
                folded = entry.key.casefold()
                original, count = counts.get(folded, (entry.key, 0))
                counts[folded] = (original, count + 1)
        return [original for original, count in counts.values() if count > 1]

    def _matching_indexes(self, key: str) -> list[int]:
        wanted = key.casefold()
        return [
            index
            for index, entry in enumerate(self.entries)
            if entry.kind is EntryKind.KEY_VALUE
            and entry.key is not None
            and entry.key.casefold() == wanted
        ]

    def _insertion_index(self) -> int:
        index = len(self.entries)
        while index and self.entries[index - 1].kind is EntryKind.BLANK:
            index -= 1
        return index

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": "section",
            "name": self.name,
            "opener_raw": self.opener_raw,
            "closer_raw": self.closer_raw,
            "node_id": self.node_id,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SectionNode:
        return cls(
            name=str(data["name"]),
            opener_raw=str(data.get("opener_raw", f"[{data['name']}]")),
            closer_raw=str(data.get("closer_raw", f"[\\{data['name']}]")),
            node_id=str(data.get("node_id") or uuid4().hex),
            entries=[EntryNode.from_dict(item) for item in data.get("entries", [])],
        )


TopNode = RawNode | SectionNode


@dataclass
class Position:
    latitude_a: str = ""
    longitude_a: str = ""
    latitude_b: str = ""
    longitude_b: str = ""
    gate: bool = False

    @property
    def is_gate(self) -> bool:
        return self.gate or bool(self.latitude_b or self.longitude_b)

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.latitude_a, self.longitude_a, self.latitude_b, self.longitude_b)
        )

    def format(self) -> str:
        if not self.latitude_a and not self.longitude_a:
            return ""
        point_a = f"{self.latitude_a},{self.longitude_a}"
        if self.is_gate:
            return f"{point_a} to {self.latitude_b},{self.longitude_b}"
        return point_a

    @classmethod
    def parse(cls, text: str) -> Position:
        value = text.strip()
        if not value:
            return cls()
        parts = value.split(" to ")
        if len(parts) > 2:
            return cls(latitude_a=value)
        first = [part.strip() for part in parts[0].split(",", 1)]
        if len(first) != 2:
            return cls(latitude_a=value)
        if len(parts) == 1:
            return cls(first[0], first[1])
        second = [part.strip() for part in parts[1].split(",", 1)]
        if len(second) != 2:
            return cls(first[0], first[1], parts[1].strip(), "", True)
        return cls(first[0], first[1], second[0], second[1], True)

    def numeric_values(self) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
        lat_a = Decimal(self.latitude_a)
        lon_a = Decimal(self.longitude_a)
        if self.is_gate:
            return lat_a, lon_a, Decimal(self.latitude_b), Decimal(self.longitude_b)
        return lat_a, lon_a, None, None


@dataclass
class EventInfo:
    section: SectionNode

    def get(self, key: str, default: str = "") -> str:
        return self.section.value(key, default)

    def set(self, key: str, value: str | None) -> None:
        self.section.set_value(key, value)

    def get_many(self, key: str) -> list[str]:
        return self.section.values(key)

    def set_many(self, key: str, values: Iterable[str]) -> None:
        self.section.set_values(key, values)


@dataclass
class Stage:
    section: SectionNode

    @property
    def kind(self) -> SectionType | None:
        return self.section.section_type

    @property
    def position(self) -> Position:
        return Position.parse(self.section.value("Position"))

    @position.setter
    def position(self, value: Position) -> None:
        self.section.set_value("Position", value.format())

    def get(self, key: str, default: str = "") -> str:
        return self.section.value(key, default)

    def set(self, key: str, value: str | None) -> None:
        self.section.set_value(key, value)

    def get_many(self, key: str) -> list[str]:
        return self.section.values(key)

    def set_many(self, key: str, values: Iterable[str]) -> None:
        self.section.set_values(key, values)


@dataclass
class EventDocument:
    nodes: list[TopNode]
    newline: str = "\r\n"
    trailing_newline: bool = True
    encoding: str = "utf-8"

    @classmethod
    def new(cls) -> EventDocument:
        def section(name: str, pairs: list[tuple[str, str]]) -> SectionNode:
            node = SectionNode(name, f"[{name}]", f"[\\{name}]")
            for key, value in pairs:
                node.set_value(key, value)
            return node

        return cls(
            nodes=[
                RawNode("[event]"),
                RawNode(""),
                section(
                    "eventinfo",
                    [
                        ("EventState", "AUTHOR"),
                        ("MaxShipSize", "N/A"),
                        ("GameType", "BOTH"),
                        ("RaceType", "SHIP"),
                    ],
                ),
                RawNode(""),
                section("start", [("PlayerState", "Ship")]),
                RawNode(""),
                section("waypoint", [("PlayerState", "ShipPass")]),
                RawNode(""),
                section("end", [("PlayerState", "ShipPass")]),
                RawNode(""),
                RawNode("[\\event]"),
            ]
        )

    def sections(self, section_type: SectionType | str | None = None) -> list[SectionNode]:
        result = [node for node in self.nodes if isinstance(node, SectionNode)]
        if section_type is None:
            return result
        wanted = section_type.value if isinstance(section_type, SectionType) else section_type
        return [section for section in result if section.name.casefold() == wanted.casefold()]

    def section_by_id(self, node_id: str) -> SectionNode | None:
        return next((section for section in self.sections() if section.node_id == node_id), None)

    @property
    def event_info(self) -> EventInfo | None:
        sections = self.sections(SectionType.EVENTINFO)
        return EventInfo(sections[0]) if sections else None

    @property
    def start(self) -> Stage | None:
        sections = self.sections(SectionType.START)
        return Stage(sections[0]) if sections else None

    @property
    def waypoints(self) -> list[Stage]:
        return [Stage(section) for section in self.sections(SectionType.WAYPOINT)]

    @property
    def end(self) -> Stage | None:
        sections = self.sections(SectionType.END)
        return Stage(sections[0]) if sections else None

    def add_waypoint(self, player_state: str = "ShipPass") -> SectionNode:
        section = SectionNode("waypoint", "[waypoint]", "[\\waypoint]")
        section.set_value("PlayerState", player_state)
        end_section = next(iter(self.sections(SectionType.END)), None)
        if end_section is None:
            if self.nodes and not isinstance(self.nodes[-1], RawNode):
                self.nodes.append(RawNode(""))
            self.nodes.append(section)
        else:
            end_index = self.nodes.index(end_section)
            self.nodes.insert(end_index, section)
            self.nodes.insert(end_index + 1, RawNode(""))
        return section

    def duplicate_waypoint(self, node_id: str) -> SectionNode | None:
        source = self.section_by_id(node_id)
        if source is None or source.section_type is not SectionType.WAYPOINT:
            return None
        duplicate = deepcopy(source)
        duplicate.node_id = uuid4().hex
        index = self.nodes.index(source)
        self.nodes.insert(index + 1, RawNode(""))
        self.nodes.insert(index + 2, duplicate)
        return duplicate

    def remove_waypoint(self, node_id: str) -> bool:
        section = self.section_by_id(node_id)
        if section is None or section.section_type is not SectionType.WAYPOINT:
            return False
        index = self.nodes.index(section)
        del self.nodes[index]
        if index < len(self.nodes) and isinstance(self.nodes[index], RawNode) and not self.nodes[index].raw:
            del self.nodes[index]
        elif index and isinstance(self.nodes[index - 1], RawNode) and not self.nodes[index - 1].raw:
            del self.nodes[index - 1]
        return True

    def move_waypoint(self, node_id: str, direction: int) -> bool:
        waypoints = self.sections(SectionType.WAYPOINT)
        current = next((index for index, item in enumerate(waypoints) if item.node_id == node_id), None)
        if current is None:
            return False
        target = current + direction
        if target < 0 or target >= len(waypoints):
            return False
        first = self.nodes.index(waypoints[current])
        second = self.nodes.index(waypoints[target])
        self.nodes[first], self.nodes[second] = self.nodes[second], self.nodes[first]
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "newline": self.newline,
            "trailing_newline": self.trailing_newline,
            "encoding": self.encoding,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventDocument:
        nodes: list[TopNode] = []
        for item in data.get("nodes", []):
            if item.get("node") == "section":
                nodes.append(SectionNode.from_dict(item))
            else:
                nodes.append(RawNode(str(item.get("raw", ""))))
        return cls(
            nodes=nodes,
            newline=str(data.get("newline", "\r\n")),
            trailing_newline=bool(data.get("trailing_newline", True)),
            encoding=str(data.get("encoding", "utf-8")),
        )


def decimal_or_none(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None
