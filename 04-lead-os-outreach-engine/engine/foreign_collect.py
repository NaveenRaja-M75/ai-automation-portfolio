#!/usr/bin/env python3
"""
FOREIGN LEADS, stage 1 of 3 — collect every foreign lead ever gathered.

Reads every spreadsheet any session produced (Lead_Generation7, Pricing, the
vault), keeps the foreign rows, and merges duplicates into one record per
business. Writes _state/foreign_raw.json for stage 2 (foreign_verify.py).

TWO RULES THAT SHAPE EVERYTHING HERE
    1. A link is never copied from an old sheet. The "See their ad" links in
       the older ad-gap files are Meta snapshot URLs with the access token in
       the query string. Only the PAGE ID is taken from a link, and every Ad
       Library link in the output is rebuilt token-free from it.

    2. "Foreign" means where the business IS, not where its ads run. Nalli,
       a Chennai silk brand, sat in the Qatar sheet because it advertises to
       the diaspora there. Stage 3 files those separately rather than calling
       a Chennai company an international lead.
"""
import re
import sys
import json
import datetime
import unicodedata
import urllib.parse
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V

DESK = Path(r"C:\Users\naveen\Desktop")
LG = DESK / "Lead_Generation7"
OUT = V.STATE / "foreign_raw.json"

COUNTRY_NAME = {"AE": "United Arab Emirates", "AU": "Australia", "GB": "United Kingdom",
                "UK": "United Kingdom", "IE": "Ireland", "QA": "Qatar", "KW": "Kuwait",
                "BH": "Bahrain", "OM": "Oman", "MY": "Malaysia", "NZ": "New Zealand",
                "SG": "Singapore", "CA": "Canada", "US": "United States", "NL": "Netherlands",
                "SA": "Saudi Arabia", "DK": "Denmark", "IN": "India"}
NAME_CODE = {v: k for k, v in COUNTRY_NAME.items() if k != "UK"}
NAME_CODE.update({"UAE": "AE", "USA": "US", "UK": "GB"})


# ------------------------------------------------------------------ helpers
def clean(v):
    # an empty YAML field in a vault note comes back as {} or [], and str()
    # of that put the literal text "{}" into a greeting: "clients in {}"
    if isinstance(v, (dict, list, tuple, set)) and not v:
        return ""
    s = unicodedata.normalize("NFKC", str(v if v is not None else "")).strip()
    return "" if s.lower() in ("nan", "none", "-", "n/a", "{}", "[]") else s


def norm(s):
    return re.sub(r"[^a-z0-9]", "", clean(s).lower())


def domain(v):
    s = clean(v).lower()
    if not s or " " in s.strip():
        return ""
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s).split("/")[0].split("?")[0]
    return s if "." in s else ""


def email_clean(v):
    s = urllib.parse.unquote(clean(v)).strip().strip(".,;:<>").lower()
    s = s.replace("mailto:", "")
    return s if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", s) else ""


def phone_clean(v):
    s = clean(v)
    if not s:
        return ""
    if re.fullmatch(r"\d+(\.\d+)?([eE]\+?\d+)?", s):      # 9.17E+11 from Excel
        try:
            s = str(int(float(s)))
        except Exception:
            return ""
    d = re.sub(r"[^\d+]", "", s)
    if d.startswith("00"):
        d = "+" + d[2:]
    if not d.startswith("+"):
        d = "+" + d
    return d if 9 <= len(d.lstrip("+")) <= 15 else ""


def ig_clean(v):
    s = clean(v)
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", s)
    h = (m.group(1) if m else s).strip().lstrip("@").strip("/").lower()
    if not re.fullmatch(r"[a-z0-9._]{2,30}", h) or h in (
            "shopify", "instagram", "p", "reel", "explore", "accounts", "open", "openig"):
        return ""
    return "@" + h


def page_id_from(url):
    u = clean(url)
    m = re.search(r"view_all_page_id=(\d+)", u) or re.search(r"m\.me/(\d{6,})", u)
    return m.group(1) if m else ""


def country_from(url):
    m = re.search(r"[?&]country=([A-Z]{2})", clean(url))
    return m.group(1) if m else ""


def read(path, sheet=None):
    """Rows as dicts, with every hyperlink target alongside its text."""
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    hdr = [clean(c.value) for c in ws[1]]
    rows = []
    for r in ws.iter_rows(min_row=2):
        d = {}
        for i, c in enumerate(r):
            if i >= len(hdr) or not hdr[i]:
                continue
            d[hdr[i]] = clean(c.value)
            if c.hyperlink and c.hyperlink.target:
                d[hdr[i] + " @link"] = c.hyperlink.target
        if any(v for k, v in d.items() if not k.endswith("@link")):
            rows.append(d)
    return rows


def ad_page_id(row):
    """The advertiser's page id, from whichever column carries it.

    Snapshot links ("See their ad") are skipped on purpose: their id is an AD
    id, not a page id, and the URL carries the access token.
    """
    for k in ("page_id",):
        if re.fullmatch(r"\d{6,}", row.get(k, "")):
            return row[k]
    for k, v in row.items():
        if k.endswith("@link") and "access_token" not in v:
            pid = page_id_from(v)
            if pid:
                return pid
    return ""


def ad_country(row, default):
    for k, v in row.items():
        if k.endswith("@link") and "view_all_page_id" in v:
            c = country_from(v)
            if c:
                return c
    return default


# ------------------------------------------------------------------ records
RECORDS = []


def add(kind, **f):
    f["kind"] = kind
    f.setdefault("sources", [])
    for k in ("email", "phone", "instagram", "website"):
        f.setdefault(k, "")
    RECORDS.append(f)


def src(file, sheet=""):
    return f"{file}" + (f" [{sheet}]" if sheet else "")


# ---- agencies -------------------------------------------------------------
def agencies():
    # agency_leads_more_intl.xlsx comes from foreign_agencies_more.py: the US,
    # which the first harvest never searched, and more cities everywhere else.
    for fname in ("agency_leads_contacts.xlsx", "gulf_agencies_contacts.xlsx",
                  "agency_leads_more_intl.xlsx"):
        if not (LG / fname).exists():
            continue
        for r in read(LG / fname):
            if not r.get("Market", "").startswith("International"):
                continue
            add("agency",
                name=r.get("Agency"), country=r.get("Country"), city=r.get("City"),
                rating=r.get("Rating"), reviews=r.get("Reviews"), team=r.get("Team size"),
                min_budget=r.get("Min budget USD"), services=r.get("Services"),
                tagline=r.get("Tagline"), priority_src=r.get("Priority"),
                website=r.get("Website @link") or r.get("Website"),
                profile=r.get("Profile @link") or r.get("Profile"),
                email=email_clean(r.get("Email")), phone=phone_clean(r.get("Phone / WhatsApp")),
                instagram=ig_clean(r.get("Instagram")), found=r.get("Found"),
                status_src=r.get("Outreach Status"), sources=[src(fname)])
    # the vault holds cleaner copies of the same agencies: hand-set greeting
    # names, fixed emails, and rejections. Those win.
    for stage in ("analyzed", "approved", "sent", "rejected"):
        for p in V.notes_in(stage):
            fm, _ = V.read_note(p)
            if fm.get("lead_type") != "agency" or not str(fm.get("market", "")).startswith("International"):
                continue
            add("agency", name=fm.get("brand"), country=fm.get("country"), city=fm.get("city"),
                email=email_clean(fm.get("email")), phone=phone_clean(fm.get("phone")),
                instagram=ig_clean(fm.get("instagram")), website=clean(fm.get("website")),
                greeting=clean(fm.get("greeting_name")) if isinstance(fm.get("greeting_name"), str) else "",
                vault_stage=stage, vault_reason=clean(fm.get("reason")),
                sources=[f"Monarc_Lead_OS vault ({stage})"])


# ---- brands with Meta evidence --------------------------------------------
def brands_meta():
    raw = [("ad_gap_UAE.xlsx", None, "AE"), ("monarc_au_raw.xlsx", None, "AU")]
    for fname, sheet, cc in raw:
        for r in read(LG / fname, sheet):
            add("brand", name=r.get("Brand"), page_id=ad_page_id(r),
                ad_country=ad_country(r, cc), niche=r.get("Niche(s)"),
                ads_then=r.get("Active ads seen"), days_then=r.get("Longest running (days)"),
                video_then=r.get("Runs video?"), found=r.get("Found"),
                sources=[src(fname)])

    for fname in ("uae_leads_contacts_v2.xlsx",):
        for r in read(LG / fname):
            add("brand", name=r.get("Brand"), page_id=ad_page_id(r), ad_country="AE",
                niche=r.get("Niche(s)"), email=email_clean(r.get("Email")),
                phone=phone_clean(r.get("Phone / WhatsApp")), instagram=ig_clean(r.get("Instagram")),
                website=r.get("Website (verified)"), sources=[src(fname)])
    for r in read(LG / "uae_leads_with_contacts.xlsx"):
        add("brand", name=r.get("Brand"), ad_country="AE", email=email_clean(r.get("Email")),
            phone=phone_clean(r.get("Phone / WhatsApp")), website=r.get("Website (verified)"),
            sources=[src("uae_leads_with_contacts.xlsx")])

    for r in read(LG / "monarc_fresh_leads.xlsx"):
        if "International" not in r.get("Market", ""):
            continue
        add("brand", name=r.get("Brand"), page_id=ad_page_id(r), ad_country=ad_country(r, "AU"),
            why_src=r.get("Why them"), email=email_clean(r.get("Email")),
            phone=phone_clean(r.get("Phone / WhatsApp")), instagram=ig_clean(r.get("Instagram")),
            website=r.get("Website (verified)"), sources=[src("monarc_fresh_leads.xlsx")])

    v2 = [("monarc_leads_international_v2.xlsx", s, c) for s, c in
          (("UAE", "AE"), ("United Kingdom", "GB"), ("Ireland", "IE"))]
    v2 += [("monarc_leads_intl_v4.xlsx", "United Kingdom", "GB"),
           ("monarc_video_gap_leads.xlsx", "Ireland", "IE")]
    for fname, sheet, cc in v2:
        for r in read(LG / fname, sheet):
            add("brand", name=r.get("Brand"), page_id=ad_page_id(r), ad_country=ad_country(r, cc),
                why_src=r.get("Why they need AI video"), email=email_clean(r.get("Email")),
                phone=phone_clean(r.get("Phone / WhatsApp")), instagram=ig_clean(r.get("Instagram")),
                website=r.get("Website"), origin=r.get("Origin"), shopify=r.get("Shopify store"),
                products_video=r.get("Products with video"), sources=[src(fname, sheet)])

    sweet = {"Qatar (low competition)": "QA", "Kuwait (low competition)": "KW",
             "Bahrain (low competition)": "BH", "Oman (low competition)": "OM",
             "Ireland (low competition)": "IE", "Malaysia (low competition)": "MY",
             "New Zealand (low competition)": "NZ"}
    for sheet, cc in sweet.items():
        for r in read(LG / "monarc_sweetspot.xlsx", sheet):
            add("brand", name=r.get("Brand"), page_id=ad_page_id(r), ad_country=ad_country(r, cc),
                why_src=r.get("Why them"), niche=r.get("Niche signal"),
                sources=[src("monarc_sweetspot.xlsx", sheet)])
        for r in read(LG / "monarc_sweetspot_contacts.xlsx", sheet):
            add("brand", name=r.get("Brand"), page_id=ad_page_id(r), ad_country=cc,
                why_src=r.get("Why them"), email=email_clean(r.get("Email")),
                phone=phone_clean(r.get("Phone / WhatsApp")), instagram=ig_clean(r.get("Instagram")),
                website=r.get("Website (verified)"), sources=[src("monarc_sweetspot_contacts.xlsx", sheet)])

    for stage in ("sourced", "analyzed", "approved", "sent", "rejected"):
        for p in V.notes_in(stage):
            fm, _ = V.read_note(p)
            if fm.get("lead_type") == "agency":
                continue
            cc = clean(fm.get("country"))
            site = clean(fm.get("website"))
            if cc in ("IN", "India", "") and not clean(fm.get("site_country")):
                continue
            if clean(fm.get("site_country")) == "India":
                continue
            add("brand", name=fm.get("brand"), page_id=clean(fm.get("page_id")).strip('"'),
                ad_country=cc if len(cc) == 2 else "", email=email_clean(fm.get("email")),
                phone=phone_clean(fm.get("phone")), instagram=ig_clean(fm.get("instagram")),
                website=site, niche=clean(fm.get("niche")),
                greeting=clean(fm.get("greeting_name")) if isinstance(fm.get("greeting_name"), str) else "",
                vault_stage=stage, vault_reason=clean(fm.get("reason")),
                site_country=clean(fm.get("site_country")),
                sources=[f"Monarc_Lead_OS vault ({stage})"])


# ---- brands without Meta evidence -----------------------------------------
def brands_other():
    for r in read(LG / "ai_ads_clients.xlsx", "International"):
        loc = r.get("Location")
        add("brand", name=r.get("Brand"), country=loc, niche=r.get("Niche"),
            email=email_clean(r.get("Contact")), instagram=ig_clean(r.get("Contact")),
            website=r.get("Website"), followers=r.get("Followers"),
            sources=[src("ai_ads_clients.xlsx", "International")])

    for sheet in ("Abroad (14)", "Abroad - later (7)"):
        for r in read(DESK / "Pricing" / "jewellery-leads.xlsx", sheet):
            add("brand", name=r.get("Company"), city=r.get("City / Area"),
                niche="Jewellery - " + r.get("Type", ""), sells=r.get("What they sell"),
                contact_name=r.get("Contact name"), contact_role=r.get("Role"),
                email=email_clean(r.get("Email")),
                phone=phone_clean(r.get("WhatsApp") or r.get("Phone")),
                instagram=ig_clean(r.get("Instagram")), website=r.get("Website"),
                notes_src=r.get("Notes"), ad_status_src=r.get("Ad Library status"),
                priority_src=r.get("Priority"), later=("later" in sheet),
                sources=[src("Pricing/jewellery-leads.xlsx", sheet)])

    # the one foreign row in the very first run: a London AI film studio.
    for r in read(LG / "leads.xlsx"):
        if clean(r.get("Name")) and clean(r.get("Country")) not in ("IN", ""):
            add("brand", name=r.get("Name"), city="London", country="United Kingdom",
                phone=phone_clean(r.get("Phone")), website=r.get("Website"),
                niche=r.get("Niche"), competitor=True, sources=[src("leads.xlsx")])


# ------------------------------------------------------------------- merge
def keys(r):
    k = []
    if r.get("page_id"):
        k.append("pid:" + r["page_id"])
    d = domain(r.get("website"))
    if d and not re.search(r"facebook|instagram|google|linktr|shopify\.com$|wa\.me", d):
        k.append("dom:" + re.sub(r"\.(com|co|net|org|io|shop|store)(\.[a-z]{2})?$|\.[a-z]{2,3}$", "", d))
    if r.get("email") and not re.search(r"@(gmail|yahoo|hotmail|outlook)\.", r["email"]):
        k.append("em:" + r["email"].split("@")[1])
    n = norm(r.get("name"))
    if len(n) >= 4:
        k.append("nm:" + n)
    return k


def merge():
    groups, index = [], {}
    for r in RECORDS:
        ks = [r["kind"] + "|" + k for k in keys(r)]
        hit = next((index[k] for k in ks if k in index), None)
        if hit is None:
            groups.append([r])
            hit = len(groups) - 1
        else:
            groups[hit].append(r)
        for k in ks:
            index.setdefault(k, hit)
    out = []
    for g in groups:
        m = {"sources": []}
        for r in g:
            for k, v in r.items():
                if k == "sources":
                    m["sources"] += [s for s in v if s not in m["sources"]]
                elif v not in ("", None, [], False) and not m.get(k):
                    m[k] = v
                elif k in ("vault_stage", "vault_reason", "greeting") and v:
                    m[k] = v                    # the vault's word is final
        m["merged_from"] = len(g)
        out.append(m)
    return out


def main():
    agencies()
    brands_meta()
    brands_other()
    merged = merge()
    V.STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    ag = [m for m in merged if m["kind"] == "agency"]
    br = [m for m in merged if m["kind"] == "brand"]
    print(f"{len(RECORDS)} rows read -> {len(merged)} unique "
          f"({len(ag)} agencies, {len(br)} brands)")
    print(f"brands with a Meta page id: {sum(1 for b in br if b.get('page_id'))}")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
