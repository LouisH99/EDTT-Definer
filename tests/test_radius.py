from __future__ import annotations

from decimal import Decimal

import pytest

from edtt_definer.edsm import parse_edsm_body_radius
from edtt_definer.radius import BodyRadiusCache


def test_radius_cache_round_trip_and_lookup_precedence() -> None:
    cache = BodyRadiusCache()
    assert cache.put(
        "5411.771",
        origin="journal",
        system="Test System",
        body="Test System 2 a",
        system_address=12345,
        body_id=7,
    )
    assert cache.dirty
    assert cache.get(system_address=12345, body_id=7).radius_km == Decimal("5411.771")
    assert cache.get(system=" test   system ", body="TEST SYSTEM 2 A").origin == "journal"

    restored = BodyRadiusCache.from_json(cache.to_json())
    record = restored.get(system="Test System", body="Test System 2 a")
    assert record is not None
    assert record.radius_km == Decimal("5411.771")
    assert not restored.dirty


def test_radius_cache_rejects_invalid_values_and_corrupt_json() -> None:
    cache = BodyRadiusCache.from_json("not json")
    assert not cache.put("0", origin="journal", system="Sol", body="Earth")
    assert not cache.put("NaN", origin="journal", system="Sol", body="Earth")
    assert cache.get(system="Sol", body="Earth") is None


def test_parse_edsm_body_radius_preserves_decimal_kilometres() -> None:
    payload = b'{"bodies":[{"name":"Test 1","radius":5411.771234},{"name":"Other","radius":1}]}'
    assert parse_edsm_body_radius(payload, "test 1") == Decimal("5411.771234")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{", "invalid JSON"),
        (b'{"name":"Sol"}', "body list"),
        (b'{"bodies":[{"name":"Earth"}]}', "no usable radius"),
        (b'{"bodies":[{"name":"Mars","radius":3396}]}', "could not find"),
    ],
)
def test_parse_edsm_body_radius_reports_unusable_responses(payload: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_edsm_body_radius(payload, "Earth")
