#!/usr/bin/env python3
"""
Monarc Lead OS — Integration Step 2: Import Enriched Local Leads from leads.db into Vault
=========================================================================================
Reads enriched leads (with emails) from Reverse Engineering/tools/03-lead-engine/data/leads.db,
backs up Monarc_Lead_OS/_state/seen.json before touching anything (Hard Rule 8),
deduplicates against seen.json, DO_NOT_CONTACT.md, and existing vault notes,
and writes Obsidian markdown notes (`lead_type: local`) into vault/02_Analyzed/
with strict `confirmed` vs `unknown` evidence grading (EVIDENCE_RULES.md).

Usage:
  python import_local_leads.py --dry-run
  python import_local_leads.py --limit 25
  python import_local_leads.py --sync-adcheck
"""

import argparse
import datetime
import json
import pathlib
import re
import shutil
import sqlite3
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple


import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import vault as V  # Lead OS's own ledger: normalized keys via V.norm, written via V.remember

DEFAULT_LEADS_DB = pathlib.Path(r"C:\Users\naveen\Desktop\Reverse Engineering\tools\03-lead-engine\data\leads.db")
DEFAULT_LEAD_OS_ROOT = pathlib.Path(r"C:\Users\naveen\Desktop\Monarc_Lead_OS")

FREEMAIL = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com")
JUNK_EMAIL_USERS = {"mymail", "email", "your", "yourname", "name", "user", "example", "test"}
JUNK_EMAIL_DOMAINS = {"mailservice.com", "example.com", "domain.com", "email.com"}


def pick_email(emails_raw: str, website: str) -> str:
    """First plausible address from the scraped list. Minified site scripts produce
    fragments like '6@jj.yad'; form templates produce 'mymail@mailservice.com'."""
    site = normalize_domain(website)
    for cand in [e.strip().lower() for e in (emails_raw or "").split(",") if e.strip()]:
        user, _, dom = cand.partition("@")
        if not dom or user in JUNK_EMAIL_USERS or dom in JUNK_EMAIL_DOMAINS:
            continue
        on_site = bool(site) and (dom == site or site.endswith("." + dom) or dom.endswith("." + site))
        if not on_site and dom not in FREEMAIL and (len(user) < 2 or len(dom.split(".")[0]) < 3):
            continue
        return cand
    return ""


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:60] or "local-lead"


def normalize_domain(url: str) -> str:
    if not url:
        return ""
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    host = (urllib.parse.urlparse(u).hostname or "").lower().removeprefix("www.")
    return host


def backup_seen_ledger(state_dir: pathlib.Path) -> Optional[pathlib.Path]:
    seen_file = state_dir / "seen.json"
    if not seen_file.exists():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = state_dir / f"seen.backup-{stamp}.json"
    shutil.copy2(seen_file, backup_path)
    return backup_path


def load_seen_set(state_dir: pathlib.Path) -> Tuple[Set[str], Any]:
    seen_file = state_dir / "seen.json"
    if not seen_file.exists():
        return set(), []
    raw = json.loads(seen_file.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {str(x).strip().lower() for x in raw if x}, raw
    elif isinstance(raw, dict):
        return {str(k).strip().lower() for k in raw.keys()}, raw
    return set(), []


def save_seen_set(state_dir: pathlib.Path, raw_container: Any, new_names: List[str]) -> None:
    seen_file = state_dir / "seen.json"
    state_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(raw_container, dict):
        now_str = datetime.date.today().isoformat()
        for n in new_names:
            raw_container[n] = now_str
        seen_file.write_text(json.dumps(raw_container, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        combined = list(raw_container) if isinstance(raw_container, list) else []
        existing_low = {str(x).strip().lower() for x in combined}
        for n in new_names:
            if n.lower() not in existing_low:
                combined.append(n)
                existing_low.add(n.lower())
        seen_file.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")


def load_do_not_contact(vault_dir: pathlib.Path) -> Set[str]:
    dnc_file = vault_dir / "00_Control" / "DO_NOT_CONTACT.md"
    if not dnc_file.exists():
        return set()
    blocked = set()
    for line in dnc_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        clean = line.strip().lstrip("-*• ").strip()
        if clean and not clean.startswith("#"):
            blocked.add(clean.lower())
            dom = normalize_domain(clean)
            if dom:
                blocked.add(dom)
    return blocked


def scan_existing_vault_notes(vault_dir: pathlib.Path) -> Tuple[Set[str], Set[str], Dict[str, pathlib.Path]]:
    domains: Set[str] = set()
    emails: Set[str] = set()
    cid_to_path: Dict[str, pathlib.Path] = {}
    stage_dirs = ["01_Sourced", "02_Analyzed", "03_Approved", "04_Sent", "99_Rejected"]
    for st in stage_dirs:
        d = vault_dir / st
        if not d.exists():
            continue
        for md in d.glob("*.md"):
            txt = md.read_text(encoding="utf-8", errors="ignore")
            m_web = re.search(r"^website:\s*[\"']?([^\"'\n]+)[\"']?", txt, re.M)
            if m_web:
                dom = normalize_domain(m_web.group(1))
                if dom:
                    domains.add(dom)
            m_em = re.search(r"^email:\s*[\"']?([^\"'\n]+)[\"']?", txt, re.M)
            if m_em and m_em.group(1).strip():
                emails.add(m_em.group(1).strip().lower())
            m_cid = re.search(r"^maps_cid:\s*[\"']?([^\"'\n]+)[\"']?", txt, re.M)
            if m_cid and m_cid.group(1).strip():
                cid_to_path[m_cid.group(1).strip()] = md
    return domains, emails, cid_to_path


def format_local_lead_note(row: Dict[str, Any], sample_url: str = "") -> str:
    name = (row.get("name") or "Local Business").strip()
    city = (row.get("city") or "").strip()
    niche = (row.get("niche") or row.get("categories") or "local service").strip()
    website = (row.get("website") or "").strip()
    emails_raw = (row.get("emails") or "").strip()
    primary_email = pick_email(emails_raw, website)
    phone = (row.get("phone") or "").strip()
    socials = (row.get("socials") or "").strip()
    ig = ""
    for s in socials.split("|"):
        if "instagram.com/" in s:
            ig = s.strip()
            break
    rating = row.get("rating")
    reviews = int(row.get("reviews") or 0)
    cid = str(row.get("cid") or "")
    platform = (row.get("platform") or "").strip()

    ad_check = (row.get("ad_check_status") or "").strip()
    advertising_val = row.get("advertising")
    adv_name = (row.get("advertiser_name") or "").strip()
    ad_match = (row.get("ad_match_type") or "").strip()
    ad_formats = (row.get("ad_formats") or "").strip()

    if ad_check == "ok" and advertising_val == 1:
        ad_evidence = "confirmed"
        ad_summary = f"Confirmed active on Google Ads Transparency (advertiser: {adv_name or 'listed'}, formats: {ad_formats or 'listed'}, match: {ad_match or 'domain'})."
    elif ad_check == "ok" and advertising_val == 0:
        ad_evidence = "confirmed"
        ad_summary = "Confirmed 0 active ads on Google Ads Transparency for this domain at time of check."
    else:
        ad_evidence = "unknown"
        ad_summary = "Google Ads status is unknown (check pending or rate-limited; do NOT mention their ads in outreach)."

    reviews_fact = (
        f"I saw {name} has {reviews} Google reviews at a {rating} rating in {city}."
        if reviews > 0 and rating
        else f"I came across {name} in {city}."
    )

    today = datetime.date.today().isoformat()
    return f"""---
brand: {json.dumps(name)}
lead_type: local
status: analyzed
market: us
niche: {json.dumps(niche)}
city: {json.dumps(city)}
maps_cid: {json.dumps(cid)}
website: {json.dumps(website)}
platform: {json.dumps(platform)}
email: {json.dumps(primary_email)}
all_emails: {json.dumps(emails_raw)}
phone: {json.dumps(phone)}
instagram: {json.dumps(ig)}
rating: {rating if rating is not None else "null"}
reviews: {reviews}
advertising: {json.dumps("yes" if (ad_check == "ok" and advertising_val == 1) else ("no" if (ad_check == "ok" and advertising_val == 0) else "unknown"))}
advertiser_name: {json.dumps(adv_name)}
ad_match_type: {json.dumps(ad_match)}
ad_formats: {json.dumps(ad_formats)}
sourced_on: {today}
evidence:
  google_maps_listing: confirmed
  reviews_and_rating: {"confirmed" if reviews > 0 and rating else "unknown"}
  google_ads_activity: {ad_evidence}
---

# {name} ({city})

## Confirmed Evidence (Safe to cite under `EVIDENCE_RULES.md`)
- **Google Maps Profile (`confirmed`)**: {name} in {city} — Rating: **{rating or '?'}** across **{reviews}** reviews.
- **Website & Tech (`confirmed`)**: {website} ({platform or 'custom/unknown platform'})
- **Contact Routes (`confirmed`)**: Email `{primary_email}` | Phone `{phone or 'none'}` | IG `{ig or 'none'}`
- **Google Ads Transparency (`{ad_evidence}`)**: {ad_summary}

## Creative gap analysis
- **What they show now**: Local {niche} website at {website}.
- **What is missing**: Short-form vertical video creative built for local homeowner booking intent in {city}.
- **Concept to pitch**: 15-second visual problem-to-solution spot for {name} in {city}.

## Draft messages

### Email
Subject: {name} in {city}

Hi there,

{reviews_fact}

We build short video ads for {niche} businesses that turn local searches into booked jobs. Here is a recent sample from our studio: {sample_url or '{{matched_sample_url}}'}

This month we're taking on a few local businesses for a 3-video ad pack, made around your real services and your city.

Reply INTERESTED and I will make a custom static ad banner for {name} at no cost so you can judge our work first.

Naveen
Monarc AI Studio
"""


def sync_ad_fields(note_path: pathlib.Path, row: Dict[str, Any]) -> None:
    """Rewrite only the ad-status frontmatter and evidence line of an existing note,
    taking them from a freshly generated note so both stay in the same format."""
    fresh = format_local_lead_note(row)
    txt = note_path.read_text(encoding="utf-8")
    for key in ("advertising", "advertiser_name", "ad_match_type", "ad_formats", "  google_ads_activity"):
        m_new = re.search(rf"^{key}:.*$", fresh, re.M)
        if m_new:
            txt = re.sub(rf"^{key}:.*$", lambda _m: m_new.group(0), txt, count=1, flags=re.M)
    m_line = re.search(r"^- \*\*Google Ads Transparency.*$", fresh, re.M)
    if m_line:
        txt = re.sub(r"^- \*\*Google Ads Transparency.*$", lambda _m: m_line.group(0), txt, count=1, flags=re.M)
    note_path.write_text(txt, encoding="utf-8")


def run_import(
    leads_db_path: pathlib.Path,
    lead_os_root: pathlib.Path,
    limit: int = 100,
    dry_run: bool = False,
    sync_adcheck: bool = False,
    sample_url: str = "",
    niche: str = "",
) -> Dict[str, Any]:
    if not leads_db_path.exists():
        raise FileNotFoundError(f"leads.db not found at {leads_db_path}")

    vault_dir = lead_os_root / "vault"
    analyzed_dir = vault_dir / "02_Analyzed"
    state_dir = lead_os_root / "_state"
    analyzed_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    backup_file = None
    if not dry_run:
        backup_file = backup_seen_ledger(state_dir)

    seen_set = V.load_seen()  # already normalized keys
    dnc_set = load_do_not_contact(vault_dir)
    existing_domains, existing_emails, cid_to_path = scan_existing_vault_notes(vault_dir)

    con = sqlite3.connect(f"file:{leads_db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r) for r in con.execute("""
            SELECT * FROM leads
            WHERE emails IS NOT NULL AND trim(emails) != ''
              AND (? = '' OR niche LIKE ?)
            ORDER BY reviews DESC
        """, (niche, f"%{niche}%")).fetchall()
    ]
    con.close()

    imported = []
    synced = []
    skipped = []
    new_seen_names = []

    for row in rows:
        cid = str(row.get("cid") or "")
        name = (row.get("name") or "").strip()
        website = (row.get("website") or "").strip()
        dom = normalize_domain(website)
        primary_email = pick_email(row.get("emails") or "", website)
        if not primary_email and not (sync_adcheck and cid in cid_to_path):
            skipped.append({"name": name, "reason": "no_plausible_email"})
            continue

        if sync_adcheck and cid in cid_to_path:
            note_path = cid_to_path[cid]
            # Only touch notes still awaiting review, and only their ad fields: a full
            # rewrite would erase the founder's edits and #approved tag, and notes in
            # 03_Approved / 04_Sent are history that must not change.
            if row.get("ad_check_status") == "ok" and note_path.parent.name == "02_Analyzed":
                if not dry_run:
                    sync_ad_fields(note_path, row)
                synced.append({"name": name, "note": str(note_path), "advertising": row.get("advertising")})
            continue

        if len(imported) >= limit:
            break

        if name.lower() in dnc_set or (dom and dom in dnc_set):
            skipped.append({"name": name, "reason": "do_not_contact"})
            continue
        if V.norm(name) in seen_set or (dom and V.norm(dom) in seen_set):
            skipped.append({"name": name, "reason": "in_seen_ledger"})
            continue
        if (dom and dom in existing_domains) or (primary_email and primary_email in existing_emails) or (cid and cid in cid_to_path):
            skipped.append({"name": name, "reason": "already_in_vault"})
            continue

        slug = slugify(f"{name}-{row.get('city') or ''}")
        note_path = analyzed_dir / f"{slug}.md"
        counter = 2
        while note_path.exists():
            note_path = analyzed_dir / f"{slug}-{counter}.md"
            counter += 1

        note_text = format_local_lead_note(row, sample_url)
        if not dry_run:
            note_path.write_text(note_text, encoding="utf-8")
            new_seen_names.append(name)
            if dom:
                existing_domains.add(dom)
            if primary_email:
                existing_emails.add(primary_email)
            seen_set.add(V.norm(name))

        imported.append({
            "name": name,
            "city": row.get("city"),
            "email": primary_email,
            "reviews": row.get("reviews"),
            "note_path": str(note_path),
        })

    if not dry_run and new_seen_names:
        V.remember(new_seen_names)

    return {
        "dry_run": dry_run,
        "leads_db_candidates_with_email": len(rows),
        "seen_ledger_backup": str(backup_file) if backup_file else None,
        "imported_count": len(imported),
        "synced_adcheck_count": len(synced),
        "skipped_count": len(skipped),
        "imported_sample": imported[:5],
        "skipped_sample": skipped[:5],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Import enriched local leads from leads.db into Monarc_Lead_OS vault")
    ap.add_argument("--leads-db", type=pathlib.Path, default=DEFAULT_LEADS_DB)
    ap.add_argument("--lead-os-root", type=pathlib.Path, default=DEFAULT_LEAD_OS_ROOT)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample-url", default="", help="single-file Frame.io link to put in every draft (else dispatch blocks the {{placeholder}})")
    ap.add_argument("--niche", default="", help="only import leads whose niche contains this text, e.g. dentist")
    ap.add_argument("--sync-adcheck", action="store_true", help="Update existing local vault notes with completed adcheck results")
    args = ap.parse_args()

    res = run_import(
        leads_db_path=args.leads_db,
        lead_os_root=args.lead_os_root,
        limit=args.limit,
        dry_run=args.dry_run,
        sync_adcheck=args.sync_adcheck,
        sample_url=args.sample_url,
        niche=args.niche,
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
