"""Turn fetched HTML into structured signals.

Absence matters as much as presence here. "No booking widget", "no email",
"WhatsApp link but no automation" are the findings that drive the score.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..models import SiteSignals

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_YEAR_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)
_PHONE_IN_RE = re.compile(r"(?:\+?91[\s\-]?)?[6-9]\d{9}")

# Addresses that are never a lead's real inbox.
_EMAIL_NOISE = (
    "example.com", "sentry.io", "wixpress.com", "godaddy.com", "squarespace.com",
    "@2x", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "@sentry",
    "domain.com", "yourdomain", "email.com",
)

_PLATFORMS = {
    "wordpress": ("wp-content", "wp-includes", "wordpress"),
    "wix": ("wix.com", "wixstatic", "parastorage"),
    "squarespace": ("squarespace", "sqsp.net"),
    "shopify": ("cdn.shopify", "shopify.com"),
    "webflow": ("webflow.io", "assets.website-files.com"),
    "godaddy": ("godaddysites", "img1.wsimg.com"),
    "elementor": ("elementor",),
}

_BOOKING_PLATFORMS = {
    "practo": ("practo.com",),
    "calendly": ("calendly.com",),
    "zocdoc": ("zocdoc.com",),
    "setmore": ("setmore.com",),
    "zoho_bookings": ("bookings.zoho",),
    "acuity": ("acuityscheduling",),
    "nobroker": ("nobroker.in",),
    "housing": ("housing.com",),
    "99acres": ("99acres.com",),
    "magicbricks": ("magicbricks.com",),
}

_SOCIALS = {
    "instagram": "instagram.com",
    "facebook": "facebook.com",
    "linkedin": "linkedin.com",
    "youtube": "youtube.com",
    "twitter": "twitter.com",
}

_BOOK_WORDS = ("book appointment", "book now", "schedule a visit", "book online",
               "request appointment", "book a slot", "schedule appointment")


def extract(html: str, final_url: str, signals: SiteSignals) -> SiteSignals:
    soup = BeautifulSoup(html, "lxml")
    lowered = html.lower()
    text = soup.get_text(" ", strip=True)

    signals.final_url = final_url
    signals.https = final_url.startswith("https://")
    signals.page_bytes = len(html)
    signals.mobile_viewport = bool(soup.find("meta", attrs={"name": "viewport"}))
    signals.text_sample = text[:6000]

    # --- platform ---------------------------------------------------------
    generator = soup.find("meta", attrs={"name": "generator"})
    gen_content = (generator.get("content", "") if generator else "").lower()
    for platform, needles in _PLATFORMS.items():
        if platform in gen_content or any(n in lowered for n in needles):
            signals.platform = platform
            break

    # --- links ------------------------------------------------------------
    hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]

    for href in hrefs:
        low = href.lower()
        if low.startswith("mailto:"):
            _add_email(signals, low[7:].split("?")[0])
        if "wa.me/" in low or "api.whatsapp.com" in low or "web.whatsapp.com" in low:
            signals.whatsapp = signals.whatsapp or href
        for network, domain in _SOCIALS.items():
            if domain in low and network not in signals.socials:
                signals.socials[network] = href
        for name, needles in _BOOKING_PLATFORMS.items():
            if any(n in low for n in needles):
                signals.booking_platform = signals.booking_platform or name

    for candidate in _EMAIL_RE.findall(text[:20000]):
        _add_email(signals, candidate)

    # --- booking method ---------------------------------------------------
    has_book_word = any(w in lowered for w in _BOOK_WORDS)
    has_form = bool(soup.find("form"))
    if signals.booking_platform:
        signals.booking_method = "online"
    elif has_book_word and has_form:
        signals.booking_method = "form_only"
    elif has_form:
        signals.booking_method = "form_only"
    elif _PHONE_IN_RE.search(text) or signals.whatsapp:
        signals.booking_method = "call_only"
    else:
        signals.booking_method = "unknown"

    # --- staff / owner ----------------------------------------------------
    doctors = set(re.findall(r"\bDr\.?\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?", text))
    if doctors:
        signals.staff_count = len(doctors)
        signals.owner_name = sorted(doctors)[0]

    # --- staleness --------------------------------------------------------
    year_match = _YEAR_RE.search(text)
    if year_match:
        try:
            signals.copyright_year = int(year_match.group(1))
        except ValueError:
            pass

    return signals


def _add_email(signals: SiteSignals, candidate: str) -> None:
    candidate = candidate.strip().lower()
    if not candidate or "@" not in candidate:
        return
    if any(noise in candidate for noise in _EMAIL_NOISE):
        return
    if candidate not in signals.emails and len(signals.emails) < 5:
        signals.emails.append(candidate)


def normalise_url(url: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    return url
