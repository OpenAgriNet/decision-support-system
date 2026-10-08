"""Distance between two points, for deciding whether a place is near the user."""

from __future__ import annotations

import math

_EARTH_RADIUS_KM = 6371.0


def distance_km(a: list[float], b: list[float]) -> float:
    """Great-circle distance between two `[lon, lat]` points, in km."""

    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))
