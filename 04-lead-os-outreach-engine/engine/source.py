#!/usr/bin/env python3
"""
Stage 1 — SOURCE. Find brands that are paying for ads and have no video.

The thesis in one line: money already moving + no video = the exact gap Monarc
fills. You are not creating a budget, only redirecting one that exists, which
is the easiest sale available.

TWO-STAGE VERIFICATION, AND WHY IT IS NOT OPTIONAL
    Stage 1  broad search per niche -> candidate advertisers
    Stage 2  ONE dedicated call per advertiser, scoped to its page_id,
             asking for its video ads specifically

    The tempting shortcut is to run one broad ALL search and one broad VIDEO
    search and subtract. That is unsound: broad searches cap around 200
    results, so the VIDEO set arrives truncated and brands whose video ads
    fell outside the sample look like they have none. Measured against real
    data it over-reported by about 3x. Stage 2 costs one extra call per
    candidate and turns a guess into a fact. See 00_Control/EVIDENCE_RULES.md.

Writes one markdown note per qualified lead into vault/01_Sourced/.
"""
import os
import re
import sys
import time
import argparse
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V

load_dotenv(V.ROOT / ".env", override=True)
TOKEN = (os.getenv("META_ACCESS_TOKEN") or "").strip()
API = "https://graph.facebook.com/v19.0/ads_archive"

FIELDS = ("page_name,page_id,ad_creation_time,ad_delivery_start_time,"
          "ad_snapshot_url,publisher_platforms,ad_creative_bodies,"
          "ad_creative_link_captions")

# Content apps flood Meta with static ads and look like flawless no-video
# leads, but they have no product to film and never buy creative.
JUNK = re.compile(r"drama|novel|stories|romantic|addicted to reading|\bfancy\b|"
                  r"reading|astrolog|lottery|casino|betting|\bloan\b|crypto", re.I)
NOT_BRAND = re.compile(r"99acres|magicbricks|skyscanner|\bolx\b|indiamart|justdial|"
                       r"quikr|naukri|govt|government|ministry|association|"
                       r"foundation|\bngo\b|magazine|university|college|hospital|"
                       # rental, property, finance and insurance pages buy huge
                       # volumes of static ads and surfaced in a SKINCARE search
                       # purely because their ad text used the word "wellness"
                       r"\brent\b|rental|realty|property|broker|insurance|"
                       r"\bloan\b|mutual fund|trading|forex|resume|visa|"
                       r"immigration|admission|coaching|classes|"
                       # affiliate and arbitrage pages that flood Meta with
                       # cheap static ads across every keyword. They survived
                       # the content-farm test because their copy is not
                       # templated, but "Build Career Hub" and "The Saving
                       # Vault" are not brands with a product to film.
                       r"\bdeals?\b|savings?|thrift|bargain|frugal|coupon|"
                       r"career|jobs? hub|talent|hiring|recruit|"
                       r"\bfinds\b|\bvault\b|\bhub\b|\bdaily\b|"
                       r"\bsignal\b|terminal|compass|bureau|digest", re.I)


CONTENT_FARM = re.compile(r"learn (more )?about|read (more )?about|discover the|"
                          r"find out (more|about)|everything you need to know|"
                          r"a guide to|top \d+ (ways|tips)|to understand (common|"
                          r"different)|erfahren sie|découvrez|descubre", re.I)

# Link captions that belong to no brand: short links, social platforms, and
# the coupon and arbitrage networks. "sarb.path-2-career.com" and its sister
# domains carried four of the nine pages in the first velocity run.
NOT_OWN_DOMAIN = re.compile(
    r"^(fb\.me|m\.me|wa\.me|bit\.ly|linktr\.ee|l\.facebook\.com|"
    r"(www\.)?(facebook|instagram|youtube|whatsapp|google)\.com)$|"
    r"^sarb\.|coupon|promo|deals?\b|voucher|offers?\b|daily-?buzz|"
    r"path-2-career|articulate|verity-voice|frequery|home-is-love|for-your-heart",
    re.I)


def own_domain(ads, page_name):
    """The domain this advertiser's ads actually send people to, or "".

    This is the single strongest test that a page is a real brand. A brand
    sends every ad to its own shop: Hot Grooves pointed 39 of 39 ads at
    hotgroovesamples.com. The arbitrage pages that swamped the first velocity
    run spread theirs across five throwaway domains, or a coupon site, or
    fb.me, and their names ("The Curiosity Vault", "Plural Perspectives")
    never appear in any of them.

    Requirements, all three:
      - one domain carries at least 70% of the ads that show a link
      - that domain is not a short link, social platform or coupon network
      - some part of the page name appears in the domain, or the reverse
    Pages that fail are not brands with a product to film. As a bonus the
    domain found here is the website enrichment needs to find an email.
    """
    counts = {}
    total = 0
    for a in ads:
        for cap in (a.get("ad_creative_link_captions") or []):
            d = str(cap).lower().strip().removeprefix("www.").split("/")[0]
            if "." not in d:
                continue
            counts[d] = counts.get(d, 0) + 1
            total += 1
    if not total:
        return ""
    dom, n = max(counts.items(), key=lambda kv: kv[1])
    if n / total < 0.7 or NOT_OWN_DOMAIN.search(dom):
        return ""
    # Drop the ending, including two-part ones. "apenutrition.co.uk" read as
    # the name "co" and a real supplement brand was thrown away for it.
    labels = dom.split(".")[:-1]
    while len(labels) > 1 and labels[-1] in ("co", "com", "org", "net", "ac",
                                             "gov", "edu", "ltd", "plc"):
        labels.pop()
    stem = re.sub(r"[^a-z0-9]", "", labels[-1] if labels else "")
    name = re.sub(r"[^a-z0-9]", "", str(page_name).lower())
    words = [w for w in re.split(r"[^a-z0-9]+", str(page_name).lower()) if len(w) >= 4]
    if not stem or not (stem in name or name in stem or any(w in stem for w in words)):
        return ""
    return dom


# Words that put an ad firmly outside consumer products even when a niche
# word matched. "Serum" is a synthesizer as well as a skincare product, and a
# naturopathy course says "wellness" as often as a supplement brand does.
#
# Whole words only, and a SHARE of ads rather than any single hit. A bare
# "synth" matched "no harsh synthetic tablets" in 24 ads of a genuine
# supplement brand, and "course" is also "a 3 month course" of capsules.
OFF_PRODUCT = re.compile(r"\bpresets?\b|\bplugins?\b|\bdaw\b|\bsynth(esizer)?s?\b|"
                         r"sample packs?|\bcertification\b|\baccreditations?\b|"
                         r"\bstudents?\b|\bdiploma\b|\benrol(l|ment)?\b|\bwebinar\b|"
                         r"\bmasterclass\b|\bjournal\b|publish your research|"
                         r"online (course|program)", re.I)


def off_product(ad_bodies):
    bodies = [b for b in ad_bodies if b]
    if not bodies:
        return False
    hits = sum(1 for b in bodies if OFF_PRODUCT.search(b))
    return hits >= 2 and hits >= len(bodies) * 0.25


def distinct_creatives(ad_bodies):
    """How many genuinely different ads is this advertiser running?

    Raw ad count is a placement count, not a creative count. A brand called
    Try Lumilux showed 56 "active ads", which read as heavy spend and heavy
    creative appetite. Every one was the same copy duplicated across
    placements: one creative, 56 slots. That is a dropshipper spraying a
    single winner, not a brand whose creative team is stretched.

    The distinction matters because the entire pitch is "your creative is
    static and you need more of it". Against a one-creative advertiser that
    pitch is simply wrong, and they have no budget for a studio anyway.
    """
    norm = set()
    for b in ad_bodies:
        if not b:
            continue
        key = re.sub(r"[^a-z0-9]", "", str(b).lower())[:120]
        if key:
            norm.add(key)
    return len(norm)


def content_farm(ad_bodies):
    """Detect SEO-bait arbitrage pages posing as brands.

    One page passed every filter with 11 live ads and no video. Its creative
    read "Learn about men's swim trunks", "Learn about the stages of a
    mastectomy", "Learn about..." across wholly unrelated subjects. That is a
    content farm buying cheap clicks, not a business with a product to film.
    Two tells together: templated informational phrasing, and topics with
    nothing in common.
    """
    bodies = [b for b in ad_bodies if b]
    if len(bodies) < 2:
        return False
    hits = sum(1 for b in bodies if CONTENT_FARM.search(b))
    # 0.3, not 0.5: the arbitrage pages mix "Learn about" with plain
    # headlines, which kept them just under half and let them through.
    return hits >= 2 and hits >= len(bodies) * 0.3


def relevant(name, ad_text, niche):
    """Does this advertiser actually belong to the niche we searched?

    Meta's search_terms matches the AD COPY, not the business category. A
    rental listing whose ad mentions "wellness" comes back in a skincare
    search and looks perfectly qualified: real spend, no video, plausible
    name. Three such pages passed every other filter in testing.

    So require the niche word, or a word from the same family, to appear in
    the brand name or its own ad copy. A skincare brand almost always says
    skin, serum, cream or hair somewhere in its creative.
    """
    hay = (str(name) + " " + str(ad_text)).lower()
    tokens = [t for t in re.split(r"\W+", niche.lower()) if len(t) > 2]
    if any(t in hay for t in tokens):
        return True
    FAMILIES = {
        "skincare": ["skin", "serum", "cream", "face", "glow", "acne", "spf",
                     "moistur", "beauty", "cosmet"],
        "serum": ["skin", "serum", "face", "glow", "vitamin c"],
        "face wash": ["face", "skin", "cleanser", "wash"],
        "hair oil": ["hair", "scalp", "oil", "growth"],
        "supplements": ["protein", "vitamin", "gummies", "capsule", "nutrition"],
        "ayurvedic": ["ayurved", "herbal", "natural", "wellness", "immunity"],
        "wellness": ["wellness", "health", "nutrition", "herbal", "supplement"],
        "specialty coffee": ["coffee", "brew", "roast", "bean", "espresso"],
        "furniture": ["furniture", "sofa", "chair", "table", "wood", "interior"],
        "home decor": ["decor", "home", "vase", "lamp", "cushion", "wall"],
        "handbags": ["bag", "tote", "leather", "purse", "clutch"],
        "footwear": ["shoe", "sneaker", "sandal", "footwear", "loafer"],
        "jewellery": ["jewel", "gold", "silver", "diamond", "necklace", "ring"],
    }
    for word in FAMILIES.get(niche.lower(), []):
        if word in hay:
            return True
    return False


def api(**params):
    params["access_token"] = TOKEN
    try:
        r = requests.get(API, params=params, timeout=40)
    except Exception:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    if "error" in d:
        msg = d["error"].get("message", "")
        if "expired" in msg.lower() or "session" in msg.lower():
            print("\n  META TOKEN EXPIRED. Regenerate, extend it in the Access "
                  "Token Debugger, and put the long-lived one in .env\n")
            raise SystemExit(1)
        return None
    return d


def discover(niche, country, pages=2):
    """Broad pass. Counts here are candidates only, never evidence."""
    out, url, params = [], API, dict(
        search_terms=niche, ad_reached_countries=f"['{country}']",
        ad_active_status="ACTIVE", ad_type="ALL", limit=100, fields=FIELDS)
    for _ in range(pages):
        d = api(**params) if url == API else None
        if d is None and url != API:
            try:
                d = requests.get(url, timeout=40).json()
            except Exception:
                break
        if not d or "data" not in d:
            break
        out.extend(d["data"])
        nxt = (d.get("paging") or {}).get("next")
        if not nxt:
            break
        url, params = nxt, {}
        time.sleep(0.5)
    return out


def verify(page_id, country):
    """Stage 2. An exact, untruncated answer for ONE advertiser."""
    base = dict(search_page_ids=f'["{page_id}"]',
                ad_reached_countries=f"['{country}']",
                ad_active_status="ACTIVE", ad_type="ALL", limit=100, fields=FIELDS)
    all_d = api(**base)
    if not all_d:
        return None
    ads = all_d.get("data", [])
    vid_d = api(**{**base, "media_type": "VIDEO"})
    vids = len(vid_d.get("data", [])) if vid_d else 0
    return {"ads": len(ads), "videos": vids, "data": ads}


def safe_ads_link(page_id, country):
    """NEVER store Meta's ad_snapshot_url in a note.

    Meta returns snapshot URLs with the access token embedded in the query
    string. A vault is synced, shared and often git-tracked, so writing that
    URL to disk leaks a live credential into a dozen places at once. The
    public Ad Library page for the advertiser shows the same creative, needs
    no token, and is safe to store and share.
    """
    return ("https://www.facebook.com/ads/library/?active_status=active"
            f"&ad_type=all&country={country}&view_all_page_id={page_id}")


def scrub(text):
    """Belt and braces: strip any token that reached a string anyway."""
    return re.sub(r"access_token=[A-Za-z0-9_\-]+", "access_token=REDACTED", str(text))


def days_live(ads):
    best = 0
    for a in ads:
        d = a.get("ad_delivery_start_time") or a.get("ad_creation_time") or ""
        try:
            best = max(best, (datetime.date.today()
                              - datetime.date.fromisoformat(d[:10])).days)
        except Exception:
            pass
    return best


def note_body(name, market, res, snapshot, copy_lines):
    live = days_live(res["data"])
    ad_copy = "\n".join("> " + c[:180] for c in copy_lines[:3]) or "> (none captured)"
    return f"""# {name}

## The gap
Running **{res['ads']} active ads** for **{live} days**, **{res['videos']} of them video**.
{"Money is already moving. The creative is static. That is the opening." if res['videos'] == 0 else "Money is already moving and they keep shipping new creative. The opening is more of it, without a shoot each time."}

## Evidence
- Ad count and video count both verified by a call scoped to this advertiser's
  page_id, not inferred from a broad search. See `EVIDENCE_RULES.md`.
- [See their live ads]({snapshot})

## Their current ad copy
{ad_copy}

## Creative gap analysis
> Filled at the pitch stage. Run `python run.py pitch` and hand the brief to
> Claude Code, which opens the ad link above and describes what they run today.

- What they show now:
- What is missing:
- The concept to pitch:

## Creative concept

**Hook angle**
> One sentence on why their current static creative underperforms, about THEM.

**3-beat shot list**
1. Opening hook:
2. Product reveal:
3. Conversion frame:

**Generation prompt**
```
(Ready-to-run prompt for Higgsfield / Flux / Runway naming their actual
product. Copy it into your tool, render one frame, look at it, and attach it
only if it is good. Nothing renders automatically: an unvetted render of a
scraped product hallucinates logos and finishes, and for a studio selling
taste one bad frame costs more than ten missed leads.)
```

**Asset attached:** no

## Draft messages
> Written at the pitch stage. Do not write these before the gap above is filled,
> or the message becomes a template and will read like one.

### Email

### Instagram

### WhatsApp

---
## To advance this lead
Write the approval tag on its own line at the bottom of this note, then run
`python run.py dispatch`. A tag inside backticks is documentation and does not
count; only a bare tag you write is a decision. To kill it, use the reject tag.
"""


def run(market, pages, per_niche_cap, dry=False):
    cfg = V.campaign()
    mode = cfg.get("mode", "gap")
    country = cfg["markets"].get(market, "IN")
    niches = cfg["niches"]
    seen = V.load_seen()
    print(f"Market {market} ({country}) | {len(niches)} niches | "
          f"ledger holds {len(seen)} names\n")

    candidates, kept, skipped = {}, 0, {"junk": 0, "dupe": 0, "thin": 0, "video": 0}
    for niche in niches:
        found = discover(niche, country, pages)
        fresh = 0
        for a in found:
            pid, nm = a.get("page_id"), (a.get("page_name") or "").strip()
            if not pid or not nm or pid in candidates:
                continue
            if JUNK.search(nm) or NOT_BRAND.search(nm):
                skipped["junk"] += 1
                continue
            if V.norm(nm) in seen:
                skipped["dupe"] += 1
                continue
            if V.blocked(nm):
                skipped["blocked"] = skipped.get("blocked", 0) + 1
                continue
            body = " ".join(a.get("ad_creative_bodies") or [])[:400]
            if not relevant(nm, body, niche):
                skipped["offniche"] = skipped.get("offniche", 0) + 1
                continue
            candidates[pid] = {"name": nm, "niche": niche, "snap": "",
                               "copy": body}
            fresh += 1
        print(f"  {niche:<18} {len(found):>4} ads -> {fresh:>3} new candidates")
        time.sleep(0.4)

    ranked = list(candidates.items())[:per_niche_cap]
    print(f"\nVerifying {len(ranked)} advertisers individually "
          f"(this is what makes the claim safe to say out loud)\n")

    made = []
    for i, (pid, c) in enumerate(ranked, 1):
        res = verify(pid, country)
        if not res or res["ads"] == 0:
            continue
        if mode == "gap" and res["videos"] > cfg["max_video_ads"]:
            skipped["video"] += 1
            continue
        live = days_live(res["data"])
        if res["ads"] < cfg["min_active_ads"] or live < cfg["min_days_running"]:
            skipped["thin"] += 1
            continue

        snap = safe_ads_link(pid, country)      # token-free by construction
        # Every fetched ad, not the first six. Counting only six capped the
        # result at six, so the velocity minimum of six was met by anything
        # with six different headlines, which describes every clickbait page.
        all_bodies = []
        for a in res["data"]:
            all_bodies.extend(a.get("ad_creative_bodies") or [])
        uniq = distinct_creatives(all_bodies)

        if off_product(all_bodies):
            skipped["offniche"] = skipped.get("offniche", 0) + 1
            print(f"  [{i:>3}/{len(ranked)}] OFF  {c['name'][:32]} "
                  f"- sells courses, software or publishing, not a product")
            continue
        site = own_domain(res["data"], c["name"])
        if not site:
            skipped["nosite"] = skipped.get("nosite", 0) + 1
            print(f"  [{i:>3}/{len(ranked)}] NOSITE {c['name'][:30]} "
                  f"- ads do not point to its own website, not a brand")
            continue
        if mode == "velocity" and uniq < cfg.get("min_distinct_creatives", 6):
            skipped["lowvelocity"] = skipped.get("lowvelocity", 0) + 1
            continue
        # One creative sprayed across many placements is not creative appetite.
        # Require real variety before believing the ad count.
        if res["ads"] >= 8 and uniq <= 1:
            skipped["onecreative"] = skipped.get("onecreative", 0) + 1
            print(f"  [{i:>3}/{len(ranked)}] SPRAY {c['name'][:30]} "
                  f"- {res['ads']} placements of 1 creative, not a studio client")
            continue

        if content_farm(all_bodies):
            skipped["farm"] = skipped.get("farm", 0) + 1
            print(f"  [{i:>3}/{len(ranked)}] FARM {c['name'][:32]} "
                  f"- scattergun content ads, not a brand")
            continue

        copy_lines = []
        for a in res["data"][:3]:
            copy_lines.extend(scrub(b) for b in (a.get("ad_creative_bodies") or []))
        fm = {
            "status": "sourced",
            "brand": c["name"],
            "market": market,
            "country": country,
            "niche": c["niche"],
            "page_id": pid,
            "active_ads": res["ads"],
            "video_ads": res["videos"],
            "days_running": live,
            "distinct_creatives": uniq,
            "mode": mode,
            "evidence": {"running_ads": "confirmed",
                         "no_video": "confirmed" if res["videos"] == 0 else "no",
                         "static_only": "probable", "collection": "unknown"},
            "lead_type": "brand",
            # A search reaching India still returns foreign brands that also
            # advertise here. apenutrition.co.uk came back from an India run.
            # The website ending is the honest signal of where they are, and
            # it decides the opt-out line and the pricing currency later.
            "site_country": next((cc for end, cc in (
                (".co.uk", "United Kingdom"), (".uk", "United Kingdom"),
                (".com.au", "Australia"), (".au", "Australia"), (".ca", "Canada"),
                (".ae", "United Arab Emirates"), (".sg", "Singapore"),
                (".ie", "Ireland"), (".nl", "Netherlands"), (".in", "India"))
                if site.endswith(end)), ""),
            "email": "", "phone": "", "instagram": "", "website": f"https://{site}",
            "fb_messenger": f"https://m.me/{pid}",
            "ads_link": snap,
            "sourced": datetime.date.today().isoformat(),
            "tags": ["lead", market],
        }
        if not dry:
            V.write_note("sourced", c["name"], fm,
                         note_body(c["name"], market, res, snap, copy_lines))
        made.append(c["name"])
        kept += 1
        print(f"  [{i:>3}/{len(ranked)}] KEEP {c['name'][:34]:<36} "
              f"ads={res['ads']:<3} days={live:<4} video={res['videos']}")
        time.sleep(0.3)

    print(f"\n{kept} qualified leads written to vault/01_Sourced/")
    print(f"  filtered out: {skipped['video']} have video, {skipped['thin']} too thin, "
          f"{skipped['junk']} junk pages, {skipped['dupe']} already contacted, "
          f"{skipped.get('blocked', 0)} on the do-not-contact list, "
          f"{skipped.get('offniche', 0)} off-niche (matched ad text only), "
          f"{skipped.get('farm', 0)} content farms, "
          f"{skipped.get('onecreative', 0)} single-creative sprayers, "
          f"{skipped.get('lowvelocity', 0)} too little creative variety, "
          f"{skipped.get('nosite', 0)} not pointing to their own website")
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="chennai")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--cap", type=int, default=120,
                    help="advertisers to verify individually (1 API call each)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not TOKEN:
        print("No META_ACCESS_TOKEN in .env")
        raise SystemExit(1)
    run(a.market, a.pages, a.cap, a.dry_run)


if __name__ == "__main__":
    main()
