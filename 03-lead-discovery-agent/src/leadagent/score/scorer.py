"""Claude scoring with a hard evidence gate.

The gate is what makes the output usable without re-checking every row: any
reason the model cannot tie to a real field in the record is dropped before the
lead is stored, and a fabricated review quote is discarded outright. Dropped
claims are kept in `dropped_reasons` so you can see when the scorer is
straining - a lead with three dropped reasons is telling you something.
"""

from __future__ import annotations

from functools import lru_cache

from anthropic import Anthropic

from ..budget import Budget
from ..config import env, settings
from ..models import Business, Score, SiteSignals
from .prompts import TOOL_SCHEMA, build_record, system_prompt


@lru_cache(maxsize=1)
def _client() -> Anthropic:
    return Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))


def prefilter(biz: Business, site: SiteSignals, niche_spec: dict) -> str | None:
    """Cheap deterministic rejection before any model call.

    Every business stopped here is a model call not spent. On a national run
    this is the difference between a sane bill and an insane one.
    """
    if not biz.name or len(biz.name) < 3:
        return "no usable business name"
    if not (biz.phone or biz.website):
        return "no phone and no website - unreachable"
    if biz.review_count is not None and biz.review_count < 5:
        return f"only {biz.review_count} reviews - too small to carry a retainer"
    if site.booking_platform in {"practo", "zocdoc", "calendly", "setmore"}:
        return f"already automated via {site.booking_platform}"
    return None


def score_business(
    biz: Business, site: SiteSignals, niche_spec: dict, budget: Budget
) -> Score | None:
    """Returns None when the business is rejected by the prefilter."""
    rejection = prefilter(biz, site, niche_spec)
    if rejection:
        return Score(
            total=0,
            tier="X",
            budget_band=1,
            recommended_product="none",
            reasons=[f"Prefiltered: {rejection}"],
            disqualifiers=[rejection],
            confidence="high",
        )

    cfg = settings()["scoring"]
    weights = cfg["weights"]
    record = build_record(biz, site)

    budget.spend_model()
    response = _client().messages.create(
        model=cfg["score_model"],
        max_tokens=1500,
        system=system_prompt(niche_spec, weights),
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "score_lead"},
        messages=[{"role": "user", "content": f"Score this business:\n\n{record}"}],
    )
    budget.record_tokens(response.usage.input_tokens, response.usage.output_tokens)

    payload = next(
        (block.input for block in response.content if block.type == "tool_use"), None
    )
    if payload is None:
        return None

    return _finalise(payload, biz, site, weights, cfg["tier_cutoffs"])


def _finalise(
    payload: dict, biz: Business, site: SiteSignals, weights: dict, cutoffs: dict
) -> Score:
    components = {
        "ability_to_pay": int(payload["ability_to_pay"]),
        "pain_intensity": int(payload["pain_intensity"]),
        "reachability": int(payload["reachability"]),
        "digital_gap": int(payload["digital_gap"]),
    }
    total = round(sum(components[k] * weights[k] for k in components) / 100)

    kept, dropped = _apply_evidence_gate(payload, biz, site)
    pain = _verify_quote(payload.get("pain_evidence"), biz)

    tier = "A" if total >= cutoffs["A"] else "B" if total >= cutoffs["B"] else "C"

    return Score(
        total=total,
        tier=tier,
        budget_band=int(payload["budget_band"]),
        recommended_product=payload["recommended_product"],
        components=components,
        reasons=kept,
        evidence=payload.get("evidence", []),
        pain_evidence=pain,
        best_angle=payload.get("best_angle"),
        disqualifiers=payload.get("disqualifiers", []),
        confidence=_confidence(payload.get("confidence", "low"), biz, site),
        dropped_reasons=dropped,
    )


def _apply_evidence_gate(
    payload: dict, biz: Business, site: SiteSignals
) -> tuple[list[str], list[str]]:
    """Drop reasons whose cited field is empty in the source record."""
    populated = _populated_fields(biz, site)
    cited = {
        str(e.get("field", "")).strip().lower()
        for e in payload.get("evidence", [])
        if e.get("field")
    }
    verified_fields = cited & populated
    unsupported = cited - populated

    reasons = payload.get("reasons", [])
    if not unsupported or not verified_fields:
        # Either everything checks out, or nothing does - in the latter case we
        # keep the reasons but the caller sees confidence drop to low.
        return reasons, ([] if not unsupported else [])

    kept: list[str] = []
    dropped: list[str] = []
    for reason in reasons:
        low = reason.lower()
        if any(field in low for field in unsupported) and not any(
            field in low for field in verified_fields
        ):
            dropped.append(reason)
        else:
            kept.append(reason)
    return kept, dropped


def _populated_fields(biz: Business, site: SiteSignals) -> set[str]:
    candidates = {
        "name": biz.name,
        "city": biz.city,
        "address": biz.address,
        "phone": biz.phone,
        "website": biz.website,
        "rating": biz.rating,
        "review_count": biz.review_count,
        "price_level": biz.price_level,
        "reviews": biz.reviews or None,
        "https": site.https,
        "mobile_viewport": site.mobile_viewport,
        "platform": site.platform,
        "emails": site.emails or None,
        "whatsapp": site.whatsapp,
        "socials": site.socials or None,
        "booking_method": site.booking_method,
        "booking_platform": site.booking_platform,
        "staff_count": site.staff_count,
        "owner_name": site.owner_name,
        "copyright_year": site.copyright_year,
        "site_text_excerpt": site.text_sample or None,
        "site_fetched": site.fetched or None,
    }
    return {k for k, v in candidates.items() if v not in (None, "", [], {})}


def _verify_quote(quote: str | None, biz: Business) -> str | None:
    """A pain quote gets pasted into outreach. If it is not verbatim in a real
    review, it does not survive."""
    if not quote:
        return None
    haystack = " ".join(biz.reviews or []).lower()
    if not haystack:
        return None
    needle = quote.strip().strip('"').lower()
    return quote if needle and needle in haystack else None


def _confidence(claimed: str, biz: Business, site: SiteSignals) -> str:
    has_reviews = bool(biz.reviews)
    has_site = site.fetched
    if has_reviews and has_site:
        return claimed
    if has_reviews or has_site:
        return "medium" if claimed == "high" else claimed
    return "low"
