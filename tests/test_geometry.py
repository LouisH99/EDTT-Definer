from __future__ import annotations

import pytest

from edtt_definer.geometry import calculate_gate, great_circle_distance_m


def test_centered_gate_is_perpendicular_to_heading_and_two_kilometres_wide() -> None:
    gate = calculate_gate("0", "0", "0", "2000", "5411.771")

    assert gate.center_latitude == "0.000000"
    assert gate.center_longitude == "0.000000"
    assert gate.latitude_a == "0.000000"
    assert gate.latitude_b == "0.000000"
    assert float(gate.longitude_a) < 0
    assert float(gate.longitude_b) > 0
    assert great_circle_distance_m(
        gate.latitude_a,
        gate.longitude_a,
        gate.latitude_b,
        gate.longitude_b,
        gate.body_radius_km,
    ) == pytest.approx(2000, abs=0.2)


def test_gate_can_be_projected_ahead_of_current_position() -> None:
    gate = calculate_gate("0", "0", "0", "2000", "5411.771", "2000")

    assert float(gate.center_latitude) > 0
    assert abs(float(gate.center_longitude)) < 0.000001
    assert great_circle_distance_m("0", "0", gate.center_latitude, gate.center_longitude, "5411.771") == pytest.approx(2000, abs=0.2)
    assert great_circle_distance_m(
        gate.latitude_a,
        gate.longitude_a,
        gate.latitude_b,
        gate.longitude_b,
        gate.body_radius_km,
    ) == pytest.approx(2000, abs=0.2)


def test_gate_longitudes_wrap_at_antimeridian() -> None:
    gate = calculate_gate("0", "179.999", "0", "2000", "5411.771")

    assert -180 <= float(gate.longitude_a) <= 180
    assert -180 <= float(gate.longitude_b) <= 180
    assert float(gate.longitude_a) > 0
    assert float(gate.longitude_b) < 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"width_m": "0"}, "Gate width"),
        ({"forward_offset_m": "-1"}, "Distance ahead"),
        ({"body_radius_km": "0"}, "BodyRadius"),
        ({"heading_degrees": "not a heading"}, "Heading"),
        ({"latitude": "91"}, "Latitude"),
    ],
)
def test_invalid_gate_inputs_are_rejected(kwargs, message: str) -> None:
    values = {
        "latitude": "1",
        "longitude": "2",
        "heading_degrees": "90",
        "width_m": "2000",
        "body_radius_km": "5411.771",
        "forward_offset_m": "0",
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        calculate_gate(**values)
