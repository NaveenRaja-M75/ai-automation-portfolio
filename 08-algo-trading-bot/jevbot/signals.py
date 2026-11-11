"""Four signal sources, scored side by side on identical inputs.

  jev       TypeSafe Jev, asked one yes/no question per horizon
  ml        logistic regression trained online on this bot's own resolved outcomes
  momentum  the original jev-trader mock heuristic (flow + imbalance + trend), noise removed
  random    a coin flip: any signal that cannot beat it has no edge
  copy      what Hyperliquid's top traders hold (copytrade.py)
  ensemble  the others combined, each weighted by its MEASURED skill

Each returns {horizon_s: p_up} or None when it has nothing to say this round. None is
never converted into a guess.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone

import aiohttp
import numpy as np
from sklearn.linear_model import SGDClassifier

from .config import Config
from .features import Features, describe, horizon_phrase, market_name

log = logging.getLogger("jevbot.signals")

Probs = dict[int, float]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


class RandomSignal:
    name = "random"

    def __init__(self, horizons: tuple[int, ...], seed: int | None = None):
        self.horizons, self.rng = horizons, random.Random(seed)

    async def predict(self, f: Features) -> Probs | None:
        return {h: self.rng.random() for h in self.horizons}


class MomentumSignal:
    """jev-trader's MockModel: returns/8 + imbalance*1.5 + flow*2 through a sigmoid.
    Its hash-noise term is dropped (it existed only to make the demo trade both ways),
    and returns are volatility-scaled so the weights mean the same on every pair."""
    name = "momentum"

    def __init__(self, horizons: tuple[int, ...]):
        self.horizons = horizons

    async def predict(self, f: Features) -> Probs | None:
        z60 = f.ret[60] / max(f.horizon_vol(60), 1e-12)
        p = _sigmoid(z60 / 2 + f.imbalance[10] * 1.5 + f.flow[60] * 2)
        return {h: p for h in self.horizons}


class _Scaler:
    """Running mean and variance (Welford), so standardisation never uses future data."""

    def __init__(self, n: int):
        self.k, self.mean, self.m2 = 0, np.zeros(n), np.zeros(n)

    def update(self, x: np.ndarray) -> None:
        self.k += 1
        d = x - self.mean
        self.mean += d / self.k
        self.m2 += d * (x - self.mean)

    def transform(self, x: np.ndarray) -> np.ndarray:
        std = np.sqrt(self.m2 / max(self.k - 1, 1))
        return (x - self.mean) / np.where(std > 1e-9, std, 1.0)


class LocalMLSignal:
    """Learns only from outcomes that have already resolved, so a prediction made at
    time t never benefits from anything after t. Silent until MIN_SAMPLES labels exist."""
    name = "ml"
    MIN_SAMPLES = 200

    def __init__(self, horizons: tuple[int, ...]):
        self.horizons = horizons
        self.models = {h: SGDClassifier(loss="log_loss", alpha=1e-3, random_state=7) for h in horizons}
        self.scalers: dict[int, _Scaler] = {}
        self.seen = {h: 0 for h in horizons}

    def learn(self, horizon: int, vector: list[float], up: bool) -> None:
        if horizon not in self.models:
            return
        x = np.asarray(vector, dtype=float)
        if not np.all(np.isfinite(x)):
            return
        sc = self.scalers.setdefault(horizon, _Scaler(len(x)))
        sc.update(x)
        self.models[horizon].partial_fit(sc.transform(x).reshape(1, -1), [int(up)], classes=[0, 1])
        self.seen[horizon] += 1

    async def predict(self, f: Features) -> Probs | None:
        x = np.asarray(f.vector(), dtype=float)
        if not np.all(np.isfinite(x)):
            return None
        out = {}
        for h in self.horizons:
            if self.seen[h] >= self.MIN_SAMPLES:
                xs = self.scalers[h].transform(x).reshape(1, -1)
                out[h] = float(self.models[h].predict_proba(xs)[0][1])
        return out or None


CONTEXT_MEANING = {
    "top_traders": "what the most profitable traders hold now",
    "recent_news": "the tone of the last two hours of crypto headlines",
}


class JevSignal:
    """One HTTP call per symbol per JEV_INTERVAL_S, one Noul question per horizon.

    Noul rather than Choice on purpose: the jev-1.13 docs note that Choice leans toward
    whichever option is listed first, and the original bot listed "buy" first. A Noul
    returns P(true) directly, with no option order to bias.
    """
    name = "jev"

    def __init__(self, cfg: Config, horizons: tuple[int, ...]):
        self.cfg, self.horizons = cfg, horizons
        self.session: aiohttp.ClientSession | None = None
        self.last_call: dict[str, float] = {}
        self.pause_until = 0.0
        self.day = ""
        self.spend_today = 0.0
        self.credit_guard: str | None = None   # set => Jev is paused to protect real money
        self.credit: dict | None = None
        self.stats = {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0, "skipped_budget": 0,
                      "spend_total_usd": 0.0, "last_latency_ms": None, "model": None, "last_error": None}

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(headers={
            "Authorization": f"Bearer {self.cfg.jev_api_key}",
            "Content-Type": "application/json",
        })

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def check_credits(self) -> None:
        """Vercel only: read the gateway's credit balance and pause Jev before real money
        is spent. Two triggers, both conservative:
          - balance below JEV_MIN_CREDIT_USD after having been above it
          - usage growing while the balance reads zero (spend not covered by credit)
        Clearing the pause is a human decision: delete the JEV_PAUSED file and restart."""
        if self.cfg.jev_provider != "vercel":
            return
        if self.cfg.jev_paused_file.exists():
            self.credit_guard = self.cfg.jev_paused_file.read_text(encoding="utf-8").strip()
        try:
            async with self.session.get("https://ai-gateway.vercel.sh/v1/credits",
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return
                body = await r.json()
            balance, used = float(body.get("balance", 0)), float(body.get("total_used", 0))
        except Exception as exc:
            log.warning("credit check failed: %s", exc)
            return
        prev = self.credit
        self.credit = {"balance": balance, "total_used": used, "checked": time.time(),
                       "peak_balance": max(balance, (prev or {}).get("peak_balance", 0.0))}
        reason = None
        if self.credit["peak_balance"] >= self.cfg.jev_min_credit_usd > balance:
            reason = f"Vercel credit down to ${balance:.2f} (floor ${self.cfg.jev_min_credit_usd:.2f})"
        elif prev is not None and balance <= 0 and used > prev["total_used"] + 1e-9:
            reason = f"Vercel usage rose ${used - prev['total_used']:.4f} with $0 credit balance: may be billing your card"
        if reason and not self.credit_guard:
            self.credit_guard = reason
            self.cfg.jev_paused_file.write_text(reason + "\n", encoding="utf-8")
            log.error("JEV PAUSED: %s", reason)

    def _budget_ok(self) -> bool:
        if self.credit_guard:
            return False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.spend_today = today, 0.0
        return self.spend_today < self.cfg.jev_daily_budget_usd

    def request_body(self, f: Features, context: dict | None = None) -> dict:
        """context: extra word-level facts (e.g. top-trader positioning). Jev's docs say
        it is not trained on niche domains but improves when given relevant context."""
        state = describe(f, self.cfg.trade_horizon_s)
        state.pop("horizon")
        if context:
            state.update(context)
        mkt = market_name(f.symbol)
        questions = {}
        for h in self.horizons:
            phrase = horizon_phrase(h)
            questions[f"up_{h}"] = {
                "type": "noul",
                "instructions": (f"Will the {mkt} price be higher at the end of {phrase} than it is now? "
                                 "Judge from `price_trend`, `taker_flow`, `order_book`, "
                                 "`trading_activity` and `volatility`"
                                 + "".join(f", and `{k}` ({CONTEXT_MEANING[k]})" for k in CONTEXT_MEANING if k in state)
                                 + "."),
                "criteria": {"true": f"Higher at the end of {phrase}",
                             "false": f"Lower or unchanged at the end of {phrase}"},
            }
        return {"model": self.cfg.jev_model_id, "state": state, "questions": questions}

    async def ask(self, state: dict, questions: dict, timeout: float | None = None) -> dict | None:
        """One System One call with the shared budget, backoff and stats. Every Jev
        consumer (market and news) goes through here, so one daily cap covers all."""
        if time.monotonic() < self.pause_until:
            return None
        if not self._budget_ok():
            self.stats["skipped_budget"] += 1
            return None
        self.stats["calls"] += 1
        t0 = time.perf_counter()
        body_in = {"model": self.cfg.jev_model_id, "state": state, "questions": questions}
        try:
            async with self.session.post(self.cfg.jev_url, json=body_in,
                                         timeout=aiohttp.ClientTimeout(total=timeout or self.cfg.jev_timeout_s)) as r:
                if r.status in (429, 529):
                    wait = float(r.headers.get("retry-after", "10") or 10)
                    self.pause_until = time.monotonic() + min(max(wait, 1.0), 120.0)
                    raise RuntimeError(f"HTTP {r.status}, backing off {wait:.0f}s")
                if r.status in (401, 402, 403):
                    # Account problem (bad key, no card, no credit): retrying every few
                    # seconds would only spam the provider. Back off for 10 minutes.
                    self.pause_until = time.monotonic() + 600
                    raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:200]}")
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:200]}")
                body = await r.json()
        except asyncio.TimeoutError:
            self.stats["timeouts"] += 1
            return None
        except Exception as exc:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(exc)[:200]
            log.warning("jev call failed: %s", exc)
            return None
        latency = (time.perf_counter() - t0) * 1000
        tokens = (body.get("usage") or {}).get("input_tokens") or 0
        cost = tokens / 1e6 * self.cfg.jev_usd_per_mtok
        self.spend_today += cost
        self.stats.update(ok=self.stats["ok"] + 1, last_latency_ms=round(latency), last_error=None,
                          model=body.get("model"), spend_total_usd=self.stats["spend_total_usd"] + cost)
        return body.get("answers") or {}

    async def predict(self, f: Features, context: dict | None = None) -> Probs | None:
        now = time.monotonic()
        if now - self.last_call.get(f.symbol, -1e9) < self.cfg.jev_interval_s:
            return None
        self.last_call[f.symbol] = now
        req = self.request_body(f, context)
        answers = await self.ask(req["state"], req["questions"])
        if answers is None:
            return None
        out = {}
        for h in self.horizons:
            p = (answers.get(f"up_{h}") or {}).get("noul")
            if isinstance(p, (int, float)) and 0.0 <= p <= 1.0:
                out[h] = float(p)
        return out or None


class EnsembleSignal:
    """Combines the other signals in log-odds space. Each signal contributes its
    CALIBRATED probability (what its record says that confidence is worth), weighted by
    its measured Brier skill times a sample-size discount. A signal with zero or negative
    skill gets zero weight, and with no skilled signal at all the ensemble stays silent:
    it never manufactures confidence out of signals that have not earned any.
    """
    name = "ensemble"
    MIN_SAMPLES = 50
    EXCLUDE = ("random", "ensemble")

    def __init__(self, evaluator, horizons: tuple[int, ...]):
        self.evaluator, self.horizons = evaluator, horizons
        self.last_weights: dict[str, dict[str, float]] = {}

    def combine(self, symbol: str, probs: dict[str, Probs]) -> Probs | None:
        out: Probs = {}
        weights_seen: dict[str, float] = {}
        for h in self.horizons:
            num = den = 0.0
            for sig, ph in probs.items():
                if sig in self.EXCLUDE or h not in ph:
                    continue
                if not self.evaluator.usable(sig, symbol, h, self.MIN_SAMPLES):
                    continue   # unskilled, or its edge has decayed recently: benched
                skill, n = self.evaluator.skill(sig, symbol, h)
                w = skill * n / (n + 200)
                pc = min(max(self.evaluator.calibrate(sig, symbol, h, ph[h]), 0.01), 0.99)
                num += w * math.log(pc / (1 - pc))
                den += w
                weights_seen[sig] = round(max(weights_seen.get(sig, 0.0), w), 4)
            if den > 0:
                out[h] = _sigmoid(num / den)
        self.last_weights[symbol] = weights_seen
        return out or None
