from __future__ import annotations

import math

import pytest

from edtt_definer.camera import CameraOrientation, Vector3


def assert_vector_close(first: Vector3, second: Vector3) -> None:
    assert first.east == pytest.approx(second.east)
    assert first.north == pytest.approx(second.north)
    assert first.altitude == pytest.approx(second.altitude)


@pytest.mark.parametrize(
    ("yaw_degrees", "tilt_degrees"),
    [(0, 0), (0, 45), (83, 45), (135, 135), (270, 315)],
)
def test_initial_orientation_matches_documented_yaw_and_tilt_projection(
    yaw_degrees: float, tilt_degrees: float
) -> None:
    orientation = CameraOrientation.from_yaw_tilt(yaw_degrees, tilt_degrees)
    point = Vector3(320, -175, 506)
    yaw = math.radians(yaw_degrees)
    tilt = math.radians(tilt_degrees)
    expected_x = math.cos(yaw) * point.east - math.sin(yaw) * point.north
    rotated_north = (
        math.sin(yaw) * point.east + math.cos(yaw) * point.north
    )

    x, y, depth = orientation.project(point.east, point.north, point.altitude)

    assert x == pytest.approx(expected_x)
    assert y == pytest.approx(
        -rotated_north * math.cos(tilt) - point.altitude * math.sin(tilt)
    )
    assert depth == pytest.approx(
        rotated_north * math.sin(tilt) - point.altitude * math.cos(tilt)
    )


def test_explicit_roll_inverts_altitude_without_changing_view_direction() -> None:
    upright = CameraOrientation.from_turntable(0, 90)
    inverted = CameraOrientation.from_turntable(0, 90, 180)

    assert_vector_close(upright.depth, inverted.depth)
    assert upright.below_surface == inverted.below_surface
    assert not upright.inverted
    assert inverted.inverted

    upright_surface_y = upright.project(0, 0, 0)[1]
    upright_high_y = upright.project(0, 0, 500)[1]
    inverted_surface_y = inverted.project(0, 0, 0)[1]
    inverted_high_y = inverted.project(0, 0, 500)[1]
    assert upright_high_y < upright_surface_y
    assert inverted_high_y > inverted_surface_y


@pytest.mark.parametrize("orbit_degrees", [45, 90, 135])
def test_turntable_orbit_keeps_positive_altitude_screen_up(
    orbit_degrees: float,
) -> None:
    orientation = CameraOrientation.from_turntable(37, orbit_degrees)

    assert not orientation.inverted
    assert orientation.project(0, 0, 950)[1] < orientation.project(0, 0, 0)[1]


def test_below_surface_turntable_view_remains_upright() -> None:
    orientation = CameraOrientation.from_turntable(0, 135)

    assert orientation.below_surface
    assert not orientation.inverted


def test_explicit_roll_retains_an_orthonormal_camera_basis() -> None:
    orientation = CameraOrientation.from_turntable(35, 135)
    for degrees in (20, -70, 145, 250):
        orientation.roll(degrees)

    for axis in (orientation.right, orientation.up, orientation.depth):
        assert axis.dot(axis) == pytest.approx(1.0)
    assert orientation.right.dot(orientation.up) == pytest.approx(0.0, abs=1e-12)
    assert orientation.right.dot(orientation.depth) == pytest.approx(
        0.0, abs=1e-12
    )
    assert orientation.up.dot(orientation.depth) == pytest.approx(0.0, abs=1e-12)
    assert_vector_close(
        orientation.depth,
        orientation.up.cross(orientation.right).normalized(),
    )


def test_turntable_stops_at_vertical_poles_instead_of_flipping() -> None:
    top = CameraOrientation.from_turntable(37, 0)
    before_top = CameraOrientation.from_turntable(37, -45)
    bottom = CameraOrientation.from_turntable(37, 180)
    beyond_bottom = CameraOrientation.from_turntable(37, 225)

    assert_vector_close(before_top.right, top.right)
    assert_vector_close(before_top.up, top.up)
    assert_vector_close(before_top.depth, top.depth)
    assert_vector_close(beyond_bottom.right, bottom.right)
    assert_vector_close(beyond_bottom.up, bottom.up)
    assert_vector_close(beyond_bottom.depth, bottom.depth)
    assert not top.inverted
    assert not bottom.inverted


@pytest.mark.parametrize("yaw_degrees", [0, 90, 180, 270])
def test_positive_altitude_is_above_ground_from_every_side(yaw_degrees: float) -> None:
    orientation = CameraOrientation.from_turntable(yaw_degrees, 90)

    assert orientation.project(0, 0, 950)[1] < orientation.project(0, 0, 0)[1]
