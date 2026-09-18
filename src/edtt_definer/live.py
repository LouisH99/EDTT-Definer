from __future__ import annotations

import json
import os
import time
import winreg
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

from .radius import BodyRadiusCache, RadiusRecord, normalize_name, positive_decimal


SAVED_GAMES_GUID = "{4C5C32FF-BB9D-43B0-BF4C-2A725E4234A0}"
HAS_LAT_LONG_FLAG = 1 << 21


class LiveState(str, Enum):
    MISSING = "missing"
    INVALID = "invalid"
    STALE = "stale"
    NO_COORDINATES = "no_coordinates"
    LIVE = "live"


@dataclass
class JournalContext:
    system: str | None = None
    system_address: int | None = None
    body: str | None = None
    body_id: int | None = None
    body_radius_km: Decimal | None = None
    body_radius_source: str | None = None
    station: str | None = None


@dataclass
class LocationSnapshot:
    timestamp: str | None = None
    system: str | None = None
    system_address: int | None = None
    body: str | None = None
    body_id: int | None = None
    body_radius_km: Decimal | None = None
    body_radius_source: str | None = None
    station: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    altitude: Decimal | None = None
    heading: Decimal | None = None
    flags: int | None = None
    flag_has_lat_long: bool | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def capture_ready(self) -> bool:
        return self.has_coordinates and self.flag_has_lat_long is not False

    def to_metadata(self) -> dict[str, str | int | None]:
        def text(value: Decimal | None) -> str | None:
            return format(value, "f") if value is not None else None

        return {
            "timestamp": self.timestamp,
            "system": self.system,
            "system_address": self.system_address,
            "body": self.body,
            "body_id": self.body_id,
            "body_radius_km": text(self.body_radius_km),
            "body_radius_source": self.body_radius_source,
            "station": self.station,
            "latitude": text(self.latitude),
            "longitude": text(self.longitude),
            "altitude": text(self.altitude),
            "heading": text(self.heading),
            "flags": self.flags,
        }


@dataclass
class LiveResult:
    state: LiveState
    snapshot: LocationSnapshot | None
    message: str


class LiveFileWatcher(QObject):
    """Watch Elite's replace-on-write Status file and newest journal safely."""

    changed = Signal()

    def __init__(
        self, journal_directory: str | Path | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.journal_directory = (
            Path(journal_directory) if journal_directory else default_journal_directory()
        )
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._source_changed)
        self.watcher.directoryChanged.connect(self._source_changed)
        self.refresh_paths()

    @property
    def status_path(self) -> Path:
        return self.journal_directory / "Status.json"

    def set_journal_directory(self, path: str | Path) -> None:
        existing = self.watcher.files() + self.watcher.directories()
        if existing:
            self.watcher.removePaths(existing)
        self.journal_directory = Path(path)
        self.refresh_paths()
        self.changed.emit()

    def _latest_journal(self) -> Path | None:
        try:
            files = list(self.journal_directory.glob("Journal.*.log"))
            return max(files, key=lambda item: item.stat().st_mtime, default=None)
        except OSError:
            return None

    def refresh_paths(self) -> None:
        desired_files: set[str] = set()
        desired_directories: set[str] = set()
        if self.journal_directory.is_dir():
            desired_directories.add(str(self.journal_directory.resolve()))
        if self.status_path.is_file():
            desired_files.add(str(self.status_path.resolve()))
        journal = self._latest_journal()
        if journal is not None:
            desired_files.add(str(journal.resolve()))

        current_files = set(self.watcher.files())
        current_directories = set(self.watcher.directories())
        remove = list(
            (current_files - desired_files) | (current_directories - desired_directories)
        )
        if remove:
            self.watcher.removePaths(remove)
        add = list(
            (desired_files - current_files) | (desired_directories - current_directories)
        )
        if add:
            self.watcher.addPaths(add)

    def _source_changed(self, _path: str) -> None:
        # Elite replaces Status.json instead of updating it in place, which
        # drops a file watch on Windows. The directory watch remains and lets
        # us attach to the replacement immediately.
        self.refresh_paths()
        self.changed.emit()


def default_journal_directory() -> Path:
    saved_games: str | None = None
    if os.name == "nt":
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                saved_games, _ = winreg.QueryValueEx(key, SAVED_GAMES_GUID)
                saved_games = os.path.expandvars(saved_games)
        except OSError:
            saved_games = None
    base = Path(saved_games) if saved_games else Path.home() / "Saved Games"
    return base / "Frontier Developments" / "Elite Dangerous"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


class LiveLocationReader:
    def __init__(
        self,
        journal_directory: str | Path | None = None,
        *,
        stale_after: float = 15.0,
        radius_cache: BodyRadiusCache | None = None,
    ):
        self.journal_directory = Path(journal_directory) if journal_directory else default_journal_directory()
        self.stale_after = stale_after
        self.radius_cache = radius_cache or BodyRadiusCache()
        self.last_snapshot: LocationSnapshot | None = None
        self.context = JournalContext()
        self._journal_path: Path | None = None
        self._journal_offset = 0
        self._journal_needs_alignment = False

    @property
    def status_path(self) -> Path:
        return self.journal_directory / "Status.json"

    def set_journal_directory(self, path: str | Path) -> None:
        self.journal_directory = Path(path)
        self.last_snapshot = None
        self.context = JournalContext()
        self._journal_path = None
        self._journal_offset = 0
        self._journal_needs_alignment = False

    def poll(self) -> LiveResult:
        self._poll_journal()
        path = self.status_path
        if not path.exists():
            return LiveResult(LiveState.MISSING, self.last_snapshot, f"Status.json not found in {self.journal_directory}")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
        except (OSError, json.JSONDecodeError, UnicodeError):
            return LiveResult(LiveState.INVALID, self.last_snapshot, "Status.json is being updated; retaining the last valid reading.")

        flags_value = data.get("Flags")
        try:
            flags = int(flags_value) if flags_value is not None else None
        except (TypeError, ValueError):
            flags = None
        flag_has_lat_long = bool(flags & HAS_LAT_LONG_FLAG) if flags is not None else None
        snapshot = LocationSnapshot(
            timestamp=data.get("timestamp"),
            system=self.context.system,
            system_address=self.context.system_address,
            body=data.get("BodyName") or self.context.body,
            station=self.context.station,
            latitude=_decimal(data.get("Latitude")),
            longitude=_decimal(data.get("Longitude")),
            altitude=_decimal(data.get("Altitude")),
            heading=_decimal(data.get("Heading")),
            flags=flags,
            flag_has_lat_long=flag_has_lat_long,
        )
        same_body = normalize_name(snapshot.body) == normalize_name(self.context.body)
        snapshot.body_id = self.context.body_id if same_body else None
        record = self._radius_for(
            snapshot.system,
            snapshot.body,
            snapshot.system_address,
            snapshot.body_id,
        )
        if same_body and self.context.body_radius_km is not None:
            snapshot.body_radius_km = self.context.body_radius_km
            snapshot.body_radius_source = self.context.body_radius_source or "Journal"
        elif record is not None:
            snapshot.body_radius_km = record.radius_km
            snapshot.body_radius_source = self._cached_source(record)
        self.last_snapshot = snapshot
        # Status.json's embedded timestamp can lag behind the PC clock even
        # while Elite is actively replacing the file with new coordinates.
        # File age measures the update we actually observed and avoids
        # misclassifying continuously changing game data as stale. The UI's
        # file-watch timer handles ongoing no-change detection after this read.
        age = _file_age_seconds(path)
        if age is not None and age > self.stale_after:
            return LiveResult(
                LiveState.STALE,
                snapshot,
                f"Status.json has not changed for {int(age)} seconds.",
            )
        if not snapshot.has_coordinates:
            return LiveResult(LiveState.NO_COORDINATES, snapshot, "Game connected, but no planetary coordinates are available.")
        if snapshot.flag_has_lat_long is False:
            return LiveResult(LiveState.NO_COORDINATES, snapshot, "Coordinates are present, but the Has Lat/Long game flag is not set.")
        return LiveResult(LiveState.LIVE, snapshot, "Live planetary coordinates available.")

    def _latest_journal(self) -> Path | None:
        try:
            files = list(self.journal_directory.glob("Journal.*.log"))
        except OSError:
            return None
        return max(files, key=lambda item: item.stat().st_mtime, default=None)

    def _poll_journal(self) -> None:
        latest = self._latest_journal()
        if latest is None:
            return
        if latest != self._journal_path:
            self._journal_path = latest
            self._journal_offset = max(0, latest.stat().st_size - 8 * 1024 * 1024)
            self._journal_needs_alignment = self._journal_offset > 0
            self.context = JournalContext()
        try:
            size = latest.stat().st_size
            if size < self._journal_offset:
                self._journal_offset = 0
                self._journal_needs_alignment = False
                self.context = JournalContext()
            with latest.open("r", encoding="utf-8-sig", errors="replace") as handle:
                handle.seek(self._journal_offset)
                if self._journal_needs_alignment:
                    handle.readline()
                    self._journal_needs_alignment = False
                for line in handle:
                    self._consume_journal_line(line)
                self._journal_offset = handle.tell()
        except OSError:
            return

    def _consume_journal_line(self, line: str) -> None:
        try:
            event = json.loads(line, parse_float=Decimal)
        except json.JSONDecodeError:
            return
        event_name = event.get("event")
        system = event.get("StarSystem")
        if system:
            self.context.system = system
        system_address = self._integer(event.get("SystemAddress"))
        if system_address is not None:
            self.context.system_address = system_address
        if event_name == "Scan":
            self._consume_scan(event)
        if event_name in {"Location", "FSDJump", "CarrierJump", "ApproachBody", "Touchdown", "Liftoff", "SupercruiseExit"}:
            body = event.get("Body") or event.get("BodyName")
            if body:
                self.context.body = body
                self.context.body_id = self._integer(event.get("BodyID"))
                self._resolve_context_radius()
            elif event_name in {"FSDJump", "CarrierJump"}:
                self._clear_body()
        if event_name == "LeaveBody":
            self._clear_body()
        if event_name == "Docked":
            self.context.station = event.get("StationName")
        elif event_name in {"Undocked", "FSDJump", "CarrierJump"}:
            self.context.station = None

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cached_source(record: RadiusRecord) -> str:
        return "EDSM cache" if record.origin == "edsm" else "Local cache"

    def _radius_for(
        self,
        system: str | None,
        body: str | None,
        system_address: int | None,
        body_id: int | None,
    ) -> RadiusRecord | None:
        return self.radius_cache.get(
            system=system,
            body=body,
            system_address=system_address,
            body_id=body_id,
        )

    def _consume_scan(self, event: dict[str, Any]) -> None:
        radius_m = positive_decimal(event.get("Radius"))
        body = event.get("BodyName") or event.get("Body")
        body_id = self._integer(event.get("BodyID"))
        system = event.get("StarSystem") or self.context.system
        system_address = self._integer(event.get("SystemAddress")) or self.context.system_address
        if radius_m is None or not body:
            return
        radius_km = radius_m / Decimal("1000")
        self.radius_cache.put(
            radius_km,
            origin="journal",
            system=system,
            body=str(body),
            system_address=system_address,
            body_id=body_id,
        )
        same_id = (
            body_id is not None
            and self.context.body_id is not None
            and body_id == self.context.body_id
            and (
                system_address is None
                or self.context.system_address is None
                or system_address == self.context.system_address
            )
        )
        same_name = (
            normalize_name(str(body)) == normalize_name(self.context.body)
            and (not system or normalize_name(str(system)) == normalize_name(self.context.system))
        )
        if same_id or same_name:
            self.context.body_radius_km = radius_km
            self.context.body_radius_source = "Journal"

    def _resolve_context_radius(self) -> None:
        record = self._radius_for(
            self.context.system,
            self.context.body,
            self.context.system_address,
            self.context.body_id,
        )
        if record is None:
            self.context.body_radius_km = None
            self.context.body_radius_source = None
            return
        self.context.body_radius_km = record.radius_km
        self.context.body_radius_source = self._cached_source(record)

    def _clear_body(self) -> None:
        self.context.body = None
        self.context.body_id = None
        self.context.body_radius_km = None
        self.context.body_radius_source = None

    def remember_radius(
        self,
        radius_km: Decimal | str,
        *,
        origin: str,
        system: str | None,
        body: str | None,
        system_address: int | None = None,
        body_id: int | None = None,
    ) -> None:
        self.radius_cache.put(
            radius_km,
            origin=origin,
            system=system,
            body=body,
            system_address=system_address,
            body_id=body_id,
        )
        if (
            normalize_name(system) == normalize_name(self.context.system)
            and normalize_name(body) == normalize_name(self.context.body)
        ):
            self._resolve_context_radius()
