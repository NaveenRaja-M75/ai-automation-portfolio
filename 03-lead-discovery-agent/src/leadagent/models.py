"""Core records that move through the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class City:
    name: str
    state: str
    country: str
    lat: float
    lng: float
    tier: int
    population: int
    radius_km: float


@dataclass
class Business:
    """A candidate discovered by a source, before enrichment."""

    source: str  # "osm" | "places"
    source_id: str  # osm node/way id, or Google place_id
    name: str
    niche: str
    city: str
    state: str
    lat: float | None = None
    lng: float | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None

    # Populated by Places only. OSM leaves these None, and the scorer is told.
    rating: float | None = None
    review_count: int | None = None
    price_level: int | None = None
    reviews: list[str] = field(default_factory=list)

    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class SiteSignals:
    """What the website told us. All fields optional - absence is itself data."""

    fetched: bool = False
    fetch_error: str | None = None
    final_url: str | None = None
    https: bool | None = None
    mobile_viewport: bool | None = None
    platform: str | None = None  # wordpress | wix | squarespace | shopify | ...
    emails: list[str] = field(default_factory=list)
    whatsapp: str | None = None
    socials: dict[str, str] = field(default_factory=dict)
    booking_method: str | None = None  # online | call_only | form_only | unknown
    booking_platform: str | None = None
    staff_count: int | None = None
    owner_name: str | None = None
    copyright_year: int | None = None
    page_bytes: int | None = None
    text_sample: str = ""


@dataclass
class Score:
    total: int
    tier: str
    budget_band: int  # 1..5, see scoring.prompts for the ladder
    recommended_product: str
    components: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    pain_evidence: str | None = None
    best_angle: str | None = None
    disqualifiers: list[str] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high
    dropped_reasons: list[str] = field(default_factory=list)


@dataclass
class Lead:
    business: Business
    site: SiteSignals
    score: Score

    def to_row(self) -> dict[str, Any]:
        b, s, sc = self.business, self.site, self.score
        return {
            "name": b.name,
            "niche": b.niche,
            "city": b.city,
            "state": b.state,
            "score": sc.total,
            "tier": sc.tier,
            "budget_band": sc.budget_band,
            "confidence": sc.confidence,
            "recommended_product": sc.recommended_product,
            "phone": b.phone or "",
            "email": ";".join(s.emails),
            "whatsapp": s.whatsapp or "",
            "website": b.website or "",
            "instagram": s.socials.get("instagram", ""),
            "address": b.address or "",
            "rating": b.rating if b.rating is not None else "",
            "review_count": b.review_count if b.review_count is not None else "",
            "booking_method": s.booking_method or "",
            "platform": s.platform or "",
            "staff_count": s.staff_count if s.staff_count is not None else "",
            "owner_name": s.owner_name or "",
            "top_reason": sc.reasons[0] if sc.reasons else "",
            "all_reasons": " | ".join(sc.reasons),
            "pain_evidence": sc.pain_evidence or "",
            "best_angle": sc.best_angle or "",
            "disqualifiers": " | ".join(sc.disqualifiers),
            "maps_url": (
                f"https://www.google.com/maps/search/?api=1&query={b.lat},{b.lng}"
                if b.lat and b.lng
                else ""
            ),
            "source": b.source,
            "source_id": b.source_id,
        }


def as_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
