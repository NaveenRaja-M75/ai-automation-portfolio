#!/usr/bin/env python3
"""
FOREIGN LEADS, stage 2b — dig deeper where stage 2 came back empty.

Stage 2 read a site's home and contact pages. That misses three things
that turned out to matter:

  1. HIDDEN EMAILS. Sites behind Cloudflare replace every address with an
     encoded blob ("data-cfemail" or /cdn-cgi/l/email-protection#...). The
     scanner saw nothing and the brand went on the Messenger-only sheet
     with a perfectly good inbox sitting on the page. Those are decoded now.

  2. THE LEGAL PAGES. Privacy policies and terms of service carry the real
     inbox (privacy@ is weak, but hello@ and support@ usually appear too)
     and, more usefully, the country: "governed by the laws of England and
     Wales", "ABN", "Pty Ltd". That resolves most of the brands whose
     country was 'Not stated'. A registration in Shenzhen or Hong Kong is
     also the clearest dropshipping tell there is.

  3. BRANDS WITHOUT A PAGE ID. The jewellery research list never had Meta
     page ids, so none of them were checked for live ads. Each is looked up
     in the Ad Library by name and verified when the page name matches.

Writes _state/foreign_deep.json. Resumable, with a hard deadline so one
slow website cannot hold the run.
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
from lib.contact_enricher import (emails_from, instagram_from, phones_from, same_org,
                                  mx_ok, resolve)
from foreign_verify import name_junk

RAW = V.STATE / "foreign_raw.json"
CHK = V.STATE / "foreign_checked.json"
OUT = V.STATE / "foreign_deep.json"
TODAY = datetime.date.today().isoformat()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

DEEP_PATHS = ["/policies/privacy-policy", "/policies/terms-of-service",
              "/policies/refund-policy", "/policies/shipping-policy", "/policies/legal-notice",
              "/pages/about", "/pages/about-us", "/about-us", "/pages/faq", "/pages/faqs",
              "/privacy-policy", "/privacy", "/terms", "/terms-of-service",
              "/terms-and-conditions", "/get-in-touch", "/lets-talk", "/start-a-project",
              "/contact", "/contact-us", "/pages/contact", ""]

CF_ATTR = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
CF_HREF = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)")
AT_DOT = re.compile(r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*([A-Za-z0-9-]+)\s*"
                    r"[\[(]\s*dot\s*[\])]\s*([A-Za-z.]{2,10})", re.I)


def cf_decode(hexs):
    """Cloudflare's email obfuscation: first byte is the XOR key."""
    try:
        b = bytes.fromhex(hexs)
        k = b[0]
        return "".join(chr(c ^ k) for c in b[1:])
    except Exception:
        return ""


def hidden_emails(html):
    out = [cf_decode(h) for h in CF_ATTR.findall(html) + CF_HREF.findall(html)]
    out += [f"{a}@{b}.{c}" for a, b, c in AT_DOT.findall(html)]
    return [e for e in out if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e or "")]


# ---------------------------------------------------------- legal country
US_STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|"
             "Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|"
             "Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|"
             "Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|"
             "North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
             "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|"
             "Wisconsin|Wyoming|United States|USA")
LAW_MAP = [
    # Australia first: "New South Wales" contains "Wales", and read as the UK
    # when England/Wales was tried first.
    (r"New South Wales|Victoria|Queensland|Western Australia|South Australia|Tasmania|"
     r"Australian Capital Territory|Australia", "Australia"),
    (r"(?<!New )England|(?<!South )Wales|Scotland|United Kingdom|Northern Ireland",
     "United Kingdom"),
    (US_STATES, "United States"),
    (r"Ontario|British Columbia|Quebec|Alberta|Manitoba|Nova Scotia|Canada", "Canada"),
    (r"New Zealand", "New Zealand"), (r"Ireland", "Ireland"), (r"Singapore", "Singapore"),
    (r"United Arab Emirates|Dubai|Abu Dhabi", "United Arab Emirates"),
    (r"Hong Kong", "Hong Kong"), (r"People'?s Republic of China|China", "China"),
    (r"Germany", "Germany"), (r"Netherlands", "Netherlands"), (r"Spain", "Spain"),
    (r"Italy", "Italy"), (r"France", "France"), (r"Denmark", "Denmark"),
    (r"Sweden", "Sweden"), (r"Estonia", "Estonia"), (r"Cyprus", "Cyprus"),
    (r"Lithuania", "Lithuania"), (r"Poland", "Poland"), (r"India", "India"),
    (r"Malaysia", "Malaysia"), (r"Qatar", "Qatar"), (r"Saudi Arabia", "Saudi Arabia"),
]
LAW = re.compile(r"(?:laws? of|governed by|jurisdiction of|courts of|registered in)\s+"
                 r"(?:the\s+)?(?:State of |Province of |Commonwealth of )?"
                 r"([A-Z][A-Za-z' ]{2,40})")
MARKERS = [
    (re.compile(r"\bABN[:\s]*\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b|\bPty\.? Ltd\b"), "Australia"),
    (re.compile(r"\bNZBN\b|\bLimited,? New Zealand\b"), "New Zealand"),
    (re.compile(r"Companies House|Company (?:No|Number|Registration)\.?[:\s]+\d{8}\b|"
                r"registered in England"), "United Kingdom"),
    (re.compile(r"\bGmbH\b"), "Germany"),
    (re.compile(r"\bB\.V\.\B|\bB\.V\b"), "Netherlands"),
    (re.compile(r"Shenzhen|Guangzhou|Hangzhou|Yiwu|Dongguan|Guangdong|Fujian|Zhejiang|"
                r"Co\.,? ?Ltd\.? .{0,40}China"), "China"),
    (re.compile(r"Kowloon|Wan Chai|Tsim Sha Tsui|Hong Kong SAR"), "Hong Kong"),
]


def legal_country(text):
    for m in LAW.finditer(text):
        place = m.group(1)
        for pat, name in LAW_MAP:
            if re.search(r"\b(" + pat + r")\b", place):
                return name, "legal terms: governed by " + place.strip()[:40]
    for rx, name in MARKERS:
        m = rx.search(text)
        if m:
            return name, "registration: " + m.group(0)[:40]
    return "", ""


def site_of(r, chk):
    for v in (r.get("website"), chk.get("meta", {}).get("own_site")):
        d = re.sub(r"^https?://", "", str(v or "")).split("/")[0].lower().removeprefix("www.")
        if d and "." in d and not re.search(r"facebook|instagram|google|youtube|linktr", d):
            return d
    return ""


def dig(r, chk, want_email, want_country):
    dom = site_of(r, chk)
    out = {"deep_checked": TODAY}
    if not dom:
        # no site at all: try to find one from the name, strictly verified
        if r["kind"] == "brand" and want_email:
            try:
                site, email, how, phone, ig = resolve(r.get("name", ""))
            except Exception:
                site = email = phone = ig = ""
            if site:
                out.update({"deep_site": site, "deep_email": email, "deep_phone": phone,
                            "deep_ig": ig, "deep_how": "site found from the name"})
        return out
    emails, igs, phones, texts = [], [], [], []
    for p in DEEP_PATHS:
        try:
            resp = requests.get("https://" + dom + p, headers=UA, timeout=(6, 10))
        except Exception:
            continue
        if resp.status_code != 200 or len(resp.text) < 300:
            continue
        html = resp.text
        for e in emails_from(html) + hidden_emails(html):
            if same_org(e, dom) and e.lower() not in [x.lower() for x in emails]:
                emails.append(e)
        ig = instagram_from(html)
        if ig and ig not in igs:
            igs.append(ig)
        for ph in phones_from(html):
            if ph not in phones:
                phones.append(ph)
        if p.startswith(("/policies", "/privacy", "/terms", "/pages/about", "/about")):
            texts.append(re.sub(r"<[^>]+>", " ", html)[:60000])
        if (not want_email or emails) and (not want_country or texts) and len(texts) >= 2:
            break
        time.sleep(0.1)
    if emails:
        # prefer a human-facing inbox over a legal one
        rank = lambda e: (bool(re.match(r"(privacy|legal|dmca|abuse|accounts|billing|noreply)",
                                        e.lower())), len(e))
        emails.sort(key=rank)
        out["deep_email"] = emails[0].lower()
        out["deep_email_all"] = emails[:4]
        out["deep_mx"] = mx_ok(out["deep_email"])
    if igs:
        out["deep_ig"] = igs[0]
    if phones:
        out["deep_phone"] = phones[0]
    if texts:
        c, why = legal_country(" ".join(texts))
        if c:
            out["deep_country"], out["deep_country_why"] = c, why
    return out


def meta_lookup(r):
    """Find a brand's Meta page by name when no page id was ever recorded."""
    name = r.get("name", "")
    want = re.sub(r"[^a-z0-9]", "", name.lower().split("(")[0])
    if len(want) < 4:
        return {}
    for cc in ("GB", "US", "AU", "CA", "AE"):
        try:
            found = S.discover(name.split("(")[0].strip(), cc, pages=1)
        except SystemExit:
            raise
        except Exception:
            found = []
        for a in found:
            pn = re.sub(r"[^a-z0-9]", "", str(a.get("page_name", "")).lower())
            if pn and (pn == want or pn.startswith(want) or want.startswith(pn)) and len(pn) >= 4:
                res = S.verify(a["page_id"], cc)
                if not res:
                    continue
                bodies = [b for x in res["data"] for b in (x.get("ad_creative_bodies") or [])]
                return {"page_id": a["page_id"], "checked_on": TODAY, "check_country": cc,
                        "ads_now": res["ads"], "videos_now": res["videos"],
                        "distinct_now": S.distinct_creatives(bodies),
                        "days_now": S.days_live(res["data"]) if res["data"] else 0,
                        "sample_copy": S.scrub(bodies[0])[:220] if bodies else "",
                        "found_by": "Ad Library search by name"}
        time.sleep(0.3)
    return {"checked_on": TODAY, "not_found_on_meta": True}


def main():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    chk = json.loads(CHK.read_text(encoding="utf-8")) if CHK.exists() else {}
    try:
        done = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        done = {}
    key = lambda r: f"{r['kind']}|{r.get('page_id') or ''}|{r.get('name')}"

    work = []
    for r in raw:
        k = key(r)
        c = chk.get(k, {})
        m = c.get("meta", {})
        s = c.get("site", {})
        if r["kind"] == "brand" and (name_junk(r.get("name")) or m.get("farm")
                                     or m.get("off_product")):
            continue
        if "deep" in done.get(k, {}):
            continue
        has_email = r.get("email") or s.get("email_found")
        want_email = not has_email
        want_country = r["kind"] == "brand"          # agencies state theirs
        if want_email or want_country:
            work.append((k, r, c, bool(want_email), want_country))

    # 1. Meta lookups for brands that never had a page id (sequential)
    meta_todo = [(k, r) for k, r, c, _, _ in work
                 if r["kind"] == "brand" and not r.get("page_id")
                 and "meta_lookup" not in done.get(k, {})]
    print(f"Meta name lookups: {len(meta_todo)}", flush=True)
    for i, (k, r) in enumerate(meta_todo, 1):
        done.setdefault(k, {})["meta_lookup"] = meta_lookup(r)
        if i % 5 == 0:
            OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")
    OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")

    # 2. deep site digs (parallel, hard deadline)
    print(f"Deep site digs: {len(work)}", flush=True)
    ex = cf.ThreadPoolExecutor(max_workers=10)
    futs = {ex.submit(dig, r, c, we, wc): k for k, r, c, we, wc in work}
    n = 0
    try:
        for f in cf.as_completed(futs, timeout=420 + 4 * len(work)):
            n += 1
            try:
                done.setdefault(futs[f], {})["deep"] = f.result()
            except Exception as e:
                done.setdefault(futs[f], {})["deep"] = {"deep_checked": TODAY, "error": str(e)[:80]}
            if n % 25 == 0:
                OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")
                print(f"  {n}/{len(work)}", flush=True)
    except cf.TimeoutError:
        late = [k for f, k in futs.items() if not f.done()]
        for k in late:
            done.setdefault(k, {})["deep"] = {"deep_checked": TODAY, "timeout": True}
        print(f"  {len(late)} timed out, recorded as such", flush=True)
    OUT.write_text(json.dumps(done, indent=1), encoding="utf-8")
    got_e = sum(1 for v in done.values() if v.get("deep", {}).get("deep_email"))
    got_c = sum(1 for v in done.values() if v.get("deep", {}).get("deep_country"))
    got_m = sum(1 for v in done.values() if v.get("meta_lookup", {}).get("page_id"))
    print(f"done: {got_e} new emails, {got_c} countries resolved, {got_m} Meta pages found",
          flush=True)
    ex.shutdown(wait=False, cancel_futures=True)
    os._exit(0)


if __name__ == "__main__":
    main()
