"""Offline checks for the parts of scoring that don't call the model.

These are the pieces that decide whether the output is trustworthy: the
prefilter that keeps model spend down, the evidence gate that drops
unsupported claims, and the quote verifier that stops a fabricated review
line reaching an outreach message. Run with: python -m tests.test_scoring_offline
"""

from __future__ import annotations

import sys

from leadagent.models import Business, SiteSignals
from leadagent.score.scorer import (
    _apply_evidence_gate,
    _confidence,
    _finalise,
    _verify_quote,
    prefilter,
)

WEIGHTS = {"ability_to_pay": 40, "pain_intensity": 35, "reachability": 15, "digital_gap": 10}
CUTOFFS = {"A": 75, "B": 55}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def busy_clinic() -> Business:
    return Business(
        source="places", source_id="p1", name="Smile Dental Care", niche="dentist",
        city="Chennai", state="Tamil Nadu", phone="+914412345678",
        website="https://smiledental.example", rating=4.6, review_count=312,
        reviews=[
            "Great treatment but I called four times and nobody picked up.",
            "Doctor is excellent. Reception never confirms appointments.",
        ],
    )


def rich_site() -> SiteSignals:
    site = SiteSignals(fetched=True, https=True, mobile_viewport=True,
                       platform="wordpress", booking_method="call_only",
                       staff_count=4, owner_name="Dr. Anitha")
    site.emails = ["hello@smiledental.example"]
    site.text_sample = "Smile Dental Care, Chennai. Call us to book."
    return site


print("prefilter")
check(
    "rejects a business with no phone and no website",
    prefilter(
        Business(source="osm", source_id="o1", name="Nameless Clinic",
                 niche="dentist", city="Chennai", state="Tamil Nadu"),
        SiteSignals(), {},
    ) is not None,
)
check(
    "rejects a business already on a booking platform",
    prefilter(busy_clinic(), SiteSignals(booking_platform="practo"), {}) is not None,
)
check("keeps a busy reachable clinic", prefilter(busy_clinic(), rich_site(), {}) is None)

print("\nevidence gate")
kept, dropped = _apply_evidence_gate(
    {
        "reasons": [
            "review_count of 312 shows real patient volume",
            "runs_ads indicates an active marketing budget",
        ],
        "evidence": [
            {"field": "review_count", "value": "312"},
            {"field": "runs_ads", "value": "true"},
        ],
    },
    busy_clinic(),
    rich_site(),
)
check("keeps the reason citing a populated field", any("review_count" in r for r in kept))
check("drops the reason citing a field with no data", any("runs_ads" in r for r in dropped),
      f"kept={kept} dropped={dropped}")

print("\nquote verification")
check(
    "accepts a verbatim review quote",
    _verify_quote("I called four times and nobody picked up", busy_clinic()) is not None,
)
check(
    "rejects a quote that appears in no review",
    _verify_quote("We are desperate for automation", busy_clinic()) is None,
)
check("rejects a quote when there are no reviews at all",
      _verify_quote("anything", Business(source="osm", source_id="o2", name="X",
                                         niche="dentist", city="Chennai",
                                         state="Tamil Nadu")) is None)

print("\nweighted total and tiering")
score = _finalise(
    {
        "ability_to_pay": 90, "pain_intensity": 80, "reachability": 70,
        "digital_gap": 40, "budget_band": 4,
        "recommended_product": "WhatsApp appointment bot",
        "reasons": ["review_count of 312 shows real patient volume"],
        "evidence": [{"field": "review_count", "value": "312"}],
        "pain_evidence": "I called four times and nobody picked up",
        "best_angle": "You are losing the callers you cannot reach mid-procedure.",
        "confidence": "high",
    },
    busy_clinic(), rich_site(), WEIGHTS, CUTOFFS,
)
expected = round((90 * 40 + 80 * 35 + 70 * 15 + 40 * 10) / 100)
check(f"weighted total is {expected}", score.total == expected, f"got {score.total}")
check("tier A above the cutoff", score.tier == "A", f"got {score.tier}")
check("verified pain quote survives", score.pain_evidence is not None)

print("\nconfidence honesty")
bare = Business(source="osm", source_id="o3", name="Some Clinic", niche="dentist",
                city="Chennai", state="Tamil Nadu", phone="123")
check("no reviews and no site forces low confidence",
      _confidence("high", bare, SiteSignals()) == "low")
check("site only downgrades high to medium",
      _confidence("high", bare, SiteSignals(fetched=True)) == "medium")
check("reviews plus site keeps the claimed confidence",
      _confidence("high", busy_clinic(), rich_site()) == "high")

print()
if failures:
    print(f"{len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All offline scoring checks passed.")
