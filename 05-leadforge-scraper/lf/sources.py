"""
LeadForge :: source adapters.

Every adapter is a plain generator yielding normalized "raw records":

    {source, external_id, url, title, body, author, company, domain,
     location, posted_at, raw}

Adapters never score or store; that is the pipeline's job.

Platform bonus (0-5) reflects how much the venue itself implies a willingness to
pay: a company posting a paid job ad or a paid board listing is a stronger buyer
than an anonymous forum comment.

Everything in here works with NO API key. Reddit and Meta Ad Library are
optional upgrades that light up when you add credentials to config.toml.

Two of them also have key-free open equivalents that need no credentials at all:
  * reddit_rss      - Reddit read through public Atom feeds (buyer posts only)
  * meta_ads_open   - live Meta advertisers scraped from the public Ad Library
  * google_maps     - local businesses, richer than OSM (phone, website, ratings)
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .core import (
    Http, BOT_UA, find_emails, fingerprint, iso, parse_when, registrable_domain,
    strip_html, utcnow,
)

MAX_BODY = 8000

# The hosting domain of each source. If a record's URL resolves to one of
# these, it is the SOURCE's own site, not the prospect's - never treat
# "https://jobicy.com/jobs/123" as company domain "jobicy.com".
SOURCE_OWN_DOMAINS = {
    "jobicy": {"jobicy.com"},
    "remoteok": {"remoteok.com"},
    "remotive": {"remotive.com"},
    "arbeitnow": {"arbeitnow.com"},
    "himalayas": {"himalayas.app"},
    "workingnomads": {"workingnomads.com"},
    "weworkremotely": {"weworkremotely.com"},
    "linkedin_jobs": {"linkedin.com"},
    "producthunt": {"producthunt.com"},
    "hn_search": {"ycombinator.com"},
    "hn_whoishiring": {"ycombinator.com"},
    "reddit": {"reddit.com", "redd.it"},
    "meta_ads": {"facebook.com", "fb.com"},
    "bingnews": {"bing.com"},
    "websearch": {"bing.com", "microsoft.com", "google.com"},
}


# --------------------------------------------------------------------------
# Record factory
# --------------------------------------------------------------------------


def make_record(
    source: str,
    *,
    external_id: str | None = None,
    url: str | None = None,
    title: str = "",
    body: str = "",
    author: str | None = None,
    company: str | None = None,
    domain: str | None = None,
    location: str | None = None,
    posted=None,
    raw: dict | None = None,
) -> dict:
    title = _clean(title)[:500]
    body = _clean(body)[:MAX_BODY]
    dt = parse_when(posted)
    if domain is None:
        domain = registrable_domain(url or "") or None
    if domain in SOURCE_OWN_DOMAINS.get(source, ()):
        domain = None
    if domain is None and company:
        cand = registrable_domain(company)
        # only accept a company-derived domain if it really looks like one
        if cand and not re.search(r"\s", company) and re.search(r"\.", company):
            domain = cand
    return {
        "source": source,
        "external_id": str(external_id) if external_id is not None else None,
        "url": url,
        "title": title,
        "body": body,
        "author": (author or None) and str(author)[:120],
        "company": (company or None) and _clean(str(company))[:180] or None,
        "domain": domain,
        "location": (location or None) and _clean(str(location))[:120] or None,
        "posted_ts": int(dt.timestamp()) if dt else None,
        "posted_at": iso(dt),
        "discovered_at": iso(utcnow()),
        "fingerprint": fingerprint(source, external_id, url, title + body[:200]),
        "raw": raw or {},
    }


def _clean(s: str) -> str:
    s = strip_html(s or "")
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return default


# --------------------------------------------------------------------------
# Feed parsing (RSS 2.0 + Atom, namespace agnostic)
# --------------------------------------------------------------------------


def parse_feed(data: bytes) -> list[dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items: list[dict] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        rec: dict = {}
        for child in list(node):
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            if ctag == "link":
                rec["link"] = (child.get("href") or child.text or "").strip() or rec.get("link")
            elif ctag in ("title", "description", "summary", "content", "encoded",
                          "pubdate", "published", "updated", "date", "creator",
                          "author", "name", "guid", "id", "category"):
                val = (child.text or "").strip()
                if ctag == "link" and child.get("href"):
                    val = child.get("href")
                if ctag not in rec or (val and not rec.get(ctag)):
                    rec[ctag] = val
        if rec:
            items.append(rec)
    return items


def feed_records(source: str, data: bytes, company_hint: str | None = None,
                 limit: int = 200, base_url: str | None = None) -> Iterator[dict]:
    for it in parse_feed(data)[:limit]:
        link = it.get("link") or it.get("guid") or it.get("id") or ""
        if link and base_url and link.startswith("/"):
            link = base_url.rstrip("/") + link
        body = it.get("content") or it.get("encoded") or it.get("description") or it.get("summary") or ""
        yield make_record(
            source,
            external_id=it.get("guid") or it.get("id") or link,
            url=link or None,
            title=it.get("title", ""),
            body=body,
            author=it.get("creator") or it.get("author") or it.get("name"),
            company=company_hint or it.get("author") or it.get("name"),
            location=it.get("category"),
            posted=it.get("pubdate") or it.get("published") or it.get("updated") or it.get("date"),
            raw=it,
        )


def _queries(cfg, name: str, default: list[str]) -> list[str]:
    q = (cfg.sources.get(name) or {}).get("queries")
    if q:
        return [str(x) for x in q]
    return list(default) if default else list(DEFAULT_QUERIES)


def _enabled(cfg, name: str, default: bool = True) -> bool:
    return bool((cfg.sources.get(name) or {}).get("enabled", default))


# Fallback query set used by job boards when config.toml has no explicit list.
# These are the phrases that live in the JOB TITLE of a buyer's posting.
DEFAULT_QUERIES = [
    "video editor", "video producer", "creative producer", "motion designer",
    "ugc creator", "content creator", "video marketing", "paid social creative",
    "brand video", "product video", "animator", "3d web", "creative director",
]


# ==========================================================================
# 1. HACKER NEWS  -- highest signal density of any free source
# ==========================================================================


def src_hackernews(http: Http, cfg, since_ts: int | None = None, limit=200) -> Iterator[dict]:
    per = min(limit, 100)

    # -- (a) the monthly "Freelancer? Seeking freelancer?" hiring thread -----
    if _enabled(cfg, "hn_whoishiring"):
        try:
            # NOTE: the monthly thread is NOT always posted by "whoishiring"
            # (Oct 2026 was posted by "jon_north"), so search by title, not author.
            data = http.get_json(
                "https://hn.algolia.com/api/v1/search_by_date"
                "?query=Freelancer%20Seeking%20freelancer&tags=story&hitsPerPage=15",
                ttl_s=6 * 3600,
            )
            for hit in data.get("hits", []):
                title = (hit.get("title") or "")
                if "seeking freelancer" not in title.lower():
                    continue
                sid = hit.get("objectID")
                comments = http.get_json(
                    f"https://hn.algolia.com/api/v1/search?tags=comment,story_{sid}"
                    f"&hitsPerPage=1000",
                    ttl_s=3 * 3600,
                )
                for c in comments.get("hits", []):
                    text = _clean(c.get("comment_text") or "")
                    if not text:
                        continue
                    # only the buyer half of the thread
                    if not re.match(r"(?i)\s*(seeking freelancer|seeking freelancers)", text):
                        continue
                    yield make_record(
                        "hn_whoishiring",
                        external_id=c.get("objectID"),
                        url=f"https://news.ycombinator.com/item?id={c.get('objectID')}",
                        title=f"HN freelance thread: {title}",
                        body=text,
                        author=c.get("author"),
                        posted=c.get("created_at_i"),
                        raw={"story_id": sid, "thread": title},
                    )
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"hn_whoishiring: {e}"}

    # -- (b) free-text comment search across all of HN ----------------------
    if _enabled(cfg, "hn_search"):
        for q in _queries(cfg, "hn_search", []):
            url = ("https://hn.algolia.com/api/v1/search_by_date?tags=comment"
                   f"&query={urllib.parse.quote(q)}&hitsPerPage={per}")
            if since_ts:
                url += f"&numericFilters=created_at_i>{since_ts}"
            try:
                data = http.get_json(url, ttl_s=1800)
            except Exception as e:  # noqa: BLE001
                yield {"_error": f"hn_search[{q}]: {e}"}
                continue
            for h in data.get("hits", []):
                yield make_record(
                    "hn_search",
                    external_id=h.get("objectID"),
                    url=f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    title=h.get("story_title") or f"HN comment on: {q}",
                    body=h.get("comment_text") or "",
                    author=h.get("author"),
                    posted=h.get("created_at_i"),
                    raw={"query": q, "story_url": h.get("story_url")},
                )


# ==========================================================================
# 2. LINKEDIN -- public guest jobs endpoint (no login, no key)
# ==========================================================================

_LI_TITLE = re.compile(r'class="base-search-card__title"[^>]*>\s*(.*?)\s*</h3>', re.S)
_LI_SUB = re.compile(r'class="base-search-card__subtitle"[^>]*>\s*(?:<a[^>]*>)?\s*(.*?)\s*</(?:a|h4)>', re.S)
_LI_LOC = re.compile(r'class="job-search-card__location"[^>]*>\s*(.*?)\s*</span>', re.S)
_LI_LINK = re.compile(r'href="(https://[a-z]{2,3}\.?linkedin\.com/jobs/view/[^"]+)"')
_LI_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_LI_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})")


def src_linkedin_jobs(http: Http, cfg, since_ts: int | None = None, limit=200) -> Iterator[dict]:
    loc = (cfg.sources.get("linkedin_jobs") or {}).get("location", "United States")
    queries = _queries(cfg, "linkedin_jobs", [])
    pages = int((cfg.sources.get("linkedin_jobs") or {}).get("pages", 2))
    per_page = 25
    got = 0
    for q in queries:
        for page in range(pages):
            url = (
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
                + urllib.parse.urlencode({
                    "keywords": q, "location": loc, "start": page * per_page,
                    "f_TPR": "r604800",  # last 7 days
                })
            )
            try:
                html = http.get_text(url, accept="text/html", ttl_s=3600)
            except Exception as e:  # noqa: BLE001
                yield {"_error": f"linkedin_jobs[{q} p{page}]: {e}"}
                break
            if not html.strip():
                break
            # split into cards so fields line up
            chunks = re.split(r'(?=<li>)', html)
            found_this_page = 0
            for ch in chunks:
                link_m = _LI_LINK.search(ch)
                if not link_m:
                    continue
                title_m = _LI_TITLE.search(ch)
                sub_m = _LI_SUB.search(ch)
                loc_m = _LI_LOC.search(ch)
                time_m = _LI_TIME.search(ch)
                link = link_m.group(1).split("?")[0]
                idm = _LI_ID.search(link)
                company = _clean(sub_m.group(1)) if sub_m else None
                found_this_page += 1
                got += 1
                yield make_record(
                    "linkedin_jobs",
                    external_id=idm.group(1) if idm else link,
                    url=link,
                    title=_clean(title_m.group(1)) if title_m else f"LinkedIn: {q}",
                    body=_clean((title_m.group(1) if title_m else "") + "\n" +
                                (loc_m.group(1) if loc_m else "")),
                    company=company,
                    domain=registrable_domain(company) if company and "." in company else None,
                    location=_clean(loc_m.group(1)) if loc_m else loc,
                    posted=time_m.group(1) if time_m else None,
                    raw={"query": q, "page": page, "company": company},
                )
                if got >= limit:
                    return
            if found_this_page == 0:
                break


# ==========================================================================
# 3-7. OPEN JOB BOARD APIs (free, no key)
# ==========================================================================


def src_remoteok(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    data = http.get_json("https://remoteok.com/api", ttl_s=3600)
    n = 0
    for job in data:
        if not isinstance(job, dict) or "legal" in job and len(job) <= 2:
            continue
        if not job.get("position"):
            continue
        salary = ""
        if job.get("salary_min"):
            salary = f"Salary: ${job.get('salary_min'):,}-${job.get('salary_max', 0):,}\n"
        n += 1
        yield make_record(
            "remoteok",
            external_id=job.get("id") or job.get("slug"),
            url=job.get("url") or job.get("apply_url"),
            title=job.get("position", ""),
            body=salary + (job.get("description") or "") + " " + " ".join(
                job.get("tags") or []),
            company=job.get("company"),
            location=job.get("location"),
            posted=job.get("date") or job.get("epoch"),
            raw={k: job.get(k) for k in ("id", "slug", "company", "position",
                                          "tags", "location", "date", "salary_min", "salary_max")},
        )
        if n >= limit:
            return


def src_remotive(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    n = 0
    for q in _queries(cfg, "remotive", []):
        try:
            data = http.get_json(
                "https://remotive.com/api/remote-jobs?limit=50&search="
                + urllib.parse.quote(q), ttl_s=3600)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"remotive[{q}]: {e}"}
            continue
        for job in data.get("jobs", []):
            n += 1
            yield make_record(
                "remotive",
                external_id=job.get("id"),
                url=job.get("url"),
                title=job.get("title", ""),
                body=(job.get("salary") or "") + "\n" + (job.get("description") or ""),
                company=job.get("company_name"),
                location=job.get("candidate_required_location"),
                posted=job.get("publication_date"),
                raw={"query": q, "category": job.get("category"), "type": job.get("job_type")},
            )
            if n >= limit:
                return


def src_jobicy(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    n = 0
    for q in _queries(cfg, "jobicy", []):
        try:
            data = http.get_json(
                "https://jobicy.com/api/v2/remote-jobs?count=50&tag="
                + urllib.parse.quote(q), ttl_s=3600)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"jobicy[{q}]: {e}"}
            continue
        for job in data.get("jobs", []):
            n += 1
            sal = ""
            if job.get("annualSalaryMin"):
                sal = (f"Salary: {job.get('salaryCurrency','USD')} "
                       f"{job.get('annualSalaryMin')}-{job.get('annualSalaryMax')}\n")
            yield make_record(
                "jobicy",
                external_id=job.get("id"),
                url=job.get("url"),
                title=job.get("jobTitle", ""),
                body=sal + (job.get("jobDescription") or job.get("jobExcerpt") or ""),
                company=job.get("companyName"),
                location=job.get("jobGeo"),
                posted=job.get("pubDate"),
                raw={"query": q, "category": job.get("jobCategory"),
                     "type": job.get("jobType")},
            )
            if n >= limit:
                return


def src_arbeitnow(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    try:
        data = http.get_json("https://www.arbeitnow.com/api/job-board-api", ttl_s=3600)
    except Exception as e:  # noqa: BLE001
        yield {"_error": f"arbeitnow: {e}"}
        return
    n = 0
    for job in data.get("data", []):
        n += 1
        yield make_record(
            "arbeitnow",
            external_id=job.get("slug"),
            url=job.get("url"),
            title=job.get("title", ""),
            body=(job.get("description") or "") + " " + " ".join(job.get("tags") or []),
            company=job.get("company_name"),
            location=job.get("location"),
            posted=(job.get("created_at") if isinstance(job.get("created_at"), int)
                    else job.get("created_at")),
            raw={"types": job.get("job_types"), "remote": job.get("remote")},
        )
        if n >= limit:
            return


def src_himalayas(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    try:
        data = http.get_json("https://himalayas.app/jobs/api?limit=100", ttl_s=3600)
    except Exception as e:  # noqa: BLE001
        yield {"_error": f"himalayas: {e}"}
        return
    jobs = data.get("jobs") or data.get("data") or []
    for i, job in enumerate(jobs[:limit]):
        if not isinstance(job, dict):
            continue
        yield make_record(
            "himalayas",
            external_id=_pick(job, "id", "guid", "slug", "applicationLink"),
            url=_pick(job, "applicationLink", "url", "guid"),
            title=_pick(job, "title", "jobTitle", default=""),
            body=_pick(job, "description", "jobDescription", "excerpt", default=""),
            company=_pick(job, "companyName", "company_name", "company"),
            location=_pick(job, "locationRestrictions", "location", "timezoneRestrictions"),
            posted=_pick(job, "pubDate", "publishedDate", "createdAt"),
            raw={"index": i},
        )


# ==========================================================================
# 8-9. RSS FEEDS
# ==========================================================================

_WWR_CATS = [
    "remote-design-jobs", "remote-marketing-jobs", "remote-product-jobs",
    "remote-all-other-remote-jobs", "remote-sales-and-marketing-jobs",
]


def src_weworkremotely(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    cats = (cfg.sources.get("weworkremotely") or {}).get("categories", _WWR_CATS)
    n = 0
    for cat in cats:
        try:
            data = http.get(
                f"https://weworkremotely.com/categories/{cat}.rss",
                accept="application/rss+xml,text/xml,*/*", ttl_s=3600)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"weworkremotely[{cat}]: {e}"}
            continue
        for rec in feed_records("weworkremotely", data, limit=60,
                                base_url="https://weworkremotely.com"):
            rec["raw"] = {"category": cat}
            n += 1
            yield rec
            if n >= limit:
                return


def src_workingnomads(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    try:
        data = http.get_json("https://www.workingnomads.com/api/exposed_jobs/",
                             ttl_s=3600)
    except Exception as e:  # noqa: BLE001
        yield {"_error": f"workingnomads: {e}"}
        return
    if not isinstance(data, list):
        yield {"_error": "workingnomads: unexpected payload"}
        return
    for job in data[:limit]:
        if not isinstance(job, dict):
            continue
        yield make_record(
            "workingnomads",
            external_id=hashlib.sha1((job.get("url") or "").encode()).hexdigest()[:16],
            url=job.get("url"),
            title=job.get("title", ""),
            body=(job.get("description") or "") + " " + " ".join(job.get("tags") or []),
            company=job.get("company_name"),
            location=job.get("location"),
            posted=job.get("pub_date"),
            raw={"category": job.get("category_name"), "tags": job.get("tags")},
        )


# ==========================================================================
# 10. PRODUCT HUNT -- every launch needs a promo video
# ==========================================================================


def src_producthunt(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    try:
        data = http.get("https://www.producthunt.com/feed",
                        accept="application/atom+xml,application/rss+xml,*/*", ttl_s=3600)
    except Exception as e:  # noqa: BLE001
        yield {"_error": f"producthunt: {e}"}
        return
    n = 0
    for rec in feed_records("producthunt", data, base_url="https://www.producthunt.com"):
        rec["title"] = "NEW LAUNCH: " + (rec.get("title") or "")
        n += 1
        yield rec
        if n >= limit:
            return


# ==========================================================================
# 11. BING NEWS RSS -- free RSS for any arbitrary query
# ==========================================================================


def src_bingnews(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    n = 0
    for q in _queries(cfg, "bingnews", []):
        try:
            data = http.get(
                "https://www.bing.com/news/search?format=rss&q=" + urllib.parse.quote(q),
                accept="application/rss+xml,text/xml,*/*", ttl_s=3600)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"bingnews[{q}]: {e}"}
            continue
        for rec in feed_records("bingnews", data, limit=40):
            rec["raw"] = {"query": q}
            n += 1
            yield rec
            if n >= limit:
                return


# ==========================================================================
# 12. WEB SEARCH (Bing RSS) -- best-effort discovery of buyer phrases
#
# Transport note: we use Bing's RSS endpoint (clean XML) rather than scraping
# the HTML SERP. Same data, no brittle parsing.
#
# Reality check: many search engines serve a DEGRADED SERP to datacenter /
# proxy / VPN IPs - they match the first keyword and ignore quotes, operators
# and the rest of the phrase. Rather than store that poison, every hit must
# corroborate the query (see _relevant). If nothing corroborates, we report it
# instead of inventing leads. From a normal home connection this source works
# well; from a rented IP it may legitimately return nothing. It is therefore
# DISABLED by default in config.toml.
# ==========================================================================

AGGREGATOR_HOSTS = {
    "jooble", "indeed", "ziprecruiter", "glassdoor", "simplyhired", "talent.com",
    "monster", "careerbuilder", "snagajob", "dice", "upwork", "fiverr",
    "freelancer.com", "peopleperhour", "guru.com", "toptal", "contra.com",
    "yelp", "tripadvisor", "yellowpages", "bbb.org", "manta.com", "thumbtack",
    "angieslist", "trustpilot", "crunchbase", "zoominfo", "apollo.io",
    "predrag", "naukri", "shine.com", "foundit.in", "timesjobs", "internshala",
    "wellfound", "remoterocketship", "jobrapido", "learn4good", "bebee",
    "jobsora", "trabajo", "expertini", "startup.jobs", "arbeitnow",
    "remoteok", "weworkremotely", "flexjobs", "remote.co", "jobgether",
    # search engines themselves are never a prospect
    "bing.com", "google.com", "google.co", "duckduckgo.com", "yahoo.com",
    "microsoft.com", "baidu.com", "yandex.com", "ecosia.org", "brave.com",
    "search.marginalia.nu", "mojeek.com", "startpage.com", "qwant.com",
    "quizlet.com", "pinterest.com", "amazon.com", "ebay.com", "etsy.com",
}
_INTITLE_JUNK = re.compile(
    r"(\d+\+?\s+jobs?\b|job(s)? in\b|vacanc|hiring now|apply now|"
    r"salary\b.*job|job search|career(s)? page|browse jobs|"
    r"job openings|recruitment agency|staffing agency|job board)", re.I)


def _is_aggregator(url: str, title: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().replace("www.", "")
    parts = host.split(".")
    if any(a in parts or host.endswith("." + a) or host == a for a in AGGREGATOR_HOSTS):
        return True
    if any(a in host for a in ("jooble", "indeed", "ziprecruiter", "jobsora",
                               "jobrapido", "bebee", "trabajo", "learn4good")):
        return True
    return bool(_INTITLE_JUNK.search(title or ""))


# --- relevance gate -------------------------------------------------------
# Search engines degrade complex queries (quoted phrases, -, OR) into generic
# SERPs rather than returning nothing. Those generic results are poison: they
# look like leads and waste your sends. So we verify every hit against the
# query's own meaningful terms and drop anything that does not corroborate.
_QUERY_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you",
    "site", "com", "www", "http", "https", "html", "about", "into", "over",
    "looking", "need", "seeking", "hiring", "want", "someone", "help",
    "official", "website", "company", "home", "homepage",
}


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-z0-9][a-z0-9+#.\-]{2,}", (query or "").lower())
    out, seen = [], set()
    for t in terms:
        t = t.strip(".-")
        if len(t) < 3 or t in _QUERY_STOP or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _relevant(query: str, title: str, snippet: str) -> bool:
    """True only if the result actually corroborates the query."""
    terms = _query_terms(query)
    if not terms:
        return True
    hay = ((title or "") + " " + (snippet or "")).lower()
    hits = sum(1 for t in terms if t in hay)
    needed = 1 if len(terms) == 1 else 2
    return hits >= needed


# Search engines are NEVER a prospect. This narrower guard runs on EVERY
# record at store time (see run.py cmd_fetch) so that no adapter - present
# or future - can leak SERP pages, redirect wrappers or bot-challenge pages
# into the database. Unlike _is_aggregator it does NOT drop job boards, so
# legitimate board-sourced leads survive.
SEARCH_ENGINE_HOSTS = {
    "bing.com", "google.com", "google.co", "google.co.uk", "google.ca",
    "google.de", "duckduckgo.com", "yahoo.com", "microsoft.com", "msn.com",
    "baidu.com", "yandex.com", "yandex.ru", "ecosia.org", "brave.com",
    "search.brave.com", "mojeek.com", "startpage.com", "qwant.com",
    "search.marginalia.nu", "ask.com", "aol.com", "naver.com", "seznam.cz",
}


def is_search_engine_url(url: str) -> bool:
    """True when the URL points at a search engine / SERP host."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    parts = host.split(".")
    return any(host == h or host.endswith("." + h) or h in parts
               for h in SEARCH_ENGINE_HOSTS)


def _ddg_candidates(q: str, http: Http) -> list[tuple[str, str, str]]:
    """Public DDG html SERP -> (url, title, snippet). Skips the ad wrappers
    (duckduckgo.com/y.js) and leaves the normal guards to the caller."""
    data = http.get(
        "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q}),
        accept="text/html,*/*", ttl_s=6 * 3600,
    ).decode("utf-8", "replace")
    pairs = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                       data, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', data, re.S)
    out: list[tuple[str, str, str]] = []
    for i, (raw, anchor) in enumerate(pairs[:30]):
        raw = raw.strip()
        if "ad_domain=" in raw or "duckduckgo.com/y.js" in raw:
            continue
        m = re.search(r"uddg=([^&]+)", raw)
        url = urllib.parse.unquote(m.group(1)) if m else raw
        if not url.startswith("http") or is_search_engine_url(url):
            continue
        title = html.unescape(_clean(re.sub(r"<[^>]+>", "", anchor)))
        snip = snips[i] if i < len(snips) else ""
        snip = html.unescape(_clean(re.sub(r"<[^>]+>", " ", snip)))
        out.append((url, title, snip))
    return out


def src_websearch(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    """Buyer-intent web search. Engine 1 = Bing RSS (clean XML). If it serves a
    degraded SERP (0 corroborated - common on datacenter IPs), engine 2 = the
    public DuckDuckGo html SERP. Same guards everywhere: search engines and
    aggregators are never leads, and every hit must corroborate the query."""
    n = 0
    for q in _queries(cfg, "websearch", []):
        cands: list[tuple[str, str, str]] = []  # (url, title, snippet)
        tried = ["bing"]
        items = None
        try:
            data = http.get(
                "https://www.bing.com/search?"
                + urllib.parse.urlencode({"q": q, "format": "rss"}),
                accept="application/rss+xml,text/xml,*/*", ttl_s=6 * 3600,
            )
            items = parse_feed(data)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"websearch[{q}] bing: {e}"}
        for it in items or []:
            url = (it.get("link") or it.get("guid") or "").strip()
            if not url.startswith("http"):
                continue
            cands.append((url, _clean(it.get("title", "")),
                          _clean(it.get("description", ""))))

        bing_ok = any(_relevant(q, t, s) for _, t, s in cands)
        if not bing_ok:
            tried.append("duckduckgo")
            try:
                cands = _ddg_candidates(q, http)
            except Exception as e:  # noqa: BLE001
                yield {"_error": f"websearch[{q}] duckduckgo: {e}"}

        kept = 0
        seen: set[str] = set()
        for url, title, snippet in cands:
            if url in seen:
                continue
            seen.add(url)
            if _is_aggregator(url, title) or is_search_engine_url(url):
                continue
            if not _relevant(q, title, snippet):
                continue
            kept += 1
            n += 1
            yield make_record(
                "websearch",
                external_id=hashlib.sha1(url.encode()).hexdigest()[:16],
                url=url,
                title=title,
                body=snippet + f"\n(query: {q})",
                company=(urllib.parse.urlsplit(url).hostname or "").replace("www.", ""),
                domain=registrable_domain(url),
                raw={"query": q},
            )
            if n >= limit:
                return
        if kept == 0:
            yield {"_error": (
                f"websearch[{q}]: engines tried {tried}, nothing corroborated "
                f"the query; nothing stored")}


# ==========================================================================
# 13. OPENSTREETMAP -- local businesses that buy ads
#
#  Overpass is a free, donation-funded public API with strict fair-use rules.
#  It WILL throttle you if you hammer it, so we rotate mirrors, honour a long
#  delay, cache for 24h, and degrade gracefully. If every mirror refuses, this
#  source simply reports and skips - it never breaks a run.
# ==========================================================================

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",   # Switzerland-only data
]


def src_osm_local(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    spec = cfg.sources.get("osm_local") or {}
    targets = spec.get("targets") or []
    radius_km = float(spec.get("radius_km", 6))
    mirrors = spec.get("mirrors") or OVERPASS_MIRRORS
    n = 0
    refused: set[str] = set()

    for t in targets:
        label = t.get("label", t.get("filter", "business"))
        osm_filter = t.get("filter")
        for city in t.get("cities", []) or []:
            lat, lon = city.get("lat"), city.get("lon")
            if lat is None or lon is None:
                continue
            radius = int(float(city.get("radius_km", radius_km)) * 1000)
            q = (f'[out:json][timeout:25];'
                 f'(node[{osm_filter}](around:{radius},{lat},{lon});'
                 f'way[{osm_filter}](around:{radius},{lat},{lon}););'
                 f'out center;')
            data = None
            last_err = ""
            for mirror in mirrors:
                if mirror in refused:
                    continue
                try:
                    data = http.get_json(
                        mirror + "?data=" + urllib.parse.quote(q),
                        ua=BOT_UA, accept="*/*", retries=0, ttl_s=24 * 3600)
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = f"{type(e).__name__}: {str(e)[:60]}"
                    if "406" in str(e) or "429" in str(e) or "403" in str(e):
                        refused.add(mirror)
                    time.sleep(2.0)
            if data is None:
                yield {"_error": f"osm_local[{label}/{city.get('name')}]: all Overpass "
                                 f"mirrors refused ({last_err}). Overpass throttles by IP - "
                                 f"retry later or lower the number of cities."}
                break

            for el in data.get("elements", []):
                tags = el.get("tags") or {}
                name = tags.get("name")
                if not name:
                    continue
                site = tags.get("website") or tags.get("contact:website") or ""
                email = tags.get("email") or tags.get("contact:email") or ""
                phone = tags.get("phone") or tags.get("contact:phone") or ""
                addr = " ".join(filter(None, [
                    tags.get("addr:street"), tags.get("addr:city"),
                    tags.get("addr:postcode"),
                ]))
                body = "\n".join(filter(None, [
                    f"Local business category: {label}",
                    f"Address: {addr}" if addr else "",
                    f"Website: {site}" if site else "",
                    f"Email: {email}" if email else "",
                    f"Phone: {phone}" if phone else "",
                    f"OSM tags: {json.dumps(tags, ensure_ascii=False)[:400]}",
                ]))
                n += 1
                yield make_record(
                    "osm_local",
                    external_id=f"{el.get('type')}/{el.get('id')}",
                    url=site or f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                    title=f"{name} - {label} ({city.get('name','')})",
                    body=body,
                    company=name,
                    domain=registrable_domain(site) if site else None,
                    location=city.get("name"),
                    posted=None,
                    raw={"category": label, "city": city.get("name"), "tags": tags},
                )
                if n >= limit:
                    return


# ==========================================================================
# 14. REDDIT (optional -- needs free OAuth app, 100 req/min)
# ==========================================================================


def _reddit_token(http: Http, cfg) -> str | None:
    cid = str(cfg.reddit.get("client_id", "") or "").strip()
    secret = str(cfg.reddit.get("client_secret", "") or "").strip()
    if not cid or not secret:
        return None
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    raw = http.post_form(
        "https://www.reddit.com/api/v1/access_token",
        body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        ua=str(cfg.reddit.get("user_agent", "leadforge/1.0 by you")),
    )
    return json.loads(raw.decode()).get("access_token")


def src_reddit(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    token = _reddit_token(http, cfg)
    if not token:
        yield {"_error": "reddit: add [reddit] client_id + client_secret to config.toml"}
        return
    auth = {"Authorization": f"Bearer {token}"}
    ua = str(cfg.reddit.get("user_agent", "leadforge/1.0 by you"))

    def api(path: str) -> dict:
        return json.loads(http.get(
            "https://oauth.reddit.com" + path,
            headers=auth, ua=ua, accept="application/json", ttl_s=900,
        ).decode())

    n = 0
    # a) targeted search
    for q in _queries(cfg, "reddit", []):
        try:
            data = api(f"/search?q={urllib.parse.quote(q)}&sort=new&limit=100&t=month"
                       f"&restrict_sr=0&type=link")
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"reddit[{q}]: {e}"}
            continue
        for child in (data.get("data", {}).get("children") or []):
            d = child.get("data") or {}
            n += 1
            yield _reddit_rec(d, q)
            if n >= limit:
                return
    # b) the buyer-side subreddits, newest first
    for sub in cfg.reddit.get("subreddits", []) or []:
        try:
            data = api(f"/r/{sub}/new?limit=100")
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"reddit/r/{sub}: {e}"}
            continue
        for child in (data.get("data", {}).get("children") or []):
            d = child.get("data") or {}
            n += 1
            yield _reddit_rec(d, f"r/{sub}")
            if n >= limit:
                return


def _reddit_rec(d: dict, tag: str) -> dict:
    return make_record(
        "reddit",
        external_id=d.get("id"),
        url="https://www.reddit.com" + (d.get("permalink") or ""),
        title=d.get("title", ""),
        body=((d.get("selftext") or "") + "\n" +
              (f"[link: {d.get('url')}]" if d.get("url_overridden_by_dest") else "")),
        author=d.get("author"),
        company=f"r/{d.get('subreddit')}",
        location=d.get("subreddit"),
        posted=d.get("created_utc"),
        raw={"subreddit": d.get("subreddit"), "tag": tag,
             "score": d.get("score"), "num_comments": d.get("num_comments")},
    )


# ==========================================================================
# 15. META AD LIBRARY (optional -- needs a verified Meta dev token)
# ==========================================================================


def src_meta_ads(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    token = str(cfg.apis.get("meta_access_token", "") or "").strip()
    if not token:
        yield {"_error": "meta_ads: optional. Add apis.meta_access_token (free, "
                         "needs Meta dev verification ~1-2 weeks) to unlock."}
        return
    spec = cfg.sources.get("meta_ads") or {}
    country = spec.get("country", "US")
    n = 0
    for q in _queries(cfg, "meta_ads", []):
        url = ("https://graph.facebook.com/v21.0/ads_archive?" + urllib.parse.urlencode({
            "access_token": token,
            "search_terms": q,
            "ad_reached_countries": json.dumps([country]),
            "ad_active_status": "ACTIVE",
            "ad_type": "ALL",
            "limit": 50,
            "fields": "id,page_id,page_name,ad_creative_link_titles,"
                      "ad_creative_bodies,ad_snapshot_url,publisher_platforms",
        }))
        try:
            data = http.get_json(url, ttl_s=3600)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"meta_ads[{q}]: {e}"}
            continue
        for ad in data.get("data", []):
            n += 1
            bodies = ad.get("ad_creative_bodies") or []
            yield make_record(
                "meta_ads",
                external_id=ad.get("id"),
                url=ad.get("ad_snapshot_url"),
                title=f"ACTIVE ADVERT: {ad.get('page_name','')}",
                body="\n".join(bodies) + f"\n[query: {q}] "
                     f"[platforms: {ad.get('publisher_platforms')}]",
                company=ad.get("page_name"),
                location=country,
                posted=None,
                raw=ad,
            )
            if n >= limit:
                return


# ==========================================================================
# 16. REDDIT RSS  -- no API key, no OAuth, buyer posts only
# ==========================================================================
# The old public .json endpoints now return 403, but Reddit's Atom feeds still
# work. We read "multireddit" feeds (r/a+b+c) so one request covers several
# subs, and we keep only posts that read like a BUYER - r/forhire is otherwise
# dominated by people selling their own services.

_REDDIT_RSS_URL = "https://www.reddit.com/r/{multi}/new/.rss"
_REDDIT_RSS_HEADERS = {"Accept": "application/atom+xml,application/rss+xml,text/xml,*/*"}
_REDDIT_RSS_BATCH = 3

_BUYER_RE = re.compile(
    r"\[hiring\]|\bhiring\b|\blooking for\b|\bneed(ed|s)?\b|\bwanted\b|"
    r"\bseeking\b|\brecommend(?:ation)?s?\b|\banyone know\b|\bwhere can i find\b",
    re.I,
)
_SELLER_RE = re.compile(r"\[for\s*hire\]|\[forhire\]", re.I)

DEFAULT_RSS_SUBS = [
    "forhire", "hiring", "VideoEditing", "editors", "motiongraphics",
    "UGCcreators", "socialmedia", "marketing", "web_design",
]


def _is_buyer_post(title: str) -> bool:
    t = title or ""
    if _SELLER_RE.search(t):
        return False
    return bool(_BUYER_RE.search(t))


def src_reddit_rss(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    spec = cfg.sources.get("reddit_rss") or {}
    subs = list(spec.get("rss_subreddits")
                or cfg.reddit.get("rss_subreddits")
                or cfg.reddit.get("subreddits")
                or []) or list(DEFAULT_RSS_SUBS)
    n = 0
    for i in range(0, len(subs), _REDDIT_RSS_BATCH):
        batch = subs[i:i + _REDDIT_RSS_BATCH]
        multi = "+".join(batch)
        try:
            data = http.get(_REDDIT_RSS_URL.format(multi=multi),
                            headers=_REDDIT_RSS_HEADERS, accept="*/*",
                            retries=2, ttl_s=1800)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"reddit_rss[r/{multi}]: {e} "
                             f"(Reddit throttles by IP - retry in a few minutes)"}
            time.sleep(4)
            continue

        for it in parse_feed(data)[:60]:
            title = it.get("title", "")
            if not _is_buyer_post(title):
                continue
            link = it.get("link") or it.get("id") or ""
            raw_when = it.get("updated") or it.get("published") or it.get("pubdate")
            ts = parse_when(raw_when)
            if since_ts and ts and ts.timestamp() < since_ts:
                continue
            body = (it.get("content") or it.get("summary")
                    or it.get("description") or "")
            m = re.search(r"/r/([^/]+)/comments", link)
            sub = m.group(1) if m else ""
            n += 1
            yield make_record(
                "reddit_rss",
                external_id=it.get("id") or link,
                url=link or None,
                title=title,
                body=body,
                author=it.get("author") or it.get("name"),
                company=f"r/{sub}" if sub else None,
                location=sub or None,
                posted=raw_when,
                raw={"subreddit": sub, "batch": multi},
            )
            if n >= limit:
                return
        time.sleep(3)


# ==========================================================================
# 17. GOOGLE MAPS  -- local businesses, richer than OSM (phone, site, ratings)
# ==========================================================================
# Google Maps needs a real browser, so this delegates to the Node scraper in
# ../cli.js (Playwright). If node or the browser is missing it yields a clear
# one-line error instead of failing the run.

def _node_cli(args: list[str], timeout: int = 900) -> list[dict]:
    cli = Path(__file__).resolve().parents[2] / "cli.js"
    if not cli.exists():
        raise RuntimeError(f"node CLI missing at {cli}")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not on PATH (needed for maps/ads)")
    proc = subprocess.run(
        [node, str(cli), *args, "--json"],
        cwd=str(cli.parent), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
        raise RuntimeError(tail[-1] if tail else f"exit {proc.returncode}")
    out = (proc.stdout or "").strip()
    return json.loads(out) if out else []


def src_google_maps(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    spec = cfg.sources.get("google_maps") or {}
    queries = spec.get("queries") or []
    if not queries:
        yield {"_error": "google_maps: set sources.google_maps.queries in config.toml, "
                         'e.g. ["dentist in austin tx", "med spa in miami fl"]'}
        return
    per = int(spec.get("max_per_query", 40))
    headless = bool(spec.get("headless", True))
    n = 0
    for q in queries:
        args = ["maps", "--queries", str(q), "--max", str(per)]
        if headless:
            args.append("--headless")
        try:
            rows = _node_cli(args)
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"google_maps[{q}]: {e}"}
            continue
        for r in rows:
            name = r.get("name")
            if not name:
                continue
            site = r.get("website") or ""
            body = "\n".join(filter(None, [
                f"Local business category: {r.get('category', '')}",
                f"Address: {r.get('address', '')}",
                f"Phone: {r.get('phone', '')}",
                f"Website: {site}",
                f"Rating: {r.get('rating', '')} ({r.get('reviews', '')} reviews)",
                f"Search: {q}",
            ]))
            n += 1
            yield make_record(
                "google_maps",
                external_id=r.get("source_url") or f"{name}|{r.get('address', '')}",
                url=site or r.get("source_url"),
                title=f"{name} - {r.get('category') or 'local business'}",
                body=body,
                company=name,
                domain=registrable_domain(site) if site else None,
                location=r.get("address") or None,
                posted=None,
                raw=r,
            )
            if n >= limit:
                return


# ==========================================================================
# 19. GOOGLE PLACES API (official) -- local businesses from the free tier
#
# The one permitted, structured path to the same data the Maps browser crawl
# guesses at. Key lives in config.toml [apis] google_places_key. One TEXT
# SEARCH request per query returns up to 20 businesses with name, category,
# address, phone, website and rating. A business with NO websiteUri is our
# cleanest 3D-web offer signal, exactly like the crawler's empty website.
# Records reuse src_google_maps' raw keys so the contact bridge treats both
# identically. This source never scrolls, never scrapes, and is priced by
# Google with a free monthly allowance that comfortably covers our volume.
# ==========================================================================

_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACES_FIELDS = ("places.id,places.displayName.text,places.formattedAddress,"
                  "places.websiteUri,places.internationalPhoneNumber,"
                  "places.rating,places.userRatingCount,"
                  "places.primaryTypeDisplayName.text,places.googleMapsUri")


def src_google_places(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    key = str(cfg.apis.get("google_places_key", "") or "").strip()
    if not key:
        yield {"_error": ("google_places: no key. Get it (console.cloud.google.com, "
                          "enable Places API (New)) then: python run.py addkey "
                          "google_places_key AIza... --section apis")}
        return
    spec = cfg.sources.get("google_places") or {}
    queries = (spec.get("queries")
               or (cfg.sources.get("google_maps") or {}).get("queries") or [])
    if not queries:
        yield {"_error": "google_places: no queries configured"}
        return
    n = 0
    for q in queries:
        payload = json.dumps({"textQuery": q, "maxResultCount": 20}).encode("utf-8")
        req = urllib.request.Request(
            _PLACES_URL, data=payload, method="POST", headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": _PLACES_FIELDS,
            })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                err = json.loads(e.read().decode("utf-8")).get("error") or {}
                detail = str(err.get("message", ""))[:180]
            except Exception:  # noqa: BLE001
                pass
            yield {"_error": f"google_places[{q}]: HTTP {e.code} {detail}"}
            continue
        except Exception as e:  # noqa: BLE001
            yield {"_error": f"google_places[{q}]: {e}"}
            continue
        places = body.get("places") or []
        if not places:
            yield {"_error": f"google_places[{q}]: 0 places, nothing stored"}
            continue
        for p in places:
            name = ((p.get("displayName") or {}).get("text") or "").strip()
            if not name:
                continue
            site = p.get("websiteUri") or ""
            cat = ((p.get("primaryTypeDisplayName") or {}).get("text") or "").strip()
            if n >= limit:
                return
            n += 1
            yield make_record(
                "google_places",
                external_id=(p.get("id")
                             or (p.get("googleMapsUri") or "")
                             or f"{name}|{p.get('formattedAddress', '')}"),
                url=site or "",
                title=f"{name} - {cat or 'local business'}",
                body="\n".join(filter(None, [
                    f"Local business category: {cat}",
                    f"Address: {p.get('formattedAddress', '')}",
                    f"Phone: {p.get('internationalPhoneNumber', '')}",
                    f"Website: {site}",
                    f"Rating: {p.get('rating', '')}"
                    f" ({p.get('userRatingCount', '')} reviews)",
                    f"Search: {q}",
                ])),
                company=name,
                domain=registrable_domain(site) if site else None,
                location=p.get("formattedAddress") or None,
                raw={
                    "name": name, "category": cat,
                    "address": p.get("formattedAddress", ""),
                    "phone": p.get("internationalPhoneNumber", ""),
                    "website": site, "rating": p.get("rating"),
                    "reviews": p.get("userRatingCount"),
                    "source_url": p.get("googleMapsUri") or "",
                    "place_id": p.get("id", ""),
                },
            )


# ==========================================================================
# 18. META AD LIBRARY (open)  -- live advertisers, NO developer token
# ==========================================================================
# Same idea as src_meta_ads, but reads the public Ad Library UI so it works
# today instead of waiting 1-2 weeks for Meta developer verification. A brand
# running ads has budget and already buys creative: the warmest free lead there
# is for an AI ad/UGC studio.

def src_meta_ads_open(http: Http, cfg, since_ts=None, limit=200) -> Iterator[dict]:
    spec = cfg.sources.get("meta_ads_open") or {}
    queries = spec.get("queries") or []
    if not queries:
        yield {"_error": "meta_ads_open: set sources.meta_ads_open.queries in config.toml, "
                         'e.g. ["skincare", "protein powder"]'}
        return
    countries = spec.get("countries") or [spec.get("country", "US")]
    media = str(spec.get("media", "video"))
    scrolls = int(spec.get("max_scrolls", 20))
    headless = bool(spec.get("headless", True))
    n = 0
    for country in countries:
        for q in queries:
            args = ["ads", "--queries", str(q), "--country", str(country),
                    "--media", media, "--max-scrolls", str(scrolls)]
            if headless:
                args.append("--headless")
            try:
                rows = _node_cli(args)
            except Exception as e:  # noqa: BLE001
                yield {"_error": f"meta_ads_open[{q}/{country}]: {e}"}
                continue
            for r in rows:
                adv = r.get("advertiser")
                if not adv:
                    continue
                n += 1
                yield make_record(
                    "meta_ads_open",
                    external_id=r.get("handle") or adv,
                    url=r.get("page_url") or r.get("library_url"),
                    title=f"RUNNING {media.upper()} ADS: {adv}",
                    body=f"{adv} is running {media} ads on Meta in {country} "
                         f"(keyword: {q}). Proven ad spend - they already buy creative.\n"
                         f"Ad library: {r.get('library_url', '')}\n"
                         f"Page: {r.get('page_url', '')}",
                    company=adv,
                    location=str(country),
                    posted=None,
                    raw=r,
                )
                if n >= limit:
                    return


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

SOURCE_REGISTRY: dict[str, dict] = {
    # name: fn, platform bonus, group, description
    "hn_whoishiring": (src_hackernews, 5, "high-signal",
                       "HN monthly 'Seeking freelancer' paid-gig thread"),
    "hn_search":      (src_hackernews, 3, "high-signal",
                       "Free-text search over every HN comment ever"),
    "linkedin_jobs":  (src_linkedin_jobs, 4, "high-signal",
                       "LinkedIn public guest job search (no login)"),
    "websearch":      (src_websearch, 2, "high-signal",
                       "Web search (Bing) for exact buyer phrases"),
    "reddit":         (src_reddit, 3, "high-signal",
                       "Reddit search + buyer subreddits (needs free API key)"),
    "meta_ads":       (src_meta_ads, 5, "high-signal",
                       "Businesses with LIVE ads = proven ad spend (needs token)"),
    "remoteok":       (src_remoteok, 4, "job-boards",
                       "RemoteOK remote job API"),
    "remotive":       (src_remotive, 4, "job-boards", "Remotive job API"),
    "jobicy":         (src_jobicy, 4, "job-boards", "Jobicy job API"),
    "arbeitnow":      (src_arbeitnow, 3, "job-boards", "Arbeitnow job board API"),
    "himalayas":      (src_himalayas, 3, "job-boards", "Himalayas remote job API"),
    "workingnomads":  (src_workingnomads, 4, "job-boards",
                       "Working Nomads job API"),
    "weworkremotely": (src_weworkremotely, 4, "job-boards", "We Work Remotely RSS"),
    "producthunt":    (src_producthunt, 4, "job-boards",
                       "Product Hunt launches - every launch needs promo"),
    "bingnews":       (src_bingnews, 1, "news", "Bing News RSS for any query"),
    "osm_local":      (src_osm_local, 5, "local",
                       "OpenStreetMap local businesses by category+city"),
    "google_maps":    (src_google_maps, 5, "local",
                       "Google Maps local businesses - phone, website, ratings"),
    "google_places":  (src_google_places, 5, "local",
                       "Official Places API - structured local businesses (free tier)"),
    "reddit_rss":     (src_reddit_rss, 3, "high-signal",
                       "Reddit via Atom RSS - no API key, buyer posts only"),
    "meta_ads_open":  (src_meta_ads_open, 5, "high-signal",
                       "Live Meta advertisers via public Ad Library - no token"),
}

GROUPS = {
    "high-signal": ["hn_whoishiring", "hn_search", "linkedin_jobs", "websearch",
                    "reddit", "meta_ads", "reddit_rss", "meta_ads_open"],
    "job-boards": ["remoteok", "remotive", "jobicy", "arbeitnow", "himalayas",
                   "workingnomads", "weworkremotely", "producthunt"],
    "news": ["bingnews"],
    "local": ["osm_local", "google_maps", "google_places"],
}


# Which sources publish JOB ADVERTS (boilerplate-heavy, title decides) versus
# human POSTS (the body IS the request). This drives the scoring mode.
JOB_BOARD_SOURCES = {
    "linkedin_jobs", "remoteok", "remotive", "jobicy", "arbeitnow",
    "himalayas", "workingnomads", "weworkremotely",
}


def source_mode(name: str) -> str:
    return "job_board" if name in JOB_BOARD_SOURCES else "post"


def resolve_sources(spec: str) -> list[str]:
    spec = (spec or "all").strip()
    if spec in ("all", "*"):
        return list(SOURCE_REGISTRY)
    out: list[str] = []
    for token in re.split(r"[,\s]+", spec):
        if not token:
            continue
        if token in GROUPS:
            out += GROUPS[token]
        elif token in SOURCE_REGISTRY:
            out.append(token)
        else:
            raise KeyError(
                f"unknown source '{token}'. Options: {', '.join(SOURCE_REGISTRY)} "
                f"| groups: {', '.join(GROUPS)}"
            )
    seen, dedup = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    return dedup
