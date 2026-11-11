"""City lookup and lat/lng tiling.

Tiling exists because Google Places returns at most ~60 results per query.
Covering "all dentists in Chennai" therefore means running many overlapping
circular searches across a grid. OSM/Overpass has no such cap and takes a
bounding box directly, which is why v0 is cheap.
"""

from __future__ import annotations

import csv
import math
from functools import lru_cache

from .config import DATA_DIR
from .models import City

_EARTH_KM = 111.32  # km per degree of latitude


@lru_cache(maxsize=1)
def _cities() -> dict[str, City]:
    out: dict[str, City] = {}
    with open(DATA_DIR / "cities_in.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            city = City(
                name=row["name"],
                state=row["state"],
                country=row["country"],
                lat=float(row["lat"]),
                lng=float(row["lng"]),
                tier=int(row["tier"]),
                population=int(row["population"]),
                radius_km=float(row["radius_km"]),
            )
            out[city.name.lower()] = city
    return out


def resolve_city(name: str) -> City:
    city = _cities().get(name.strip().lower())
    if city is None:
        known = ", ".join(sorted(c.name for c in _cities().values()))
        raise KeyError(f"Unknown city {name!r}. Known cities: {known}")
    return city


def cities_in_state(state: str) -> list[City]:
    target = state.strip().lower()
    found = [c for c in _cities().values() if c.state.lower() == target]
    return sorted(found, key=lambda c: -c.population)


def all_cities(max_tier: int = 3) -> list[City]:
    found = [c for c in _cities().values() if c.tier <= max_tier]
    return sorted(found, key=lambda c: -c.population)


def bbox(city: City) -> tuple[float, float, float, float]:
    """(south, west, north, east) covering the city's radius."""
    dlat = city.radius_km / _EARTH_KM
    dlng = city.radius_km / (_EARTH_KM * max(math.cos(math.radians(city.lat)), 0.01))
    return (city.lat - dlat, city.lng - dlng, city.lat + dlat, city.lng + dlng)


def tiles(city: City, tile_radius_m: int) -> list[tuple[float, float]]:
    """Grid of search centres covering the city.

    Spacing is tile_radius * sqrt(2) so the circles overlap rather than leaving
    diamond-shaped gaps at the corners of the grid.
    """
    step_km = (tile_radius_m / 1000.0) * math.sqrt(2)
    south, west, north, east = bbox(city)
    dlat = step_km / _EARTH_KM
    dlng = step_km / (_EARTH_KM * max(math.cos(math.radians(city.lat)), 0.01))

    points: list[tuple[float, float]] = []
    lat = south
    while lat <= north:
        lng = west
        while lng <= east:
            # Trim the grid corners back to a circle - a square grid over a
            # round city wastes ~21% of calls on empty land.
            if _haversine_km(city.lat, city.lng, lat, lng) <= city.radius_km:
                points.append((round(lat, 6), round(lng, 6)))
            lng += dlng
        lat += dlat
    return points


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
