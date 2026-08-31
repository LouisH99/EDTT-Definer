from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from edtt_definer.live import HAS_LAT_LONG_FLAG, LiveLocationReader, LiveState


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
