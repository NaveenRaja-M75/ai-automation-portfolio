"""Fetch a business website and extract signals.

Politeness rules, because these are small businesses and you will be emailing
them later: robots.txt is honoured, the user agent identifies the crawler and
carries a contact address, responses are size-capped, and only a handful of
obvious pages are fetched per site.
"""

from __future__ import annotations

import urllib.robotparser
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import httpx

from ..budget import Budget
from ..config import settings
from ..models import Business, SiteSignals
from .extract import extract, normalise_url


@lru_cache(maxsize=512)
def _robots(origin: str, user_agent: str) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(urljoin(origin, "/robots.txt"))
    try:
        resp = httpx.get(
            urljoin(origin, "/robots.txt"),
            headers={"User-Agent": user_agent},
            timeout=10,
            follow_redirects=True,
        )
        parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except Exception:
        parser.parse([])  # unreachable robots.txt is treated as permissive
    return parser


def enrich(biz: Business, budget: Budget) -> SiteSignals:
    cfg = settings()["enrichment"]
    signals = SiteSignals()

    if not cfg.get("enabled", True):
        return signals

    url = normalise_url(biz.website or "")
    if not url:
        signals.fetch_error = "no website listed"
        return signals

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    user_agent = cfg["user_agent"]
    robots = _robots(origin, user_agent) if cfg.get("respect_robots", True) else None

    combined_html = ""
    final_url = url
    fetched_any = False

    for path in cfg.get("paths", ["/"]):
        target = urljoin(origin, path)
        if robots is not None and not robots.can_fetch(user_agent, target):
            continue
        try:
            budget.spend_enrichment()
        except Exception:
            break  # ceiling reached - keep whatever we already fetched

        html = _get(target, user_agent, int(cfg["timeout_s"]), int(cfg["max_bytes"]))
        if html is None:
            continue
        fetched_any = True
        if path == "/":
            final_url = target
        combined_html += html

    if not fetched_any:
        signals.fetch_error = "site unreachable or blocked"
        return signals

    signals.fetched = True
    return extract(combined_html, final_url, signals)


def _get(url: str, user_agent: str, timeout_s: int, max_bytes: int) -> str | None:
    try:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            follow_redirects=True,
        ) as resp:
            if resp.status_code >= 400:
                return None
            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type.lower():
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception:
        return None
