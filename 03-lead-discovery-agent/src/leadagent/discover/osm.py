"""OpenStreetMap / Overpass discovery.

Free, no key, no billing, and no per-query result cap - so a whole city comes
back in one bounding-box request. What it does NOT have is reviews or ratings,
which means the pain-intensity and ability-to-pay signals stay empty in v0.
The scorer is told this explicitly and lowers its confidence accordingly.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..budget import Budget
from ..config import settings
from ..models import Business, City

_UA = "leadagent/0.1 (OpenStreetMap Overpass client)"

# The public Overpass instances rate-limit aggressively and shed load with 429
# and 504. Rotating across mirrors turns "target failed" into "target slower".
_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]


class OverpassBusy(RuntimeError):
    pass


def _build_query(filters: list[str], south: float, west: float, north: float, east: float,
                 timeout_s: int) -> str:
    box = f"({south},{west},{north},{east})"
    parts = "\n  ".join(f"{f}{box};" for f in filters)
    return f"[out:json][timeout:{timeout_s}];\n(\n  {parts}\n);\nout center tags;"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=10, max=90),
    retry=retry_if_exception_type(OverpassBusy),
    reraise=True,
)
def _post(preferred: str, query: str, timeout_s: int) -> dict:
    mirrors = [preferred] + [m for m in _MIRRORS if m != preferred]
    last_error: Exception | None = None

    for endpoint in mirrors:
        try:
            resp = httpx.post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": _UA},
                timeout=timeout_s + 30,
            )
            if resp.status_code in (429, 502, 503, 504):
                last_error = OverpassBusy(f"{endpoint} returned {resp.status_code}")
                continue  # try the next mirror before backing off
            resp.raise_for_status()
            return resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = OverpassBusy(f"{endpoint}: {exc!r}")
            continue

    raise last_error or OverpassBusy("all Overpass mirrors unavailable")


def find(city: City, niche_spec: dict, budget: Budget) -> list[Business]:
    from ..geo import bbox

    cfg = settings()["discovery"]
    filters = niche_spec.get("osm_filters") or []
    if not filters:
        return []

    south, west, north, east = bbox(city)
    timeout_s = int(cfg.get("osm_timeout_s", 180))
    query = _build_query(filters, south, west, north, east, timeout_s)

    budget.spend_discovery()
    payload = _post(cfg["osm_endpoint"], query, timeout_s)

    out: list[Business] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("operator")
        if not name:
            continue  # an unnamed node is not a lead

        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")

        out.append(
            Business(
                source="osm",
                source_id=f"{el.get('type')}/{el.get('id')}",
                name=name,
                niche=niche_spec["key"],
                city=city.name,
                state=city.state,
                lat=lat,
                lng=lng,
                address=_address(tags),
                phone=tags.get("phone") or tags.get("contact:phone"),
                website=tags.get("website") or tags.get("contact:website"),
            )
        )
    return out


def _address(tags: dict) -> str | None:
    bits = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    joined = ", ".join(b for b in bits if b)
    return joined or None
