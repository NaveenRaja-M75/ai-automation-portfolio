#!/usr/bin/env python3
"""
Stage 2 — ENRICH. Put a real contact route on every sourced lead.

Reuses the extractors that were debugged against live data in the previous
system. Each guard below exists because it caught a specific failure:

  domain match      brand-site.example published info@theme-agency.example. Theme-built
                    stores ship with the original shop's address still in the
                    footer, so an email on a DIFFERENT corporate domain is a
                    template leak, not their address. Free providers (gmail
                    and friends) are allowed, because small brands really use
                    them.
  redirect guard    a parked domain 301s somewhere unrelated and still returns
                    200. The final host must still match what we asked for.
  phone parsing     Indian pages are full of number-shaped things that are not
                    phones: pincodes are 6 digits, GST numbers 15 characters,
                    prices anything. Only an explicit country code or a
                    10-digit number starting 6-9 is accepted.
  MX check          a syntactically perfect address on a dead domain still
                    bounces, and bounces burn the sending domain.

For a Meta advertiser there is always a Facebook Messenger route, because
running Meta ads guarantees a page. That is the fallback when a brand
publishes no email and no phone, and it is often better than email anyway.
"""
import re
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import vault as V
from lib.contact_enricher import (resolve, mx_ok, emails_from, phones_from,
                                  instagram_from, same_org, H)
import requests

PATHS = ["", "/contact", "/contact-us", "/pages/contact", "/pages/contact-us",
         "/about", "/policies/refund-policy"]

FREE_MAIL = ("gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "outlook.com",
             "hotmail.com", "rediffmail.com", "protonmail.com", "icloud.com")


def email_quality(email, brand):
    """Grade a contact address. Blanket-rejecting free mail is the wrong call.

    Plenty of real Indian businesses run on Gmail: somebrand.1920@gmail.com
    belongs to a genuine jewellery brand. Rejecting every free provider would
    throw those away. What actually separates a business contact from a
    personal one is whether the local part relates to the BRAND or to a
    PERSON. firstname.lastname@gmail.com on a page called "a wellness brand" is
    somebody's private address; somebrand@gmail.com is a business inbox
    that happens to sit on Gmail.
    """
    if not email or "@" not in email:
        return "none"
    local, _, domain = email.lower().partition("@")
    if domain not in FREE_MAIL:
        return "business"                       # own domain: strongest
    brand_tokens = [t for t in re.split(r"\W+", str(brand).lower()) if len(t) > 3]
    if any(t in local for t in brand_tokens):
        return "business-freemail"              # brand-named Gmail: acceptable
    generic = ("info", "contact", "hello", "sales", "support", "care", "orders",
               "enquiry", "team", "admin", "help")
    if any(local.startswith(g) for g in generic):
        return "business-freemail"
    return "personal"                           # someone's private inbox: weak


def from_known_site(site):
    """When the site is already known, skip domain guessing entirely."""
    import re
    dom = re.sub(r"^https?://", "", str(site or "")).split("/")[0]
    dom = re.sub(r"^www\.", "", dom).lower()
    if not dom or dom == "nan":
        return "", "", ""
    email = phone = insta = ""
    for p in PATHS:
        try:
            r = requests.get("https://" + dom + p, headers=H, timeout=12)
        except Exception:
            continue
        if r.status_code != 200 or len(r.text) < 300:
            continue
        if not email:
            c = [e for e in emails_from(r.text) if same_org(e, dom)]
            if c:
                email = c[0]
        if not phone:
            ph = phones_from(r.text)
            if ph:
                phone = ph[0]
        if not insta:
            insta = instagram_from(r.text)
        if email and phone and insta:
            break
        time.sleep(0.15)
    return email, phone, insta


def run(limit=0):
    notes = V.notes_in("sourced")
    if limit:
        notes = notes[:limit]
    if not notes:
        print("Nothing in 01_Sourced. Run `python run.py source` first.")
        return
    print(f"Enriching {len(notes)} leads\n")

    stats = {"email": 0, "phone": 0, "insta": 0, "reach": 0}
    for i, path in enumerate(notes, 1):
        fm, body = V.read_note(path)
        brand = fm.get("brand", path.stem)
        site = fm.get("website", "")

        if site:
            email, phone, insta = from_known_site(site)
            how = "known site"
        else:
            site, email, how, phone, insta = resolve(brand)
        if email and mx_ok(email) != "Verified":
            email = ""                     # never ship an address that bounces

        fm["email"] = email
        fm["phone"] = phone
        fm["instagram"] = insta
        fm["website"] = site
        fm["contact_found"] = how
        fm["email_quality"] = email_quality(email, brand)

        # Lead quality, stated openly rather than silently dropped. A weak
        # lead is still worth seeing; it just should not be the first call.
        score = 0
        if fm["email_quality"] == "business":        score += 40
        elif fm["email_quality"] == "business-freemail": score += 25
        elif fm["email_quality"] == "personal":      score += 5
        if phone:  score += 20
        if insta:  score += 15
        if site:   score += 15
        try:
            if int(fm.get("active_ads") or 0) >= 5:  score += 10
        except Exception:
            pass
        fm["quality"] = score
        fm["tier"] = ("A - strong" if score >= 70 else
                      "B - workable" if score >= 45 else
                      "C - weak, low priority")
        routes = [x for x in (
            ("Email: " + email) if email else "",
            ("Phone/WA: " + phone) if phone else "",
            ("IG DM: " + insta) if insta else "",
            "FB Messenger" if fm.get("fb_messenger") else "") if x]
        fm["reach"] = " | ".join(routes)

        if email:
            stats["email"] += 1
        if phone:
            stats["phone"] += 1
        if insta:
            stats["insta"] += 1
        if routes:
            stats["reach"] += 1

        V.move_note(path, "analyzed", fm)
        tag = {"A": "A  ", "B": "B  ", "C": "c  "}[fm["tier"][0]]
        print(f"{tag}{i:>3}/{len(notes)} {brand[:30]:<32} "
              f"{(email or phone or insta or 'messenger only')[:34]:<36}"
              f"{fm['email_quality']}")
        time.sleep(0.2)

    print(f"\nemail {stats['email']} | phone {stats['phone']} | "
          f"instagram {stats['insta']}")
    print(f"reachable by some channel: {stats['reach']}/{len(notes)}")
    print("Leads moved to vault/02_Analyzed/ — review them in Obsidian.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    run(ap.parse_args().limit)
