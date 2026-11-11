"""Prompt and output schema for the scorer."""

from __future__ import annotations

import json
from typing import Any

from ..models import Business, SiteSignals

# The band ladder is deliberately an OUTPUT, not an input filter. You have not
# fixed your pricing yet, so the agent estimates what each business could carry
# and you apply the cutoff at export time - forever, at zero extra cost.
BUDGET_BANDS = """
1 = under Rs 10k/month  - survival-mode business, no discretionary spend
2 = Rs 10k-25k/month    - single location, steady, price-sensitive
3 = Rs 25k-50k/month    - established, some marketing spend, feels the pain
4 = Rs 50k-1L/month     - multi-staff or multi-location, actively advertising
5 = Rs 1L+/month        - chain, heavy ad spend, or premium positioning
"""

OFFER = """
You are scoring leads for an AI automation agency in India. What they sell:

  - Niche-specific AI agent systems that absorb REPEATED, SCRIPTED tasks.
    Examples: a WhatsApp appointment bot for dental clinics; a lead finding
    and qualifying bot for real estate; an AI voice receptionist that answers
    calls nobody is free to pick up.
  - An AI content studio (planning and research, product/ad shooting, editing,
    posting, campaign videos, managed AI workspace) sold as an upsell.
  - The overall positioning: automate the whole business with AI.

This changes what a good lead looks like, and it is the opposite of what a web
design agency would want. A busy clinic with a polished website and 400 reviews
is a BETTER lead than a struggling one with no website, because the value is in
removing repetitive human work - and only a business with volume has enough of
it to be worth removing. Poor or missing websites are a minor supporting signal,
not the point.
"""

RUBRIC = """
Score each dimension 0-100, then the weighted total is computed for you.

ABILITY TO PAY - can they carry a monthly retainer?
  Review count and review velocity, price level, number of staff or agents,
  multiple locations, and above all whether they are currently spending on ads.
  A business that already buys leads or runs ads has proven budget.

PAIN INTENSITY - how much repeated scripted work is happening by hand?
  Highest-value evidence is customer review text: "no one answered", "never
  called back", "still waiting for a reply", "no confirmation". Also: booking
  by phone only, a WhatsApp number that is clearly worked by hand, high inbound
  volume, appointment- or enquiry-driven operations.

REACHABILITY - can you actually start a conversation tomorrow?
  Named owner or principal, direct phone, published email, WhatsApp, open DMs.
  A generic office line with no name scores low.

DIGITAL GAP - supporting signal only, low weight.
  No site, stale site, no online booking, no mobile layout.
"""

RULES = """
Hard rules:

1. EVERY reason must be backed by an entry in `evidence` naming the exact field
   it came from, and quoting the value. If you cannot point at a field, do not
   make the claim. Do not infer ad spend, revenue, or staff size that is not in
   the data.
2. Missing data is not negative data. If review_count is null because the
   discovery source does not provide it, say so via `confidence` - do not
   penalise the business and do not invent a number.
3. `pain_evidence` must be a VERBATIM quote from a customer review, or null.
   Never paraphrase it and never write one yourself. It gets pasted into an
   outreach message, so a fabricated quote is worse than an empty field.
4. `best_angle` is one or two sentences addressed to the owner, specific to
   what you actually found. No flattery, no "I loved your website".
5. Set confidence honestly: high = review text plus site data; medium = one of
   the two; low = neither, i.e. you are scoring on structure alone.
6. If a disqualifier applies (already automated, too small to pay, closed),
   list it and score accordingly rather than being generous.
"""


def system_prompt(niche_spec: dict[str, Any], weights: dict[str, int]) -> str:
    return f"""{OFFER}

TARGET NICHE: {niche_spec['label']}
PRODUCT TO PITCH: {niche_spec['product']}
KNOWN PAIN IN THIS NICHE:
{niche_spec['core_pain'].strip()}

POSITIVE SIGNALS FOR THIS NICHE:
{json.dumps(niche_spec.get('signals_positive', {}), indent=2)}

NEGATIVE SIGNALS / DISQUALIFIERS:
{json.dumps(niche_spec.get('signals_negative', []), indent=2)}

{RUBRIC}

WEIGHTS (already applied for you when computing the total):
  ability_to_pay {weights['ability_to_pay']}%, pain_intensity {weights['pain_intensity']}%,
  reachability {weights['reachability']}%, digital_gap {weights['digital_gap']}%

BUDGET BANDS:
{BUDGET_BANDS}

{RULES}
"""


TOOL_SCHEMA = {
    "name": "score_lead",
    "description": "Record the evaluated score for one business.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ability_to_pay": {"type": "integer", "minimum": 0, "maximum": 100},
            "pain_intensity": {"type": "integer", "minimum": 0, "maximum": 100},
            "reachability": {"type": "integer", "minimum": 0, "maximum": 100},
            "digital_gap": {"type": "integer", "minimum": 0, "maximum": 100},
            "budget_band": {"type": "integer", "minimum": 1, "maximum": 5},
            "recommended_product": {
                "type": "string",
                "description": "Which specific product to lead the pitch with.",
            },
            "reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ranked, most persuasive first. Each must map to an evidence entry.",
            },
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["field", "value"],
                },
            },
            "pain_evidence": {
                "type": ["string", "null"],
                "description": "Verbatim customer review quote, or null. Never invent one.",
            },
            "best_angle": {"type": "string"},
            "disqualifiers": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": [
            "ability_to_pay",
            "pain_intensity",
            "reachability",
            "digital_gap",
            "budget_band",
            "recommended_product",
            "reasons",
            "evidence",
            "best_angle",
            "confidence",
        ],
    },
}


def build_record(biz: Business, site: SiteSignals) -> str:
    """The only data the model may reason from. Field names match the schema
    so the evidence gate can verify citations."""
    record = {
        "name": biz.name,
        "city": biz.city,
        "state": biz.state,
        "address": biz.address,
        "phone": biz.phone,
        "website": biz.website,
        "rating": biz.rating,
        "review_count": biz.review_count,
        "price_level": biz.price_level,
        "reviews": biz.reviews or None,
        "site_fetched": site.fetched,
        "site_error": site.fetch_error,
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
        "site_text_excerpt": site.text_sample[:2500] or None,
    }
    missing = [k for k, v in record.items() if v is None]
    return (
        json.dumps(record, ensure_ascii=False, indent=2)
        + f"\n\nFIELDS WITH NO DATA (do not penalise, do not invent): {missing}"
    )
