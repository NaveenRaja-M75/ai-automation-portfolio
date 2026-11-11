"""
LeadForge :: enrichment.

Four things happen here, in order of how much they actually matter:

1. AD-PIXEL DETECTION  (free, unlimited, and brutally predictive)
   If a prospect's website fires Meta Pixel / GTM / TikTok Pixel / LinkedIn
   Insight, they are ALREADY paying for ads. That is a buyer. This one signal
   beats every paid list filter, and no SaaS sells it to you.

2. CONTACT DISCOVERY
   Crawl their own site (robots.txt respected) for mailto: addresses, named
   people, social handles and phone numbers. Public business contact data, from
   the source of truth, for free.

3. VERIFICATION
   MX lookup over DNS-over-HTTPS (no dependency, no port-25 blocking) so you
   never waste a send on a domain that cannot receive mail.

4. PATTERN ENRICHMENT + OPTIONAL PAID APIS
   first@domain / first.last@domain guesses, plus plug-in support for the free
   tiers of Hunter / Snov / Apollo if you add keys. Entirely optional.

Nothing here touches a logged-in surface. It reads public pages the same way a
browser does.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field

from .core import Http, find_emails, find_urls, registrable_domain

# --------------------------------------------------------------------------
# 1. Ad-pixel detection
# --------------------------------------------------------------------------

PIXELS: dict[str, list[str]] = {
    "meta_pixel":      ["connect.facebook.net", "fbq('init'", 'fbq("init"',
                        "fbevents.js", "facebook.com/tr"],
    "google_ads":      ["googleadservices.com", "google_conversion_id",
                        "gtag('config', 'aw-", 'gtag("config", "aw-',
                        "doubleclick.net"],
    "gtm":             ["googletagmanager.com/gtm.js", "googletagmanager.com/ns.html"],
    "ga4":             ["gtag/js?id=g-", "gtag('config', 'g-", "analytics.js"],
    "tiktok_pixel":    ["analytics.tiktok.com", "ttq.load", "ttq.page"],
    "linkedin_insight": ["snap.licdn.com", "_linkedin_partner_id", "linkedin.com/px"],
    "twitter_pixel":   ["static.ads-twitter.com", "twq('config'", "twitter.com/oct"],
    "pinterest_tag":   ["ct.pinterest.com", "pintrk('load'"],
    "snap_pixel":      ["sc-static.net/scevent", "snaptr('init'"],
    "reddit_pixel":    ["redditstatic.com/ads/pixel", "rdt('init'"],
    "hotjar":          ["static.hotjar.com"],
    "klaviyo":         ["static.klaviyo.com", "klaviyo.js"],
    "shopify":         ["cdn.shopify.com", "shopify.theme"],
    "intercom":        ["widget.intercom.io"],
    "hubspot":         ["js.hs-scripts.com", "js.hsforms.net"],
    "calendly":        ["assets.calendly.com"],
    "wistia_video":    ["fast.wistia.com"],
    "vimeo":           ["player.vimeo.com"],
}


def detect_ad_stack(html: str) -> dict:
    low = (html or "").lower()
    found = [name for name, needles in PIXELS.items()
             if any(nd.lower() in low for nd in needles)]
    paid = [f for f in found if f in (
        "meta_pixel", "google_ads", "tiktok_pixel", "linkedin_insight",
        "twitter_pixel", "pinterest_tag", "snap_pixel", "reddit_pixel")]
    return {
        "stack": found,
        "paid_media": paid,
        "likely_advertiser": bool(paid),
    }


# --------------------------------------------------------------------------
# 2. Contact discovery
# --------------------------------------------------------------------------

CONTACT_PATHS = [
    "", "/contact", "/contact-us", "/contactus", "/about", "/about-us",
    "/team", "/company", "/impressum", "/support", "/help", "/pages/contact",
    "/get-in-touch", "/work-with-us", "/hire-us",
]

_NAME_RX = re.compile(
    r"\b([A-Z][a-z]{2,15})\s+([A-Z][a-z]{2,20})\b(?:[^A-Za-z0-9@]{0,40})"
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24})"
)
_ROLE_RX = re.compile(
    r"\b(founder|co-?founder|ceo|cto|cmo|coo|owner|director|marketing manager|"
    r"head of marketing|growth|brand manager|e-?commerce manager|"
    r"content lead|creative director|producer|manager|partner)\b", re.I)

SOCIAL_RX = {
    "linkedin":  re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|company)/[A-Za-z0-9\-_%]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+", re.I),
    "twitter":   re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]+", re.I),
    "tiktok":    re.compile(r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9_.]+", re.I),
    "youtube":   re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|c/|channel/)[A-Za-z0-9_\-]+", re.I),
    "threads":   re.compile(r"https?://(?:www\.)?threads\.(?:net|com)/@[A-Za-z0-9_.]+", re.I),
    "facebook":  re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.\-/]+", re.I),
}


@dataclass
class Enrichment:
    domain: str
    mx_ok: bool | None = None
    emails: list[str] = field(default_factory=list)
    guessed: list[str] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)   # {name, role, email?}
    socials: dict = field(default_factory=dict)
    phones: list[str] = field(default_factory=list)
    ad_stack: dict = field(default_factory=dict)
    title: str = ""
    pages_read: int = 0
    source_notes: list[str] = field(default_factory=list)

    @property
    def best_email(self) -> str | None:
        for e in self.emails:
            if not _is_generic(e):
                return e
        return self.emails[0] if self.emails else (self.guessed[0] if self.guessed else None)


_GENERIC = {"info", "hello", "contact", "hi", "support", "admin", "sales",
            "enquiries", "inquiries", "office", "team", "help", "mail", "enquiry"}


def _is_generic(addr: str) -> bool:
    return addr.split("@")[0].lower() in _GENERIC


class Robots:
    def __init__(self, http: Http):
        self.http = http
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str, ua: str = "*") -> bool:
        try:
            host = urllib.parse.urlsplit(url).netloc
        except Exception:
            return True
        if host not in self._cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                txt = self.http.get(
                    f"https://{host}/robots.txt", retries=0, ttl_s=86400,
                    headers={"Accept": "text/plain"}).decode("utf-8", "replace")
                rp.parse(txt.splitlines())
            except Exception:
                rp = None
            self._cache[host] = rp
        rp = self._cache[host]
        if rp is None:
            return True
        try:
            return rp.can_fetch(ua, url)
        except Exception:
            return True


def mx_ok(http: Http, domain: str) -> bool | None:
    """MX lookup via DNS-over-HTTPS. No dependencies, no blocked port 25."""
    for ep, key in (
        ("https://cloudflare-dns.com/dns-query", "Answer"),
        ("https://dns.google/resolve", "Answer"),
    ):
        try:
            data = http.get_json(
                f"{ep}?name={urllib.parse.quote(domain)}&type=MX",
                headers={"Accept": "application/dns-json"}, retries=1, ttl_s=86400)
            answers = data.get(key) or []
            if any(a.get("type") == 15 for a in answers):
                return True
            if answers is not None and data.get("Status") == 0:
                # NXDOMAIN-ish or no MX at all
                if not answers:
                    return False
        except Exception:
            continue
    return None


def enrich_domain(http: Http, domain: str, cfg=None, max_pages: int = 5,
                  robots: Robots | None = None) -> Enrichment:
    out = Enrichment(domain=domain)
    robots = robots or Robots(http)
    base = "https://" + domain.replace("https://", "").replace("http://", "").strip("/")

    seen_pages = 0
    for path in CONTACT_PATHS:
        if seen_pages >= max_pages:
            break
        url = base + path
        if not robots.allowed(url):
            out.source_notes.append(f"robots-disallowed:{path}")
            continue
        try:
            raw = http.get(url, accept="text/html,application/xhtml+xml", retries=1,
                           ttl_s=7 * 86400)
        except Exception:
            continue
        html = raw.decode("utf-8", "replace")
        seen_pages += 1

        if not out.ad_stack:
            out.ad_stack = detect_ad_stack(html)
        if not out.title:
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
            if m:
                out.title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]

        for e in find_emails(html):
            if e not in out.emails and (
                    e.endswith("@" + domain) or domain.endswith(e.split("@")[-1])):
                out.emails.append(e)

        text = _html_to_text(html)
        for e in find_emails(text):
            if e not in out.emails and e.endswith("@" + domain):
                out.emails.append(e)

        for m in _NAME_RX.finditer(html):
            name, _surname, email = m.group(1), m.group(2), m.group(3)
            if not email.endswith("@" + domain):
                continue
            if not any(p["email"] == email for p in out.people):
                out.people.append({"name": f"{m.group(1)} {m.group(2)}",
                                   "role": None, "email": email})
        for pm in _ROLE_RX.finditer(text):
            snippet = text[max(0, pm.start() - 90): pm.start() + 40]
            roles = [p for p in out.people if not p.get("role")]
            if roles:
                roles[0]["role"] = pm.group(1).lower()

        for m in re.finditer(r"tel:([+\d\s\-\(\)\.]{7,22})", html):
            ph = re.sub(r"\s+", " ", m.group(1)).strip()
            if ph not in out.phones:
                out.phones.append(ph)

        for key, rx in SOCIAL_RX.items():
            if out.socials.get(key):
                continue
            m = rx.search(html)
            if m:
                candidate = m.group(0).rstrip("/")
                if not re.search(r"/(sharer|share|intent|plugins|policy|help)", candidate, re.I):
                    out.socials[key] = candidate

        if out.emails and out.socials and out.ad_stack:
            break

    out.pages_read = seen_pages
    out.mx_ok = mx_ok(http, domain)
    return out


def _html_to_text(html: str) -> str:
    from .core import strip_html
    return strip_html(html)


# --------------------------------------------------------------------------
# 3b. COMPANY NAME -> WEBSITE  (the step that makes everything else work)
#
# A job-board lead gives you "Athelas" and nothing else. Without a website you
# cannot find an email or check whether they buy ads. So: search the web for the
# company, take the first result whose domain actually matches the company name,
# and cache it forever. This turns a name-only list into an enrichable one.
# --------------------------------------------------------------------------

_SEARCH_HOST_BLOCK = {
    "linkedin", "facebook", "twitter", "x", "instagram", "crunchbase", "bloomberg",
    "glassdoor", "indeed", "wikipedia", "youtube", "tiktok", "pitchbook", "zoominfo",
    "apollo", "g2", "capterra", "trustpilot", "yelp", "owler", "rocketreach",
    "signalhire", "lusha", "leadiq", "similarweb", "builtin", "wellfound",
    "angel", "twitter", "reddit", "medium", "substack", "github", "gitlab",
    "naukri", "ziprecruiter", "monster", "dice", "stackshare", "tracxn",
    "findymail", "prospeo", "hunter", "snov", "bitbucket", "producthunt",
    "jobicy", "remoteok", "remotive", "arbeitnow", "himalayas", "weworkremotely",
    "workingnomads", "forbes", "techcrunch", "reuters", "yahoo", "msn",
}


def _bing_urls(http: Http, query: str, ttl_s: int = 30 * 86400) -> list[str]:
    """Search-result URLs for a query.

    Uses Bing's RSS transport (clean XML) instead of scraping the HTML SERP -
    same data, nothing to break when the layout changes.
    """
    from .sources import parse_feed
    try:
        data = http.get(
            "https://www.bing.com/search?" + urllib.parse.urlencode(
                {"q": query, "format": "rss"}),
            accept="application/rss+xml,text/xml,*/*", ttl_s=ttl_s,
        )
    except Exception:
        return []
    out: list[str] = []
    for it in parse_feed(data):
        url = (it.get("link") or it.get("guid") or "").strip()
        if url.startswith("http"):
            out.append(url)
    return out


def resolve_company_domain(http: Http, company: str, location: str | None = None,
                           cfg=None) -> str | None:
    """Company name -> verified website.

    Safety rule: a WRONG domain is worse than no domain, because you would email
    an unrelated company. So a candidate is accepted only if its domain label
    strictly matches the company name (allowing get-/use-/try- affixes) AND the
    live site's own title/og:site_name/h1 corroborates the name.

    This is why "League" no longer resolves to leagueoflegends.com.
    """
    from .core import company_to_slug
    slug = company_to_slug(company)
    # Very short brand names are too ambiguous to auto-resolve safely.
    if not slug or len(slug) < 5:
        return None
    spec = (cfg.sources.get("resolve") if cfg else None) or {}
    candidates: list[str] = []

    if spec.get("via_search", True):
        q = f'"{company}" official website'
        if location:
            q += f" {location}"
        for url in _bing_urls(http, q)[:8]:
            dom = registrable_domain(url)
            if not dom:
                continue
            label = dom.split(".")[0].replace("-", "")
            if _label_matches(label, slug) and dom not in candidates:
                candidates.append(dom)

    for tld in (".com", ".io", ".co", ".ai"):
        cand = slug + tld
        if cand not in candidates:
            candidates.append(cand)

    for dom in candidates[:5]:
        if dom.split(".")[0] in _SEARCH_HOST_BLOCK:
            continue
        if _verify_company_domain(http, dom, company, slug):
            return dom
    return None


_AFFIX_PREFIX = ("get", "use", "try", "join", "go", "my", "hey", "with")
_AFFIX_SUFFIX = ("app", "hq", "ai", "labs", "group", "inc", "team", "co", "io")


def _label_matches(label: str, slug: str) -> bool:
    """Strict: label must BE the slug, or a small brand affix away from it."""
    if label == slug:
        return True
    for p in _AFFIX_PREFIX:
        if label == p + slug:
            return True
    for s in _AFFIX_SUFFIX:
        if label == slug + s:
            return True
    return False


def _site_identity(http: Http, domain: str) -> str:
    """Pull the site's own claim of who it is, from its homepage."""
    for scheme in ("https://", "http://"):
        try:
            raw = http.get(scheme + domain,
                           accept="text/html,application/xhtml+xml",
                           retries=0, ttl_s=60 * 86400, timeout=8)
        except Exception:
            continue
        html = raw.decode("utf-8", "replace")
        parts: list[str] = []
        mt = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        if mt:
            parts.append(re.sub(r"<[^>]+>", " ", mt.group(1)))
        for prop in ("og:site_name", "og:title", "application-name", "twitter:title"):
            mm = re.search(
                r'(?is)<meta[^>]+(?:property|name)\s*=\s*"[^"]*'
                + re.escape(prop) + r'[^"]*"[^>]+content\s*=\s*"([^"]*)"', html)
            if mm:
                parts.append(mm.group(1))
        mh = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html)
        if mh:
            parts.append(re.sub(r"<[^>]+>", " ", mh.group(1)))
        return " ".join(parts)
    return ""


def _verify_company_domain(http: Http, domain: str, company: str, slug: str) -> bool:
    ident = _site_identity(http, domain)
    if not ident:
        return False
    norm = re.sub(r"[^a-z0-9]", "", ident.lower())
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    if len(words) >= 2:
        return "".join(words) in norm or slug in norm
    return slug in norm


# --------------------------------------------------------------------------
# 4. Pattern guesses + optional paid providers
# --------------------------------------------------------------------------


def guess_emails(domain: str, first: str | None, last: str | None) -> list[str]:
    if not first:
        return []
    f = re.sub(r"[^a-z]", "", first.lower())
    l = re.sub(r"[^a-z]", "", (last or "").lower())
    if not f:
        return []
    pats = [f"{f}@{domain}"]
    if l:
        pats = [
            f"{f}@{domain}", f"{f}.{l}@{domain}", f"{f}{l}@{domain}",
            f"{f[0]}{l}@{domain}", f"{f}_{l}@{domain}", f"{f}-{l}@{domain}",
            f"{l}@{domain}", f"{l}.{f}@{domain}",
        ]
    pats += [f"hello@{domain}", f"info@{domain}", f"contact@{domain}"]
    seen, out = set(), []
    for p in pats:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def provider_lookup(http: Http, domain: str, cfg) -> tuple[list[str], list[str]]:
    """Hit whatever paid-provider free tiers have keys configured."""
    apis = cfg.apis if cfg else {}
    emails: list[str] = []
    notes: list[str] = []

    key = str(apis.get("hunter_api_key", "") or "").strip()
    if key:
        try:
            d = http.get_json(
                f"https://api.hunter.io/v2/domain-search?domain={urllib.parse.quote(domain)}"
                f"&limit=10&api_key={urllib.parse.quote(key)}", ttl_s=86400)
            for e in d.get("data", {}).get("emails", []) or []:
                if e.get("value"):
                    emails.append(e["value"].lower())
            notes.append(f"hunter:{len(emails)}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"hunter-error:{type(e).__name__}")

    key = str(apis.get("apollo_api_key", "") or "").strip()
    if key:
        try:
            d = http.post_json(
                "https://api.apollo.io/api/v1/mixed_people/api_search",
                {"q_organization_domains_list": [domain],
                 "person_titles": ["founder", "ceo", "cmo", "head of marketing",
                                   "growth", "creative director", "brand manager",
                                   "ecommerce manager"],
                 "page": 1, "per_page": 10},
                headers={"X-Api-Key": key, "Cache-Control": "no-cache"},
                ttl_s=86400)
            people = d.get("people") or d.get("contacts") or []
            for p in people:
                if p.get("email"):
                    emails.append(p["email"].lower())
            notes.append(f"apollo:{len(people)}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"apollo-error:{type(e).__name__}")

    token = str(apis.get("snov_token", "") or "").strip()
    if token:
        try:
            d = http.get_json(
                "https://api.snov.io/v1/get-domain-emails-with-info?" +
                urllib.parse.urlencode({"domain": domain, "access_token": token}),
                ttl_s=86400)
            for e in d.get("emails", []) or []:
                if e.get("email"):
                    emails.append(e["email"].lower())
            notes.append(f"snov:{len(d.get('emails', []) or [])}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"snov-error:{type(e).__name__}")

    seen, uniq = set(), []
    for e in emails:
        if e not in seen and "@" in e:
            seen.add(e)
            uniq.append(e)
    return uniq, notes


# --------------------------------------------------------------------------
# Personalised opener (template-driven, no LLM, no hallucination)
# --------------------------------------------------------------------------


def draft_outreach(lead: dict, offer_name: str | None, cfg) -> dict:
    o = cfg.outreach or {}
    sender = o.get("sender_name", "there")
    company = lead.get("company") or lead.get("domain") or "your team"
    signal = _signal_line(lead)
    subject = _fill(o.get("subject", "Quick idea for {company}"),
                    company=company, offer=offer_name or "video",
                    signal=signal, sender=sender,
                    title=lead.get("title") or "")
    body = _fill(o.get("body", ""), company=company,
                 offer=offer_name or "video", signal=signal, sender=sender,
                 title=lead.get("title") or "", url=lead.get("url") or "your post")
    return {"subject": subject, "body": body}


def _signal_line(lead: dict) -> str:
    tags = lead.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    if "budget-1k-plus" in tags:
        return "you have budget behind this"
    if "says-they-need-it" in tags:
        return "you are looking for exactly this"
    if "multi-signal" in tags:
        return "this keeps coming up for you"
    if "advertiser" in tags:
        return "you are already running paid ads"
    return "this looked relevant to you"


def _fill(template: str, **kw) -> str:
    out = template or ""
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v or ""))
    return out
