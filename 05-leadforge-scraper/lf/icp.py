"""
LeadForge :: ICP (ideal customer profile) matching and intent scoring.

Scoring philosophy
------------------
Apollo/Apify sell you *lists*. A list is a guess. What actually converts is an
observed **signal of need** published by the buyer themselves: "we need a video
editor", "launching next month", "$2k budget", "hiring a UGC creator".

The hard part is that *every job description on earth* contains "we are looking
for" and "we need". So a naive keyword match scores Sales Director roles at 100.
LeadForge solves this with two ideas:

  1. TWO SCORING MODES.
       post       - a human wrote this to ask for something (HN, Reddit, forums,
                    search results). Title and body carry equal weight, because
                    the body IS the request.
       job_board  - a company is hiring a role (LinkedIn, RemoteOK, WWR...).
                    These are boilerplate-heavy, so the TITLE decides and the
                    body is only weak supporting context.

  2. A FIT GATE.
       In job_board mode, if the job title is not a creative/video/marketing
       role and does not name one of your offers, the lead is capped at 30 no
       matter how many keywords its body happens to contain. "Sales Director"
       is not your customer even if the company sells video tools.

Axes (sum 100):
  FIT       (0-45)  does this name one of YOUR offers, or hire for one?
  INTENT    (0-20)  did they say, in their own words, that they need it?
  MONEY     (0-12)  is there evidence of a budget?
  FRESHNESS (0-8)   how recent is the signal?
  REACH     (0-10)  can you actually reach a decision maker?
  PLATFORM  (0-5)   does the venue imply willingness to pay?
  minus PENALTIES for students, unpaid work and suppliers

Tier A >= 65, B >= 45, C >= 25, D below.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .core import find_emails, registrable_domain

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass
class Offer:
    id: str
    name: str
    strong: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    pitch: str = ""


@dataclass
class Config:
    path: Path
    profile: dict
    limits: dict
    buy_phrases: list[str]
    money_phrases: list[str]
    negatives: list[str]
    hard_negatives: list[str]
    extraction_negatives: list[str]
    role_words: list[str]
    offers: list[Offer]
    sources: dict
    reddit: dict
    apis: dict
    outreach: dict
    raw: dict

    # convenience -----------------------------------------------------
    @property
    def offer_by_id(self) -> dict[str, Offer]:
        return {o.id: o for o in self.offers}


def _norm_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def load_config(path: str | Path) -> Config:
    p = Path(path)
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)

    offers = []
    for o in raw.get("offers", []):
        offers.append(Offer(
            id=o["id"],
            name=o.get("name", o["id"]),
            strong=_norm_list(o.get("strong")),
            weak=_norm_list(o.get("weak")),
            pitch=o.get("pitch", ""),
        ))
    if not offers:
        raise ValueError("config has no [[offers]] blocks")

    kw = raw.get("keywords", {})
    return Config(
        path=p,
        profile=raw.get("profile", {}),
        limits=raw.get("limits", {}),
        buy_phrases=_norm_list(kw.get("buy")),
        money_phrases=_norm_list(kw.get("money")),
        negatives=_norm_list(kw.get("negative")),
        hard_negatives=_norm_list(kw.get("hard_negative")),
        extraction_negatives=_norm_list(kw.get("extraction_negative")),
        role_words=_norm_list(kw.get("role")),
        offers=offers,
        sources=raw.get("sources", {}),
        reddit=raw.get("reddit", {}),
        apis=raw.get("apis", {}),
        outreach=raw.get("outreach", {}),
        raw=raw,
    )


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

_WORDISH = re.compile(r"[^a-z0-9+#]+")


def _pattern(term: str) -> re.Pattern:
    """
    Build a matcher for a keyword phrase.

    - word-ish boundary instead of \\b so terms with '+' or '.' still match
    - whitespace inside a term is flexible (\\s+)
    - trailing 's' optional when it is not already there (cheap pluralisation)
    """
    t = term.strip().lower()
    body = re.escape(t).replace(r"\ ", r"\s+")
    if not t.endswith("s") and " " not in t:
        body += "s?"
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.I)


class Matcher:
    """Pre-compiled phrase -> regex index, built once per run."""

    def __init__(self, terms: list[str]):
        self.terms = terms
        self.index: list[tuple[str, re.Pattern]] = [
            (t, _pattern(t)) for t in terms if t and t.strip()
        ]

    def find(self, text: str) -> list[str]:
        if not text:
            return []
        low = text.lower()
        hits = []
        for term, rx in self.index:
            if term.lower() in low and rx.search(low):
                hits.append(term)
        return hits

    def has(self, text: str) -> bool:
        return bool(self.find(text))

    def score(self, text: str, hit_points: int, cap: int) -> tuple[int, list[str]]:
        hits = self.find(text)
        return min(cap, len(hits) * hit_points), hits


# --------------------------------------------------------------------------
# Budget extraction
# --------------------------------------------------------------------------

_CUR = {
    "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP",
    "gbp": "GBP", "₹": "INR", "inr": "INR", "aud": "AUD", "cad": "CAD",
}

# $2,500  |  $2.5k  |  ₹50,000  |  2500 usd  |  5-10k
_MONEY_RX = re.compile(
    r"(?P<cur>\$|usd|€|eur|£|gbp|₹|inr|aud|cad)?\s*"
    r"(?P<num>\d{1,3}(?:[,\d]{0,12})(?:\.\d{1,2})?)\s*"
    r"(?P<suf>k|m)?\s*(?P<cur2>usd|eur|gbp|inr|aud|cad|dollars?|bucks?)?",
    re.I,
)
_RANGE_RX = re.compile(
    r"(?P<a>\d[\d,\.]*)\s*(?P<sufa>k|m)?\s*(?:-|–|to)\s*"
    r"(?P<b>\d[\d,\.]*)\s*(?P<sufb>k|m)?",
    re.I,
)


def _to_amount(num: str, suffix: str | None) -> float:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return 0.0
    s = (suffix or "").lower()
    if s == "k":
        v *= 1_000
    elif s == "m":
        v *= 1_000_000
    return v


def extract_budget(text: str) -> tuple[str | None, float | None, str | None]:
    """Return (raw_span, max_amount, currency) if a credible money figure exists."""
    if not text:
        return None, None, None
    low = text.lower()

    rng = _RANGE_RX.search(low)
    if rng:
        cur = _CUR.get((rng.group("sufa") or "")[:0] or "", "") or ""
        a = _to_amount(rng.group("a"), rng.group("sufa"))
        b = _to_amount(rng.group("b"), rng.group("sufb"))
        hi = max(a, b)
        if _plausible_budget(hi):
            cur = _currency_near(low, rng.start()) or "USD"
            return rng.group(0).strip(), hi, cur

    best = (None, 0.0, None)
    for m in _MONEY_RX.finditer(text):
        cur = m.group("cur") or m.group("cur2") or ""
        cur_code = _CUR.get(cur.strip().lower(), _CUR.get(cur.strip(), None))
        amount = _to_amount(m.group("num"), m.group("suf"))
        if not _plausible_budget(amount):
            continue
        if cur_code is None:
            # no currency token anywhere near -> only trust $ or suffixed (2k)
            if not (m.group("cur") or m.group("suf")):
                continue
            cur_code = "USD"
        if amount > best[1]:
            best = (m.group(0).strip(), amount, cur_code)
    if best[1]:
        return best
    return None, None, (None)


def _plausible_budget(v: float) -> bool:
    # below $250 it is almost always years/salaries/counts, not a project budget
    return 250 <= v <= 5_000_000


def _currency_near(text: str, pos: int) -> str | None:
    window = text[max(0, pos - 30): pos]
    for sym, code in _CUR.items():
        if sym in window:
            return code
    return None


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    score: int
    tier: str
    tags: list[str]
    offers: list[str]
    budget_raw: str | None = None
    budget_max: float | None = None
    currency: str | None = None
    excluded: str | None = None
    breakdown: dict = field(default_factory=dict)


class Scorer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.buy = Matcher(cfg.buy_phrases)
        self.money = Matcher(cfg.money_phrases)
        self.neg = Matcher(cfg.negatives)
        self.hard_neg = Matcher(cfg.hard_negatives)
        self.extraction_neg = Matcher(cfg.extraction_negatives)
        self.roles = Matcher(cfg.role_words)
        self.offer_strong = {o.id: Matcher(o.strong) for o in cfg.offers}
        self.offer_weak = {o.id: Matcher(o.weak) for o in cfg.offers}

    def score(self, title: str, body: str, meta: dict | None = None,
              platform_bonus: int = 0, mode: str = "post") -> Verdict:
        meta = meta or {}
        title = title or ""
        body = body or ""
        blob = title + "\n" + body
        tags: list[str] = []
        bd: dict[str, int] = {}

        # ---- hard excludes ------------------------------------------------
        if self.hard_neg.has(blob):
            hit = self.hard_neg.find(blob)[0]
            return Verdict(0, "D", ["excluded"], [],
                           excluded=f"supplier/self-promo: {hit}",
                           breakdown={"excluded": 1})
        if self.extraction_neg.has(blob):
            hit = self.extraction_neg.find(blob)[0]
            return Verdict(0, "D", ["excluded"], [],
                           excluded=f"index page: {hit}",
                           breakdown={"excluded": 1})

        job_board = (mode == "job_board")

        # ---- FIT (0-45) ---------------------------------------------------
        matched: list[str] = []
        title_strong = title_weak = 0
        for o in self.cfg.offers:
            if self.offer_strong[o.id].has(title):
                title_strong += 1
                matched.append(o.id)
            elif self.offer_weak[o.id].has(title):
                title_weak += 1
                matched.append(o.id)

        body_strong = 0
        if not job_board:
            for o in self.cfg.offers:
                if o.id not in matched and self.offer_strong[o.id].has(body):
                    matched.append(o.id)
                if self.offer_strong[o.id].has(body):
                    body_strong += 1

        role_hit = [r for r in self.roles.find(title)]
        title_fit = 0

        if job_board:
            # the TITLE decides everything on a job board
            if title_strong:
                fit = 35 + min(10, (title_strong - 1) * 8)
            elif title_weak:
                fit = 15 + min(8, (title_weak - 1) * 4)
            elif role_hit:
                fit = 28
            else:
                fit = 0
            # weak supporting evidence from the body
            body_ctx = 0
            for o in self.cfg.offers:
                if self.offer_strong[o.id].has(body):
                    body_ctx += 6
                elif self.offer_weak[o.id].has(body):
                    body_ctx += 2
            fit += min(12, body_ctx)
            title_fit = fit
        else:
            # a human asking for something: title *and* body both count
            if title_strong:
                fit = 35 + min(10, (title_strong - 1) * 8)
                title_fit = fit
            elif title_weak:
                fit = 15 + min(8, (title_weak - 1) * 4)
                title_fit = fit
            elif body_strong:
                fit = 30 + min(12, (body_strong - 1) * 6)
            elif role_hit and len(role_hit) and self.buy.has(blob):
                fit = 18
                title_fit = 18
            else:
                fit = min(10, sum(
                    4 for o in self.cfg.offers
                    if self.offer_weak[o.id].has(body)))
            fit = min(45, fit)
        bd["fit"] = min(45, fit)
        for oid in matched:
            tags.append(f"offer:{oid}")
        if role_hit:
            tags.append("hires:" + role_hit[0].replace(" ", "-"))

        # ---- INTENT (0-20) ------------------------------------------------
        buy_title = self.buy.find(title)
        buy_body = self.buy.find(body)
        intent = min(14, len(buy_title) * 7)
        # body language is boilerplate-prone on job boards -> heavily damped
        intent += min(4 if job_board else 6, len(buy_body) * (1 if job_board else 2))
        if buy_title or (not job_board and buy_body):
            tags.append("says-they-need-it")
        bd["intent"] = min(20, intent)

        # ---- MONEY (0-12) -------------------------------------------------
        raw_amt, amount, currency = extract_budget(blob)
        bd["money"] = 0
        if amount:
            bd["money"] = 8 if amount < 1000 else 12
            tags.append("budget-stated" if amount < 1000 else "budget-1k-plus")

        # ---- FRESHNESS (0-8) ----------------------------------------------
        age_h = _age_hours(meta.get("posted_ts"))
        if age_h is None:
            bd["freshness"] = 2
        elif age_h <= 48:
            bd["freshness"] = 8
            tags.append("fresh-48h")
        elif age_h <= 168:
            bd["freshness"] = 6
        elif age_h <= 720:
            bd["freshness"] = 3
        else:
            bd["freshness"] = 1

        # ---- REACH (0-10) -------------------------------------------------
        bd["reach"] = 0
        inline = find_emails(blob)
        if inline:
            bd["reach"] += 7
            tags.append("email-in-post")
        dom = meta.get("domain") or registrable_domain(meta.get("url") or "")
        if dom:
            bd["reach"] += 3
            tags.append("company-known")

        # ---- PLATFORM (0-5) -----------------------------------------------
        bd["platform"] = min(5, platform_bonus)
        if platform_bonus:
            tags.append("paid-venue")

        # ---- PENALTIES -----------------------------------------------------
        penalty = 0
        neg_hits = self.neg.find(blob)
        if neg_hits:
            penalty += min(24, len(neg_hits) * 12)
            tags.append("penalty:" + ",".join(neg_hits[:2]))

        total = sum(bd.values()) - penalty

        # ---- FIT GATE ------------------------------------------------------
        # No fit signal at all -> this is not your customer, whatever the
        # body happens to mention. Cap it hard.
        if bd["fit"] < 8:
            total = min(total, 24)
            tags.append("low-fit")
        elif job_board and title_fit == 0:
            total = min(total, 32)
            tags.append("title-not-relevant")

        total = max(0, min(100, total))
        bd["penalty"] = -penalty
        tier = tier_for(total)

        return Verdict(
            score=total, tier=tier, tags=tags, offers=matched,
            budget_raw=raw_amt, budget_max=amount, currency=currency, breakdown=bd,
        )

    # ------------------------------------------------------------------
    def best_offer(self, offer_ids: list[str]) -> str | None:
        if not offer_ids:
            return None
        by = self.cfg.offer_by_id
        return by[offer_ids[0]].name if offer_ids[0] in by else offer_ids[0]


# --------------------------------------------------------------------------
# Tiering + the enrichment adjustment (kept in ONE place so `enrich` and
# `rescore` can never disagree about a lead's grade)
# --------------------------------------------------------------------------

ADVERTISER_BONUS = 8     # their site runs paid-ad pixels: budget already exists
NO_MX_PENALTY = 15       # their domain cannot receive mail: a dead end

# Tags owned by ENRICHMENT rather than by the scorer. `regrade_all` re-derives
# the scorer's own tags but must never erase evidence it cannot recompute: a
# detected ad pixel is a fact about their website, not an opinion about a post.
ENRICH_TAGS = frozenset({"advertiser", "no-mx", "multi-signal"})


def tier_for(score: int) -> str:
    return "A" if score >= 65 else "B" if score >= 45 else "C" if score >= 25 else "D"


def apply_enrich_adjust(score: int, tags) -> int:
    """Move a text-derived score using facts only enrichment can observe."""
    tags = set(tags or ())
    if "advertiser" in tags:
        score = min(100, score + ADVERTISER_BONUS)
    if "no-mx" in tags:
        score = max(0, score - NO_MX_PENALTY)
    return score


def _age_hours(posted_ts) -> float | None:
    if not posted_ts:
        return None
    try:
        return max(0.0, (datetime.now(timezone.utc).timestamp() - float(posted_ts)) / 3600.0)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Cluster boost constants: the same company surfacing in several places at once
# is real demand, not a coincidence.
# --------------------------------------------------------------------------

CLUSTER_MIN_REPEAT = 2
CLUSTER_PER_EXTRA = 4
CLUSTER_CAP = 12


def cluster_extra(count: int) -> int:
    """Extra points for a domain that appears `count` times (0 if it doesn't)."""
    if count < CLUSTER_MIN_REPEAT:
        return 0
    return min(CLUSTER_CAP, (count - 1) * CLUSTER_PER_EXTRA)


def cluster_extra_for_domain(store, domain: str | None) -> int:
    """Cluster boost for one domain, straight from the database.

    `enrich` needs this so that writing a fresh score never silently drops the
    cluster component that `fetch`/`rescore` added.
    """
    if not domain:
        return 0
    row = store.conn.execute(
        "SELECT COUNT(*) n FROM leads WHERE domain = ? AND status = 'new'",
        (domain,),
    ).fetchone()
    return cluster_extra(row["n"] if row else 0)


def regrade_all(store, scorer) -> dict:
    """Re-materialise score / tier / tags / offers for EVERY stored lead.

    This is the single source of truth for a lead's grade, and it is the one
    function that mutates it. It always starts from a fresh text score, then
    layers (a) the facts enrichment observed and (b) the cluster boost. Because
    nothing is ever *incremented in place*, it is idempotent: running it twice
    changes nothing, and running it after a scorer change re-grades the whole
    database. Scores therefore cannot drift upward across repeated fetches.

    Returns a summary dict for reporting.
    """
    import json

    from .sources import SOURCE_REGISTRY, source_mode

    counts: dict[str, int] = {}
    for r in store.conn.execute(
        "SELECT domain, COUNT(*) n FROM leads "
        "WHERE domain IS NOT NULL AND domain <> '' AND status = 'new' "
        "GROUP BY domain"
    ):
        counts[r["domain"]] = r["n"]

    changed = 0
    moves: list[tuple[str, str, str, int]] = []
    for row in store.conn.execute("SELECT * FROM leads").fetchall():
        r = dict(row)
        name = r.get("source") or ""
        entry = SOURCE_REGISTRY.get(name)
        bonus = entry[1] if entry else 1
        v = scorer.score(r.get("title") or "", r.get("body") or "", meta=r,
                         platform_bonus=bonus, mode=source_mode(name))
        try:
            old_tags = json.loads(r.get("tags") or "[]")
        except (TypeError, ValueError):
            old_tags = []
        if not isinstance(old_tags, list):
            old_tags = []
        # keep only the evidence the scorer cannot recompute
        new_tags = v.tags + [t for t in old_tags
                             if t in ENRICH_TAGS and t not in v.tags]

        if v.excluded:
            score = 0
        else:
            score = apply_enrich_adjust(v.score, new_tags)
            extra = cluster_extra(counts.get(r.get("domain") or "", 0))
            if extra:
                score = min(100, score + extra)
                if "multi-signal" not in new_tags:
                    new_tags.append("multi-signal")

        tier = tier_for(score)
        if (r.get("score"), r.get("tier"), old_tags) == (score, tier, new_tags):
            continue
        store.update(r["id"], score=score, tier=tier,
                     tags=json.dumps(new_tags), offers=json.dumps(v.offers),
                     budget_raw=v.budget_raw, budget_max=v.budget_max,
                     currency=v.currency)
        changed += 1
        if "A" in (r.get("tier"), tier) or "B" in (r.get("tier"), tier):
            label = r.get("company") or r.get("title") or "?"
            moves.append((label[:44], r.get("tier") or "?", tier, score))

    boosted = sum(1 for n in counts.values()
                  if cluster_extra(n) and n >= CLUSTER_MIN_REPEAT)
    return {"changed": changed, "boosted_domains": boosted, "moves": moves}

