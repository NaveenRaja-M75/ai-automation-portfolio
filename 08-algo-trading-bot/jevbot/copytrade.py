"""Copy-trader signal: what Hyperliquid's best-performing traders are holding right now.

Read-only public data (no account, no key, nothing traded on Hyperliquid):
  leaderboard  https://stats-data.hyperliquid.xyz/Mainnet/leaderboard
  positions    POST https://api.hyperliquid.xyz/info {"type": "clearinghouseState", "user": addr}

The traps in copy trading, and what this module does about each:
  - hindsight: a leaderboard shows who WAS lucky. Every prediction here is scored on
    what happens AFTER it, and the scorecard's multiple-comparison bar counts it as a
    hypothesis like any other. The calibration layer reduces it to its measured worth.
  - market makers: huge volume on small capital is a liquidity provider, not a view.
    Wallets trading more than MAX_TURNOVER x their equity per month are excluded.
  - one-hit wonders: a wallet must be profitable this month AND all-time.
  - lag: we read positions, not trades, so we see a view while it is still held, not
    after the exit. Data older than MAX_AGE_S is treated as no signal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time

import aiohttp

log = logging.getLogger("jevbot.copy")

LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO = "https://api.hyperliquid.xyz/info"

MIN_ACCOUNT_USD = 100_000
MAX_TURNOVER = 100          # month volume / account value; above this it is a market maker
TOP_N = 30
REFRESH_WALLETS_S = 12 * 3600
POLL_S = 60
MAX_AGE_S = 180


def _perf(row: dict) -> dict[str, dict]:
    return {w: {k: float(v) for k, v in d.items()} for w, d in row["windowPerformances"]}


def select_wallets(rows: list[dict], top_n: int = TOP_N) -> list[str]:
    """Consistent, directional, sizeable traders, ranked by this month's return."""
    picked = []
    for r in rows:
        try:
            value = float(r["accountValue"])
            p = _perf(r)
            month, all_time = p["month"], p["allTime"]
        except (KeyError, ValueError, TypeError):
            continue
        if value < MIN_ACCOUNT_USD or month["roi"] <= 0 or all_time["roi"] <= 0:
            continue
        if month["vlm"] / value > MAX_TURNOVER:
            continue
        picked.append((month["roi"], r["ethAddress"]))
    picked.sort(reverse=True)
    return [addr for _, addr in picked[:top_n]]


def coin_of(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def positioning(states: dict[str, dict], coin: str) -> tuple[float, int]:
    """Net long share of notional among tracked wallets holding `coin`, in [-1, 1],
    and how many wallets hold it."""
    long_n = short_n = 0.0
    holders = 0
    for st in states.values():
        for ap in st.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") != coin:
                continue
            szi, value = float(pos.get("szi", 0)), abs(float(pos.get("positionValue", 0)))
            if szi == 0 or value == 0:
                continue
            holders += 1
            if szi > 0:
                long_n += value
            else:
                short_n += value
    total = long_n + short_n
    return ((long_n - short_n) / total if total else 0.0), holders


def to_probability(net: float, holders: int) -> float:
    """Raw mapping only; what it is really worth is learned by the evaluator's
    calibration. Few holders means little information, so it is damped toward 0.5."""
    damp = min(holders / 5.0, 1.0)
    return 0.5 + 0.4 * net * damp


def describe(net: float, holders: int) -> str:
    """Words for Jev, following its docs: semantic buckets, not numbers."""
    if holders == 0:
        return "no tracked top trader holds it"
    side = ("almost all long" if net > 0.6 else "mostly long" if net > 0.2 else
            "split between long and short" if net >= -0.2 else "mostly short" if net >= -0.6 else "almost all short")
    size = "a few" if holders < 5 else "many" if holders < 15 else "most"
    return f"{size} top traders hold it, {side}"


class CopyTradeSignal:
    name = "copy"

    def __init__(self, horizons: tuple[int, ...]):
        self.horizons = horizons
        self.wallets: list[str] = []
        self.states: dict[str, dict] = {}
        self.updated = 0.0
        self.wallets_at = 0.0
        self.status = "starting"

    async def run(self) -> None:
        async with aiohttp.ClientSession() as http:
            while True:
                try:
                    if time.time() - self.wallets_at > REFRESH_WALLETS_S or not self.wallets:
                        await self._refresh_wallets(http)
                    await self._poll(http)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:   # public API hiccups are routine; keep going
                    self.status = f"error: {exc!r}"[:160]
                    log.warning("copy-trader poll failed: %s", exc)
                await asyncio.sleep(POLL_S)

    async def _refresh_wallets(self, http: aiohttp.ClientSession) -> None:
        async with http.get(LEADERBOARD, timeout=aiohttp.ClientTimeout(total=120)) as r:
            r.raise_for_status()
            raw = await r.read()
        rows = (await asyncio.to_thread(json.loads, raw.decode("utf-8")))["leaderboardRows"]
        self.wallets = await asyncio.to_thread(select_wallets, rows)
        self.wallets_at = time.time()
        log.info("copy-trader: tracking %d of %d leaderboard wallets", len(self.wallets), len(rows))

    async def _poll(self, http: aiohttp.ClientSession) -> None:
        async def one(addr: str):
            async with http.post(INFO, json={"type": "clearinghouseState", "user": addr},
                                 timeout=aiohttp.ClientTimeout(total=15)) as r:
                r.raise_for_status()
                return addr, await r.json()
        results = await asyncio.gather(*(one(a) for a in self.wallets), return_exceptions=True)
        fresh = {a: s for res in results if not isinstance(res, Exception) for a, s in [res]}
        if fresh:
            self.states = fresh
            self.updated = time.time()
            self.status = f"ok: {len(fresh)}/{len(self.wallets)} wallets"

    def view(self, symbol: str) -> tuple[float, int] | None:
        if time.time() - self.updated > MAX_AGE_S:
            return None
        return positioning(self.states, coin_of(symbol))

    async def predict(self, f) -> dict[int, float] | None:
        v = self.view(f.symbol)
        if v is None:
            return None
        net, holders = v
        if holders == 0:
            return None
        p = to_probability(net, holders)
        return {h: p for h in self.horizons} if math.isfinite(p) else None

    def words(self, symbol: str) -> str | None:
        v = self.view(symbol)
        return None if v is None else describe(*v)
