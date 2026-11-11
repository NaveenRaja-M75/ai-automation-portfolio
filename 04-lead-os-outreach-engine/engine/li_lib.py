#!/usr/bin/env python3
"""
LinkedIn engine, shared layer: config, polite fetching, parsers, scoring.

PUBLIC DATA ONLY
    Nothing here logs in, sends a cookie, or touches an account. Every URL is
    one a logged-out visitor in a private window can open:
      - linkedin.com/jobs-guest/...      the public job search LinkedIn serves
                                         to search engines and logged-out users
      - linkedin.com/company/<x>         public company page (size, industry,
                                         website, HQ)
      - linkedin.com/posts/<x>           public post page (full text, date,
                                         author)
    Posts are discovered through a search engine, never through LinkedIn's
    own (login-only) post search.

WHY A CACHE
    Every response is stored in _state/li_cache.sqlite. A re-run, a crash or
    a rebuild costs zero requests for anything already fetched, which is what
    keeps the request count low enough that LinkedIn never notices us.

WHY IT STOPS INSTEAD OF PUSHING
    On a 429, a 999 (LinkedIn's "slow down") or a search-engine challenge, it
    waits once, then raises Blocked. The caller saves progress and exits.
    Hammering through a block is how an IP gets flagged for days.
"""
import os
import re
import html
import json
import time
import random
import sqlite3
import datetime
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "_state"
CACHE = STATE / "li_cache.sqlite"
STORE = STATE / "li_state.json"
CONTROL = ROOT / "vault" / "00_Control" / "LINKEDIN.md"
TODAY = datetime.date.today()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}


class Blocked(Exception):
    """The source asked us to slow down. Save and stop, never push through.

    `source` is "linkedin" or "search". The two are independent: a search
    engine challenge must not stop the company-page stream, which never
    touches a search engine, and the reverse.
    """

    def __init__(self, msg, source="linkedin"):
        super().__init__(msg)
        self.source = source


# ------------------------------------------------------------------ config
def config():
    """Read LINKEDIN.md. Lists are `- item` lines under `name:` in yaml fences.

    `- key: value` lines become (key, value) pairs, which is how settings and
    market-tagged locations are written. No pyyaml, matching the vault layer.
    """
    txt = CONTROL.read_text(encoding="utf-8") if CONTROL.exists() else ""
    raw = "\n".join(re.findall(r"```yaml(.*?)```", txt, re.S))
    cfg, cur = {}, None
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^(\w+):\s*$", s)
        if m and not line.startswith(" "):
            cur = m.group(1)
            cfg[cur] = []
            continue
        if s.startswith("- ") and cur:
            item = re.sub(r"\s+#.*$", "", s[2:]).strip()
            if (item.startswith("'") and item.endswith("'")) or \
               (item.startswith('"') and item.endswith('"') and item.count('"') == 2):
                item = item[1:-1]
            kv = re.match(r"^(\w+):\s*(.+)$", item)
            cfg[cur].append((kv.group(1), kv.group(2).strip()) if kv and cur in
                            ("settings", "job_locations") else item)
    st = dict(cfg.get("settings", []))
    cfg["settings"] = {
        "max_age_days": int(st.get("max_age_days", 21)),
        "pages_per_search": int(st.get("pages_per_search", 2)),
        "search_pages": int(st.get("search_pages", 1)),
        "min_company_size": int(st.get("min_company_size", 2)),
        "max_company_size": int(st.get("max_company_size", 500)),
        "max_linkedin_requests": int(st.get("max_linkedin_requests", 400)),
        "max_hiring_company_size": int(st.get("max_hiring_company_size", 1000)),
        "vault_tiers": [x.strip().upper() for x in
                        st.get("vault_tiers", "A,B").split(",") if x.strip()],
    }
    for k in ("job_roles", "post_queries", "niche_queries", "agency_industries",
              "brand_industries", "skip_words", "job_locations", "never_companies"):
        cfg.setdefault(k, [])
    return cfg


def env(key):
    if os.environ.get(key):
        return os.environ[key]
    for d in (ROOT, ROOT.parent / "Lead_Generation7"):
        f = d / ".env"
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    return ""


# ------------------------------------------------------------------ state
def load_state():
    try:
        s = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    for k in ("jobs", "posts", "companies", "done", "contacts"):
        s.setdefault(k, {})
    return s


def save_state(s):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    for i in range(6):                   # Windows refuses a replace while a reader holds the file
        try:
            tmp.replace(STORE)
            return
        except PermissionError:
            time.sleep(0.5 * (i + 1))
    raise


LOCK = STATE / "li_state.lock"


def lock():
    """One LinkedIn command at a time.

    The collector, build and vault each load and save the whole state file.
    Two at once means whichever saves last silently erases the other's work.
    A lock older than 3 hours is treated as left behind by a crash.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
        raise SystemExit(f"Another LinkedIn command is running (started "
                         f"{time.ctime(LOCK.stat().st_mtime)}). Wait for it to finish. "
                         f"If nothing is running, delete {LOCK}")
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    import atexit
    atexit.register(lambda: LOCK.exists() and LOCK.unlink())


def done_recently(s, key, hours=12):
    t = s["done"].get(key)
    return bool(t) and (time.time() - t) < hours * 3600


# ------------------------------------------------------------------ fetching
_db = None


def _cache():
    global _db
    if _db is None:
        STATE.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(CACHE))
        _db.execute("create table if not exists c (k text primary key, t real, "
                    "code int, body text)")
    return _db


def _cget(key, ttl_h):
    row = _cache().execute("select t, code, body from c where k=?", (key,)).fetchone()
    if row and (time.time() - row[0]) < ttl_h * 3600:
        return row[1], row[2]
    return None


def _cput(key, code, body):
    _cache().execute("insert or replace into c values (?,?,?,?)",
                     (key, time.time(), code, body))
    _cache().commit()


_last = {"linkedin": 0.0, "search": 0.0, "site": 0.0}
GAP = {"linkedin": (2.5, 4.5), "search": (5.0, 8.0), "site": (0.5, 1.0)}
STATS = {"requests": 0, "cached": 0}


def _pace(kind):
    lo, hi = GAP[kind]
    wait = _last[kind] + random.uniform(lo, hi) - time.time()
    if wait > 0:
        time.sleep(wait)
    _last[kind] = time.time()


def li_get(url, ttl_h=24):
    """GET a public LinkedIn URL.

    Returns html, "" for a page that is gone (404 / 410 / dead redirect), or
    None for a TEMPORARY failure (network error, 403, 5xx). The difference
    matters: an empty string is recorded as final, None means "try again next
    run". Storing a network blip as "" once marked live jobs as closed forever.

    Measured 2026-10-06: LinkedIn answered 999 after about 490 requests in 30
    minutes from one IP. So each run stops itself at a budget below that
    (max_linkedin_requests in LINKEDIN.md) instead of waiting to be told.
    """
    hit = _cget(url, ttl_h)
    if hit:
        STATS["cached"] += 1
        return hit[1] if hit[0] == 200 else ""
    budget = config()["settings"]["max_linkedin_requests"] if "budget" not in STATS \
        else STATS["budget"]
    STATS["budget"] = budget
    if STATS.get("linkedin", 0) >= budget:
        raise Blocked(f"this run's LinkedIn budget of {budget} requests is used up "
                      f"(set in LINKEDIN.md). Run again in a few hours to continue")
    for attempt in (1, 2):
        _pace("linkedin")
        STATS["requests"] += 1
        STATS["linkedin"] = STATS.get("linkedin", 0) + 1
        try:
            r = requests.get(url, headers=HDR, timeout=20, allow_redirects=True)
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(10)
            continue
        login_wall = "authwall" in r.url or "/login" in r.url or "uas/login" in r.url
        if r.status_code in (429, 999) or login_wall:
            # Found 2026-10-06: one company page 301-redirects to a broken slug
            # that always answers 999. Treated as a rate limit, it stopped every
            # run on the same URL, so the queue behind it never moved. A 999 at
            # the end of a redirect is a dead page, and so is any URL that
            # already stopped a run once: skip it, never stall on it.
            if (r.history and r.status_code == 999) or _cget("strike|" + url, 24 * 7):
                _cput(url, 404, "")
                return ""
            if attempt == 1:
                print(f"    LinkedIn asked us to slow down ({r.status_code}). "
                      f"Waiting 90s once...")
                time.sleep(90)
                continue
            _cput("strike|" + url, r.status_code, "")
            raise Blocked(f"LinkedIn {r.status_code} on {url[:80]}")
        if r.status_code in (404, 410):
            _cput(url, r.status_code, "")
            return ""
        if r.status_code != 200:
            return None                  # 403 / 5xx: temporary, not recorded
        _cput(url, 200, r.text)
        return r.text
    return None


def site_get(url, ttl_h=24 * 14):
    hit = _cget(url, ttl_h)
    if hit:
        STATS["cached"] += 1
        return hit[1] if hit[0] == 200 else ""
    _pace("site")
    STATS["requests"] += 1
    try:
        r = requests.get(url, headers=HDR, timeout=12, allow_redirects=True)
        code, body = r.status_code, (r.text if r.status_code == 200 else "")
    except requests.RequestException:
        code, body = 0, ""
    _cput(url, code, body[:600000])
    return body


# ------------------------------------------------------------------ search
def search(query, pages=1, month=True):
    """Search-engine results as a list of URLs.

    Serper (Google results, 2,500 queries one-time on signup) is used when
    SERPER_API_KEY is in .env. Otherwise DuckDuckGo's HTML endpoint, which is
    free with no key, is paced slowly and stops on a challenge.
    """
    key = env("SERPER_API_KEY")
    ck = f"search|{'serper' if key else 'ddg'}|{month}|{pages}|{query}"
    hit = _cget(ck, 12)
    if hit:
        STATS["cached"] += 1
        return json.loads(hit[1])
    urls = _serper(query, pages, month, key) if key else _ddg(query, pages, month)
    _cput(ck, 200, json.dumps(urls))
    return urls


def _serper(query, pages, month, key):
    out = []
    for p in range(1, pages + 1):
        _pace("search")
        STATS["requests"] += 1
        body = {"q": query, "num": 10, "page": p}    # num above 10 costs 2 credits
        if month:
            body["tbs"] = "qdr:m"
        r = requests.post("https://google.serper.dev/search", json=body, timeout=20,
                          headers={"X-API-KEY": key, "Content-Type": "application/json"})
        if r.status_code in (401, 403, 429):
            raise Blocked(f"Serper {r.status_code}: check the key or the credit", "search")
        out += [o.get("link", "") for o in r.json().get("organic", [])]
    return [u for u in dict.fromkeys(out) if u]


def _ddg(query, pages, month):
    out, data = [], {"q": query, "kl": "wt-wt"}
    if month:
        data["df"] = "m"
    for _ in range(pages):
        for attempt in (1, 2):
            _pace("search")
            STATS["requests"] += 1
            try:
                r = requests.post("https://html.duckduckgo.com/html/", data=data,
                                  headers=HDR, timeout=20)
            except requests.RequestException:
                r = None
            challenged = (r is None or r.status_code != 200 or
                          "anomaly" in r.text.lower()[:20000] or
                          "challenge" in r.url)
            if not challenged:
                break
            if attempt == 1:
                print("    Search engine paused us. Waiting 120s once...")
                time.sleep(120)
                continue
            raise Blocked("DuckDuckGo challenge. Try again later, or add "
                          "SERPER_API_KEY to .env", "search")
        page = r.text
        for enc in re.findall(r'uddg=([^&"\']+)', page):
            out.append(urllib.parse.unquote(enc))
        for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', page):
            if href.startswith("http"):
                out.append(html.unescape(href))
        form = re.search(r'<form[^>]*action="/html/"[^>]*>((?:(?!</form>).)*?'
                         r'value="Next"(?:(?!</form>).)*)</form>', page, re.S)
        if not form:
            break
        data = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
                               form.group(1)))
        data = {k: html.unescape(v) for k, v in data.items()}
    return [u.split("?")[0] if "linkedin.com" in u else u
            for u in dict.fromkeys(out)]


# ------------------------------------------------------------------ helpers
def clean(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(str(s or "")))
    return re.sub(r"\s+", " ", s).strip()


def norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def company_url(u):
    """Canonical company page URL: www host, no query, no trailing path."""
    m = re.search(r"linkedin\.com/company/([^/?#\"]+)", u or "")
    return f"https://www.linkedin.com/company/{m.group(1)}" if m else ""


def activity_date(url):
    """LinkedIn post and activity ids carry their own timestamp.

    The top 41 bits of the 19-digit id are milliseconds since the epoch, so
    the post date is exact without even opening the page.
    """
    m = re.search(r"(?:activity|ugcPost|share)[-:](\d{18,20})", url or "")
    if not m:
        return None
    try:
        return datetime.datetime.utcfromtimestamp((int(m.group(1)) >> 22) / 1000).date()
    except (ValueError, OverflowError, OSError):
        return None


def age_days(d):
    if isinstance(d, str):
        try:
            d = datetime.date.fromisoformat(d[:10])
        except ValueError:
            return 999
    return (TODAY - d).days if d else 999


def size_range(txt):
    """'11-50 employees' -> (11, 50). '10,001+ employees' -> (10001, 10**7)."""
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", txt or "")]
    if not nums:
        return None
    return (nums[0], nums[1] if len(nums) > 1 else 10 ** 7)


def matches_any(text, words):
    t = (text or "").lower()
    return next((w for w in words if w.lower() in t), "")


def matches_word(text, words, whole=False):
    """Like matches_any, but a word must START at a word boundary.

    'recruit' still matches 'Recruitment', but 'jobs' no longer matches inside
    'Steve Jobs' and 'careers' only counts where it is a word of its own.
    whole=True also demands the END boundary: 'Meta' must not catch 'Metamorph'.
    """
    t = (text or "").lower()
    tail = r"(?![a-z0-9])" if whole else ""
    return next((w for w in words
                 if re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + tail, t)), "")


# ------------------------------------------------------------------ parsers
def parse_job_cards(page):
    """The public job search returns <li> cards. One dict per job."""
    jobs = []
    for card in re.split(r"<li[\s>]", page)[1:]:
        jid = re.search(r"urn:li:jobPosting:(\d+)", card)
        title = re.search(r'base-search-card__title[^>]*>(.*?)</h3>', card, re.S)
        if not (jid and title):
            continue
        sub = re.search(r'base-search-card__subtitle[^>]*>(.*?)</h4>', card, re.S)
        curl = re.search(r'href="(https://[^"]*linkedin\.com/company/[^"?]+)', card)
        loc = re.search(r'job-search-card__location[^>]*>(.*?)</span>', card, re.S)
        date = re.search(r'<time[^>]*datetime="([\d-]+)"', card)
        sal = re.search(r'job-search-card__salary-info[^>]*>(.*?)</span>', card, re.S)
        jobs.append({
            "id": jid.group(1),
            "title": clean(title.group(1)),
            "company": clean(sub.group(1)) if sub else "",
            "company_url": company_url(curl.group(1)) if curl else "",
            "location": clean(loc.group(1)) if loc else "",
            "posted": date.group(1) if date else "",
            "salary": clean(sal.group(1)) if sal else "",
            "link": f"https://www.linkedin.com/jobs/view/{jid.group(1)}",
        })
    return jobs


def parse_job_detail(page):
    out = {}
    d = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
    if d:
        out["description"] = clean(re.sub(r"<(br|/p|/li)[^>]*>", "\n", d.group(1)))[:4000]
    for k, v in re.findall(r'job-criteria-subheader[^>]*>\s*(.*?)\s*</h3>\s*'
                           r'<span[^>]*>\s*(.*?)\s*</span>', page, re.S):
        out[clean(k).lower().replace(" ", "_")] = clean(v)
    a = re.search(r'num-applicants__caption[^>]*>\s*(.*?)\s*<', page, re.S)
    if a:
        out["applicants"] = clean(a.group(1))
    s = re.search(r'compensation__salary[^>]*>\s*(.*?)\s*<', page, re.S)
    if s:
        out["salary"] = clean(s.group(1))
    return out


def parse_company(page):
    """Public company page -> facts. Every field is read, none inferred."""
    if not page:
        return {}
    out = {}
    for label, key in (("Website", "website"), ("Industry", "industry"),
                       ("Company size", "size"), ("Headquarters", "hq"),
                       ("Type", "type"), ("Founded", "founded")):
        m = re.search(r"<dt[^>]*>\s*" + label + r"\s*</dt>\s*<dd[^>]*>(.*?)</dd>",
                      page, re.S)
        if m:
            out[key] = clean(m.group(1)).replace("External link for", "").strip()
    if out.get("website"):
        out["website"] = out["website"].split(" ")[0]
    t = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    if t:
        out["name"] = clean(t.group(1)).split(" | ")[0]
    d = re.search(r'about-us__description[^>]*>(.*?)</p>', page, re.S)
    if d:
        out["about"] = clean(d.group(1))[:600]
    f = re.search(r"([\d,.]+K?M?)\s+followers", page)
    if f:
        out["followers"] = f.group(1)
    dates = [activity_date(u) for u in
             re.findall(r"linkedin\.com/posts/[^\"'\s]+", page)]
    dates = [d for d in dates if d]
    if dates:
        out["last_post"] = max(dates).isoformat()
    return out


def parse_post(page):
    """Public post page -> text, date, author. ld+json is LinkedIn's own markup."""
    out = {}
    for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                           page or "", re.S):
        try:
            j = json.loads(blob)
        except ValueError:
            continue
        if not isinstance(j, dict) or not (j.get("articleBody") or j.get("text")):
            continue
        a = j.get("author") or {}
        stats = a.get("interactionStatistic") or {}
        out = {
            "text": (j.get("articleBody") or j.get("text") or "")[:3000],
            "headline": j.get("headline", "")[:200],
            "date": (j.get("datePublished") or "")[:10],
            "author": a.get("name", ""),
            "author_url": (a.get("url") or "").split("?")[0],
            "author_type": a.get("@type", ""),
            "followers": stats.get("userInteractionCount", "") if isinstance(stats, dict) else "",
        }
        break
    return out


# ------------------------------------------------------------------ judgement
# Monarc sells AI-GENERATED ads: AI ads, AI commercials, AI films, AI UGC, AI
# product ads, AI launch ads. Editing is a side line. So a role or post passes
# the topic gate if it is about ad/video creative at all, but the SCORE is
# what separates "wants AI generation" (top) from "wants an editor" (bottom).
ROLE_RX = re.compile(
    r"video|ugc|motion|reel|short[- ]?form|animat|creative strateg|commercial|"
    r"performance creative|content (creator|producer)|videograph|film|"
    r"ai (ads?|content|creative|generalist|producer|artist|creator)|ad creative|"
    r"creative producer|launch (video|ad|film)|product (video|ad|film)|generative",
    re.I)
EDIT_ONLY = re.compile(r"\beditor\b|editing|post[- ]production|colou?rist", re.I)
WRONG_ROLE = re.compile(
    r"engineer|developer|software|sales|account (executive|manager)|nurse|"
    r"teacher|tutor|driver|accountant|data |analyst|devops|java|python|"
    r"recruiter|security|surveillance|cctv|game|unity|unreal|3d artist|vfx compositor|"
    r"scientist|research|machine learning|\bml\b|infrastructure|platform|"
    r"youtuber|educator|instructor|professor|edge computing|"
    r"commercial (leader|lead|manager|director|head|officer|excellence|operations|"
    r"analyst|finance|counsel|partner)|chief commercial|commercial real estate|"
    # product / UI design and big-studio craft roles that "motion" lets in
    r"product designer|\bui\b|\bux\b|supervisor|content designer|\bstaff\b|principal|"
    r"modell?ing|rigging|character (artist|modell?er)|technical director",
    re.I)
AI_GEN = re.compile(r"\bai\b|a\.i\.|gen ?ai|generative|ai[- ]generated|runway|kling|"
                    r"\bveo\b|seedance|higgsfield|heygen|midjourney|\bsora\b|synthesia|"
                    r"arcads|creatify|pika|luma", re.I)
UGC = re.compile(r"\bugc\b|user[- ]generated", re.I)
PRODUCT_LINE = re.compile(r"commercial|launch (video|ad|film)|product (video|ad|film)|"
                          r"\bfilm\b|brand film|\bads?\b|ad creative", re.I)
OUTSIDE_HELP = re.compile(r"freelanc|contract|part[- ]time|project[- ]based|"
                          r"per video|remote|agency|outsourc|retainer", re.I)

BUYER = re.compile(
    r"\b(looking for|we'?re looking|hiring|we'?re hiring|is hiring|need(ed|s)?|"
    r"seeking|searching for|in search of|recommend(ations?)?|anyone know|"
    r"who knows|on the hunt for|wanted|want to hire|open role|join (our|my) team)\b",
    re.I)
# Everything that is NOT a buyer, in four families. Tested against the labelled
# posts in li_selftest.py: change a pattern, run `python run.py audit`.
SEEKER = re.compile(
    # 1. job seekers: "my next role", "I'm an AI UGC creator", "open to work"
    r"#opentowork|open to work|open for work|open to (new )?(roles|opportunit|freelance)|"
    r"available for (new )?(projects|work|hire|freelance|roles)|hire me|i('m| am) available|"
    r"\b(my|a) next (role|job|position|opportunit|gig)|"
    r"(i'?m|i am) (looking|searching|seeking) for [^.\n]{0,45}\b(jobs?|gigs?|internships?|positions?)\b|"
    r"(i'?m|i am) (looking|searching) for (a |an |my )?(new |next )?(role|opportunit|work\b|clients|projects)|"
    r"looking for (new |more |my )?(clients|projects|opportunit|gigs?|internships?|collaborations?|a job\b)|"
    r"seeking (a |an |my )?(new |next )?(job|role|position|opportunit|employment|internship)|"
    r"(i'?m|i am) an? [^.\n]{0,40}(creator|editor|designer|filmmaker|artist|animator|producer|strategist)\b|"
    # 2. sellers: freelancers and agencies offering the service with buyer words
    r"my services|our services|we offer|offering (my|our)|my portfolio|check out my|"
    r"\bi (help|edit|craft) |\bwe (help|craft) |"
    r"\b(i|we) (create|make|produce|deliver|build) (ai |ugc|video|content|ads?\b|"
    r"commercials|films|reels|creatives|scroll)|"
    r"looking for \d+ [^.\n]{0,25}(brands|clients|businesses|companies|founders)|"
    r"(brands|founders|businesses|companies) to (try|test|work with|partner)|"
    # "our AI video agency" sells; "recommend a good AI video agency" buys
    r"we('| a)re (a|an) [^.\n]{0,40}(agency|studio)|(our|my) (ai ugc|ai ads?|ai video) (agency|studio|service)|"
    r"dm me (for|to get|if)|book a (free )?call|let'?s work together|slots? (open|available)|"
    r"(comment|dm) [\"'“]?\w+[\"'”]? (below|and i|to get)|(link|sample) in (bio|comments)|"
    # 3. recruiters posting for someone else's company
    r"for (our|a|my) client|on behalf of|client of ours|recruiting for|talent partner|"
    # 4. hooks that use buyer words to sell: "Stop hiring video editors",
    # "You don't need a production crew", "Why every brand needs AI UGC"
    r"(stop|don'?t|no more|instead of|without|quit|never) (hiring|paying|looking|needing)|"
    r"(you|brands?|businesses|everyone|companies) (don'?t|no longer) need|"
    r"why (every|your|most) |here'?s (why|what|how)|\d+ (reasons|ways|tips|mistakes|lessons)",
    re.I)
# A real ask. Bare "need" is not enough: "every brand needs AI UGC" is a pitch.
STRONG_ASK = re.compile(
    r"\b(looking for|we'?re looking|i'?m looking|hiring|seeking|searching for|"
    r"in search of|recommend(ations?)?|anyone know|who knows|want to hire|"
    r"on the hunt for|open role|(we|i) need (an?|some|someone|help)|need (an?|someone)\b)",
    re.I)
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

FOREIGN = re.compile(
    r"united states|usa|\bus\b|united kingdom|\buk\b|england|london|scotland|"
    r"united arab emirates|uae|dubai|abu dhabi|sharjah|australia|sydney|"
    r"melbourne|brisbane|perth|new york|california|texas|florida|"
    r"\b[A-Z]{2}\b, United States", re.I)


def market_of(location):
    loc = location or ""
    if re.search(r"india|chennai|bengaluru|bangalore|mumbai|delhi|hyderabad|pune|"
                 r"kolkata|noida|gurugram|gurgaon|ahmedabad|coimbatore|kochi", loc, re.I):
        return "india"
    if re.search(r"remote|worldwide|anywhere", loc, re.I):
        return "remote"
    if FOREIGN.search(loc):
        return "foreign"
    return "other"


def company_kind(name, industry, about, cfg):
    """agency / brand / staffing / vendor / other. Staffing and vendors are never leads.

    Skip words are matched on the NAME and INDUSTRY only. Matched against the
    'about' text, "careers" in Apple's description made Apple a staffing firm.
    """
    if matches_word(name, cfg.get("never_companies", []), whole=True):
        return "vendor"
    if matches_word(f"{name} | {industry}", cfg["skip_words"]):
        return "staffing"
    if matches_any(industry, cfg["agency_industries"]) or re.search(
            r"\bagency\b|\bmedia\b|\bstudios?\b|marketing|advertis|creative|digital|"
            r"growth|\bads\b|performance", f"{name} {industry}", re.I):
        return "agency"
    if matches_any(industry, cfg["brand_industries"]) or re.search(
            r"d2c|dtc|direct.to.consumer|shopify|ecommerce|e-commerce|brand", about or "",
            re.I):
        return "brand"
    return "other"


def need_phrase(text):
    """What the post asked for, in their words: 'an AI video editor'."""
    for sent in re.split(r"(?<=[.!?\n])\s+", text or ""):
        m = re.search(r"\b(looking for|hiring|need|seeking|searching for|in search of)\s+"
                      r"((?:an?|some|1|2|3|two|three|a few|experienced|talented|"
                      r"freelance|skilled)?\s*[^.,!?\n:;()]{3,70})", sent, re.I)
        if m and ROLE_RX.search(m.group(2)):
            p = re.split(r"\s+(who|that|with|to|for|in|on|at|and|asap|-)\s+",
                         m.group(2).strip(), maxsplit=1)[0]
            words = p.split()
            if 1 <= len(words) <= 8:
                return " ".join(words).rstrip(" !")
    return ""


def score_job(j, co, cfg):
    """0-100. Why each weight: see the comments, all tied to 'will they buy'."""
    s, why = 0, []
    t = j.get("title", "")
    desc = j.get("description", "")
    if AI_GEN.search(t):
        s += 30; why.append("AI generation role")    # exactly what Monarc makes
    elif UGC.search(t):
        s += 18; why.append("UGC role")              # AI UGC replaces it
    elif PRODUCT_LINE.search(t):
        s += 10; why.append("ads/film role")
    else:
        s += 3
    if EDIT_ONLY.search(t) and not AI_GEN.search(t):
        s -= 10; why.append("editing role (low fit)")  # editing is a side line
    blob = f"{t} {j.get('employment_type', '')} {desc[:1500]}"
    if OUTSIDE_HELP.search(blob):
        s += 10; why.append("open to outside help")  # contract / remote / agency
    if AI_GEN.search(desc) and not AI_GEN.search(t):
        s += 10; why.append("AI tools in description")
    a = age_days(j.get("posted"))
    s += 15 if a <= 3 else 10 if a <= 7 else 5 if a <= 14 else 0
    kind = co.get("kind") or "other"
    s += {"agency": 20, "brand": 20, "other": 6}.get(kind, -100)
    if kind in ("agency", "brand"):
        why.append(kind)
    sz = size_range(co.get("size"))
    if sz:
        s += 14 if sz[1] <= 50 else 11 if sz[1] <= 200 else 4 if sz[1] <= 1000 else -6
    else:
        s += 6
    if j.get("market") in ("foreign", "remote"):
        s += 8; why.append(j["market"])               # dollar budgets
    if re.search(r"first 25|under 10|\b([0-9]|1[0-9]) applicants", j.get("applicants", ""), re.I):
        s += 4
    return max(0, min(100, s)), why


def score_post(p):
    s, why = 25, []
    t = p.get("text", "")
    if AI_GEN.search(t):
        s += 22; why.append("asks for AI")
    if UGC.search(t) or PRODUCT_LINE.search(t):
        s += 10; why.append("UGC/ads/film")
    if EDIT_ONLY.search(t) and not AI_GEN.search(t):
        s -= 12; why.append("editing only (low fit)")
    if re.search(r"dtc|d2c|brand|ecom|shopify|meta ads|tiktok|launch", t, re.I):
        s += 8; why.append("brand context")
    if OUTSIDE_HELP.search(t):
        s += 8; why.append("freelance/remote ok")
    if p.get("emails"):
        s += 10; why.append("email in post")
    a = age_days(p.get("date"))
    s += 20 if a <= 3 else 14 if a <= 7 else 7 if a <= 14 else 0
    return max(0, min(100, s)), why


def score_company(co):
    s, why = 25, []
    kind = co.get("kind")
    s += {"agency": 22, "brand": 22}.get(kind, 0)
    sz = size_range(co.get("size"))
    if sz:
        s += 16 if 2 <= sz[1] <= 50 else 12 if sz[1] <= 200 else 5 if sz[1] <= 500 else -10
    a = age_days(co.get("last_post"))
    if a <= 14:
        s += 12; why.append("active on LinkedIn")
    elif a <= 60:
        s += 6
    if co.get("website"):
        s += 8
    if co.get("hiring"):
        s += 15; why.append("also hiring")
    return max(0, min(100, s)), why


def tier(score):
    return "A" if score >= 70 else "B" if score >= 50 else "C"
