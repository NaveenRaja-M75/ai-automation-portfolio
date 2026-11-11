#!/usr/bin/env python3
"""
Import agency leads into the vault as notes.

WHY AGENCIES BECAME THE MAIN LINE
    The brand pipeline is sound engineering that ran into a closed market.
    Across four runs and roughly three hundred verified advertisers, about
    87% already ran video, and every survivor failed human inspection: a
    dropshipper cloaking a headtorch behind anti-aging ads, a content farm,
    a two-ad micro-brand on a personal Gmail. The "zero video" gap barely
    exists among real brands with budget.

    Agencies do not need that gap to exist. They already sell video, already
    hold production budget, and routinely subcontract specialised work. The
    pitch stops being a diagnosis of their weakness and becomes an offer of
    capacity, which removes the entire class of failure that the brand
    approach kept hitting.

Reads the enriched agency sheet and writes one note per agency, using the
same frontmatter shape as brand leads so every existing command, dashboard
query and safety rail works unchanged.
"""
import re
import sys
import argparse
import datetime
import unicodedata
import urllib.parse
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V

DEFAULT_SRC = Path(r"C:\Users\naveen\Desktop\Lead_Generation7\agency_leads_contacts.xlsx")


def clean(v):
    # NFKC folds decorative Unicode ("𝐔𝐕 𝐃𝐢𝐠𝐢𝐭𝐚𝐥") back to plain letters, which
    # a greeting and a filename both need.
    s = unicodedata.normalize("NFKC", str(v or "")).strip()
    return "" if s.lower() in ("nan", "none") else s


def clean_email(v):
    """Scraped mailto links arrive URL-encoded ("%20info@agency.example"). Sending
    to that bounces, and a bounce on a new sender hurts every later email."""
    s = urllib.parse.unquote(clean(v)).strip().strip(".,;:<>").lower()
    return s if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", s) else ""


def phone_key(v):
    d = re.sub(r"\D", "", clean(v))
    return d[-10:] if len(d) >= 10 else ""


def tier_for(row):
    """Agencies are graded on reach and evidence of a real client book."""
    score = 0
    if clean(row.get("Email")):
        score += 35
    if clean(row.get("Phone / WhatsApp")):
        score += 20
    if clean(row.get("Instagram")):
        score += 15
    try:
        rev = int(float(row.get("Reviews") or 0))
    except Exception:
        rev = 0
    score += 20 if rev >= 20 else 12 if rev >= 5 else 0
    if re.search(r"video", str(row.get("Services") or ""), re.I):
        score += 10                      # already sells video: warmest of all
    tier = ("A - strong" if score >= 70 else
            "B - workable" if score >= 45 else "C - weak, low priority")
    return score, tier


def body_for(row, tier):
    name = clean(row.get("Agency"))
    city = clean(row.get("City")) or clean(row.get("Country"))
    svc = clean(row.get("Services"))[:200]
    rev = clean(row.get("Reviews"))
    site = clean(row.get("Website"))
    return f"""# {name}

## Why them
They run paid media for clients out of {city}. Creative production is the
bottleneck in that model: they are judged on ad performance but staffed for
media buying, so specialised motion work gets subcontracted. One agency deal
recurs across their whole roster instead of winning a single brand.

## What is true about them
- Services they list: {svc or "(not listed)"}
- Location: {city or "(unknown)"}
- Public reviews: {rev or "(none listed)"}
- Site: {site or "(none)"}

Everything above comes from their own public listing, so all of it is safe to
reference. Do not claim anything about the quality of their work or that they
are short-staffed. See `00_Control/AGENCY_PITCH.md`.

## The angle
White-label production arm. Their name on the work, Monarc behind it. Say
the word white-label early, or an agency reads a capability pitch as a
competitor coming for their clients.

## Draft messages
> Run `python run.py pitch` and give the brief to Claude Code, which reads
> AGENCY_PITCH.md and VOICE.md and writes these three.

### Email

### Instagram

### WhatsApp

---
## To advance this lead
Write the approval tag on its own line at the bottom, then run
`python run.py dispatch`. A tag inside backticks does not count.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--region", default="tn",
                    choices=["tn", "india", "international", "all"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    src = Path(a.src)
    if not src.exists():
        print(f"Not found: {src}")
        return
    df = pd.read_excel(src, dtype=str)

    # only agencies we can actually reach
    df = df[df["Email"].notna() | df["Phone / WhatsApp"].notna()
            | df["Instagram"].notna()]

    if a.region == "tn":
        df = df[df["City"].astype(str).str.contains(
            "Chennai|Coimbatore|Madurai", case=False, na=False)]
    elif a.region == "india":
        df = df[df["Market"].astype(str).str.startswith("India")]
    elif a.region == "international":
        df = df[df["Market"].astype(str).str.startswith("International")]
    if a.limit:
        df = df.head(a.limit)

    # A note for this agency in ANY stage means it is already in the system.
    # Re-importing would write a fresh copy into 02_Analyzed next to the one
    # in 04_Sent, and the fresh copy has no send history, so the same agency
    # would be pitched from scratch a second time.
    existing = {p.stem for st in V.STAGES for p in V.notes_in(st)}
    # Directory listings carry the same office twice under two names ("The
    # Vital Media - ..." and "The Vital Media | ..."). The name differs, the
    # email and phone do not, so those are what identify an agency.
    taken_email, taken_phone = set(), set()
    for st in V.STAGES:
        for q in V.notes_in(st):
            qfm, _ = V.read_note(q)
            if clean_email(qfm.get("email")):
                taken_email.add(clean_email(qfm.get("email")))
            if phone_key(qfm.get("phone")):
                taken_phone.add(phone_key(qfm.get("phone")))
    made = skipped = already = 0
    print(f"Importing {len(df)} reachable agencies (region: {a.region})\n")

    for _, row in df.iterrows():
        name = clean(row.get("Agency"))
        if not name:
            continue
        # The ledger holds these already from the earlier delivery, so a plain
        # dedupe check would reject every one. Agencies are a NEW channel, not
        # a repeat pitch, so only the do-not-contact list applies here.
        if V.blocked(name):
            skipped += 1
            continue
        em, pk = clean_email(row.get("Email")), phone_key(row.get("Phone / WhatsApp"))
        if V.slug(name) in existing or (em and em in taken_email) or                 (pk and pk in taken_phone):
            already += 1
            continue
        if em:
            taken_email.add(em)
        if pk:
            taken_phone.add(pk)
        score, tier = tier_for(row)
        fm = {
            "status": "analyzed",
            "brand": name,
            "lead_type": "agency",
            "market": clean(row.get("Market")),
            "city": clean(row.get("City")),
            "country": clean(row.get("Country")),
            "services": clean(row.get("Services"))[:150],
            "reviews": clean(row.get("Reviews")),
            "rating": clean(row.get("Rating")),
            "email": clean_email(row.get("Email")),
            "phone": clean(row.get("Phone / WhatsApp")),
            "instagram": clean(row.get("Instagram")),
            "website": clean(row.get("Website")),
            "evidence": {"runs_paid_media": "confirmed",
                         "services_listed": "confirmed",
                         "creative_capacity": "unknown"},
            "quality": score,
            "tier": tier,
            "sourced": datetime.date.today().isoformat(),
            "tags": ["lead", "agency"],
        }
        routes = [x for x in (
            ("Email: " + fm["email"]) if fm["email"] else "",
            ("Phone/WA: " + fm["phone"]) if fm["phone"] else "",
            ("IG DM: " + fm["instagram"]) if fm["instagram"] else "") if x]
        fm["reach"] = " | ".join(routes)

        V.write_note("analyzed", name, fm, body_for(row, tier))
        made += 1
        print(f"  {tier[0]}  {name[:44]:<46} {fm['reach'][:52]}")

    print(f"\n{made} agency notes written to vault/02_Analyzed/")
    if skipped:
        print(f"{skipped} skipped (do-not-contact)")
    if already:
        print(f"{already} already in the vault or duplicate listings, left untouched")
    print("Next: python run.py pitch")


if __name__ == "__main__":
    main()
