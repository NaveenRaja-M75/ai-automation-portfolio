#!/usr/bin/env python3
"""
FOREIGN LEADS, stage 3 of 3 — build Monarc_Foreign_Leads.xlsx.

Reads _state/foreign_raw.json (stage 1), _state/foreign_checked.json
(stage 2) and _state/foreign_contacted.json (Gmail history), classifies every
lead, and writes one workbook where nothing is dropped: every business lands
on exactly one sheet, and the ones not worth contacting say why.
"""
import re
import sys
import json
import datetime
import urllib.parse
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
from draft_all import short_name, work_phrase
from foreign_verify import name_junk

RAW = V.STATE / "foreign_raw.json"
CHK = V.STATE / "foreign_checked.json"
CON = V.STATE / "foreign_contacted.json"
DEEP = V.STATE / "foreign_deep.json"
OUT = V.ROOT / "Monarc_Foreign_Leads.xlsx"
TODAY = datetime.date.today()

CC_NAME = {"AE": "United Arab Emirates", "AU": "Australia", "GB": "United Kingdom",
           "IE": "Ireland", "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain", "OM": "Oman",
           "MY": "Malaysia", "NZ": "New Zealand", "SG": "Singapore", "CA": "Canada",
           "US": "United States", "NL": "Netherlands", "SA": "Saudi Arabia",
           "DK": "Denmark", "IN": "India"}
NAME_CC = {v: k for k, v in CC_NAME.items()}
NAME_CC.update({"UAE": "AE", "USA": "US", "UK": "GB"})


def t(v):
    if isinstance(v, str):
        v = v.strip()
        return "" if v in ("{}", "[]") else v
    if v is None or (isinstance(v, (dict, list, tuple, set)) and not v):
        return ""
    return str(v)


# ------------------------------------------------------------- geography
TLD_COUNTRY = [(".co.uk", "United Kingdom"), (".uk", "United Kingdom"), (".ie", "Ireland"),
               (".com.au", "Australia"), (".au", "Australia"), (".co.nz", "New Zealand"),
               (".nz", "New Zealand"), (".ca", "Canada"), (".ae", "United Arab Emirates"),
               (".sg", "Singapore"), (".my", "Malaysia"), (".qa", "Qatar"), (".kw", "Kuwait"),
               (".om", "Oman"), (".bh", "Bahrain"), (".sa", "Saudi Arabia"),
               (".nl", "Netherlands"), (".de", "Germany"), (".it", "Italy"), (".fr", "France"),
               (".es", "Spain"), (".dk", "Denmark"), (".us", "United States")]


def country_of(r):
    """Where the business IS.

    Order of trust: a country stated in the source, the site's own country
    ending, a researched city, the phone's country code, and never the market
    its ads run in. One brand (info@brand-uk.example) came from a UAE ad search
    and was listed as a UAE brand; it is British and merely advertises there.
    """
    c = t(r.get("country"))
    if c in NAME_CC:
        return CC_NAME[NAME_CC[c]]
    if t(r.get("site_country")):
        return t(r.get("site_country"))
    # the company's own terms say where it is registered, which beats the
    # domain: a .co.uk store governed by the laws of Guangdong is Chinese
    if t(r.get("legal_country")):
        return t(r.get("legal_country"))
    for dom in (site_domain(r), t(r.get("email")).partition("@")[2]):
        for end, name in TLD_COUNTRY:
            if dom.endswith(end):
                return name
    # The first place named is the headquarters. "Houston, Texas, USA
    # (Tarinika Inc) + India office" read as India when the whole string was
    # searched and "India" happened to match before "USA".
    city = t(r.get("city"))
    first_place = re.split(r"[(+]", city)[0]
    for text in (first_place, city):
        if re.search(r"\b(USA|Texas|New York|Illinois|Los Angeles|California)\b", text):
            return "United States"
        if re.search(r"\bUK\b|London|Brighton|Manchester|United Kingdom", text):
            return "United Kingdom"
        if "Copenhagen" in text:
            return "Denmark"
        if "Toronto" in text:
            return "Canada"
        for name, cc in list(NAME_CC.items()):
            if re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
                return CC_NAME[cc]
    # A researched city is more reliable than a number scraped off a page:
    # Astrid & Miyu is in London, and a +1 555 number made it American.
    pc = phone_info(r.get("phone"))[2]
    if pc and pc != "India":
        return pc
    # All that is known is where the ads run. Calling TYMO Beauty a UAE brand
    # because it advertises there would be a guess presented as a fact.
    return "Not stated"


def ad_market(r):
    cc = t(r.get("check_country") or r.get("ad_country"))
    return CC_NAME.get(cc, cc)


def indian_based(r):
    """True when the business itself is Indian, whatever market it targets."""
    ph = t(r.get("phone"))          # already cleared of scanner misreads
    dom = site_domain(r)
    em = t(r.get("email"))
    if ph.startswith("+91"):
        return "Indian phone number (+91)"
    if dom.endswith(".in") or dom.endswith(".co.in") or em.endswith(".in"):
        return "Indian website (.in)"
    # Only the FIRST place named counts. "Houston, Texas, USA (Tarinika Inc) +
    # India office" is an American company with a factory in India, and was
    # wrongly filed as Indian when any mention of India anywhere counted.
    first_place = re.split(r"[,(+\-]", t(r.get("city")))[0]
    if re.search(r"\bIndia\b|Bengaluru|Bangalore|Mumbai|Delhi|Chennai|Banaras|Varanasi|"
                 r"Jaipur|Kolkata|Hyderabad", first_place + " " + t(r.get("origin"))):
        return "Based in India"
    # Selling Indian clothing is not the same as being an Indian company.
    # Fashefy sells sarees from Edmonton (+1 780), and Embellished Kurta sells
    # Pakistani lawn suits. Either kind of foreign evidence wins.
    hay = " ".join((t(r.get("name")), t(r.get("sample_copy")), t(r.get("niche")), dom))
    pc = phone_info(ph)[2]
    foreign = (pc and pc != "India") or any(dom.endswith(e) for e, _ in TLD_COUNTRY)
    if ETHNIC.search(hay) and not foreign and not re.search(r"pakistan", hay, re.I):
        return "Indian ethnic-wear brand selling to the diaspora"
    return ""


# city -> (UTC offset in hours on today's date, label). The offsets are for
# late September 2026: UK and EU on summer time until 25 Oct, eastern
# Australia moves to summer time on 4 Oct, New Zealand already has (27 Sep).
CITY_TZ = [
    (r"vancouver|victoria|kelowna|burnaby|surrey|richmond", -7),
    (r"calgary|edmonton", -6), (r"winnipeg|saskatoon|regina", -5),
    (r"halifax|moncton", -3),
    (r"perth", 8), (r"adelaide", 9.5), (r"darwin", 9.5), (r"brisbane|gold coast", 10),
    (r"texas|houston|austin|dallas|chicago|illinois|aurora", -5),
    (r"los angeles|california|san francisco|seattle", -7),
]
COUNTRY_TZ = {"United Kingdom": 1, "Ireland": 1, "Netherlands": 2, "Denmark": 2,
              "United Arab Emirates": 4, "Oman": 4, "Qatar": 3, "Kuwait": 3,
              "Bahrain": 3, "Saudi Arabia": 3, "Singapore": 8, "Malaysia": 8,
              "Australia": 10, "New Zealand": 13, "Canada": -4, "United States": -4,
              "India": 5.5}
WEEKDAYS = {"Qatar": "Sun-Thu", "Kuwait": "Sun-Thu", "Bahrain": "Sun-Thu",
            "Oman": "Sun-Thu", "Saudi Arabia": "Sun-Thu"}


def send_time(country, city=""):
    """When to send so it lands 9:30-10:30am their time, written in India time."""
    off = None
    for pat, o in CITY_TZ:
        if re.search(pat, city or "", re.I):
            off = o
            break
    if off is None:
        off = COUNTRY_TZ.get(country)
    if off is None:
        return ""
    ist = (9.5 - off + 5.5) % 24
    def fmt(h):
        hh, mm = int(h), int(round((h - int(h)) * 60))
        ap = "am" if hh < 12 else "pm"
        return f"{(hh % 12) or 12}:{mm:02d} {ap}"
    days = WEEKDAYS.get(country, "Tue-Thu best")
    note = "  (schedule it in Gmail)" if ist < 8 or ist >= 22 else ""
    return f"{fmt(ist)} - {fmt((ist + 1) % 24)} IST, {days}{note}"


# ---------------------------------------------------------------- contacts
MOBILE = {"971": r"5", "974": r"[3567]", "965": r"[569]", "968": r"[79]", "973": r"3",
          "966": r"5", "44": r"7", "353": r"8", "61": r"4", "64": r"2", "65": r"[89]",
          "60": r"1", "31": r"6", "91": r"[6789]"}
CODE_COUNTRY = {"971": "United Arab Emirates", "974": "Qatar", "965": "Kuwait", "968": "Oman",
                "973": "Bahrain", "966": "Saudi Arabia", "44": "United Kingdom",
                "353": "Ireland", "61": "Australia", "64": "New Zealand", "65": "Singapore",
                "60": "Malaysia", "31": "Netherlands", "91": "India", "1": "United States / Canada",
                "45": "Denmark", "39": "Italy", "49": "Germany", "34": "Spain", "33": "France",
                "852": "Hong Kong", "86": "China", "81": "Japan", "82": "South Korea",
                "27": "South Africa", "46": "Sweden", "47": "Norway", "41": "Switzerland",
                "43": "Austria", "32": "Belgium", "351": "Portugal", "48": "Poland",
                "30": "Greece", "90": "Turkey", "92": "Pakistan", "880": "Bangladesh",
                "94": "Sri Lanka", "63": "Philippines", "62": "Indonesia", "66": "Thailand",
                "84": "Vietnam", "20": "Egypt", "234": "Nigeria", "254": "Kenya", "7": "Russia"}
EXTRA_CODES = [c for c in CODE_COUNTRY if c not in MOBILE]


def phone_info(p):
    """(pretty, kind, code_country). kind: mobile, landline, unknown or bad.

    The trunk zero is dropped after the country code: "+44 01273 675556" is
    how a UK site prints a number, but dialled from abroad the 0 must go, and
    a wa.me link with it opens a chat with nobody. Italy is the one country
    that keeps its 0, so it is left alone.
    """
    d = re.sub(r"\D", "", t(p))
    if not d:
        return "", "", ""
    for code in sorted(list(MOBILE) + list(EXTRA_CODES), key=len, reverse=True):
        if d.startswith(code):
            rest = d[len(code):]
            if code != "39":
                rest = rest.lstrip("0")
            if len(rest) < 7:
                return f"+{code} {rest}", "bad", CODE_COUNTRY.get(code, "")
            # placeholders sites print in their templates
            if rest in ("9876543210", "1234567890", "0123456789") or re.search(r"(\d)\1{5}", rest):
                return f"+{code} {rest}", "bad", CODE_COUNTRY.get(code, "")
            if code in MOBILE:
                kind = "mobile" if re.match(MOBILE[code], rest) else "landline"
            else:
                kind = "unknown"
            cn = CODE_COUNTRY.get(code, "")
            if code == "1":             # one code, two countries: the area code decides
                if rest[:3] == "555" or rest[3:6] == "555":
                    return f"+1 {rest}", "bad", ""        # the film-and-TV range
                if rest[:3] in ("800", "833", "844", "855", "866", "877", "888"):
                    return f"+1 {rest}", "toll-free", ""  # valid in both countries
                cn = "Canada" if rest[:3] in CA_AREA else "United States"
            return f"+{code} {rest}", kind, cn
    return "+" + d, "nocode", ""


CA_AREA = set("204 226 236 249 250 257 263 289 306 343 354 365 367 368 382 387 403 416 418 "
              "428 431 437 438 450 468 474 506 514 519 548 579 581 584 587 604 613 639 647 "
              "672 683 705 709 742 753 778 780 782 807 819 825 867 873 879 902 905".split())


FREE = ("gmail.", "yahoo.", "hotmail.", "outlook.", "icloud.", "live.")


def email_kind(e, name):
    if not e:
        return ""
    local, _, dom = e.partition("@")
    if dom.startswith(FREE):
        toks = [x for x in re.split(r"\W+", name.lower()) if len(x) > 3]
        return "brand Gmail" if any(x in local for x in toks) else "personal Gmail"
    if re.match(r"(privacy|legal|accounts|billing|dmca|abuse|noreply|no-reply|returns)", local):
        return "admin inbox"
    if re.match(r"(support|customer|care|service|orders|help|shop|eshop|clientsupport)", local):
        return "support inbox"
    if re.match(r"(\w{0,3}info|hello|hi|hey|contact|team|office|enquir|admin|mail|sales|studio|general|res\b|reservations)", local):
        return "general inbox"
    return "named person"


def site_domain(r):
    for k in ("website", "own_site", "site_final"):
        v = t(r.get(k)).lower()
        v = re.sub(r"^https?://", "", v)
        v = re.sub(r"^www\.", "", v).split("/")[0]
        if "." in v and not re.search(r"facebook|instagram|google|youtube|linktr", v):
            return v
    return ""


def ads_link(r):
    pid = t(r.get("page_id"))
    cc = t(r.get("check_country") or r.get("ad_country")) or "ALL"
    if pid:
        return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
                f"&country={cc}&view_all_page_id={pid}")
    q = urllib.parse.quote(t(r.get("name")))
    return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country=ALL&q={q}&search_type=keyword_unordered")


# --------------------------------------------------------- hand review
# Verdicts from reading the lead myself, where no rule can tell. Each one
# says why, and the sheet shows it, so nothing is hidden behind a filter.
MANUAL = {
    "school of natural skincare": ("skip", "Sells DIY skincare courses and recipes, not "
                                           "products (their ads teach formulation)"),
}
GREETING = {"velourahomeuk": "Veloura Home", "ape_nutrition": "Ape Nutrition",
            "urban_style": "Urban Style", "royal-sweets": "Royal Sweets",
            "cel - coco et loco the edition": "Coco et Loco"}


def greeting_for(r):
    g = t(r.get("greeting"))
    if g:
        return g
    n = t(r.get("name"))
    if n.lower() in GREETING:
        return GREETING[n.lower()]
    s = short_name(n)
    s = re.sub(r"(?<=[A-Za-z])[_](?=[A-Za-z])", " ", s)
    s = re.sub(r"\s+(&|and|\||,|/|-)+$", "", s).strip()    # "Storyhouse Communications &"
    words = s.split()
    # "SALLY SKOUFIS" shouts in a greeting. Single-word caps names (SIXGUN,
    # OHMMO) are how those agencies write themselves, so they stay.
    if len(words) >= 2 and all(w.isupper() and w.isalpha() for w in words) and \
            any(len(w) >= 5 for w in words):
        s = " ".join(w.capitalize() for w in words)
    return s


SCAM_COPY = re.compile(r"\bfor \d+ years\b.{0,40}\b(i|we)\b|we are closing|closing down|"
                       r"retir(e|ing)|no longer need to prick|tortured|doctors (hate|are)|"
                       r"last chance to buy|heartbreaking", re.I)
STORE_NAME = re.compile(r"-[A-Z]$|-(US|UK|AU)$|e-?store|mart\b|mart/|/shop|picks$|\bcool$",
                        re.I)


def dropship_signals(r):
    """Reasons to think a 'brand' is a dropshipping store, and a score.

    Dropshippers were the single biggest source of bad leads before: Try
    Lumilux ran 56 ads for one headtorch behind anti-aging claims. They make
    their own video cheaply at volume and rarely last long enough to become
    clients. No one signal is proof, so they are added up, and a brand
    investing in its own Instagram counts against the verdict.
    """
    sig, s = [], 0
    ads, vids = r.get("ads_now") or 0, r.get("videos_now") or 0
    if vids >= 30 and vids >= 0.9 * ads:
        s += 2
        sig.append(f"all {vids} of its ads are video, made at volume already")
    if STORE_NAME.search(t(r.get("name"))):
        s += 3
        sig.append("store-style page name")
    niche = t(r.get("niche"))
    if niche.count(",") >= 2:
        s += 1
        sig.append("matched unrelated product searches")
    local = t(r.get("email")).partition("@")[0]
    if re.match(r"(support|service|privacy|customerservice|cs)$", local):
        s += 1
        sig.append(f"only a {local}@ inbox")
    if SCAM_COPY.search(t(r.get("sample_copy"))):
        s += 3
        sig.append("ad copy uses a known dropshipping script")
    if t(r.get("legal_country")) in ("China", "Hong Kong"):
        s += 3
        sig.append(f"registered in {t(r.get('legal_country'))} "
                   f"({t(r.get('legal_country_why'))})")
    ig = t(r.get("instagram")).lstrip("@").lower()
    toks = [x for x in re.split(r"[^a-z0-9]+", t(r.get("name")).lower()) if len(x) >= 4]
    if ig and any(x in ig.replace(".", "").replace("_", "") for x in toks):
        s -= 1
    return s, sig


def business_type(r):
    """D2C brand, or one of the other kinds of business that buys ads."""
    if r["kind"] == "brand" and dropship_signals(r)[0] >= 3:
        return "Likely dropshipping store"
    hay = t(r.get("name")) + " " + t(r.get("niche")) + " " + t(r.get("sample_copy"))[:120]
    if re.search(r"resort|hotel|\bpark\b|castle|museum|house and park|spa\b|lodge", hay, re.I):
        return "Hotel or attraction"
    if re.search(r"academy|school\b|training", t(r.get("name")), re.I):
        return "Training academy"
    if re.search(r"\bshoes\b|furniture|home ?store|department", t(r.get("name")), re.I):
        return "Retail store"
    return "D2C brand"


IG_BAD = None


def ig_ok(h):
    """Re-validate every handle with the scraper's own rules. Old sheets were
    written before those rules existed and still carry '@en_us' and friends."""
    global IG_BAD
    if IG_BAD is None:
        from lib.contact_enricher import IG_SYSTEM, FILE_EXT, HANDLE_OK
        IG_BAD = (IG_SYSTEM, FILE_EXT, HANDLE_OK)
    system, ext, ok = IG_BAD
    x = t(h).lstrip("@").lower()
    # 3 characters minimum: '@o1' came off one site's markup
    if len(x) < 3 or x in system or x.split(".")[0] in system or x in ("shopify", "instagram"):
        return ""
    if "." in x and x.rsplit(".", 1)[-1] in ext:
        return ""
    return "@" + x if ok.match(x) else ""


ETHNIC = re.compile(r"banaras|banarasi|saree|\bsari\b|kurta|lehenga|salwar|handloom|"
                    r"\bsilks?\b|\bnalli\b|ethnic wear|indian ethnic|desi\b", re.I)


def fake_indian(phone, r):
    """A +91 number read off a FOREIGN website by the scanner is almost always
    a misread: the scanner turns any bare ten-digit number starting 6 to 9
    into an Indian mobile. That put +91 numbers on Missoma (London), Emma
    Lewisham (New Zealand) and IM8 Health, and one was +91 6666666667. Such a
    number is kept only when the business is independently Indian."""
    if not t(phone).startswith("+91"):
        return False
    dom = site_domain(r)
    if dom.endswith(".in") or t(r.get("email")).endswith(".in"):
        return False
    return not ETHNIC.search(" ".join((t(r.get("name")), t(r.get("sample_copy")),
                                       t(r.get("niche")), dom)))


def dropped_phone_note(r):
    p = t(r.get("phone_discarded"))
    if p.startswith("+91"):
        return f"A +91 number ({p}) read off their site was a scanner misread, left out"
    return f"The number found ({p}) is a placeholder or incomplete, left out"


SHARED_DOMAINS = re.compile(r"alibaba|amazon|etsy|ebay|myshopify|shopify\.com|wixsite|"
                            r"squarespace|linktr|google|facebook|instagram|tiktok", re.I)


def dedupe_after_check(raw):
    """Second duplicate pass, now that websites are known.

    The first pass (stage 1) only had names and page ids. One brand can run
    several Facebook pages: 'IM8 Health' and 'IM8' are the same company on
    im8health.com and would have been pitched twice. Records of the same kind
    sharing a website or a business email domain are folded into the one
    with the most live ads.
    """
    parent = list(range(len(raw)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, r in enumerate(raw):
        keys = []
        d = site_domain(r)
        if d and not SHARED_DOMAINS.search(d):
            keys.append("d:" + d)
        e = t(r.get("email")).lower()
        if "@" in e and not e.split("@")[1].startswith(FREE):
            keys.append("e:" + e.split("@")[1])
        for k in keys:
            k = r["kind"] + "|" + k
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i
    groups = {}
    for i in range(len(raw)):
        groups.setdefault(find(i), []).append(raw[i])
    out = []
    for g in groups.values():
        if len(g) == 1:
            out.append(g[0])
            continue
        g.sort(key=lambda r: -(r.get("ads_now") or 0))
        keep = dict(g[0])
        for other in g[1:]:
            for k, v in other.items():
                if k == "sources":
                    keep["sources"] = list(dict.fromkeys(keep.get("sources", []) + v))
                elif v not in ("", None, [], False) and not keep.get(k):
                    keep[k] = v
        keep["also_known_as"] = [t(o.get("name")) for o in g[1:]]
        out.append(keep)
    return out


# ------------------------------------------------------------------ load
def load():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    chk = json.loads(CHK.read_text(encoding="utf-8")) if CHK.exists() else {}
    deep = json.loads(DEEP.read_text(encoding="utf-8")) if DEEP.exists() else {}
    con = json.loads(CON.read_text(encoding="utf-8"))
    for r in raw:
        k = f"{r['kind']}|{r.get('page_id') or ''}|{r.get('name')}"
        c = chk.get(k, {})
        for part in ("meta", "site"):
            for kk, vv in (c.get(part) or {}).items():
                r.setdefault(kk, vv)
        # stage 2b: a Meta page found by name, and a deeper read of the site
        d = deep.get(k, {})
        ml = d.get("meta_lookup") or {}
        if ml.get("page_id") and not r.get("page_id"):
            for kk, vv in ml.items():
                r.setdefault(kk, vv)
            r["page_id"] = ml["page_id"]
        dp = d.get("deep") or {}
        for src, dst in (("deep_email", "email_found"), ("deep_phone", "phone_found"),
                         ("deep_ig", "ig_found"), ("deep_site", "website")):
            if dp.get(src) and not r.get(dst):
                r[dst] = dp[src]
        if dp.get("deep_email") and not r.get("email_mx"):
            r["email_mx"] = dp.get("deep_mx", "")
        if dp.get("deep_country"):
            r["legal_country"] = dp["deep_country"]
            r["legal_country_why"] = dp.get("deep_country_why", "")
        # contacts found on the site fill gaps, never overwrite
        r["instagram"] = ig_ok(r.get("instagram"))
        for f, found in (("email", "email_found"), ("phone", "phone_found"),
                         ("instagram", "ig_found")):
            val = ig_ok(r.get(found)) if f == "instagram" else r.get(found)
            if f == "phone" and val and fake_indian(val, r):
                r["phone_discarded"] = val
                val = ""
            if not r.get(f) and val:
                r[f] = val
                r[f + "_from_site"] = True
        # Contacts scraped off websites skip the cleaning the source sheets
        # got: mailto links arrive URL-encoded ("%20info@..."), and American
        # sites print "407 712 0494" with no country code, which came out as
        # "+4077120494", a Romanian-looking number that dials nobody.
        e = urllib.parse.unquote(t(r.get("email"))).strip().strip(".,;:<>").lower()
        r["email"] = e if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e) else ""
        digits = re.sub(r"\D", "", t(r.get("phone")))
        if len(digits) == 10 and t(r.get("country")) in ("United States", "Canada", "USA"):
            r["phone"] = "+1" + digits
        # A number that matches no country code, or is a placeholder (555,
        # 9876543210), is not shown at all: a wrong number in the sheet is
        # worse than an empty cell. It is kept aside and noted on the row.
        if t(r.get("phone")) and phone_info(r.get("phone"))[1] in ("bad", "nocode"):
            r["phone_discarded"] = r["phone"]
            r["phone"] = ""
        # the same misread reached the older sheets through the same scanner:
        # a foreign agency or brand showing a +91 number is not evidence of
        # anything unless something else about it is Indian too
        if r["kind"] == "agency" and t(r.get("phone")).startswith("+91") and \
                not site_domain(r).endswith(".in"):
            r["phone_discarded"] = r["phone"]
            r["phone"] = ""
    raw = dedupe_after_check(raw)
    sent = {}
    for j in con["job_posts"]:
        sent[j["email"].lower()] = ("job post", j["first_sent"])
    for b in con["brand_emails"]:
        sent[b["email"].lower()] = ("brand email", b["sent"])
    sent_dom = {e.split("@")[1]: v for e, v in sent.items()
                if not e.split("@")[1].startswith(FREE)}
    return raw, con, sent, sent_dom


# --------------------------------------------------------------- classify
def classify_brand(r):
    """Return (sheet, reason). Sheets: brand, other, nocontact, indian, skip."""
    m = MANUAL.get(t(r.get("name")).lower())
    if m:
        return m
    if r.get("competitor"):
        return "skip", "An AI film studio itself (a competitor), not a client"
    if t(r.get("vault_stage")) == "rejected":
        return "skip", "Rejected on inspection: " + t(r.get("vault_reason"))
    j = name_junk(r.get("name"))
    if j:
        return "skip", j.capitalize()
    if r.get("farm"):
        return "skip", "Clickbait article ads (a content farm), no product to film"
    if r.get("off_product"):
        return "skip", "Sells courses, software or publishing, not a physical product"
    if re.search(r"\b(dr\.?|doctor)\b|\bmd\b", t(r.get("name")), re.I) and not r.get("own_site"):
        return "skip", "An individual's page (doctor or influencer), not a brand"
    if r.get("site_moved") and not SOCIAL.search(t(r.get("site_final"))):
        return "skip", f"Website now redirects to {t(r.get('site_final'))}, business looks closed or sold"
    ib = indian_based(r)
    if ib:
        return "indian", ib
    has = r.get("email") or r.get("instagram") or phone_info(r.get("phone"))[1] == "mobile"
    if not has:
        return "nocontact", ""
    return ("brand" if business_type(r) == "D2C brand" else "other"), ""


SOCIAL = re.compile(r"linkedin|facebook|instagram|linktr|wix|carrd", re.I)


PRODUCTION = re.compile(r"\b(films?|cinema|pictures|productions?|animation|motion|"
                        r"videography|video (company|studio|agency))\b", re.I)


def classify_agency(r):
    if t(r.get("vault_stage")) == "rejected":
        return "skip", "Rejected on inspection: " + t(r.get("vault_reason"))
    # A film or video production house sells exactly what Monarc sells.
    # Cedar Ads (Hyderabad) was rejected for this by hand; the rule catches
    # the rest, unless the name also says it is a marketing agency.
    if PRODUCTION.search(t(r.get("name"))) and not re.search(
            r"marketing|media|digital|agency|ads|advertising", t(r.get("name")), re.I):
        return "skip", "A video or film production company: a competitor, not a client"
    # An agency whose domain now forwards elsewhere has usually rebranded or
    # been bought (bravebison.com, themediagroup.co.uk), not closed. It stays,
    # flagged and held at Priority C. Only a forward to a search engine means
    # the site is simply gone.
    if r.get("site_moved") and re.search(r"(^|\.)google\.|bing\.com|yahoo\.",
                                         t(r.get("site_final"))):
        return "skip", "Website is gone (forwards to a search engine)"
    return "agency", ""


# ------------------------------------------------------------------ score
def score_agency(r):
    s = 0
    ek = email_kind(t(r.get("email")), t(r.get("name")))
    s += {"named person": 35, "general inbox": 32, "brand Gmail": 22,
          "support inbox": 20, "admin inbox": 8, "personal Gmail": 8}.get(ek, 0)
    if t(r.get("instagram")):
        s += 15
    pk = phone_info(r.get("phone"))[1]
    s += 12 if pk == "mobile" else 6 if pk else 0
    try:
        rev = int(float(t(r.get("reviews")) or 0))
    except ValueError:
        rev = 0
    s += 18 if rev >= 20 else 10 if rev >= 5 else 0
    if re.search(r"video|content|creative|production|branding", t(r.get("services")), re.I):
        s += 10
    if r.get("site_up") is False:
        s -= 25
    if r.get("site_moved") and not SOCIAL.search(t(r.get("site_final"))):
        s = min(s, 40)                  # rebranded or acquired: check before sending
    return s, ("A" if s >= 70 else "B" if s >= 48 else "C")


def score_brand(r):
    s = 0
    ek = email_kind(t(r.get("email")), t(r.get("name")))
    s += {"named person": 30, "general inbox": 28, "brand Gmail": 22,
          "support inbox": 18, "admin inbox": 6, "personal Gmail": 8}.get(ek, 0)
    if t(r.get("instagram")):
        s += 22
    if phone_info(r.get("phone"))[1] == "mobile":
        s += 8
    ads = r.get("ads_now")
    if isinstance(ads, int):
        s += 15 if ads >= 4 else 6 if ads >= 1 else -20
    if (r.get("distinct_now") or 0) >= 6:
        s += 10
    if (r.get("days_now") or 0) >= 30:
        s += 8
    if r.get("own_site") or site_domain(r):
        s += 7
    if business_type(r) == "Likely dropshipping store":
        s = min(s, 40)
    return s, ("A" if s >= 70 else "B" if s >= 48 else "C")


def best_channel(r, kind):
    e = t(r.get("email"))
    ek = email_kind(e, t(r.get("name")))
    ig = t(r.get("instagram"))
    pk = phone_info(r.get("phone"))[1]
    if e and ek not in ("personal Gmail",):
        first = "Email"
    elif ig:
        first = "Instagram DM"
    elif pk == "mobile":
        first = "WhatsApp"
    elif e:
        first = "Email (personal inbox)"
    elif r.get("page_id"):
        first = "Facebook Messenger"
    else:
        first = "Website contact form"
    order = [x for x in ("Email" if e else "", "WhatsApp" if pk == "mobile" else "",
                         "Instagram" if ig else "") if x]
    return first, " > ".join(order)


# ------------------------------------------------------------ descriptions
def agency_what(r):
    svc = t(r.get("services"))
    parts = [x.strip() for x in svc.split(",") if x.strip()]
    top = ", ".join(parts[:4]) if parts else "digital marketing"
    team = t(r.get("team"))
    rev = t(r.get("reviews"))
    bits = [f"Marketing agency: {top}"]
    if re.search(r"video", svc, re.I):
        bits.append("already sells video")
    if team:
        bits.append(f"team {team}")
    if rev and rev not in ("0", "0.0"):
        bits.append(f"{rev} reviews")
    return ". ".join([bits[0]] + ["; ".join(bits[1:])] if len(bits) > 1 else bits)


def human_niche(r):
    """The niche only when a person wrote it. In the ad-gap scans the 'niche'
    is the SEARCH WORD that matched, so a tactical-hoodie store came out as
    'Handbags, interior design brand' and a clothing page as 'Clothing brand
    brand'. Their own ad text says what they sell far more honestly."""
    src = " ".join(r.get("sources", []))
    if re.search(r"Pricing|ai_ads_clients|vault", src):
        return t(r.get("niche"))
    return ""


def brand_what(r):
    niche = human_niche(r)
    copy = re.sub(r"\s+", " ", t(r.get("sample_copy"))).strip()
    if t(r.get("sells")):
        return f"{niche}. Sells: {t(r.get('sells'))}"
    if copy:
        lead = f"{niche}. " if niche else ""
        return f"{lead}Their ad says: \"{copy[:130]}{'...' if len(copy) > 130 else ''}\""
    if niche:
        return niche
    if t(r.get("niche")):
        return f"Came up in a '{t(r.get('niche'))}' ad search (no ad text captured)"
    return "Not captured: open their live ads to see what they sell"


def brand_evidence(r):
    ads = r.get("ads_now")
    if not isinstance(ads, int):
        return t(r.get("ad_status_src")) or "Not checked on Meta (no page id)"
    if ads == 0:
        return f"No ads running on {r.get('checked_on')} (they were in August)"
    v = r.get("videos_now", 0)
    return (f"{ads} live ads, {v} video, {r.get('distinct_now', 0)} different, "
            f"running {r.get('days_now', 0)} days (checked {r.get('checked_on')})")


def brand_angle(r):
    ads = r.get("ads_now")
    if isinstance(ads, int) and ads > 0:
        if (r.get("videos_now") or 0) == 0:
            return "First video ads: they pay for ads but run zero video"
        return "More creatives without a new shoot each time"
    if "jewel" in t(r.get("niche")).lower():
        return "Festive and wedding-season product films"
    return "AI product commercials and UGC ads"


# ================================================================== write
ARIAL = "Arial"
F_BASE = Font(name=ARIAL, size=10)
F_HEAD = Font(name=ARIAL, size=10, bold=True, color="FFFFFF")
F_LINK = Font(name=ARIAL, size=10, color="0563C1", underline="single")
F_BOLD = Font(name=ARIAL, size=10, bold=True)
F_TITLE = Font(name=ARIAL, size=18, bold=True, color="111827")
F_H2 = Font(name=ARIAL, size=12, bold=True, color="111827")
F_MUTE = Font(name=ARIAL, size=9, color="6B7280")
FILL_HEAD = PatternFill("solid", fgColor="1F2937")
FILL_A = PatternFill("solid", fgColor="D1FAE5")
FILL_B = PatternFill("solid", fgColor="FEF3C7")
FILL_C = PatternFill("solid", fgColor="F3F4F6")
FILL_HOT = PatternFill("solid", fgColor="FEE2E2")
FILL_NOTE = PatternFill("solid", fgColor="EFF6FF")
THIN = Side(style="thin", color="E5E7EB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")
STATUS = ("Not contacted,Emailed,WhatsApp sent,Instagram sent,Replied,"
          "Interested,Not interested,Closed")


def table(ws, headers, rows, widths, wrap_cols=(), start=1, status_col=None,
          prio_col=None, freeze="D2"):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=start, column=i, value=h)
        c.font, c.fill, c.border = F_HEAD, FILL_HEAD, BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[start].height = 30
    for ri, row in enumerate(rows, start + 1):
        for ci, val in enumerate(row, 1):
            h = headers[ci - 1]
            c = ws.cell(row=ri, column=ci)
            if isinstance(val, tuple):             # (text, url)
                txt, url = val
                c.value = txt or None
                if url and txt:
                    c.hyperlink = url
                    c.font = F_LINK
                else:
                    c.font = F_BASE
            else:
                c.value = val if val != "" else None
                c.font = F_BASE
            c.border = BORDER
            c.alignment = WRAP if h in wrap_cols else TOP
            if h == "Phone":
                c.number_format = "@"
        if prio_col:
            p = ws.cell(row=ri, column=prio_col)
            p.fill = {"A": FILL_A, "B": FILL_B}.get(str(p.value)[:1], FILL_C)
            p.font = F_BOLD
            p.alignment = Alignment(horizontal="center", vertical="top")
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    last = start + max(len(rows), 1)
    ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{last}"
    if freeze:
        ws.freeze_panes = freeze
    if status_col:
        dv = DataValidation(type="list", formula1=f'"{STATUS}"', allow_blank=True)
        ws.add_data_validation(dv)
        col = get_column_letter(status_col)
        dv.add(f"{col}{start + 1}:{col}{last + 200}")
    return last


def L(text, url):
    return (text, url) if text else ("", "")


def ig_url(h):
    h = t(h).lstrip("@")
    return f"https://www.instagram.com/{h}/" if h else ""


def wa(p):
    pretty, kind, _ = phone_info(p)
    if kind in ("mobile", "unknown") and pretty:
        return ("Chat", "https://wa.me/" + re.sub(r"\D", "", pretty))
    return ("", "")


def web(r):
    d = site_domain(r)
    return L(d, "https://" + d) if d else ("", "")


def found_in(r):
    return "; ".join(dict.fromkeys(r.get("sources", [])))


def mail(e):
    e = t(e)
    return L(e, "mailto:" + e) if e else ("", "")


def ready_message(r, first):
    """The first message for this lead, on the channel it should go out on.

    Built by draft_all.drafts_for, the same code that writes the drafts in
    the vault, so the workbook and the system can never disagree. Brands get
    the opener their evidence supports: a brand with no verified live ads is
    never told it is running ads. Every message is linted before it is shown.
    """
    from draft_all import drafts_for, check
    country = country_of(r)
    fm = {"brand": t(r.get("name")), "greeting_name": greeting_for(r),
          "lead_type": "agency" if r["kind"] == "agency" else "brand",
          "business_type": business_type(r) if r["kind"] == "brand" else "",
          "city": t(r.get("city")) if t(r.get("city")) != "Not verified" else "",
          "country": country, "market": "International - " + country,
          "services": t(r.get("services")), "niche": human_niche(r),
          "ads_now": r.get("ads_now"), "videos_now": r.get("videos_now"),
          "distinct_now": r.get("distinct_now")}
    d = drafts_for(fm)
    if check(d, (fm["greeting_name"], fm["brand"])):
        return "(message blocked by the linter, write this one by hand)"
    chan = {"Email": "email", "Email (personal inbox)": "email", "Instagram DM": "instagram",
            "WhatsApp": "whatsapp"}.get(first, "email")
    return d[chan]


BR_HEAD = ["#", "Priority", "Brand", "Say hi to", "Type", "What they sell", "Country",
           "Advertises in", "City", "Email", "Email type", "Phone", "Phone type", "WhatsApp",
           "Instagram", "Website", "Meta Ad Library", "FB Messenger", "Ads today",
           "Pitch angle", "First channel", "Full sequence", "Send at (India time)",
           "Watch out", "Found in", "Status", "Date sent", "Reply / notes",
           "Ready first message"]


def brand_sheet(ws, br):
    for r in br:
        r["_score"], r["_tier"] = score_brand(r)
    br.sort(key=lambda r: (r["_tier"], country_of(r), -r["_score"]))
    rows = []
    for n, r in enumerate(br, 1):
        country = country_of(r)
        adc = CC_NAME.get(t(r.get("check_country") or r.get("ad_country")), "")
        pretty, pkind, pcountry = phone_info(r.get("phone"))
        ek = email_kind(t(r.get("email")), t(r.get("name")))
        first, seq = best_channel(r, "brand")
        warn = []
        if r.get("_sent"):
            warn.append(f"Already emailed on {r['_sent'][1]}")
        if ek == "support inbox":
            warn.append("Email is a support inbox: an Instagram DM reaches the owner faster")
        if ek == "personal Gmail":
            warn.append("Email is a personal Gmail")
        if r.get("ads_now") == 0:
            warn.append("No ads running today")
        if r.get("site_up") is False:
            warn.append("Website did not load today")
        if r.get("email_mx") in ("No MX", "Unresolved"):
            warn.append("Email domain did not verify, may bounce")
        if r.get("later"):
            warn.append("Marked 'later' in your jewellery list: big brand, pitch after a case study")
        if pkind == "landline":
            warn.append("Phone is a landline: call, do not WhatsApp")
        if pkind == "bad":
            warn.append("Phone number looks incomplete: check it on their website")
        if pretty and pcountry and pcountry != country and not (
                False):
            warn.append(f"Phone is a {pcountry} number")
        if t(r.get("contact_name")):
            warn.append(f"Named contact: {t(r.get('contact_name'))} ({t(r.get('contact_role'))})")
        if business_type(r) == "Likely dropshipping store":
            warn.append("Likely dropshipping store: " + ", ".join(dropship_signals(r)[1]))
        if t(r.get("notes_src")):
            warn.append("Research note: " + t(r.get("notes_src"))[:160])
        if r.get("also_known_as"):
            warn.append("Same business also appears as: " + ", ".join(r["also_known_as"]))
        if r.get("site_moved") and SOCIAL.search(t(r.get("site_final"))):
            warn.append(f"Website forwards to {t(r.get('site_final'))}")
        if ek == "admin inbox":
            warn.append("Email is an admin inbox (privacy@, accounts@): marketing rarely reads it")
        if r.get("phone_discarded"):
            warn.append(dropped_phone_note(r))
        ig = t(r.get("instagram")).lstrip("@").lower().replace(".", "").replace("_", "")
        toks = [x for x in re.split(r"[^a-z0-9]+", t(r.get("name")).lower()) if len(x) >= 4]
        stem = site_domain(r).split(".")[0].replace("-", "")
        if ig and toks and not any(x in ig for x in toks) and not (stem and stem[:5] in ig):
            warn.append("Instagram handle does not match the brand name: check it is theirs")
        if r.get("email_from_site") or r.get("instagram_from_site") or r.get("phone_from_site"):
            warn.append("Some contacts found on their website today")
        pid = t(r.get("page_id"))
        rows.append([
            n, r["_tier"], t(r.get("name")), greeting_for(r), business_type(r), brand_what(r),
            country, adc if adc and adc != country else "",
            "" if t(r.get("city")) == "Not verified" else t(r.get("city"))[:40],
            mail(r.get("email")), ek, pretty, pkind, wa(r.get("phone")),
            L(t(r.get("instagram")), ig_url(r.get("instagram"))), web(r),
            ("Their live ads" if pid else "Search their ads", ads_link(r)),
            ("Message", f"https://m.me/{pid}") if pid else ("", ""),
            brand_evidence(r), brand_angle(r), first, seq,
            send_time(country, t(r.get("city"))) or
            (send_time(adc).replace("  (schedule it in Gmail)", "") +
             ", timed to their ad market" if adc else ""),
            "; ".join(warn), found_in(r),
            "Emailed" if r.get("_sent") else "Not contacted",
            r["_sent"][1] if r.get("_sent") else "", "",
            ready_message(r, first)])
    table(ws, BR_HEAD, rows,
          [5, 8, 26, 18, 14, 50, 16, 16, 16, 30, 13, 17, 10, 9, 22, 24, 15, 10, 34, 30, 16,
           26, 30, 42, 30, 14, 12, 30, 70],
          wrap_cols=("What they sell", "Watch out", "Found in", "Ads today", "Pitch angle",
                     "Brand", "Send at (India time)", "Full sequence", "Ready first message"),
          status_col=BR_HEAD.index("Status") + 1, prio_col=2)


def main():
    raw, con, sent, sent_dom = load()
    sheets = {"agency": [], "agency_nocontact": [], "brand": [], "other": [], "nocontact": [],
              "indian": [], "skip": []}

    for r in raw:
        sh, why = classify_agency(r) if r["kind"] == "agency" else classify_brand(r)
        # an agency with no email, Instagram or phone cannot be worked from
        # the main list: it sits on its own tab, like Messenger-only brands
        if sh == "agency" and not (r.get("email") or r.get("instagram") or r.get("phone")):
            sh = "agency_nocontact"
        r["_why"] = why
        e = t(r.get("email")).lower()
        hit = sent.get(e) or (sent_dom.get(e.split("@")[1]) if "@" in e else None)
        if hit:
            r["_sent"] = hit
        sheets[sh].append(r)

    total = sum(len(v) for v in sheets.values())
    assert total == len(raw), "a lead went missing between classify and write"

    wb = Workbook()
    wb.remove(wb.active)
    ws0 = wb.create_sheet("START HERE")

    # ------------------------------------------------------ Warm - act now
    wsw = wb.create_sheet("Warm - act now")
    warm = [j for j in con["job_posts"] if j.get("replied")]
    rows = [[j["state"], j["who"], mail(j["email"]), j["role"], j["replied"],
             j["what_they_said"], j["next"]] for j in warm]
    table(wsw, ["State", "Who", "Email", "What they posted", "They replied on",
                "What they said", "What to do next"], rows,
          [26, 20, 34, 30, 14, 60, 62],
          wrap_cols=("What they said", "What to do next", "State", "What they posted"),
          freeze="C2")
    for ri in range(2, 2 + len(rows)):
        if str(wsw.cell(ri, 1).value).startswith("HOT"):
            for ci in range(1, 8):
                wsw.cell(ri, ci).fill = FILL_HOT

    # ------------------------------------------------------------ Agencies
    wsa = wb.create_sheet("Agencies")
    ag = sheets["agency"]
    for r in ag:
        r["_score"], r["_tier"] = score_agency(r)
    ag.sort(key=lambda r: (r["_tier"], country_of(r), -r["_score"]))
    rows = []
    for n, r in enumerate(ag, 1):
        country = country_of(r)
        pretty, pkind, pcountry = phone_info(r.get("phone"))
        ek = email_kind(t(r.get("email")), t(r.get("name")))
        first, seq = best_channel(r, "agency")
        warn = []
        if r.get("_sent"):
            warn.append(f"Already emailed on {r['_sent'][1]}")
        same_region = (pcountry == country or
                       (False))
        if pretty and pcountry and not same_region:
            warn.append(f"Phone is a {pcountry} number, not {country}: check it before calling")
        if ek == "personal Gmail":
            warn.append("Email is a personal Gmail")
        if r.get("site_up") is False:
            warn.append("Website did not load today")
        if r.get("email_mx") in ("No MX", "Unresolved"):
            warn.append("Email domain did not verify, may bounce")
        if r.get("email_from_site") or r.get("instagram_from_site") or r.get("phone_from_site"):
            warn.append("Some contacts found on their website today")
        if pkind == "landline":
            warn.append("Phone is a landline: call, do not WhatsApp")
        if r.get("phone_discarded"):
            warn.append(dropped_phone_note(r))
        if ek == "admin inbox":
            warn.append("Email is an admin inbox: marketing rarely reads it")
        if r.get("site_moved") and SOCIAL.search(t(r.get("site_final"))):
            warn.append(f"Website forwards to {t(r.get('site_final'))}")
        elif r.get("site_moved"):
            warn.append(f"Website now forwards to {t(r.get('site_final'))}: rebranded or "
                        f"acquired. Open it and pitch the new name, or skip")
        if r.get("also_known_as"):
            warn.append("Same agency also listed as: " + ", ".join(r["also_known_as"]))
        if not (r.get("email") or r.get("instagram") or pretty):
            warn.append("No email, Instagram or phone found: use the form on their website")
        rows.append([
            n, r["_tier"], t(r.get("name")), greeting_for(r),
            agency_what(r), country, t(r.get("city")), t(r.get("rating")), t(r.get("reviews")),
            t(r.get("team")), mail(r.get("email")), ek, pretty, pkind, wa(r.get("phone")),
            L(t(r.get("instagram")), ig_url(r.get("instagram"))), web(r),
            "Up" if r.get("site_up") else "Down" if r.get("site_up") is False else "Not checked",
            ("Profile", t(r.get("profile"))) if t(r.get("profile")) else ("", ""),
            ("Search their ads", ads_link(r)), first, seq, send_time(country, t(r.get("city"))),
            "; ".join(warn), found_in(r),
            "Emailed" if r.get("_sent") else "Not contacted",
            r["_sent"][1] if r.get("_sent") else "", "",
            ready_message(r, first)])
    AG_HEAD = ["#", "Priority", "Agency", "Say hi to", "What they do", "Country", "City",
               "Rating", "Reviews", "Team size", "Email", "Email type", "Phone", "Phone type",
               "WhatsApp", "Instagram", "Website", "Website today", "Directory profile",
               "Meta Ad Library", "First channel", "Full sequence", "Send at (India time)",
               "Watch out", "Found in", "Status", "Date sent", "Reply / notes",
               "Ready first message"]
    table(wsa, AG_HEAD, rows,
          [5, 8, 30, 18, 46, 16, 14, 7, 8, 9, 30, 13, 17, 10, 9, 22, 26, 11, 11, 15,
           14, 26, 30, 40, 30, 14, 12, 30, 70],
          wrap_cols=("What they do", "Watch out", "Found in", "Agency",
                     "Send at (India time)", "Full sequence", "Ready first message"),
          status_col=AG_HEAD.index("Status") + 1, prio_col=2)

    # ------------------------------------------ D2C Brands, Other businesses
    brand_sheet(wb.create_sheet("D2C Brands"), sheets["brand"])
    brand_sheet(wb.create_sheet("Other businesses"), sheets["other"])

    # ------------------------------------------- Agencies - contact form only
    wsq = wb.create_sheet("Agencies - form only")
    anc = sorted(sheets["agency_nocontact"],
                 key=lambda r: (country_of(r), -int(float(t(r.get("reviews")) or 0))))
    rows = []
    for n, r in enumerate(anc, 1):
        rows.append([n, t(r.get("name")), agency_what(r), country_of(r), t(r.get("city")),
                     t(r.get("reviews")), web(r),
                     "Up" if r.get("site_up") else "Down" if r.get("site_up") is False
                     else "Not checked",
                     ("Profile", t(r.get("profile"))) if t(r.get("profile")) else ("", ""),
                     "Use the contact form on their website, or find the founder on LinkedIn",
                     found_in(r)])
    table(wsq, ["#", "Agency", "What they do", "Country", "City", "Reviews", "Website",
                "Website today", "Directory profile", "How to reach", "Found in"], rows,
          [5, 30, 50, 16, 14, 8, 28, 11, 11, 40, 30],
          wrap_cols=("What they do", "How to reach", "Found in", "Agency"), freeze="C2")

    # ------------------------------------------------ Brands - Messenger only
    wsn = wb.create_sheet("Brands - Messenger only")
    nc = sorted(sheets["nocontact"], key=lambda r: -(r.get("ads_now") or 0))
    rows = []
    for n, r in enumerate(nc, 1):
        pid = t(r.get("page_id"))
        rows.append([n, t(r.get("name")), brand_what(r), country_of(r),
                     ("Their live ads" if pid else "Search their ads", ads_link(r)),
                     ("Message", f"https://m.me/{pid}") if pid else ("", ""), web(r),
                     brand_evidence(r),
                     "Facebook Messenger only. No email, Instagram or mobile on their ads "
                     "or website." if pid else "No contact found anywhere", found_in(r)])
    table(wsn, ["#", "Brand", "What they sell", "Advertises in", "Meta Ad Library",
                "FB Messenger", "Website", "Ads today", "How to reach", "Found in"], rows,
          [5, 28, 55, 18, 15, 11, 26, 36, 40, 30],
          wrap_cols=("What they sell", "Ads today", "How to reach", "Found in", "Brand"),
          freeze="C2")

    # ------------------------------------------------- Indian brands abroad
    wsi = wb.create_sheet("Indian brands abroad")
    rows = []
    for n, r in enumerate(sorted(sheets["indian"], key=lambda r: t(r.get("name")).lower()), 1):
        pid = t(r.get("page_id"))
        rows.append([n, t(r.get("name")), r["_why"],
                     CC_NAME.get(t(r.get("ad_country")), t(r.get("ad_country"))) or country_of(r),
                     brand_what(r), mail(r.get("email")), phone_info(r.get("phone"))[0],
                     L(t(r.get("instagram")), ig_url(r.get("instagram"))), web(r),
                     ("Their live ads" if pid else "Search their ads", ads_link(r)),
                     brand_evidence(r), found_in(r)])
    table(wsi, ["#", "Brand", "Why it is Indian", "Advertises in", "What they sell", "Email",
                "Phone", "Instagram", "Website", "Meta Ad Library", "Ads today", "Found in"],
          rows, [5, 26, 30, 18, 50, 30, 17, 22, 24, 15, 34, 30],
          wrap_cols=("What they sell", "Ads today", "Found in", "Why it is Indian", "Brand"),
          freeze="C2")

    # ---------------------------------------------------- Already contacted
    wsc = wb.create_sheet("Already contacted")
    wsc["A1"] = "People abroad you have already emailed (read from your Gmail Sent folder)"
    wsc["A1"].font = F_H2
    wsc["A2"] = ("Never send these a first message again. The job posts were clients HIRING "
                 "an AI ad editor, so they buy exactly what Monarc sells.")
    wsc["A2"].font = F_MUTE
    rows = [[j["state"], j["who"], mail(j["email"]), j["role"], j["first_sent"],
             j["last_touch"], j.get("replied") or "No", j.get("what_they_said", ""),
             j["next"]] for j in con["job_posts"]]
    last = table(wsc, ["State", "Who", "Email", "What they posted", "First sent",
                       "Last touch", "Replied", "What they said", "Next step"], rows,
                 [26, 26, 34, 32, 11, 11, 11, 50, 50],
                 wrap_cols=("State", "What they said", "Next step", "What they posted", "Who"),
                 start=4, freeze="A5")
    for ri in range(5, 5 + len(rows)):
        s = str(wsc.cell(ri, 1).value)
        if s.startswith(("HOT", "WARM")):
            for ci in range(1, 10):
                wsc.cell(ri, ci).fill = FILL_HOT if s.startswith("HOT") else FILL_B
    b0 = last + 3
    wsc.cell(b0 - 1, 1, "Brands you cold-emailed in July").font = F_H2
    hdr = ["State", "Brand", "Email", "Sent", "Bounced", "Next step"]
    for i, h in enumerate(hdr, 1):
        c = wsc.cell(b0, i, h)
        c.font, c.fill, c.border = F_HEAD, FILL_HEAD, BORDER
    for ri, b in enumerate(con["brand_emails"], b0 + 1):
        age = (TODAY - datetime.date.fromisoformat(b["sent"])).days
        vals = ["Bounced: address is dead" if b.get("bounced") else f"No reply in {age} days",
                b["brand"], mail(b["email"]), b["sent"], "Yes" if b.get("bounced") else "No",
                "Find a working address before trying again" if b.get("bounced") else
                "Closed for email. One Instagram DM with a new angle is fair now."]
        for ci, v in enumerate(vals, 1):
            c = wsc.cell(ri, ci)
            if isinstance(v, tuple):
                c.value, c.hyperlink, c.font = v[0], v[1], F_LINK
            else:
                c.value, c.font = v, F_BASE
            c.border, c.alignment = BORDER, WRAP

    # ------------------------------------------------ Not worth contacting
    wsx = wb.create_sheet("Not worth contacting")
    rows = []
    for n, r in enumerate(sorted(sheets["skip"], key=lambda r: t(r.get("_why"))), 1):
        rows.append([n, t(r.get("name")), "Agency" if r["kind"] == "agency" else "Brand",
                     country_of(r), r["_why"],
                     ("Their ads", ads_link(r)) if r.get("page_id") else ("", ""), found_in(r)])
    table(wsx, ["#", "Name", "Type", "Country / market", "Why not", "Meta Ad Library",
                "Found in"], rows, [5, 34, 9, 20, 62, 11, 34],
          wrap_cols=("Why not", "Found in", "Name"), freeze="C2")

    messages_sheet(wb.create_sheet("Messages"))
    start_sheet(ws0, sheets, con, len(raw))

    wb.calculation.fullCalcOnLoad = True
    wb.save(OUT)
    print(f"Saved {OUT}")
    for k, v in sheets.items():
        print(f"  {k:<10} {len(v)}")
    print(f"  total      {total}  (= {len(raw)} unique leads, none dropped)")
    return sheets


# --------------------------------------------------------------- Messages
def messages_sheet(ws):
    from draft_all import (A_EMAIL, A_INSTAGRAM, A_WHATSAPP, B_EMAIL, B_INSTAGRAM,
                           B_WHATSAPP, OPTOUT_INTL)
    from outreach_queue import T as NUDGE
    ws["A1"] = "Messages, approved style"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Replace the words in {curly brackets}. {name} is the 'Say hi to' column, "
                "{city} and {work} come from the Agencies sheet, {niche} from 'What they sell'. "
                "No links and never the word 'free' in a first message.")
    ws["A2"].font = F_MUTE
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:C2")
    ws.row_dimensions[2].height = 30
    fill = lambda s: s.replace("{optout}", OPTOUT_INTL)
    rows = [
        ("AGENCY", "Email (day 0)", fill(A_EMAIL)),
        ("AGENCY", "WhatsApp (day 4)", NUDGE[("agency", "whatsapp", "nudge")]),
        ("AGENCY", "Instagram (day 10, last)", NUDGE[("agency", "instagram", "final")]),
        ("AGENCY", "Instagram as FIRST message (no email)", A_INSTAGRAM),
        ("AGENCY", "WhatsApp as FIRST message (no email)", A_WHATSAPP),
        ("BRAND", "Email (day 0)", fill(B_EMAIL)),
        ("BRAND", "WhatsApp (day 4)", NUDGE[("brand", "whatsapp", "nudge")]),
        ("BRAND", "Instagram (day 10, last)", NUDGE[("brand", "instagram", "final")]),
        ("BRAND", "Instagram as FIRST message (no email)", B_INSTAGRAM),
        ("BRAND", "WhatsApp as FIRST message (no email)", B_WHATSAPP),
        ("BRAND running ZERO video", "Swap the first line of any brand message for this",
         "Saw you're running quite a few ads right now, all of them still images. Video "
         "usually gets tested last because it means a shoot, a crew and weeks of waiting."),
        ("WARM: Paulius (trial, $40/ad)", "Reply in the same email thread",
         "Hi Paulius, following up on the trial. Happy to start whenever suits you.\n\n"
         "If it helps, send me the link to one product you're selling now and I'll make a "
         "15 second AI UGC ad for it, so you have something real to react to before we "
         "go further.\n\nNaveen"),
        ("WARM: Meder (talking-head ads)", "Reply in the same thread, only once the sample exists",
         "Hi Meder, I made an AI talking-head ad in the same style as the one you shared: "
         "{sample link}\n\nAnd here's a 60 second Loom intro: {loom link}\n\n"
         "Happy to do a paid trial on one of your products next.\n\nNaveen"),
    ]
    for i, h in enumerate(("For", "When / channel", "Message"), 1):
        c = ws.cell(4, i, h)
        c.font, c.fill, c.border = F_HEAD, FILL_HEAD, BORDER
    for ri, (who, when, msg) in enumerate(rows, 5):
        for ci, v in enumerate((who, when, msg), 1):
            c = ws.cell(ri, ci, v)
            c.font = F_BOLD if ci == 1 else F_BASE
            c.alignment, c.border = WRAP, BORDER
        ws.row_dimensions[ri].height = min(15 * (msg.count("\n") + len(msg) // 95 + 2), 300)
        if who.startswith("WARM"):
            for ci in range(1, 4):
                ws.cell(ri, ci).fill = FILL_HOT
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 100
    ws.freeze_panes = "A5"


# ------------------------------------------------------------- START HERE
def start_sheet(ws, sheets, con, total):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 30
    for col in "BCD":
        ws.column_dimensions[col].width = 12
    ws.column_dimensions["E"].width = 52
    r = 1
    ws.cell(r, 1, "Monarc Studio | Foreign Leads").font = F_TITLE
    r += 1
    ws.cell(r, 1, f"Built {TODAY:%d %b %Y} from every lead file on this laptop, the Monarc "
                  f"vault, and your Gmail Sent folder. Every brand was re-checked live on "
                  f"Meta and every website was opened today.").font = F_MUTE
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    r += 2

    def h2(text):
        nonlocal r
        ws.cell(r, 1, text).font = F_H2
        r += 1

    def line(a, e="", bold=False, fill=None):
        nonlocal r
        c = ws.cell(r, 1, a)
        c.font = F_BOLD if bold else F_BASE
        c.alignment = WRAP
        if e:
            d = ws.cell(r, 2, e)
            d.font, d.alignment = F_BASE, WRAP
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
            ws.row_dimensions[r].height = max(15, 15 * (len(e) // 80 + 1))
        if fill:
            for ci in range(1, 6):
                ws.cell(r, ci).fill = fill
        r += 1

    h2("Do these first")
    line("1. Paulius offered paid work", "He liked your portfolio and offered a trial at $40 "
         "per ad (AI UGC and AI animation). You said yes on 23 Aug and it went quiet. "
         "Reply today. The message is ready on the Messages sheet.", True, FILL_HOT)
    line("2. Meder told you exactly what to send", "An AI talking-head ad like his reference, "
         "plus a 60 second Loom. Make that one sample and reply in the same thread.", True,
         FILL_B)
    line("3. Then work down Agencies, Priority A", "Email first. Up to 20 a day, sent at "
         "the India time in the 'Send at' column so it lands at 9:30am their time.", True)
    line("4. Every lead has its message written", "The last column, 'Ready first message', "
         "is the exact text for that lead's first channel, already checked for spam words "
         "and false claims. A brand is only told it is running ads when Meta confirmed it.",
         True)
    line("5. Or let Claude send them", "Every reachable lead here is also loaded into the "
         "Monarc system. Say 'send UK agencies batch 1' and Claude emails 20, logs them, "
         "and the WhatsApp and Instagram follow-ups come up on day 4 and day 10 by "
         "themselves.", True)
    r += 1

    h2("What is in this workbook")
    for i, hh in enumerate(("Sheet", "Leads", "What it is", "", ""), 1):
        c = ws.cell(r, i, hh or None)
        c.font, c.fill = F_HEAD, FILL_HEAD
    r += 1
    job_n, br_n = len(con["job_posts"]), len(con["brand_emails"])
    b0 = 7 + job_n
    rows = [
        ("Warm - act now", "=COUNTA('Warm - act now'!B2:B100)",
         "Clients abroad who replied to you. Highest value in the whole file."),
        ("Agencies", "=COUNTA(Agencies!C2:C2000)",
         "Marketing agencies abroad. Pitch: we make the ad videos for their clients, under "
         "their name, at a flat price."),
        ("Agencies - form only", "=COUNTA('Agencies - form only'!B2:B2000)",
         "Real agencies abroad with no email, Instagram or phone published anywhere. "
         "Reach them through the form on their website. Lower priority."),
        ("D2C Brands", "=COUNTA('D2C Brands'!C2:C2000)",
         "Product brands abroad that you can actually reach: email, Instagram or a mobile."),
        ("Other businesses", "=COUNTA('Other businesses'!C2:C2000)",
         "Hotels, attractions, academies and shops abroad that run ads. Reachable, but not "
         "D2C brands, so the pitch needs adapting."),
        ("Brands - Messenger only", "=COUNTA('Brands - Messenger only'!B2:B2000)",
         "Real brands running ads, but no email, Instagram or mobile found. Reach them "
         "through Facebook Messenger, lower priority."),
        ("Indian brands abroad", "=COUNTA('Indian brands abroad'!B2:B2000)",
         "Indian companies advertising to NRIs abroad (Nalli, Tilfi and others). Real leads, "
         "but they belong with your India list, not this one."),
        ("Already contacted", f"=COUNTA('Already contacted'!B5:B{4 + job_n})"
                              f"+COUNTA('Already contacted'!B{b0 + 1}:B{b0 + br_n})",
         "Everyone abroad you have already emailed. Do not send them a first message again."),
        ("Not worth contacting", "=COUNTA('Not worth contacting'!B2:B2000)",
         "Kept so nothing is lost, each with the reason: fiction apps, clickbait pages, "
         "course sellers, dead websites, a competitor."),
        ("Messages", "", "Your approved messages for agencies and brands, plus the two warm "
                         "replies."),
    ]
    for name, f, what in rows:
        ws.cell(r, 1, name).font = F_BOLD
        if name != "Messages":
            ws.cell(r, 1).hyperlink = f"#'{name}'!A1"
            ws.cell(r, 1).font = Font(name=ARIAL, size=10, bold=True, color="0563C1",
                                      underline="single")
        c = ws.cell(r, 2, f or None)
        c.font = F_BOLD
        c.alignment = Alignment(horizontal="center", vertical="top")
        d = ws.cell(r, 3, what)
        d.font, d.alignment = F_BASE, WRAP
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
        ws.row_dimensions[r].height = max(15, 15 * (len(what) // 70 + 1))
        r += 1
    r += 1

    h2("Leads by country")
    for i, hh in enumerate(("Country", "Agencies", "D2C Brands", "Other", "Total"), 1):
        c = ws.cell(r, i, hh)
        c.font, c.fill = F_HEAD, FILL_HEAD
    r += 1
    counts = {}
    for k, col in (("agency", 0), ("brand", 1), ("other", 2)):
        for x in sheets[k]:
            counts.setdefault(country_of(x), [0, 0, 0])[col] += 1
    ws._expected_country = counts            # read back by the self-check
    first_row = r
    for country, v in sorted(counts.items(), key=lambda kv: -sum(kv[1])):
        ws.cell(r, 1, country).font = F_BASE
        ws.cell(r, 2, f"=COUNTIF(Agencies!$F$2:$F$2000,A{r})").font = F_BASE
        ws.cell(r, 3, f"=COUNTIF('D2C Brands'!$G$2:$G$2000,A{r})").font = F_BASE
        ws.cell(r, 4, f"=COUNTIF('Other businesses'!$G$2:$G$2000,A{r})").font = F_BASE
        ws.cell(r, 5, f"=SUM(B{r}:D{r})").font = F_BOLD
        for ci in (2, 3, 4, 5):
            ws.cell(r, ci).alignment = Alignment(horizontal="center")
        r += 1
    ws.cell(r, 1, "Total").font = F_BOLD
    for ci, col in ((2, "B"), (3, "C"), (4, "D"), (5, "E")):
        c = ws.cell(r, ci, f"=SUM({col}{first_row}:{col}{r - 1})")
        c.font, c.alignment = F_BOLD, Alignment(horizontal="center")
    r += 2

    h2("How every lead was checked")
    for a_, e_ in (
            ("Duplicates", "Merged across every file, by name, website, email and Facebook "
                           "page. One business, one row."),
            ("Live on Meta", "Every brand with a Facebook page was re-checked on the day "
                             "this was built: live ads, how many are video, how many "
                             "different ones."),
            ("Websites opened", "Every site was loaded. Dead sites and domains that now "
                                "redirect elsewhere are flagged or moved out."),
            ("Hidden emails", "Addresses hidden behind Cloudflare, and those in privacy and "
                              "terms pages, were decoded and read."),
            ("Real country", "From the company's own terms ('governed by the laws of...'), "
                             "its website ending, a researched city or its phone. Never "
                             "from where its ads happen to run."),
            ("Fake numbers removed", "Placeholder numbers (555, 9876543210) and misread +91 "
                                     "numbers are left out and noted."),
            ("Competitors removed", "Film and video production companies sell what you "
                                    "sell, so they are on the 'Not worth contacting' tab."),
            ("Already contacted", "Your Gmail Sent folder was read, so nobody here gets a "
                                  "first message twice.")):
        line(a_, e_)
    r += 1

    h2("How to read Priority")
    line("A", "Strong: a real business inbox or Instagram, active, and a clear reason to "
              "buy. Start here.")
    line("B", "Workable: reachable, but a weaker inbox (support@) or less proof of spend.")
    line("C", "Weak: one thin route. Send only after A and B are done.")
    r += 1

    h2("When to send (it should land at 9:30am their time)")
    for country in ("United Kingdom", "Ireland", "Netherlands", "United Arab Emirates", "Qatar",
                    "Singapore", "Australia", "New Zealand", "Canada", "United States"):
        extra = {"Australia": " (Sydney/Melbourne. Perth is 3 hours later)",
                 "Canada": " (Toronto/Montreal. Vancouver is 3 hours later)",
                 "United States": " (New York. Texas +1h, California +3h)",
                 "Qatar": " (same for Kuwait, Bahrain, Saudi. Their weekend is Fri-Sat)",
                 "United Arab Emirates": " (same for Oman)"}.get(country, "")
        line(country, send_time(country) + extra)
    line("Tip", "For anything before 8am or after 10pm, write the email now and use Gmail's "
                "Schedule send (the arrow next to Send).")
    line("Clocks change soon", "Australia on 4 Oct: send 1 hour earlier. UK, Ireland and "
                               "Europe on 25 Oct, USA and Canada on 1 Nov: send 1 hour later. "
                               "New Zealand changed on 27 Sep and is already included.")
    r += 1

    h2("Rules that keep you out of spam")
    for a, e in (("20 emails a day, maximum", "From one Gmail. More than that gets the account "
                  "flagged, and then even replies to warm leads stop landing."),
                 ("No links, no attachments in the first email", "Offer the sample. Send it "
                  "when they say yes."),
                 ("Never the word 'free'", "It is the single biggest spam trigger."),
                 ("A new channel each time", "Email on day 0, WhatsApp on day 4, Instagram on "
                  "day 10, then stop. A reply on any channel stops everything."),
                 ("WhatsApp etiquette", "Normal for business in the UAE, Gulf and Malaysia. "
                  "In the UK, Ireland, Canada and Australia, only after email and Instagram.")):
        line(a, e, True)
    r += 1

    h2("Price guide abroad")
    line("Agencies", "USD 250-600 per finished ad video. They buy at your price and mark it "
                     "up to their client, so volume discounts are expected.")
    line("Brands direct", "Quote at the top of that range: there is no agency margin in between.")
    r += 1
    ws.cell(r, 1, f"{total} unique businesses in total. Every lead ever gathered is on exactly "
                  f"one sheet. Duplicates across files were merged, and 'Found in' says "
                  f"where each came from.").font = F_MUTE
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)


if __name__ == "__main__":
    main()
