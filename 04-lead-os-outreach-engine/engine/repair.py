#!/usr/bin/env python3
"""
REPAIR — re-run current logic over notes that already exist.

WHY THIS COMMAND HAD TO EXIST
    A bug was fixed in the Instagram parser so that locale paths like
    /en_us/ could never be mistaken for a handle. The code was correct
    afterwards, and the note on disk still said `instagram: "@en_us"`,
    because it had been written before the fix. Dispatch would have opened
    instagram.com/en_us and the outreach would have gone nowhere.

    Fixing code does not fix data already written. Any pipeline that stores
    its output needs a way to re-run current logic over old records, or every
    bug fix leaves a tail of corrupted rows behind it.

WHAT IT REPAIRS
    - social handles and contacts re-extracted with today's parsers
    - email re-graded and MX re-checked, so a dead address is dropped
    - quality tier recomputed
    - status field re-synced to the folder the note actually sits in
    - any credential that ever reached a note is scrubbed

It never invents data. If the site no longer publishes a phone, the field is
cleared rather than left holding something stale and wrong.
"""
import re
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import vault as V
from enrich import from_known_site, email_quality
from lib.contact_enricher import resolve, mx_ok, instagram_from

TOKEN_RE = re.compile(r"access_token=[A-Za-z0-9_\-]{20,}")


def repair_note(path, deep=False):
    """Return a list of the changes made to one note."""
    fm, body = V.read_note(path)
    brand = fm.get("brand", path.stem)
    changed = []

    # 1. status must match the folder it lives in
    stage = next((s for s, folder in V.STAGES.items()
                  if Path(path).parent == folder), None)
    if stage and fm.get("status") != stage:
        changed.append(f"status {fm.get('status')} -> {stage}")
        fm["status"] = stage

    # 2. strip any credential that ever landed in the note
    if TOKEN_RE.search(body) or TOKEN_RE.search(str(fm)):
        body = TOKEN_RE.sub("access_token=REDACTED", body)
        for k, v in list(fm.items()):
            if isinstance(v, str) and TOKEN_RE.search(v):
                fm[k] = TOKEN_RE.sub("access_token=REDACTED", v)
        changed.append("scrubbed credential")

    # 3. re-validate the stored Instagram handle with the REAL parser.
    #    A second copy of the rules lived here and immediately fell behind:
    #    it knew about locale paths but not embed.js, so a fixed parser still
    #    left a bad handle on disk. One source of truth, applied everywhere.
    ig = str(fm.get("instagram") or "").lstrip("@").strip()
    if ig:
        valid = instagram_from(f'<a href="https://instagram.com/{ig}">')
        if not valid:
            changed.append(f"dropped invalid instagram @{ig}")
            fm["instagram"] = ""

    # 4. optionally re-fetch contacts with today's parsers
    if deep:
        site = fm.get("website", "")
        if site:
            email, phone, insta = from_known_site(site)
        else:
            site, email, _, phone, insta = resolve(brand)
        if email and mx_ok(email) != "Verified":
            email = ""
        for field, new in (("email", email), ("phone", phone),
                           ("instagram", insta), ("website", site)):
            old = str(fm.get(field) or "")
            if new and new != old:
                changed.append(f"{field}: {old or '(empty)'} -> {new}")
                fm[field] = new
            elif old and not new and field in ("email", "phone", "instagram"):
                changed.append(f"{field} no longer published, cleared")
                fm[field] = ""

    # 5. recompute grading from whatever is now true
    fm["email_quality"] = email_quality(fm.get("email", ""), brand)
    score = 0
    q = fm["email_quality"]
    score += 40 if q == "business" else 25 if q == "business-freemail" else 5 if q == "personal" else 0
    if fm.get("phone"):
        score += 20
    if fm.get("instagram"):
        score += 15
    if fm.get("website"):
        score += 15
    try:
        if int(fm.get("active_ads") or 0) >= 5:
            score += 10
    except Exception:
        pass
    tier = ("A - strong" if score >= 70 else
            "B - workable" if score >= 45 else "C - weak, low priority")
    if fm.get("tier") != tier:
        changed.append(f"tier {fm.get('tier') or '(none)'} -> {tier}")
    fm["quality"], fm["tier"] = score, tier

    routes = [x for x in (
        ("Email: " + fm["email"]) if fm.get("email") else "",
        ("Phone/WA: " + fm["phone"]) if fm.get("phone") else "",
        ("IG DM: " + fm["instagram"]) if fm.get("instagram") else "",
        "FB Messenger" if fm.get("fb_messenger") else "") if x]
    fm["reach"] = " | ".join(routes)

    if changed:
        path.write_text("---\n" + V._fm_dump(fm) + "\n---\n\n" + body.strip() + "\n",
                        encoding="utf-8")
    return changed


def main():
    ap = argparse.ArgumentParser(description="Re-run current logic over existing notes")
    ap.add_argument("--deep", action="store_true",
                    help="also re-fetch contacts from each site (slower)")
    a = ap.parse_args()

    notes = []
    for stage in ("sourced", "analyzed", "approved", "sent"):
        notes.extend(V.notes_in(stage))
    if not notes:
        print("No notes to repair.")
        return
    print(f"Repairing {len(notes)} notes" + (" (deep: re-fetching contacts)" if a.deep else "") + "\n")

    touched = 0
    for p in notes:
        changes = repair_note(p, a.deep)
        if changes:
            touched += 1
            print(f"  {p.name}")
            for c in changes:
                print(f"      - {c}")
        if a.deep:
            time.sleep(0.3)

    print(f"\n{touched} of {len(notes)} notes updated.")
    if not touched:
        print("Everything already matches current logic.")


if __name__ == "__main__":
    main()
