from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from edtt_definer.live import HAS_LAT_LONG_FLAG, LiveLocationReader, LiveState
from edtt_definer.radius import BodyRadiusCache


def write_status(path: Path, **updates) -> None:
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "Status",
        "Flags": HAS_LAT_LONG_FLAG,
    }
    data.update(updates)
    (path / "Status.json").write_text(json.dumps(data), encoding="utf-8")


def test_live_status_and_journal_context(tmp_path: Path) -> None:
    journal = tmp_path / "Journal.2026-01-01T000000.01.log"
    journal.write_text(
        json.dumps({"event": "Location", "StarSystem": "Sol", "Body": "Earth"}) + "\n"
        + json.dumps({"event": "Docked", "StationName": "Test Port", "StarSystem": "Sol"}) + "\n",
        encoding="utf-8",
    )
    write_status(
        tmp_path,
        BodyName="Earth",
        Latitude=-12.345678,
        Longitude=45.678901,
        Altitude=123.5,
        Heading=90,
    )
    result = LiveLocationReader(tmp_path).poll()
    assert result.state is LiveState.LIVE
    assert result.snapshot.system == "Sol"
    assert result.snapshot.station == "Test Port"
    assert str(result.snapshot.latitude) == "-12.345678"


def test_appended_journal_line_is_not_skipped(tmp_path: Path) -> None:
    journal = tmp_path / "Journal.2026-01-01T000000.01.log"
    journal.write_text(json.dumps({"event": "Location", "StarSystem": "Sol"}) + "\n", encoding="utf-8")
    write_status(tmp_path)
    reader = LiveLocationReader(tmp_path)
    reader.poll()
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "ApproachBody", "StarSystem": "Sol", "Body": "Earth"}) + "\n")
    reader.poll()
    assert reader.context.body == "Earth"


def test_invalid_replacement_retains_last_snapshot(tmp_path: Path) -> None:
    write_status(tmp_path, Latitude=1, Longitude=2)
    reader = LiveLocationReader(tmp_path)
    first = reader.poll()
    (tmp_path / "Status.json").write_text("{", encoding="utf-8")
    second = reader.poll()
    assert first.state is LiveState.LIVE
    assert second.state is LiveState.INVALID
    assert second.snapshot is first.snapshot


def test_missing_coordinates_is_normal_state(tmp_path: Path) -> None:
    write_status(tmp_path, Flags=0)
    result = LiveLocationReader(tmp_path).poll()
    assert result.state is LiveState.NO_COORDINATES
    assert not result.snapshot.capture_ready


def test_missing_status_file(tmp_path: Path) -> None:
    result = LiveLocationReader(tmp_path).poll()
    assert result.state is LiveState.MISSING


def test_scan_event_supplies_exact_body_radius_and_persistent_cache(tmp_path: Path) -> None:
    journal = tmp_path / "Journal.2026-01-01T000000.01.log"
    events = [
        {
            "event": "Location",
            "StarSystem": "Radius System",
            "SystemAddress": 12345,
            "Body": "Radius System 2 a",
            "BodyID": 7,
        },
        {
            "event": "Scan",
            "StarSystem": "Radius System",
            "SystemAddress": 12345,
            "BodyName": "Radius System 2 a",
            "BodyID": 7,
            "Radius": 5411771.234,
        },
    ]
    journal.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    write_status(
        tmp_path,
        BodyName="Radius System 2 a",
        Latitude=1,
        Longitude=2,
    )
    reader = LiveLocationReader(tmp_path)

    result = reader.poll()

    assert result.snapshot.system_address == 12345
    assert result.snapshot.body_id == 7
    assert str(result.snapshot.body_radius_km) == "5411.771234"
    assert result.snapshot.body_radius_source == "Journal"
    assert result.snapshot.to_metadata()["body_radius_km"] == "5411.771234"
    assert reader.radius_cache.dirty

    restored_cache = BodyRadiusCache.from_json(reader.radius_cache.to_json())
    cached = restored_cache.get(system_address=12345, body_id=7)
    assert cached is not None and str(cached.radius_km) == "5411.771234"


def test_cached_radius_is_used_when_current_journal_has_no_scan(tmp_path: Path) -> None:
    cache = BodyRadiusCache()
    cache.put(
        "1000.125",
        origin="journal",
        system="Cached System",
        body="Cached Body",
        system_address=99,
        body_id=3,
    )
    cache.mark_clean()
    journal = tmp_path / "Journal.2026-01-01T000000.01.log"
    journal.write_text(
        json.dumps(
            {
                "event": "Location",
                "StarSystem": "Cached System",
                "SystemAddress": 99,
                "Body": "Cached Body",
                "BodyID": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_status(tmp_path, BodyName="Cached Body", Latitude=1, Longitude=2)

    result = LiveLocationReader(tmp_path, radius_cache=cache).poll()

    assert str(result.snapshot.body_radius_km) == "1000.125"
    assert result.snapshot.body_radius_source == "Local cache"


def test_journal_rotation_retains_scanned_radius_in_cache(tmp_path: Path) -> None:
    first_journal = tmp_path / "Journal.2026-01-01T000000.01.log"
    first_journal.write_text(
        json.dumps(
            {
                "event": "Location",
                "StarSystem": "Rotation System",
                "SystemAddress": 777,
                "Body": "Rotation Body",
                "BodyID": 4,
            }
        )
        + "\n"
        + json.dumps(
            {
                "event": "Scan",
                "SystemAddress": 777,
                "BodyName": "Rotation Body",
                "BodyID": 4,
                "Radius": 2500500,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(first_journal, (1, 1))
    write_status(tmp_path, BodyName="Rotation Body", Latitude=1, Longitude=2)
    reader = LiveLocationReader(tmp_path)
    first = reader.poll()
    assert str(first.snapshot.body_radius_km) == "2500.5"

    second_journal = tmp_path / "Journal.2026-01-02T000000.01.log"
    second_journal.write_text(
        json.dumps(
            {
                "event": "Location",
                "StarSystem": "Rotation System",
                "SystemAddress": 777,
                "Body": "Rotation Body",
                "BodyID": 4,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(second_journal, (2, 2))

    second = reader.poll()

    assert str(second.snapshot.body_radius_km) == "2500.5"
    assert second.snapshot.body_radius_source == "Local cache"
