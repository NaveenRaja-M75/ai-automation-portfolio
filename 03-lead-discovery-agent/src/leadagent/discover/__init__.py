"""Discovery sources.

Every source implements `find(city, niche_spec, budget) -> list[Business]`.
Switching between them is a settings.yaml change, not a code change.
"""

from __future__ import annotations

from typing import Callable

from ..models import Business, City
from ..budget import Budget

Source = Callable[[City, dict, Budget], list[Business]]


def get_source(name: str) -> Source:
    if name == "osm":
        from .osm import find

        return find
    if name == "places":
        from .places import find

        return find
    raise ValueError(f"Unknown discovery source {name!r}. Use 'osm' or 'places'.")
