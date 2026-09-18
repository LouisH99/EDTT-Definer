from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Vector3:
    east: float
    north: float
    altitude: float

    def __add__(self, other: Vector3) -> Vector3:
        return Vector3(
            self.east + other.east,
            self.north + other.north,
            self.altitude + other.altitude,
        )

    def __sub__(self, other: Vector3) -> Vector3:
        return Vector3(
            self.east - other.east,
            self.north - other.north,
            self.altitude - other.altitude,
        )

    def __mul__(self, amount: float) -> Vector3:
        return Vector3(
            self.east * amount,
            self.north * amount,
            self.altitude * amount,
        )

    def dot(self, other: Vector3) -> float:
        return (
            self.east * other.east
            + self.north * other.north
            + self.altitude * other.altitude
        )

    def cross(self, other: Vector3) -> Vector3:
        return Vector3(
            self.north * other.altitude - self.altitude * other.north,
            self.altitude * other.east - self.east * other.altitude,
            self.east * other.north - self.north * other.east,
        )

    def normalized(self) -> Vector3:
        length = math.sqrt(self.dot(self))
        if length < 1e-12:
            raise ValueError("A camera basis vector cannot have zero length.")
        return self * (1.0 / length)


def _rotate(vector: Vector3, axis: Vector3, degrees: float) -> Vector3:
    """Rotate a vector around a world-space axis using Rodrigues' formula."""
    if not degrees:
        return vector
    unit_axis = axis.normalized()
    radians = math.radians(degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (
        vector * cosine
        + unit_axis.cross(vector) * sine
        + unit_axis * (unit_axis.dot(vector) * (1.0 - cosine))
    )


@dataclass
class CameraOrientation:
    """An orthonormal world-space camera basis.

    ``right`` maps to positive screen X, ``up`` maps to negative screen Y, and
    ``depth`` controls painter ordering. Keeping all three axes makes camera
    roll explicit instead of inferring it from a two-angle hemisphere.
    """

    right: Vector3
    up: Vector3
    depth: Vector3

    @classmethod
    def from_yaw_tilt(cls, yaw_degrees: float, tilt_degrees: float) -> CameraOrientation:
        yaw = math.radians(yaw_degrees)
        tilt = math.radians(tilt_degrees)
        sine_yaw = math.sin(yaw)
        cosine_yaw = math.cos(yaw)
        sine_tilt = math.sin(tilt)
        cosine_tilt = math.cos(tilt)
        return cls(
            right=Vector3(cosine_yaw, -sine_yaw, 0.0),
            up=Vector3(
                sine_yaw * cosine_tilt,
                cosine_yaw * cosine_tilt,
                sine_tilt,
            ),
            depth=Vector3(
                sine_yaw * sine_tilt,
                cosine_yaw * sine_tilt,
                -cosine_tilt,
            ),
        )

    @classmethod
    def from_turntable(
        cls,
        yaw_degrees: float,
        tilt_degrees: float,
        roll_degrees: float = 0.0,
    ) -> CameraOrientation:
        """Build an upright turntable camera with an independent roll axis.

        Normal orbital tilt is limited to the complete top-to-bottom range.
        Crossing either vertical pole would make world-up undefined and would
        force the course to flip by 180 degrees, so callers stop at the poles
        and use yaw to continue around the body. Only explicit roll may invert
        planetary altitude on screen.
        """
        tilt = max(0.0, min(180.0, float(tilt_degrees)))
        orientation = cls.from_yaw_tilt(yaw_degrees % 360.0, tilt)
        orientation.roll(roll_degrees)
        return orientation

    @property
    def inverted(self) -> bool:
        return self.up.altitude < -1e-9

    @property
    def below_surface(self) -> bool:
        return self.depth.altitude > 1e-9

    def project(self, east: float, north: float, altitude: float) -> tuple[float, float, float]:
        offset = Vector3(east, north, altitude)
        return (
            self.right.dot(offset),
            -self.up.dot(offset),
            self.depth.dot(offset),
        )

    def roll(self, degrees: float) -> None:
        """Roll around the viewing direction without changing that direction."""
        if not degrees:
            return
        axis = self.depth.normalized()
        self.right = _rotate(self.right, axis, degrees)
        self.up = _rotate(self.up, axis, degrees)
        self._orthonormalize()

    def _orthonormalize(self) -> None:
        right = self.right.normalized()
        up = (self.up - right * self.up.dot(right)).normalized()
        self.right = right
        self.up = up
        self.depth = up.cross(right).normalized()
