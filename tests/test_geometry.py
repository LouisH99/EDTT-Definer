from __future__ import annotations

import pytest

from edtt_definer.geometry import calculate_gate, great_circle_distance_m, spherical_midpoint


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


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("91", "0", "0", "0", "1000"), "Latitude A"),
        (("0", "181", "0", "0", "1000"), "Longitude A"),
        (("0", "0", "-91", "0", "1000"), "Latitude B"),
        (("0", "0", "0", "-181", "1000"), "Longitude B"),
        (("0", "0", "0", "0", "0"), "BodyRadius"),
    ],
)
def test_great_circle_distance_validates_coordinates_and_radius(values, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        great_circle_distance_m(*values)


def test_spherical_midpoint_of_equatorial_gate() -> None:
    assert spherical_midpoint("0", "-1", "0", "1") == ("0.000000", "0.000000")


def test_spherical_midpoint_handles_antimeridian_and_high_latitude() -> None:
    latitude, longitude = spherical_midpoint("70", "179", "70", "-179")

    assert float(latitude) > 70
    assert abs(float(longitude)) == pytest.approx(180)


def test_spherical_midpoint_of_identical_points_is_unchanged() -> None:
    assert spherical_midpoint("12.5", "-42.25", "12.5", "-42.25") == (
        "12.500000",
        "-42.250000",
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("0", "0", "0", "180"), "undefined"),
        (("91", "0", "0", "0"), "Latitude A"),
        (("0", "0", "0", "181"), "Longitude B"),
        (("bad", "0", "0", "0"), "Latitude A"),
    ],
)
def test_spherical_midpoint_rejects_undefined_or_invalid_inputs(values, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        spherical_midpoint(*values)
