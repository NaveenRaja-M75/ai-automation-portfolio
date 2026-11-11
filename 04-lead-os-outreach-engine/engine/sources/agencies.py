#!/usr/bin/env python3
"""
Agency Lead Finder — white-label clients for Monarc Studio
==========================================================
Pulls verified marketing agencies from Pick an Agency's free REST API
(47,000+ agencies, 90+ countries, no key, no signup) and keeps only the
ones whose own service list says they run PAID ADS or VIDEO for clients.

WHY AGENCIES ARE THE BEST CLIENT A STUDIO CAN HAVE
    An agency running Google/Meta ads for twenty clients needs fresh
    creative for all twenty, every month, forever. They are judged on ad
    performance but staffed for media buying, so creative is their
    permanent bottleneck. One agency deal therefore produces recurring
    volume across their whole book — you are not selling twenty brands
    one at a time.

    They also already understand the product. You never have to explain
    what ad creative is worth; they bill their clients for it.

WHY THIS SOURCE IS SAFE
    Public REST API, no key, no login, CC-BY licensed with a link-back.
    Nothing here touches anyone's account or credentials.

DEDUPLICATION
    Any agency already present in previously produced sheets is skipped.

USAGE
    python agency_leads.py                     # India + Chennai + international
    python agency_leads.py --region india
    python agency_leads.py --region international
"""
import re
import json
import time
import argparse
import datetime
import requests
import pandas as pd

API = "https://www.pickanagency.com/api/v1/search"
H = {"User-Agent": "Monarc-LeadFinder/1.0 (+agency research)"}

# Services that mean "this agency buys ad placements and needs creative".
AD_SERVICES = ["Facebook Ads", "Instagram Ads", "Google Ads", "Video Advertising",
               "PPC Management", "Social Media Ads", "YouTube Ads", "Display Ads",
               "Creative Services", "Video Production", "Social Media Marketing"]

INDIA_CITIES = ["Chennai", "Coimbatore", "Madurai", "Bangalore", "Mumbai",
                "Delhi", "Hyderabad", "Pune", "Kochi", "Ahmedabad", "Jaipur"]
INTL = ["United Arab Emirates", "Australia", "United Kingdom", "Singapore",
        "Canada", "New Zealand", "Ireland", "Netherlands"]

# An agency worth pitching must actually run ads or make video for clients.
NEEDS_CREATIVE = re.compile(
    r"(facebook ads|instagram ads|google ads|video advertis|ppc|social media ads|"
    r"youtube ads|display ads|creative services|video production|paid social|"
    r"performance marketing|social media marketing|content marketing)", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def query(**params):
    params.setdefault("limit", 25)
    try:
        r = requests.get(API, params=params, headers=H, timeout=30)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    try:
        return r.json().get("results", []) or []
    except Exception:
        return []


def harvest(region, seen, delay=0.4):
    found, dup = {}, 0
    combos = []
    if region in ("india", "all"):
        for c in INDIA_CITIES:
            combos.append(({"city": c}, "India - " + c))
            for s in AD_SERVICES[:6]:
                combos.append(({"city": c, "service": s}, "India - " + c))
    if region in ("international", "all"):
        for c in INTL:
            combos.append(({"country": c}, "International - " + c))
            for s in AD_SERVICES[:6]:
                combos.append(({"country": c, "service": s}, "International - " + c))

    for params, market in combos:
        for a in query(**params):
            slug = a.get("slug") or a.get("name")
            if not slug:
                continue
            key = norm(a.get("name"))
            if key in seen:
                dup += 1
                continue
            if slug in found:
                continue
            svc = ", ".join(a.get("services") or [])
            if not NEEDS_CREATIVE.search(svc):
                continue                     # doesn't buy ads: not our client
            loc = a.get("location") or {}
            found[slug] = {
                "Market": market,
                "Agency": a.get("name", ""),
                "Tagline": (a.get("tagline") or "")[:110],
                "City": (loc.get("city") or "").title(),
                "Country": (loc.get("country") or "").title(),
                "Rating": a.get("rating"),
                "Reviews": a.get("total_reviews"),
                "Team size": a.get("team_size"),
                "Founded": a.get("founded_year"),
                "Min budget USD": a.get("min_budget_usd"),
                "Services": svc[:190],
                "Website": a.get("website", ""),
                "Profile": a.get("profile_url", ""),
            }
        time.sleep(delay)
        if len(found) % 50 == 0 and found:
            print("    ...%d agencies collected" % len(found))
    print("  %s: %d agencies kept (%d already known)" % (region, len(found), dup))
    return list(found.values())


def score(r):
    """Bigger review counts and real budgets mean an agency with live clients
    - and live clients are what generate the recurring creative work."""
    s = 0
    rev = r.get("Reviews") or 0
    rat = r.get("Rating") or 0
    try:
        mb = int(r.get("Min budget USD") or 0)
    except Exception:
        mb = 0
    s += 40 if rev >= 50 else 30 if rev >= 20 else 18 if rev >= 5 else 5
    s += 20 if rat and float(rat) >= 4.5 else 12 if rat and float(rat) >= 4 else 0
    s += 20 if mb >= 5000 else 12 if mb >= 1000 else 0
    if re.search(r"video", r.get("Services", ""), re.I):
        s += 15                                   # already sells video: warm
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="all", choices=["all", "india", "international"])
    ap.add_argument("--seen", default="_seen.json")
    ap.add_argument("--out", default="agency_leads.xlsx")
    a = ap.parse_args()

    try:
        seen = set(json.load(open(a.seen)))
    except Exception:
        seen = set()
    print("Excluding %d already-delivered names" % len(seen))
    print("Source: Pick an Agency free API (no key, no login)\n")

    rows = harvest(a.region, seen)
    if not rows:
        print("Nothing found.")
        return
    for r in rows:
        r["Score"] = score(r)
        r["Priority"] = ("A - big book" if r["Score"] >= 70 else
                         "B - worth pitching" if r["Score"] >= 45 else "C - small")
        r["Why them"] = "Runs paid ads for clients; creative is their bottleneck"
        r["Outreach Status"] = "Not contacted"
        r["Notes"] = ""
        r["Found"] = datetime.date.today().isoformat()

    df = pd.DataFrame(rows).sort_values(["Priority", "Score"], ascending=[True, False])
    cols = ["Priority", "Market", "Agency", "City", "Country", "Rating", "Reviews",
            "Team size", "Min budget USD", "Services", "Website", "Profile",
            "Tagline", "Why them", "Outreach Status", "Notes", "Found"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_excel(a.out, index=False, sheet_name="Agency leads")

    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = load_workbook(a.out)
    ws = wb.active
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F3864", end_color="1F3864")
        c.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    for col, w in {"A": 18, "B": 22, "C": 34, "D": 16, "E": 16, "F": 9, "G": 10,
                   "H": 12, "I": 14, "J": 46, "K": 30, "L": 16, "M": 40,
                   "N": 42, "O": 15, "P": 22, "Q": 12}.items():
        ws.column_dimensions[col].width = w
    fills = {"A": "C6EFCE", "B": "FFEB9C", "C": "EEEEEE"}
    for row in ws.iter_rows(min_row=2, min_col=1, max_col=1):
        row[0].fill = PatternFill("solid",
                                  start_color=fills.get(str(row[0].value)[0], "FFFFFF"),
                                  end_color=fills.get(str(row[0].value)[0], "FFFFFF"))
    # Keep the RAW url as the cell value. Replacing it with display text
    # destroys the data for any downstream script that reads the column -
    # which is exactly what happened the first time this ran.
    hdr = [c.value for c in ws[1]]
    for lbl in ("Website", "Profile"):
        ci = hdr.index(lbl) + 1
        for row in ws.iter_rows(min_row=2, min_col=ci, max_col=ci):
            c = row[0]
            if c.value and str(c.value).startswith("http"):
                c.hyperlink = c.value          # clickable, value preserved
                c.font = Font(color="0563C1", underline="single")
    wb.save(a.out)

    print("\n%d agencies that run paid ads for clients" % len(df))
    print(df["Priority"].value_counts().to_string())
    print(df["Market"].value_counts().head(12).to_string())
    print("\nSaved -> %s" % a.out)
    print("Attribution: data from Pick an Agency (pickanagency.com), CC-BY - keep a link back.")


if __name__ == "__main__":
    main()
