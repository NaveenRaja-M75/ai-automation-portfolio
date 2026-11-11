#!/usr/bin/env python3
"""
More foreign agencies — the markets and cities the first harvest never asked for.

The first agency harvest searched by COUNTRY only, and the directory returns
25 agencies per query with no paging. So a country as big as the UK yielded
the same 25 names seven times over, and the United States, the largest
market for AI ad work, was never searched at all.

This asks city by city and service by service instead, which is how the
directory actually spreads its results, and keeps only agencies that:
  - run paid ads or social for clients (they buy creative, so they buy us)
  - are not themselves a video or film production house (a competitor)
  - have some proof of a real client book (reviews or a stated budget)

Source: Pick an Agency public API, no key, CC-BY. Output feeds stage 1
(foreign_collect.py) through Lead_Generation7/agency_leads_more_intl.xlsx.
"""
import re
import sys
import time
import json
import datetime
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V

API = "https://www.pickanagency.com/api/v1/search"
H = {"User-Agent": "Monarc-LeadFinder/1.0 (+agency research)"}
OUT = Path(r"C:\Users\naveen\Desktop\Lead_Generation7\agency_leads_more_intl.xlsx")
RAW = V.STATE / "foreign_raw.json"

CITIES = {
    "United States": ["New York", "Los Angeles", "Chicago", "Houston", "Dallas", "Austin",
                      "Miami", "Atlanta", "San Francisco", "San Diego", "Seattle", "Denver",
                      "Boston", "Phoenix", "Philadelphia", "Nashville", "Charlotte",
                      "Orlando", "Tampa", "Las Vegas", "Salt Lake City", "Minneapolis",
                      "Portland", "Scottsdale", "Brooklyn"],
    "United Kingdom": ["London", "Manchester", "Birmingham", "Leeds", "Bristol", "Glasgow",
                       "Edinburgh", "Liverpool", "Brighton", "Nottingham"],
    "Canada": ["Toronto", "Vancouver", "Montreal", "Calgary", "Ottawa", "Edmonton"],
    "Australia": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast"],
    "New Zealand": ["Auckland", "Wellington", "Christchurch"],
    "Ireland": ["Dublin", "Cork", "Galway"],
    "Singapore": ["Singapore"],
    "United Arab Emirates": ["Dubai", "Abu Dhabi", "Sharjah"],
}
SERVICES = [None, "Facebook Ads", "Instagram Ads", "Google Ads", "PPC Management",
            "Social Media Ads", "Social Media Marketing", "Video Advertising"]

# They must BUY ad creative: somebody who runs ads for clients.
BUYS_ADS = re.compile(r"facebook ads|instagram ads|google ads|ppc|social media ads|"
                      r"media buying|paid social|performance marketing|youtube ads|"
                      r"display ads|tiktok ads|linkedin ads|social media marketing", re.I)
# A production house sells what Monarc sells. Pitching them is pitching a rival.
PRODUCTION = re.compile(r"\b(films?|cinema|pictures|productions?|animation|motion|"
                        r"videography|video (company|studio|agency)|studios?)\b", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def dom(u):
    u = str(u or "").lower()
    u = re.sub(r"^https?://", "", u)
    return re.sub(r"^www\.", "", u).split("/")[0]


def query(**params):
    params = {k: v for k, v in params.items() if v}
    params["limit"] = 25
    for attempt in range(3):
        try:
            r = requests.get(API, params=params, headers=H, timeout=30)
            if r.status_code == 200:
                return r.json().get("results", []) or []
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return []
        except Exception:
            time.sleep(2)
    return []


def known():
    """Every agency already held anywhere: names and domains."""
    names, doms = set(V.load_seen()), set()
    try:
        for r in json.loads(RAW.read_text(encoding="utf-8")):
            if r["kind"] == "agency":
                names.add(norm(r.get("name")))
                d = dom(r.get("website"))
                if d:
                    doms.add(d)
    except Exception:
        pass
    for st in V.STAGES:
        for p in V.notes_in(st):
            fm, _ = V.read_note(p)
            names.add(norm(fm.get("brand")))
            d = dom(fm.get("website"))
            if d:
                doms.add(d)
    return names, doms


def main():
    names, doms = known()
    found, skipped = {}, {"known": 0, "no_ads": 0, "production": 0, "thin": 0}
    combos = [(c, city, s) for c, cities in CITIES.items() for city in cities for s in SERVICES]
    print(f"{len(combos)} directory queries across {len(CITIES)} countries")
    for i, (country, city, svc) in enumerate(combos, 1):
        for a in query(city=city, service=svc):
            loc = a.get("location") or {}
            if (loc.get("country") or "").lower() != country.lower():
                continue                         # 'Portland' can be Maine or Oregon, fine; not Jamaica
            slug = a.get("slug") or a.get("name")
            if not slug or slug in found:
                continue
            n, d = norm(a.get("name")), dom(a.get("website"))
            if n in names or (d and d in doms):
                skipped["known"] += 1
                continue
            services = ", ".join(a.get("services") or [])
            if not BUYS_ADS.search(services):
                skipped["no_ads"] += 1
                continue
            label = f"{a.get('name', '')} {a.get('tagline', '')}"
            if PRODUCTION.search(label) and not re.search(r"marketing|media|digital|agency|ads",
                                                           a.get("name", ""), re.I):
                skipped["production"] += 1
                continue
            reviews = a.get("total_reviews") or 0
            budget = a.get("min_budget_usd") or 0
            if reviews < 3 and budget < 1000:
                skipped["thin"] += 1
                continue
            found[slug] = {
                "Priority": "", "Market": f"International - {country}",
                "Agency": a.get("name", ""), "City": (loc.get("city") or city).title(),
                "Country": country, "Rating": a.get("rating"), "Reviews": reviews,
                "Team size": a.get("team_size"), "Min budget USD": budget or "",
                "Services": services[:200], "Website": a.get("website", ""),
                "Profile": a.get("profile_url", ""), "Tagline": (a.get("tagline") or "")[:120],
                "Why them": "Runs paid ads for clients; creative is their bottleneck",
                "Outreach Status": "Not contacted", "Notes": "",
                "Found": datetime.date.today().isoformat(),
                "Email": "", "Phone / WhatsApp": "", "Instagram": "", "Instagram URL": "",
                "Contact source": "", "How to reach them": "", "Enriched": "",
            }
            names.add(n)
            if d:
                doms.add(d)
        if i % 40 == 0:
            print(f"  {i}/{len(combos)} queries, {len(found)} new agencies so far", flush=True)
        time.sleep(0.4)

    df = pd.DataFrame(list(found.values()))
    if not df.empty:
        df = df.sort_values(["Country", "Reviews"], ascending=[True, False])
    df.to_excel(OUT, index=False)
    print(f"\n{len(found)} new agencies written to {OUT}")
    print("by country:", df["Country"].value_counts().to_dict() if not df.empty else {})
    print("skipped:", skipped)
    print("Attribution: data from Pick an Agency (pickanagency.com), CC-BY.")


if __name__ == "__main__":
    main()
