from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class CalculatedGate:
    center_latitude: str
    center_longitude: str
    latitude_a: str
    longitude_a: str
    latitude_b: str
    longitude_b: str
    heading: str
    width_m: str
    forward_offset_m: str
    body_radius_km: str


def _decimal(value: Decimal | str | int | float, label: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be numeric.") from None
    if not number.is_finite():
        raise ValueError(f"{label} must be a finite number.")
    return number


def _coordinate_text(value: float, places: int = 6) -> str:
    threshold = 0.5 * 10 ** -places
    if abs(value) < threshold:
        value = 0.0
    return f"{value:.{places}f}"


def _number_text(value: Decimal) -> str:
    return format(value, "f")


def destination_point(
    latitude: Decimal | str | int | float,
    longitude: Decimal | str | int | float,
    bearing_degrees: Decimal | str | int | float,
    distance_m: Decimal | str | int | float,
    body_radius_km: Decimal | str | int | float,
) -> tuple[float, float]:
    latitude_number = _decimal(latitude, "Latitude")
    longitude_number = _decimal(longitude, "Longitude")
    bearing_number = _decimal(bearing_degrees, "Heading")
    distance_number = _decimal(distance_m, "Distance")
    radius_number = _decimal(body_radius_km, "BodyRadius")

    if not Decimal("-90") <= latitude_number <= Decimal("90"):
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not Decimal("-180") <= longitude_number <= Decimal("180"):
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    if distance_number < 0:
        raise ValueError("Distance cannot be negative.")
    if radius_number <= 0:
        raise ValueError("BodyRadius must be greater than zero kilometres.")

    latitude_radians = math.radians(float(latitude_number))
    longitude_radians = math.radians(float(longitude_number))
    bearing_radians = math.radians(float(bearing_number % Decimal("360")))
    angular_distance = float(distance_number / (radius_number * Decimal("1000")))

    destination_latitude = math.asin(
        math.sin(latitude_radians) * math.cos(angular_distance)
        + math.cos(latitude_radians) * math.sin(angular_distance) * math.cos(bearing_radians)
    )
    destination_longitude = longitude_radians + math.atan2(
        math.sin(bearing_radians) * math.sin(angular_distance) * math.cos(latitude_radians),
        math.cos(angular_distance)
        - math.sin(latitude_radians) * math.sin(destination_latitude),
    )

    latitude_degrees = math.degrees(destination_latitude)
    longitude_degrees = (math.degrees(destination_longitude) + 540.0) % 360.0 - 180.0
    return latitude_degrees, longitude_degrees


def calculate_gate(
    latitude: Decimal | str | int | float,
    longitude: Decimal | str | int | float,
    heading_degrees: Decimal | str | int | float,
    width_m: Decimal | str | int | float,
    body_radius_km: Decimal | str | int | float,
    forward_offset_m: Decimal | str | int | float = 0,
) -> CalculatedGate:
    latitude_number = _decimal(latitude, "Latitude")
    longitude_number = _decimal(longitude, "Longitude")
    heading_number = _decimal(heading_degrees, "Heading")
    width_number = _decimal(width_m, "Gate width")
    radius_number = _decimal(body_radius_km, "BodyRadius")
    offset_number = _decimal(forward_offset_m, "Distance ahead")

    if width_number <= 0:
        raise ValueError("Gate width must be greater than zero metres.")
    if offset_number < 0:
        raise ValueError("Distance ahead cannot be negative.")
    if radius_number <= 0:
        raise ValueError("BodyRadius must be greater than zero kilometres.")
    circumference_m = Decimal(str(2 * math.pi)) * radius_number * Decimal("1000")
    if width_number >= circumference_m:
        raise ValueError("Gate width must be smaller than the body's circumference.")

    center_latitude, center_longitude = destination_point(
        latitude_number,
        longitude_number,
        heading_number,
        offset_number,
        radius_number,
    )
    half_width = width_number / Decimal("2")
    latitude_a, longitude_a = destination_point(
        center_latitude,
        center_longitude,
        heading_number - Decimal("90"),
        half_width,
        radius_number,
    )
    latitude_b, longitude_b = destination_point(
        center_latitude,
        center_longitude,
        heading_number + Decimal("90"),
        half_width,
        radius_number,
    )

    return CalculatedGate(
        center_latitude=_coordinate_text(center_latitude),
        center_longitude=_coordinate_text(center_longitude),
        latitude_a=_coordinate_text(latitude_a),
        longitude_a=_coordinate_text(longitude_a),
        latitude_b=_coordinate_text(latitude_b),
        longitude_b=_coordinate_text(longitude_b),
        heading=_number_text(heading_number % Decimal("360")),
        width_m=_number_text(width_number),
        forward_offset_m=_number_text(offset_number),
        body_radius_km=_number_text(radius_number),
    )


def great_circle_distance_m(
    latitude_a: Decimal | str | int | float,
    longitude_a: Decimal | str | int | float,
    latitude_b: Decimal | str | int | float,
    longitude_b: Decimal | str | int | float,
    body_radius_km: Decimal | str | int | float,
) -> float:
    latitude_a_number = _decimal(latitude_a, "Latitude A")
    longitude_a_number = _decimal(longitude_a, "Longitude A")
    latitude_b_number = _decimal(latitude_b, "Latitude B")
    longitude_b_number = _decimal(longitude_b, "Longitude B")
    radius_number = _decimal(body_radius_km, "BodyRadius")
    for label, value in (("Latitude A", latitude_a_number), ("Latitude B", latitude_b_number)):
        if not Decimal("-90") <= value <= Decimal("90"):
            raise ValueError(f"{label} must be between -90 and 90 degrees.")
    for label, value in (("Longitude A", longitude_a_number), ("Longitude B", longitude_b_number)):
        if not Decimal("-180") <= value <= Decimal("180"):
            raise ValueError(f"{label} must be between -180 and 180 degrees.")
    if radius_number <= 0:
        raise ValueError("BodyRadius must be greater than zero kilometres.")
    lat_a = math.radians(float(latitude_a_number))
    lon_a = math.radians(float(longitude_a_number))
    lat_b = math.radians(float(latitude_b_number))
    lon_b = math.radians(float(longitude_b_number))
    radius_m = float(radius_number * Decimal("1000"))
    latitude_delta = lat_b - lat_a
    longitude_delta = lon_b - lon_a
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * radius_m * math.asin(min(1.0, math.sqrt(haversine)))
