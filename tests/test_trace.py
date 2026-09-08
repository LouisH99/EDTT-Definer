from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from edtt_definer.live import LocationSnapshot
from edtt_definer.trace import (
    RaceTrace,
    TracePoint,
    TraceRecorder,
    TraceSegment,
    TraceState,
    load_trace_csv,
    load_trace_json,
    save_trace_csv,
    save_trace_json,
)


def snapshot(
    latitude: str,
    longitude: str,
    *,
    altitude: str = "100",
    heading: str = "90",
    system: str = "Demo System",
    body: str = "Demo A 1",
) -> LocationSnapshot:
    return LocationSnapshot(
        system=system,
        body=body,
        body_radius_km=Decimal("5411.771"),
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        altitude=Decimal(altitude),
        heading=Decimal(heading),
        flag_has_lat_long=True,
    )


def sample_trace() -> RaceTrace:
    return RaceTrace(
        system="Demo System",
        body="Demo A 1",
        radius_km=Decimal("5411.771"),
        created_utc="2026-09-05T12:00:00Z",
        segments=[
            TraceSegment(
                reason="Live recording",
                points=[
                    TracePoint(
                        timestamp_utc="2026-09-05T12:00:00.000Z",
                        unix_time=Decimal("1788609600.000"),
                        elapsed_ms=0,
                        latitude=Decimal("12.345678900"),
                        longitude=Decimal("-45.600000001"),
                        altitude_m=Decimal("123.45"),
                        heading_degrees=Decimal("90.0"),
                        radius_km=Decimal("5411.771"),
                        speed_mps=None,
                    ),
                    TracePoint(
                        timestamp_utc="2026-09-05T12:00:00.250Z",
                        unix_time=Decimal("1788609600.250"),
                        elapsed_ms=250,
                        latitude=Decimal("12.345679900"),
                        longitude=Decimal("-45.599999001"),
                        altitude_m=Decimal("124.45"),
                        heading_degrees=Decimal("91.0"),
                        radius_km=Decimal("5411.771"),
                        speed_mps=Decimal("2.345"),
                    ),
                ],
            )
        ],
    )


def test_trace_json_round_trip_preserves_decimal_text(tmp_path) -> None:
    source = sample_trace()
    path = tmp_path / "flight.edtttrace.json"

    save_trace_json(source, path)
    loaded = load_trace_json(path)

    assert loaded.to_dict() == source.to_dict()
    assert "12.345678900" in path.read_text(encoding="utf-8")


def test_trace_csv_v3_is_raceviz_compatible_and_reopens(tmp_path) -> None:
    source = sample_trace()
    path = tmp_path / "flight.csv"

    save_trace_csv(source, path)
    raw = path.read_bytes()
    loaded = load_trace_csv(path)

    assert raw.startswith(b"#Version3\r\nTime,UnixTime,Latitude,Longitude,Radius")
    assert loaded.point_count == 2
    assert loaded.segments[0].points[0].latitude == Decimal("12.345678900")
    assert loaded.segments[0].points[1].speed_mps == Decimal("2.345")
    assert loaded.radius_km == Decimal("5411.771")


def test_csv_loader_reports_line_for_malformed_data(tmp_path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text(
        "#Version2\n"
        "Time,UnixTime,Latitude,Longitude,Radius,Altitude,Heading,Speed,CargoScoop,LandingGear,Lights,TargetWaypointNum\n"
        "date,100,nope,2,5411,3,4,5,False,False,False,0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 3"):
        load_trace_csv(path)


def test_version1_csv_uses_explicit_course_radius(tmp_path) -> None:
    path = tmp_path / "legacy.csv"
    path.write_text(
        "# version 1\n1761593639,63.6810955,22.7090513,0,180\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no body radius"):
        load_trace_csv(path)
    loaded = load_trace_csv(path, fallback_radius_km="5411.771")
    assert loaded.point_count == 1
    assert loaded.radius_km == Decimal("5411.771")


def test_recorder_derives_speed_deduplicates_and_splits_after_pause() -> None:
    recorder = TraceRecorder()
    start_time = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    recorder.start(
        system="Demo System",
        body="Demo A 1",
        radius_km="5411.771",
        now_monotonic=10,
        now_utc=start_time,
    )

    assert recorder.consume(snapshot("0", "0"), now_monotonic=10, now_utc=start_time)
    assert not recorder.consume(snapshot("0", "0"), now_monotonic=10.25, now_utc=start_time)
    assert recorder.consume(
        snapshot("0", "0.001", altitude="104"),
        now_monotonic=11,
        now_utc=start_time,
    )
    speed = recorder.trace.segments[0].points[-1].speed_mps
    assert speed is not None and speed > 90

    recorder.pause()
    assert recorder.state is TraceState.PAUSED
    recorder.resume()
    assert recorder.consume(
        snapshot("0", "0.002"), now_monotonic=12, now_utc=start_time
    )
    assert len(recorder.trace.segments) == 2
    assert recorder.trace.point_count == 3
    assert recorder.dirty


def test_recorder_waits_on_mismatch_and_invalid_snapshots() -> None:
    recorder = TraceRecorder()
    recorder.start(system="Demo", body="A", radius_km="1000", now_monotonic=1)

    assert not recorder.consume(None, now_monotonic=1)
    assert not recorder.consume(
        snapshot("0", "0", system="Other", body="A"), now_monotonic=2
    )
    assert "does not match" in recorder.status
    assert recorder.trace.point_count == 0


def test_invalid_trace_schema_and_coordinates_are_rejected(tmp_path) -> None:
    path = tmp_path / "bad.edtttrace.json"
    path.write_text('{"schema_version": 99, "segments": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        load_trace_json(path)

    with pytest.raises(ValueError, match="Latitude"):
        TracePoint(
            timestamp_utc="",
            unix_time=Decimal("1"),
            elapsed_ms=0,
            latitude=Decimal("91"),
            longitude=Decimal("0"),
            altitude_m=Decimal("0"),
            heading_degrees=None,
            radius_km=Decimal("1"),
        )
