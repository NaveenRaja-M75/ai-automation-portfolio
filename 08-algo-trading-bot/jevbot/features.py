"""Turn the live book into features: numbers for code, words for Jev.

Jev's own docs (model-jaggedness/jev-1.13, reviewed 2026-10-02) say it "struggles with
tasks that require numeric precision" and does better on named buckets than on raw
numbers. So all arithmetic happens here, and Jev only ever sees the bucket labels.
Every bucket is scaled by current volatility, so "rising sharply" means the same thing
on a calm day and a wild one.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

from .feed import SymbolBook

HISTORY_S = 1800  # 30 minutes of 1-second mids


class MidHistory:
    """One mid per second per symbol. Sampling on a fixed clock (rather than per
    message) keeps return windows comparable between busy and quiet periods."""

    def __init__(self) -> None:
        self.points: deque[tuple[float, float]] = deque(maxlen=HISTORY_S)

    def add(self, ts: float, mid: float) -> None:
        """Rejects samples closer than 0.5 s to the previous one (or out of order), so a
        timer that fires twice near a second boundary cannot compress the history and
        make volatility read as zero."""
        if mid > 0 and math.isfinite(mid) and (not self.points or ts - self.points[-1][0] >= 0.5):
            self.points.append((ts, mid))

    def at_or_before(self, ts: float) -> float | None:
        """Latest mid sampled at or before ts. Never looks forward."""
        best = None
        for t, m in reversed(self.points):
            if t <= ts:
                best = m
                break
        return best

    def ret(self, seconds: int) -> float | None:
        if not self.points:
            return None
        now_t, now_m = self.points[-1]
        past = self.at_or_before(now_t - seconds)
        return None if past is None else now_m / past - 1.0

    def vol_1s(self, window: int) -> float | None:
        """Std of 1-second log returns over the last `window` samples."""
        pts = list(self.points)[-(window + 1):]
        if len(pts) < 30:
            return None
        rs = [math.log(b[1] / a[1]) for a, b in zip(pts, pts[1:])]
        mu = sum(rs) / len(rs)
        var = sum((r - mu) ** 2 for r in rs) / (len(rs) - 1)
        # A minute with no mid change is a real (very calm) market, not missing data.
        return max(math.sqrt(var), 1e-7)

    def warm(self, seconds: int) -> bool:
        return len(self.points) >= 2 and self.points[-1][0] - self.points[0][0] >= seconds


@dataclass(frozen=True)
class Features:
    symbol: str
    ts: float
    mid: float
    bid: float
    ask: float
    spread_pct: float
    ret: dict[int, float]          # window seconds -> fractional return
    vol_1s: float                  # per-second log-return std over 5 min
    vol_ratio: float               # 1-min vol / 15-min vol
    imbalance: dict[int, float]    # band bps -> (bid-ask)/(bid+ask) resting size
    flow: dict[int, float]         # window seconds -> taker (buy-sell)/(buy+sell)
    intensity: float               # trades last 60 s / average 60 s over 15 min

    def horizon_vol(self, seconds: int) -> float:
        return self.vol_1s * math.sqrt(seconds)

    def vector(self) -> list[float]:
        """Inputs for the local model. Returns are volatility-normalised so the model
        learns shapes rather than the absolute size of moves on one particular day."""
        z = [self.ret[w] / max(self.horizon_vol(w), 1e-9) for w in (10, 60, 300, 900)]
        return z + [self.imbalance[10], self.imbalance[25], self.flow[60], self.flow[300],
                    math.log(max(self.intensity, 1e-3)), math.log(max(self.vol_ratio, 1e-3))]


RETURN_WINDOWS = (10, 60, 300, 900)


def _imbalance(book: SymbolBook, band_bps: int) -> float:
    mid = book.mid
    lo, hi = mid * (1 - band_bps / 1e4), mid * (1 + band_bps / 1e4)
    b = sum(q for p, q in book.bids if p >= lo)
    a = sum(q for p, q in book.asks if p <= hi)
    return (b - a) / (b + a) if b + a > 0 else 0.0


def _flow(book: SymbolBook, now: float, seconds: int) -> tuple[float, int]:
    buy = sell = 0.0
    n = 0
    for t in reversed(book.trades):
        if t.ts < now - seconds:
            break
        n += 1
        if t.taker_buy:
            buy += t.qty
        else:
            sell += t.qty
    return ((buy - sell) / (buy + sell) if buy + sell > 0 else 0.0), n


def compute(book: SymbolBook, hist: MidHistory, now: float | None = None) -> Features | None:
    """None until there is enough history to fill every field honestly."""
    if not book.ready() or not hist.warm(900):
        return None
    now = now or time.time()
    rets = {w: hist.ret(w) for w in RETURN_WINDOWS}
    vol = hist.vol_1s(300)
    vol_short, vol_long = hist.vol_1s(60), hist.vol_1s(900)
    if vol is None or vol_short is None or vol_long is None or any(v is None for v in rets.values()):
        return None
    f60, n60 = _flow(book, now, 60)
    f300, _ = _flow(book, now, 300)
    _, n900 = _flow(book, now, 900)
    # Until the trade buffer spans 15 minutes the baseline is unknown, so report
    # "normal" rather than a ratio computed against a partial window.
    spans = bool(book.trades) and book.trades[0].ts <= now - 900
    intensity = n60 / (n900 / 15) if spans and n900 else 1.0
    return Features(
        symbol=book.symbol, ts=now, mid=book.mid, bid=book.bid, ask=book.ask,
        spread_pct=book.spread_pct, ret=rets, vol_1s=vol, vol_ratio=vol_short / vol_long,
        imbalance={10: _imbalance(book, 10), 25: _imbalance(book, 25)},
        flow={60: f60, 300: f300}, intensity=intensity,
    )


# ---- words for Jev -------------------------------------------------------------------

def _bucket(x: float, edges: tuple[float, ...], labels: tuple[str, ...]) -> str:
    for edge, label in zip(edges, labels):
        if x < edge:
            return label
    return labels[-1]


def trend_word(z: float) -> str:
    return _bucket(z, (-2.0, -0.75, 0.75, 2.0),
                   ("falling sharply", "falling", "flat", "rising", "rising sharply"))


def flow_word(x: float) -> str:
    return _bucket(x, (-0.4, -0.15, 0.15, 0.4),
                   ("sellers dominating", "more selling than buying", "balanced",
                    "more buying than selling", "buyers dominating"))


def book_word(x: float) -> str:
    return _bucket(x, (-0.5, -0.2, 0.2, 0.5),
                   ("far more resting asks than bids", "more resting asks than bids", "balanced",
                    "more resting bids than asks", "far more resting bids than asks"))


def activity_word(x: float) -> str:
    return _bucket(x, (0.5, 0.8, 1.25, 2.0),
                   ("very quiet", "quieter than usual", "normal", "busier than usual", "very busy"))


def vol_word(x: float) -> str:
    return _bucket(x, (0.6, 0.85, 1.2, 1.7),
                   ("much calmer than usual", "calmer than usual", "normal",
                    "more volatile than usual", "much more volatile than usual"))


def horizon_phrase(seconds: int) -> str:
    return f"the next {seconds // 60} minutes" if seconds >= 120 else f"the next {seconds} seconds"


def market_name(symbol: str) -> str:
    for quote in ("USDT", "USDC", "FDUSD", "BTC"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[:-len(quote)]}/{quote}"
    return symbol


def describe(f: Features, horizon_s: int) -> dict:
    """The state Jev sees. Words only, no prices, no arithmetic left for the model."""
    z = {w: f.ret[w] / max(f.horizon_vol(w), 1e-12) for w in RETURN_WINDOWS}
    return {
        "market": f"{market_name(f.symbol)} spot",
        "horizon": horizon_phrase(horizon_s),
        "price_trend": {
            "last 10 seconds": trend_word(z[10]),
            "last 1 minute": trend_word(z[60]),
            "last 5 minutes": trend_word(z[300]),
            "last 15 minutes": trend_word(z[900]),
        },
        "taker_flow": {
            "last 1 minute": flow_word(f.flow[60]),
            "last 5 minutes": flow_word(f.flow[300]),
        },
        "order_book": {
            "within 0.10% of price": book_word(f.imbalance[10]),
            "within 0.25% of price": book_word(f.imbalance[25]),
        },
        "trading_activity": activity_word(f.intensity),
        "volatility": vol_word(f.vol_ratio),
    }
