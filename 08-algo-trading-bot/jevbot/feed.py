"""Binance spot public market data: best bid/ask, top-20 depth and aggregated trades.

Uses the market-data-only endpoint (no account, no key). Prices come from mainnet in
every mode, including testnet, because testnet books are thin and do not track the real
market; see the README for what that means for testnet P&L.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import aiohttp
import websockets

log = logging.getLogger("jevbot.feed")

WS_BASE = "wss://data-stream.binance.vision/stream?streams="
REST_BASE = "https://data-api.binance.vision"


@dataclass
class Trade:
    ts: float          # exchange time, seconds
    price: float
    qty: float
    taker_buy: bool    # True when the aggressor bought (Binance m=False)


@dataclass
class SymbolBook:
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    bids: list[tuple[float, float]] = field(default_factory=list)   # best first
    asks: list[tuple[float, float]] = field(default_factory=list)
    trades: deque = field(default_factory=lambda: deque(maxlen=50_000))
    updated: float = 0.0     # local monotonic time of the last message of any kind

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else 0.0

    @property
    def spread_pct(self) -> float:
        m = self.mid
        return (self.ask - self.bid) / m if m else 0.0

    def age(self) -> float:
        return time.monotonic() - self.updated if self.updated else float("inf")

    def ready(self) -> bool:
        return self.bid > 0 and self.ask > self.bid and bool(self.bids) and bool(self.asks)


@dataclass(frozen=True)
class SymbolRules:
    """Exchange filters. Orders that break them are rejected by Binance, so paper mode
    applies them too, otherwise paper fills would be ones live could never get."""
    symbol: str
    step_size: float
    min_qty: float
    tick_size: float
    min_notional: float


class BinanceFeed:
    def __init__(self, symbols: tuple[str, ...]):
        self.books = {s: SymbolBook(s) for s in symbols}
        self.rules: dict[str, SymbolRules] = {}
        self.connected = False
        self.reconnects = 0
        self._stop = asyncio.Event()

    async def load_rules(self, session: aiohttp.ClientSession) -> None:
        syms = json.dumps(list(self.books), separators=(",", ":"))
        async with session.get(f"{REST_BASE}/api/v3/exchangeInfo", params={"symbols": syms},
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            r.raise_for_status()
            info = await r.json()
        for s in info["symbols"]:
            f = {x["filterType"]: x for x in s["filters"]}
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            self.rules[s["symbol"]] = SymbolRules(
                symbol=s["symbol"],
                step_size=float(f["LOT_SIZE"]["stepSize"]),
                min_qty=float(f["LOT_SIZE"]["minQty"]),
                tick_size=float(f["PRICE_FILTER"]["tickSize"]),
                min_notional=float(notional.get("minNotional", 0.0)),
            )
        missing = set(self.books) - set(self.rules)
        if missing:
            raise RuntimeError(f"unknown symbols on Binance spot: {sorted(missing)}")

    async def history_1s(self, session: aiohttp.ClientSession, symbol: str,
                         seconds: int) -> list[tuple[float, float]]:
        """Recent 1-second closes, so features are ready at startup instead of after a
        15-minute warm-up. Closes are last-trade prices, not mids; the difference is a
        fraction of one tick on these pairs and only affects the first minutes."""
        out: list[tuple[float, float]] = []
        end = int(time.time() * 1000)
        start = end - seconds * 1000
        while start < end:
            async with session.get(f"{REST_BASE}/api/v3/klines",
                                   params={"symbol": symbol, "interval": "1s",
                                           "startTime": start, "limit": 1000},
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()
                rows = await r.json()
            if not rows:
                break
            # Skip the still-forming candle: its close time is in the future.
            out += [((k[6] + 1) / 1000.0, float(k[4])) for k in rows if k[6] < end]
            start = rows[-1][6] + 1
        return out

    def _url(self) -> str:
        streams = []
        for s in self.books:
            k = s.lower()
            streams += [f"{k}@bookTicker", f"{k}@depth20@100ms", f"{k}@aggTrade"]
        return WS_BASE + "/".join(streams)

    def _on_message(self, raw: str) -> None:
        msg = json.loads(raw)
        stream, d = msg.get("stream", "").lower(), msg.get("data", {})
        sym = stream.split("@", 1)[0].upper()
        book = self.books.get(sym)
        if book is None:
            return
        if stream.endswith("@bookticker"):
            book.bid, book.bid_qty = float(d["b"]), float(d["B"])
            book.ask, book.ask_qty = float(d["a"]), float(d["A"])
        elif "@depth20" in stream:
            book.bids = [(float(p), float(q)) for p, q in d["bids"]]
            book.asks = [(float(p), float(q)) for p, q in d["asks"]]
        elif stream.endswith("@aggtrade"):
            book.trades.append(Trade(d["T"] / 1000.0, float(d["p"]), float(d["q"]), not d["m"]))
        book.updated = time.monotonic()

    async def run(self) -> None:
        """Reconnect forever with capped exponential backoff. Binance drops every
        connection at 24 h, so reconnecting is normal operation, not an error path."""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self._url(), ping_interval=20, ping_timeout=20,
                                              max_size=2**22) as ws:
                    self.connected, backoff = True, 1.0
                    log.info("feed connected: %s", ", ".join(self.books))
                    async for raw in ws:
                        self._on_message(raw)
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors are expected; log and retry
                log.warning("feed dropped (%s); reconnecting in %.0fs", exc, backoff)
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def stop(self) -> None:
        self._stop.set()
