#!/usr/bin/env python3
"""
FOREIGN LEADS, stage 2 of 3 — check every lead against today's reality.

The brand scans are three to four weeks old. Ads stop, pages die, and the
early scans ran before the content-farm, off-product and own-website filters
existed. So every brand with a Meta page id is re-verified live, and every
website is fetched to confirm it is up and to fill missing contacts.

Resumable: progress is checkpointed to _state/foreign_checked.json, so an
interrupted run (or a Meta rate limit) picks up where it stopped.
"""
import os
import re
import sys
import json
import time
import datetime
import concurrent.futures as cf
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
import source as S
from enrich import from_known_site
from lib.contact_enricher import mx_ok

RAW = V.STATE / "foreign_raw.json"
OUT = V.STATE / "foreign_checked.json"
TODAY = datetime.date.today().isoformat()

NAME_CODE = {"United Arab Emirates": "AE", "UAE": "AE", "Australia": "AU",
             "United Kingdom": "GB", "Ireland": "IE", "New Zealand": "NZ",
             "Canada": "CA", "United States": "US", "USA": "US"}

# content apps and fiction pages flood Meta in every market
EXTRA_JUNK = re.compile(r"drama|novel|fiction|story|stories|reads\b|webtoon|"
                        r"manga|comic|episode|chapter|romance|billionaire|"
                        r"werewolf|alpha|mafia|ceo'?s|wife|husband", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def name_junk(n):
    n = str(n or "")
    if S.JUNK.search(n) or EXTRA_JUNK.search(n):
        return "content or fiction app, not a product brand"
    if S.NOT_BRAND.search(n):
        return "listing, finance, education or deals page, not a product brand"
    return ""


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def meta_check(rec):
    cc = rec.get("ad_country") or NAME_CODE.get(rec.get("country", ""), "")
    if len(cc) != 2:
        cc = "GB"
    res = S.verify(rec["page_id"], cc)
    if res is None:
        return None
    bodies = [b for a in res["data"] for b in (a.get("ad_creative_bodies") or [])]
    return {
        "checked_on": TODAY, "check_country": cc,
        "ads_now": res["ads"], "videos_now": res["videos"],
        "distinct_now": S.distinct_creatives(bodies),
        "days_now": S.days_live(res["data"]) if res["data"] else 0,
        "own_site": S.own_domain(res["data"], rec.get("name", "")) if res["data"] else "",
        "farm": bool(res["data"]) and S.content_farm(bodies),
        "off_product": bool(res["data"]) and S.off_product(bodies),
        "page_name_now": (res["data"][0].get("page_name") if res["data"] else ""),
        "sample_copy": S.scrub(bodies[0])[:220] if bodies else "",
    }


def site_check(rec):
    """Is the website up, and what contacts does it publish?"""
    site = rec.get("website") or rec.get("own_site") or ""
    dom = re.sub(r"^https?://", "", str(site)).split("/")[0].lower().removeprefix("www.")
    out = {"site_checked": TODAY}
    if not dom or "." not in dom or re.search(r"facebook|instagram|google|youtube|linktr", dom):
        return out
    try:
        r = requests.get("https://" + dom, headers=UA, timeout=12, allow_redirects=True)
        final = re.sub(r"^https?://(www\.)?", "", r.url).split("/")[0].lower()
        out["site_up"] = r.status_code < 400
        out["site_final"] = final
        # a domain that now redirects somewhere unrelated is parked or sold
        stem = dom.split(".")[0]
        out["site_moved"] = bool(final) and stem not in final
    except Exception:
        out["site_up"] = False
    if out.get("site_up") and not out.get("site_moved"):
        need = not (rec.get("email") and rec.get("phone") and rec.get("instagram"))
        if need:
            try:
                e, p, i = from_known_site(dom)
            except Exception:
                e = p = i = ""
            if e and not rec.get("email"):
                out["email_found"] = e
            if p and not rec.get("phone"):
                out["phone_found"] = p
            if i and not rec.get("instagram"):
                out["ig_found"] = i
    email = rec.get("email") or out.get("email_found")
    if email:
        out["email_mx"] = mx_ok(email)
    return out


def main():
    raw = load(RAW, [])
    done = load(OUT, {})
    key = lambda r: f"{r['kind']}|{r.get('page_id') or ''}|{r.get('name')}"

    # ---- 1. Meta, sequential and gentle: the Ad Library rate-limits hard
    todo = [r for r in raw if r["kind"] == "brand" and r.get("page_id")
            and not name_junk(r.get("name")) and "meta" not in done.get(key(r), {})]
    print(f"Meta checks to run: {len(todo)}")
    fails = 0
    for i, r in enumerate(todo, 1):
        m = meta_check(r)
        if m is None:
            fails += 1
            if fails >= 6:
                print("  Meta is refusing calls (likely a rate limit). Progress is "
                      "saved, re-run in an hour to continue.")
                break
            time.sleep(4)
            continue
        fails = 0
        done.setdefault(key(r), {})["meta"] = m
        if i % 20 == 0:
            OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")
            print(f"  {i}/{len(todo)} checked")
        time.sleep(0.35)
    OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")

    # ---- 2. websites, in parallel
    def ready(r):
        m = done.get(key(r), {}).get("meta", {})
        if r["kind"] == "brand":
            if name_junk(r.get("name")) or m.get("farm") or m.get("off_product"):
                return False
        return "site" not in done.get(key(r), {})
    work = []
    for r in raw:
        if not ready(r):
            continue
        rr = dict(r)
        rr["own_site"] = done.get(key(r), {}).get("meta", {}).get("own_site", "")
        work.append((key(r), rr))
    print(f"Website checks to run: {len(work)}")
    # A per-request timeout does not bound a server that drips bytes slowly:
    # one such site held the whole run for ten minutes. So the pass has a hard
    # deadline, anything unfinished is recorded as a timeout, and the process
    # exits without waiting for the stuck threads.
    ex = cf.ThreadPoolExecutor(max_workers=8)
    futs = {ex.submit(site_check, rr): k for k, rr in work}
    n = 0
    try:
        # the deadline scales with the work: 240s held for 25 sites, and a
        # thousand new agencies would all have been recorded as timeouts
        for f in cf.as_completed(futs, timeout=240 + 3 * len(work)):
            n += 1
            try:
                done.setdefault(futs[f], {})["site"] = f.result()
            except Exception as e:
                done.setdefault(futs[f], {})["site"] = {"site_checked": TODAY, "error": str(e)[:80]}
            if n % 25 == 0:
                OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")
                print(f"  {n}/{len(work)} sites", flush=True)
    except cf.TimeoutError:
        for f, k in futs.items():
            if not f.done():
                done.setdefault(k, {})["site"] = {"site_checked": TODAY, "timeout": True}
        print(f"  {sum(1 for f in futs if not f.done())} sites timed out, recorded as such")
    OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")
    print(f"done. {OUT}", flush=True)
    ex.shutdown(wait=False, cancel_futures=True)
    os._exit(0)


if __name__ == "__main__":
    main()
