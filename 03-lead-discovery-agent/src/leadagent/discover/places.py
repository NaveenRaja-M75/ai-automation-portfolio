"""Google Places API (New) discovery.

Not used by default. Flip `discovery.source` to "places" in settings.yaml and
set GOOGLE_PLACES_API_KEY once you have a GCP project with billing enabled.

Two things to know before you switch:

1. Results are capped at roughly 60 per query, so each city is covered by a
   grid of circular searches (see geo.tiles). More tiles = more calls = more
   money. `places_tile_radius_m` is the dial.
2. `places.reviews` is a premium field and returns only about five reviews per
   business. That is a sample, not a census - good enough to detect pain
   signals, not good enough to measure them. The scorer treats it as such.

Set a quota cap in the Google Cloud console too. That is your hard stop,
independent of this app's budget guard.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..budget import Budget
from ..config import env, settings
from ..models import Business, City

_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        "places.businessStatus",
        "places.reviews",  # premium SKU - drop this line to cut cost sharply
        "nextPageToken",
    ]
)

_PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _search(api_key: str, body: dict) -> dict:
    resp = httpx.post(
        _ENDPOINT,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def find(city: City, niche_spec: dict, budget: Budget) -> list[Business]:
    from ..geo import tiles

    api_key = env("GOOGLE_PLACES_API_KEY", required=True)
    cfg = settings()["discovery"]
    radius_m = int(cfg.get("places_tile_radius_m", 2500))
    queries = niche_spec.get("places_queries") or [niche_spec["label"]]

    seen: dict[str, Business] = {}
    for centre_lat, centre_lng in tiles(city, radius_m):
        for query in queries:
            page_token = None
            for _ in range(3):  # 3 pages x 20 results = the ~60 cap
                body: dict = {
                    "textQuery": f"{query} in {city.name}",
                    "pageSize": 20,
                    "locationBias": {
                        "circle": {
                            "center": {"latitude": centre_lat, "longitude": centre_lng},
                            "radius": float(radius_m),
                        }
                    },
                }
                if page_token:
                    body["pageToken"] = page_token

                budget.spend_discovery()
                payload = _search(api_key, body)

                for place in payload.get("places", []):
                    biz = _to_business(place, city, niche_spec["key"])
                    if biz and biz.source_id not in seen:
                        seen[biz.source_id] = biz

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

    return list(seen.values())


def _to_business(place: dict, city: City, niche_key: str) -> Business | None:
    place_id = place.get("id")
    name = (place.get("displayName") or {}).get("text")
    if not place_id or not name:
        return None
    if place.get("businessStatus") in {"CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"}:
        return None

    location = place.get("location") or {}
    reviews = [
        (r.get("text") or {}).get("text", "")
        for r in (place.get("reviews") or [])
        if (r.get("text") or {}).get("text")
    ]

    return Business(
        source="places",
        source_id=place_id,
        name=name,
        niche=niche_key,
        city=city.name,
        state=city.state,
        lat=location.get("latitude"),
        lng=location.get("longitude"),
        address=place.get("formattedAddress"),
        phone=place.get("nationalPhoneNumber"),
        website=place.get("websiteUri"),
        rating=place.get("rating"),
        review_count=place.get("userRatingCount"),
        price_level=_PRICE_LEVELS.get(place.get("priceLevel", "")),
        reviews=reviews,
    )
