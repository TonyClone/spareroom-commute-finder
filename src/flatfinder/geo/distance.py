from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (rough crow-flies)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def is_way_too_far(
    origin_lat: float | None,
    origin_lon: float | None,
    dest_lat: float,
    dest_lon: float,
    max_km: float,
) -> tuple[bool, float | None]:
    """
    Rough prefilter: True if crow-flies distance exceeds max_km.
    Returns (too_far, distance_km_or_None_if_unknown).
    Missing coords → not too far (caller may still skip for other reasons).
    """
    if origin_lat is None or origin_lon is None:
        return False, None
    if max_km <= 0:
        return False, None
    km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    return km > max_km, round(km, 2)
