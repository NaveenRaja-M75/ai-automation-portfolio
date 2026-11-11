#!/usr/bin/env python3
"""
FOREIGN LEADS, stage 4 — put every reachable foreign lead into the vault.

WHY
    The workbook is a view. Everything that actually works a lead lives in
    the vault: the sending, the three-channel sequence (cadence.py), the
    WhatsApp and Instagram queue page, the daily Gmail reply check, the
    do-not-contact list, and the ledger that stops anyone being pitched
    twice. A lead that exists only in a spreadsheet gets none of that, and
    after a hundred sends from a spreadsheet nobody can say who was nudged,
    who replied, or who is due today.

WHAT GOES IN
    Agencies, D2C brands and other businesses that can be reached by email,
    Instagram or a mobile, and have not already been contacted. Dropshipping
    stores, Messenger-only pages, Indian brands and anything on the skip
    sheet stay in the workbook only.

WHAT IT NEVER DOES
    Overwrite a note. A lead already in the vault (matched by file name,
    email or website) only has its EMPTY fields filled. Approval tags,
    drafts written by hand and send history are never touched.

    python run.py foreign-vault            import
    python run.py foreign-vault --dry-run  show what would change
"""
import re
import sys
import argparse
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
import foreign_build as B

TODAY = datetime.date.today().isoformat()


def dom(v):
    v = re.sub(r"^https?://", "", str(v or "").lower())
    return re.sub(r"^www\.", "", v).split("/")[0]


def existing_index():
    idx = {}
    for st in V.STAGES:
        for p in V.notes_in(st):
            fm, _ = V.read_note(p)
            keys = ["slug:" + p.stem]
            e = str(fm.get("email") or "").lower()
            if "@" in e and not e.split("@")[1].startswith(B.FREE):
                keys.append("email:" + e)
            d = dom(fm.get("website"))
            if d and "." in d:
                keys.append("dom:" + d)
            for k in keys:
                idx.setdefault(k, (p, st))
    return idx


def frontmatter(r):
    country = B.country_of(r)
    pretty, pkind, _ = B.phone_info(r.get("phone"))
    first, seq = B.best_channel(r, r["kind"])
    fm = {
        "status": "analyzed",
        "brand": B.t(r.get("name")),
        "greeting_name": B.greeting_for(r),
        "lead_type": "agency" if r["kind"] == "agency" else "brand",
        "market": f"International - {country}",
        "country": country,
        "city": B.t(r.get("city")) if B.t(r.get("city")) != "Not verified" else "",
        "email": B.t(r.get("email")),
        "email_type": B.email_kind(B.t(r.get("email")), B.t(r.get("name"))),
        "phone": pretty,
        "phone_type": pkind,
        "instagram": B.t(r.get("instagram")),
        "website": B.site_domain(r),
        "tier": {"A": "A - strong", "B": "B - workable"}.get(r["_tier"], "C - weak, low priority"),
        "quality": r["_score"],
        "first_channel": first,
        "send_at_ist": B.send_time(country, B.t(r.get("city"))),
        "sourced": TODAY,
        "source": "foreign workbook",
        "tags": ["lead", "foreign", "agency" if r["kind"] == "agency" else "brand"],
    }
    if r["kind"] == "agency":
        fm.update({"services": B.t(r.get("services"))[:150], "reviews": B.t(r.get("reviews")),
                   "rating": B.t(r.get("rating"))})
    else:
        fm.update({"business_type": B.business_type(r), "niche": B.human_niche(r)})
        if r.get("page_id"):
            fm.update({"page_id": B.t(r.get("page_id")), "ads_link": B.ads_link(r),
                       "fb_messenger": f"https://m.me/{B.t(r.get('page_id'))}"})
        if isinstance(r.get("ads_now"), int):
            fm.update({"active_ads": r["ads_now"], "video_ads": r.get("videos_now", 0),
                       "distinct_creatives": r.get("distinct_now", 0),
                       "days_running": r.get("days_now", 0),
                       "ads_checked_on": B.t(r.get("checked_on"))})
    return {k: v for k, v in fm.items() if v not in ("", None)}


def body(r, fm):
    what = B.agency_what(r) if r["kind"] == "agency" else B.brand_what(r)
    evidence = "" if r["kind"] == "agency" else B.brand_evidence(r)
    angle = ("White-label production arm: their name on the work, Monarc behind it."
             if r["kind"] == "agency" else B.brand_angle(r))
    return f"""# {fm['brand']}

## What they are
{what}

## What is verified
- Country: {fm.get('country')}{' (' + B.t(r.get('legal_country_why')) + ')' if r.get('legal_country_why') else ''}
- Ads: {evidence or 'not applicable for an agency'}
- Found in: {B.found_in(r)}

Only claim what is written in this section. An agency is never told its work
is weak. A brand is only told it runs ads when the line above says so.

## The angle
{angle}

## Draft messages
> Written by draft_all.py from the facts above.

### Email

### Instagram

### WhatsApp

---
## To advance this lead
Write the approval tag on its own line at the bottom, then ask Claude to send.
A tag inside backticks does not count.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    # A and B only by default. A thousand notes land in Obsidian at once and
    # the forty worth sending this week disappear under the eight hundred
    # that are merely reachable. Priority C stays in the workbook until asked.
    ap.add_argument("--tiers", default="A,B",
                    help="which priorities to load, e.g. A,B,C for everything")
    a = ap.parse_args()
    tiers = {x.strip().upper() for x in a.tiers.split(",") if x.strip()}

    raw, con, sent, sent_dom = B.load()
    idx = existing_index()
    added = filled = skipped_sent = held = 0
    per_sheet = {}
    for r in raw:
        sheet, _ = B.classify_agency(r) if r["kind"] == "agency" else B.classify_brand(r)
        if sheet not in ("agency", "brand", "other"):
            continue
        if sheet == "other" and B.business_type(r) == "Likely dropshipping store":
            continue
        if not (r.get("email") or r.get("instagram") or
                B.phone_info(r.get("phone"))[1] in ("mobile", "unknown")):
            continue
        e = B.t(r.get("email")).lower()
        if sent.get(e) or ("@" in e and sent_dom.get(e.split("@")[1])):
            skipped_sent += 1
            continue
        r["_score"], r["_tier"] = (B.score_agency(r) if r["kind"] == "agency"
                                   else B.score_brand(r))
        if r["_tier"] not in tiers:
            held += 1
            continue
        fm = frontmatter(r)
        keys = ["slug:" + V.slug(fm["brand"])]
        if "@" in e and not e.split("@")[1].startswith(B.FREE):
            keys.append("email:" + e)
        if fm.get("website"):
            keys.append("dom:" + fm["website"])
        hit = next((idx[k] for k in keys if k in idx), None)
        if hit:
            path, stage = hit
            old, old_body = V.read_note(path)
            gaps = {k: v for k, v in fm.items()
                    if k not in ("status", "sourced", "tags", "source") and
                    (old.get(k) in (None, "", {}, []))}
            if gaps:
                filled += 1
                if not a.dry_run:
                    old.update(gaps)
                    path.write_text("---\n" + V._fm_dump(old) + "\n---\n\n" +
                                    old_body.strip() + "\n", encoding="utf-8")
            continue
        added += 1
        per_sheet[sheet] = per_sheet.get(sheet, 0) + 1
        if not a.dry_run:
            p = V.write_note("analyzed", fm["brand"], fm, body(r, fm))
            for k in keys:
                idx.setdefault(k, (p, "analyzed"))

    verb = "would be" if a.dry_run else "were"
    print(f"{added} new foreign leads {verb} added to vault/02_Analyzed {per_sheet}")
    print(f"{filled} existing notes {verb} given missing contact details (nothing overwritten)")
    print(f"{skipped_sent} left out because they were already emailed")
    print(f"{held} Priority {', '.join(sorted({'A', 'B', 'C'} - tiers)) or '-'} leads kept in the "
          f"workbook only (load them with --tiers A,B,C)")
    if not a.dry_run and added:
        print("Next: python engine/draft_all.py   (writes their drafts from the verified facts)")


if __name__ == "__main__":
    main()
