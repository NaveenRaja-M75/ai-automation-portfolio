"""
LeadForge :: core utilities + SQLite store.

Dependency-free (Python 3.11+ standard library only).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Overpass / Nominatim reject browser UAs; they want an honest bot UA.
BOT_UA = "leadforge/1.0 (personal lead research; contact: you@example.com)"

_SSL = ssl.create_default_context()

# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_when(value) -> datetime | None:
    """Best-effort parse of anything a source throws at us -> aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_when(int(text))
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    cleaned = text.replace("Z", "+00:00")
    for candidate in (cleaned, cleaned.split(".")[0], cleaned[:19]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


def parse_window(spec: str) -> float | None:
    """'30d', '12h', '2w', '3m' -> age in hours. None = no limit."""
    if not spec or spec.lower() in ("all", "any", "0"):
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([hdwmy])\s*", spec.lower())
    if not m:
        raise ValueError(f"bad window {spec!r} (use e.g. 24h, 7d, 4w, 3m)")
    n, unit = int(m.group(1)), m.group(2)
    return float(n) * {"h": 1, "d": 24, "w": 168, "m": 730, "y": 8760}[unit]


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = _SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = unescape_entities(text)
    text = re.sub(r"[ \t\xa0\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def unescape_entities(text: str) -> str:
    import html as _html

    prev = None
    out = text
    for _ in range(3):  # double-encoded entities are common in scraped HTML
        if out == prev:
            break
        prev = out
        out = _html.unescape(out)
    return out


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
URL_RE = re.compile(r"https?://[^\s<>\"'\)\]\}]+")
HANDLE_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_]{3,30})\b")

_JUNK_EMAIL = re.compile(
    r"(example\.|\.png$|\.jpg$|\.jpeg$|\.gif$|\.webp$|\.svg$|sentry|wixpress|"
    r"@sentry|@2x|schema\.org|@w3\.org|domain\.com|yourdomain|email\.com|"
    r"@email|noreply@noreply|u003|cdn\.|@sites\.|placeholder)",
    re.I,
)
_JUNK_LOCAL = {
    "noreply", "no-reply", "donotreply", "postmaster", "abuse", "webmaster",
    "mailer-daemon", "bounce", "notifications", "wordpress",
}


def find_emails(text: str) -> list[str]:
    out, seen = [], set()
    for m in EMAIL_RE.finditer(text or ""):
        addr = m.group(0).strip(".,;:").lower()
        if addr in seen or _JUNK_EMAIL.search(addr):
            continue
        local = addr.split("@")[0]
        if local in _JUNK_LOCAL and local != "info":
            continue
        if len(addr) > 90:
            continue
        seen.add(addr)
        out.append(addr)
    return out


def find_urls(text: str) -> list[str]:
    out, seen = [], set()
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?)")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


_FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "outlook.com", "live.com", "icloud.com", "me.com", "proton.me",
    "protonmail.com", "aol.com", "gmx.com", "mail.com", "yandex.com",
    "rediffmail.com", "zoho.com", "qq.com", "163.com",
}

_SOCIAL_HOSTS = {
    "twitter.com", "x.com", "facebook.com", "instagram.com", "threads.net",
    "linkedin.com", "tiktok.com", "youtube.com", "youtu.be", "reddit.com",
    "github.com", "medium.com", "substack.com", "linktr.ee", "linktree.com",
    "behance.net", "dribbble.com", "notion.so", "calendly.com", "t.me",
    "discord.gg", "patreon.com", "gofundme.com", "producthunt.com",
    "news.ycombinator.com", "t.co", "bit.ly", "docs.google.com",
    "google.com", "forms.gle", "wa.me", "api.whatsapp.com", "zoom.us",
}


_HOST_RX = re.compile(
    r"^[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$"
)
# company-name noise that is not a domain label
_LEGAL_SUFFIX_RX = re.compile(
    r"[,\.]?\s*\b(inc|llc|l\.l\.c|ltd|limited|gmbh|corp|corporation|co|company|"
    r"plc|pvt|private|group|holdings|ag|sa|bv|nv|pty|srl|oy|ab|as)\b\.?\s*$",
    re.I,
)


def registrable_domain(url_or_domain: str) -> str | None:
    """'https://www.acme.co.uk/x' -> 'acme.co.uk'. Skips social/aggregator hosts.

    Returns None for anything that is not a real hostname - crucially, this must
    never turn a COMPANY NAME ("Checkr, Inc.") into a "domain".
    """
    if not url_or_domain:
        return None
    raw = str(url_or_domain).strip().rstrip(".")
    if not raw or " " in raw.strip():
        return None
    if "//" not in raw:
        raw = "http://" + raw
    try:
        host = (urllib.parse.urlsplit(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    if not _HOST_RX.match(host):
        return None
    tld = host.rsplit(".", 1)[-1]
    if not tld.isalpha() or not (2 <= len(tld) <= 24):
        return None
    if host.startswith("www."):
        host = host[4:]
    if host in _SOCIAL_HOSTS or any(host.endswith("." + h) for h in _SOCIAL_HOSTS):
        return None
    if host in _FREE_MAIL:
        return host
    parts = host.split(".")
    two_part = {"co", "com", "org", "net", "gov", "ac", "edu"}
    if len(parts) >= 3 and parts[-2] in two_part and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def company_to_slug(company: str) -> str | None:
    """'Checkr, Inc.' -> 'checkr'. Used to match a company name to a domain."""
    if not company:
        return None
    name = str(company).strip()
    name = _LEGAL_SUFFIX_RX.sub("", name)
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    return slug if len(slug) >= 3 else None


def email_domain(addr: str) -> str | None:
    return addr.split("@")[-1].lower() if "@" in addr else None


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------


class HttpError(Exception):
    pass


class Http:
    """Polite HTTP: per-host rate limiting, retries, optional disk cache."""

    def __init__(self, cache_dir=None, delay_ms=900, timeout=25, verbose=False):
        self.cache_dir = cache_dir
        self.delay_s = max(0.0, delay_ms / 1000.0)
        self.timeout = timeout
        self.verbose = verbose
        self._last: dict[str, float] = {}
        self.stats = {"ok": 0, "cached": 0, "error": 0, "bytes": 0}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # -- cache ------------------------------------------------------------
    def _cache_path(self, key: str):
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, hashlib.sha1(key.encode()).hexdigest() + ".cache")

    def _cache_read(self, key: str, ttl_s: int):
        p = self._cache_path(key)
        if not p or not os.path.exists(p):
            return None
        try:
            if time.time() - os.path.getmtime(p) > ttl_s:
                return None
            with open(p, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def _cache_write(self, key: str, body: bytes):
        p = self._cache_path(key)
        if not p:
            return
        try:
            with open(p, "wb") as fh:
                fh.write(body)
        except OSError:
            pass

    # -- throttle ---------------------------------------------------------
    def _wait(self, host: str):
        last = self._last.get(host, 0.0)
        gap = time.time() - last
        if gap < self.delay_s:
            time.sleep(self.delay_s - gap)
        self._last[host] = time.time()

    # -- request ----------------------------------------------------------
    def get(
        self,
        url: str,
        headers: dict | None = None,
        ua: str | None = None,
        accept: str | None = None,
        retries: int = 2,
        ttl_s: int = 0,
        method: str = "GET",
        timeout: float | None = None,
    ) -> bytes:
        timeout = self.timeout if timeout is None else timeout
        key = method + " " + url
        if ttl_s:
            hit = self._cache_read(key, ttl_s)
            if hit is not None:
                self.stats["cached"] += 1
                return hit

        hdrs = {
            "User-Agent": ua or BROWSER_UA,
            "Accept": accept or "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        if headers:
            hdrs.update(headers)

        host = urllib.parse.urlsplit(url).hostname or "?"
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                req = urllib.request.Request(url, headers=hdrs, method=method)
                with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
                    body = resp.read()
                self.stats["ok"] += 1
                self.stats["bytes"] += len(body)
                if ttl_s:
                    self._cache_write(key, body)
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                # 4xx (except 429) will not fix themselves
                if e.code not in (429, 408, 500, 502, 503, 504):
                    self.stats["error"] += 1
                    raise HttpError(f"HTTP {e.code} {e.reason} :: {url}") from e
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:  # noqa: BLE001 - network layer, report and retry
                last_err = e
                time.sleep(1.2 * (attempt + 1))
        self.stats["error"] += 1
        raise HttpError(f"{type(last_err).__name__}: {last_err} :: {url}")

    def post_form(self, url: str, body: bytes, headers: dict | None = None,
                  ua: str | None = None, retries: int = 2) -> bytes:
        """POST an application/x-www-form-urlencoded body (used for OAuth)."""
        hdrs = {
            "User-Agent": ua or BOT_UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        if headers:
            hdrs.update(headers)
        host = urllib.parse.urlsplit(url).hostname or "?"
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL) as resp:
                    out = resp.read()
                self.stats["ok"] += 1
                return out
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code not in (429, 408, 500, 502, 503, 504):
                    raise HttpError(f"HTTP {e.code} {e.reason} :: {url}") from e
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.2 * (attempt + 1))
        raise HttpError(f"{type(last_err).__name__}: {last_err} :: {url}")

    def post_json(self, url: str, payload: dict, headers: dict | None = None,
                  ttl_s: int = 0) -> dict:
        """POST a JSON body. Used for provider APIs that require POST."""
        key = "POST " + url + json.dumps(payload, sort_keys=True)
        if ttl_s:
            hit = self._cache_read(key, ttl_s)
            if hit is not None:
                self.stats["cached"] += 1
                return json.loads(hit.decode("utf-8", "replace"))
        hdrs = {"User-Agent": BOT_UA, "Accept": "application/json",
                "Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        host = urllib.parse.urlsplit(url).hostname or "?"
        self._wait(host)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL) as resp:
            body = resp.read()
        self.stats["ok"] += 1
        self.stats["bytes"] += len(body)
        if ttl_s:
            self._cache_write(key, body)
        return json.loads(body.decode("utf-8", "replace"))

    def get_json(self, url, **kw):
        kw.setdefault("accept", "application/json, */*")
        raw = self.get(url, **kw)
        return json.loads(raw.decode("utf-8", "replace"))

    def get_text(self, url, **kw):
        return self.get(url, **kw).decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Fingerprints
# --------------------------------------------------------------------------


def fingerprint(source: str, external_id: str | None, url: str | None, fallback: str) -> str:
    base = f"{source}|{external_id}" if external_id else (url or fallback or "")
    norm = re.sub(r"\s+", " ", (base or "").lower()).strip()
    norm = norm.split("?")[0].rstrip("/")
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:20]


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint    TEXT UNIQUE NOT NULL,
  source         TEXT NOT NULL,
  external_id    TEXT,
  url            TEXT,
  title          TEXT,
  body           TEXT,
  author         TEXT,
  company        TEXT,
  domain         TEXT,
  location       TEXT,
  posted_ts      INTEGER,
  posted_at      TEXT,
  discovered_at  TEXT,
  budget_raw     TEXT,
  budget_max     REAL,
  currency       TEXT,
  score          INTEGER DEFAULT 0,
  tier           TEXT DEFAULT 'D',
  tags           TEXT,
  offers         TEXT,
  emails         TEXT,
  contact_email  TEXT,
  contact_name   TEXT,
  contact_role   TEXT,
  mx_ok          INTEGER,
  status         TEXT DEFAULT 'new',
  notes          TEXT,
  raw            TEXT,
  updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_score  ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_tier   ON leads(tier);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_src    ON leads(source);

CREATE TABLE IF NOT EXISTS runs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, ended_at TEXT,
  source     TEXT, found INTEGER, new INTEGER, error TEXT
);
"""

LEAD_COLUMNS = [
    "fingerprint", "source", "external_id", "url", "title", "body", "author",
    "company", "domain", "location", "posted_ts", "posted_at", "discovered_at",
    "budget_raw", "budget_max", "currency", "score", "tier", "tags", "offers",
    "emails", "contact_email", "contact_name", "contact_role", "mx_ok",
    "status", "notes", "raw", "updated_at",
]


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # -- write ------------------------------------------------------------
    def upsert(self, lead: dict) -> tuple[int, bool]:
        """Insert or refresh a lead. Returns (id, created)."""
        fp = lead["fingerprint"]
        row = self.conn.execute(
            "SELECT id, score FROM leads WHERE fingerprint = ?", (fp,)
        ).fetchone()
        now = iso(utcnow())
        lead = dict(lead)
        lead["updated_at"] = now
        lead.setdefault("discovered_at", now)

        if row:
            updates = {
                k: v for k, v in lead.items()
                if k in LEAD_COLUMNS and k not in ("fingerprint", "status", "notes")
            }
            sets = ", ".join(f"{k} = ?" for k in updates)
            self.conn.execute(
                f"UPDATE leads SET {sets} WHERE id = ?",
                list(updates.values()) + [row["id"]],
            )
            self.conn.commit()
            return row["id"], False

        cols = [c for c in LEAD_COLUMNS if c in lead]
        placeholders = ", ".join("?" for _ in cols)
        cur = self.conn.execute(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})",
            [lead[c] for c in cols],
        )
        self.conn.commit()
        return cur.lastrowid, True

    def update(self, lead_id: int, **fields):
        fields["updated_at"] = iso(utcnow())
        fields = {k: v for k, v in fields.items() if k in LEAD_COLUMNS}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE leads SET {sets} WHERE id = ?", list(fields.values()) + [lead_id]
        )
        self.conn.commit()

    def log_run(self, source, found, new, error=None, started=None, ended=None):
        self.conn.execute(
            "INSERT INTO runs (started_at, ended_at, source, found, new, error) "
            "VALUES (?,?,?,?,?,?)",
            (iso(started) or iso(utcnow()), iso(ended) or iso(utcnow()),
             source, found, new, error),
        )
        self.conn.commit()

    # -- read -------------------------------------------------------------
    def find(self, fp: str):
        return self.conn.execute(
            "SELECT * FROM leads WHERE fingerprint = ?", (fp,)
        ).fetchone()

    @staticmethod
    def _as_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return [str(v) for v in value]

    def query(
        self,
        tier=None,
        min_score=None,
        sources=None,
        status=None,
        since_hours=None,
        domain=None,
        offer=None,
        where_extra=None,
        limit=200,
        order="score DESC, posted_ts DESC",
    ) -> list[sqlite3.Row]:
        clauses, params = [], []
        tiers = self._as_list(tier)
        if tiers:
            clauses.append(f"tier IN ({', '.join('?' for _ in tiers)})")
            params += tiers
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        srcs = self._as_list(sources)
        if srcs:
            clauses.append(f"source IN ({', '.join('?' for _ in srcs)})")
            params += srcs
        sts = self._as_list(status)
        if sts:
            clauses.append(f"status IN ({', '.join('?' for _ in sts)})")
            params += sts
        if since_hours:
            clauses.append("COALESCE(posted_ts, 0) >= ?")
            params.append(int(time.time() - since_hours * 3600))
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if offer:
            clauses.append("offers LIKE ?")
            params.append(f'%"{offer}"%')
        if where_extra:
            clauses.append(f"({where_extra})")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM leads{where} ORDER BY {order} LIMIT ?"
        params.append(limit)
        return list(self.conn.execute(sql, params))

    def unenriched(self, tier=None, min_score=None, limit=50, only_missing_email=True,
                   source=None):
        clauses = ["(domain IS NOT NULL OR company IS NOT NULL)"]
        params: list = []
        tiers = self._as_list(tier)
        if tiers:
            clauses.append(f"tier IN ({', '.join('?' for _ in tiers)})")
            params += tiers
        if source:
            srcs = self._as_list(source)
            if srcs:
                clauses.append(f"source IN ({', '.join('?' for _ in srcs)})")
                params += srcs
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        if only_missing_email:
            clauses.append("(contact_email IS NULL OR contact_email = '')")
        params.append(limit)
        return list(self.conn.execute(
            f"SELECT * FROM leads WHERE {' AND '.join(clauses)} "
            f"ORDER BY score DESC LIMIT ?",
            params,
        ))

    def counts(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT source, COUNT(*) n, MAX(discovered_at) last "
            "FROM leads GROUP BY source ORDER BY n DESC"
        ))

    def total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
