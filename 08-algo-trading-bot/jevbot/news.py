"""News signal: Jev reads crypto headlines and judges them, which is the job Jev's own
docs say it is built for ("instinctive judgment and common sense over large bodies of
text"), unlike guessing the next tick from numbers.

Free public RSS feeds, polled every 2 minutes. Each new headline gets ONE Jev call with
six yes/no questions (which coin, whole market, good news, bad news). Per symbol, the
signal is the relevance-weighted, time-decayed tone of the last two hours of headlines.

Headlines are third-party text. Jev's docs warn that state written to steer the model
can move its answer, so headlines only ever feed a probability that the evaluator
calibrates and the guards bound. They can never place an order on their own.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import aiohttp

log = logging.getLogger("jevbot.news")

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}
POLL_S = 120
WINDOW_S = 2 * 3600
HALF_LIFE_S = 1800
MAX_SCORED_PER_POLL = 25
ATOM = "{http://www.w3.org/2005/Atom}"

COINS = {"BTCUSDT": ("btc", "Bitcoin (BTC)"), "ETHUSDT": ("eth", "Ethereum (ETH)"), "SOLUSDT": ("sol", "Solana (SOL)")}


def _noul(instr: str, yes: str, no: str) -> dict:
    return {"type": "noul", "instructions": instr, "criteria": {"true": yes, "false": no}}


def questions(symbols: tuple[str, ...]) -> dict:
    q = {}
    for sym in symbols:
        if sym in COINS:
            key, name = COINS[sym]
            q[key] = _noul(f"Is `headline` mainly about {name}?",
                           f"Mainly about {name}", f"Not mainly about {name}")
    q["market"] = _noul(
        "Does `headline` describe something that moves the whole crypto market, such as regulation, "
        "interest rates, ETF flows, a major exchange, or a large hack?",
        "Affects the whole crypto market", "Only affects a single project, person or niche")
    q["good"] = _noul(
        "Would a typical crypto trader read `headline` as good news for crypto prices?",
        "Good news: makes buyers more eager", "Not good news: neutral, unclear, or bad")
    q["bad"] = _noul(
        "Would a typical crypto trader read `headline` as bad news for crypto prices?",
        "Bad news: makes sellers more eager", "Not bad news: neutral, unclear, or good")
    return q


def parse_feed(xml: bytes, source: str, now: float | None = None) -> list[tuple[str, str, float]]:
    """(title, link, published_ts) for RSS 2.0 or Atom. Undated items get `now`."""
    now = now or time.time()
    out = []
    root = ET.fromstring(xml)
    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    for it in items:
        title = (it.findtext("title") or it.findtext(f"{ATOM}title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:
            el = it.find(f"{ATOM}link")
            link = el.get("href", "") if el is not None else ""
        raw = it.findtext("pubDate") or it.findtext(f"{ATOM}updated") or it.findtext(f"{ATOM}published")
        ts = now
        if raw:
            try:
                ts = parsedate_to_datetime(raw.strip()).timestamp()
            except (TypeError, ValueError):
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = now
        if title:
            out.append((title, link or title, min(ts, now)))
    return out


@dataclass
class Headline:
    title: str
    source: str
    ts: float
    scores: dict = field(default_factory=dict)


def aggregate(headlines: list[Headline], symbol: str, now: float) -> tuple[float, float, int]:
    """(tone in [-1, 1], evidence weight, count). Relevance is the stronger of 'about this
    coin' and 'moves the whole market'; older headlines fade with a 30-minute half-life."""
    coin = COINS.get(symbol, ("", ""))[0]
    num = den = 0.0
    n = 0
    for h in headlines:
        if not h.scores or now - h.ts > WINDOW_S:
            continue
        rel = max(h.scores.get(coin, 0.0), h.scores.get("market", 0.0))
        if rel < 0.5:
            continue
        w = rel * 0.5 ** ((now - h.ts) / HALF_LIFE_S)
        num += w * (h.scores.get("good", 0.0) - h.scores.get("bad", 0.0))
        den += w
        n += 1
    return (num / den if den else 0.0), den, n


def tone_words(tone: float, n: int) -> str:
    if n == 0:
        return "no relevant headlines in the last two hours"
    word = ("mostly bad news" if tone < -0.3 else "somewhat bad news" if tone < -0.1 else
            "mixed or neutral news" if tone <= 0.1 else "somewhat good news" if tone <= 0.3 else "mostly good news")
    return f"{word} ({'one headline' if n == 1 else 'several headlines' if n < 6 else 'many headlines'})"


class NewsSignal:
    name = "news"

    def __init__(self, symbols: tuple[str, ...], jev, horizons: tuple[int, ...]):
        self.symbols, self.jev, self.horizons = symbols, jev, horizons
        self.headlines: dict[str, Headline] = {}
        self.status = "starting"
        self.questions = questions(symbols)

    async def run(self) -> None:
        headers = {"User-Agent": "Mozilla/5.0 (jevbot news reader)"}
        async with aiohttp.ClientSession(headers=headers) as http:
            while True:
                try:
                    await self._poll(http)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.status = f"error: {exc!r}"[:160]
                    log.warning("news poll failed: %s", exc)
                await asyncio.sleep(POLL_S)

    async def _fetch(self, http: aiohttp.ClientSession, source: str, url: str):
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            return parse_feed(await r.read(), source)

    async def _poll(self, http: aiohttp.ClientSession) -> None:
        now = time.time()
        results = await asyncio.gather(*(self._fetch(http, s, u) for s, u in FEEDS.items()), return_exceptions=True)
        ok_feeds = 0
        for source, res in zip(FEEDS, results):
            if isinstance(res, Exception):
                continue
            ok_feeds += 1
            for title, link, ts in res:
                if now - ts <= WINDOW_S and link not in self.headlines:
                    self.headlines[link] = Headline(title, source, ts)
        for k in [k for k, h in self.headlines.items() if now - h.ts > WINDOW_S]:
            del self.headlines[k]
        pending = sorted((h for h in self.headlines.values() if not h.scores), key=lambda h: -h.ts)
        scored = 0
        for h in pending[:MAX_SCORED_PER_POLL]:
            answers = await self.jev.ask({"headline": h.title, "source": h.source}, self.questions, timeout=5.0)
            if answers is None:
                break   # budget, backoff or error: try again next poll
            h.scores = {k: float(v.get("noul", 0.0)) for k, v in answers.items() if isinstance(v, dict)}
            scored += 1
        total = len(self.headlines)
        done = sum(1 for h in self.headlines.values() if h.scores)
        self.status = f"ok: {ok_feeds}/{len(FEEDS)} feeds, {done}/{total} headlines judged"

    def view(self, symbol: str, now: float | None = None) -> tuple[float, float, int]:
        return aggregate(list(self.headlines.values()), symbol, now or time.time())

    async def predict(self, f) -> dict[int, float] | None:
        tone, weight, n = self.view(f.symbol)
        if weight < 0.5:
            return None   # too little relevant news to say anything
        p = 0.5 + 0.35 * tone * min(1.0, weight / 2.0)
        return {h: p for h in self.horizons} if math.isfinite(p) else None

    def words(self, symbol: str) -> str:
        tone, _, n = self.view(symbol)
        return tone_words(tone, n)

    def recent(self, limit: int = 8) -> list[dict]:
        hs = sorted((h for h in self.headlines.values() if h.scores), key=lambda h: -h.ts)[:limit]
        return [{"title": h.title, "source": h.source, "age_min": int((time.time() - h.ts) / 60),
                 "good": round(h.scores.get("good", 0), 2), "bad": round(h.scores.get("bad", 0), 2)} for h in hs]
