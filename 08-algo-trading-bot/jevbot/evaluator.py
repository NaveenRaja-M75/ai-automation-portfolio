"""Scores every prediction against what the market actually did.

For each (signal, symbol, horizon) it tracks:
  - Brier score and skill vs the base rate (is the probability any good?)
  - hit rate and calibration buckets (when it says 70%, is it right 70% of the time?)
  - a long-only trade simulation net of spread, fees and TDS, sampled NON-OVERLAPPING,
    because overlapping windows share the same price path and would inflate the t-stat

The trade samples go through core.metrics.scorecard with the honest strategies_tested
count, so a lucky signal among 36 hypotheses does not get promoted.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.metrics import EDGE_PLAUSIBLE, scorecard

from .config import Config
from .features import Features, MidHistory
from .policy import MEAN_ABS_NORMAL, round_trip_cost

log = logging.getLogger("jevbot.eval")

LABEL = "_label"   # pseudo-signal: carries feature vectors to train the local model


@dataclass
class Pending:
    signal: str
    symbol: str
    horizon: int
    ts: float
    mid: float
    half_spread: float
    p: float
    vector: list[float] | None = None
    cost_ok: bool = True     # would the live policy's cost filter have allowed this entry?


@dataclass
class Stat:
    n: int = 0
    brier_sum: float = 0.0
    hits: int = 0
    ups: int = 0
    buckets: list[list[float]] = field(default_factory=lambda: [[0, 0.0, 0] for _ in range(10)])
    trades: list[float] = field(default_factory=list)          # net %, with TDS if enabled
    trades_no_tds: list[float] = field(default_factory=list)
    last_trade_ts: float = -1e18
    recent: deque = field(default_factory=lambda: deque(maxlen=200))   # (p, up): catches a decaying edge

    def add(self, p: float, up: bool) -> None:
        self.recent.append((p, up))
        self.n += 1
        self.brier_sum += (p - float(up)) ** 2
        self.hits += int((p >= 0.5) == up)
        self.ups += int(up)
        b = self.buckets[min(int(p * 10), 9)]
        b[0] += 1
        b[1] += p
        b[2] += int(up)


def trade_return(cfg: Config, mid0: float, half_spread: float, mid1: float, tds: bool) -> float:
    """Net % of a long bought at the ask at t and sold at the bid at t+h. Uses the entry
    spread for both legs; on BTC/ETH/SOL the spread is one tick almost all the time."""
    cost = mid0 * (1 + half_spread) * (1 + cfg.fee_pct)
    proceeds = mid1 * (1 - half_spread) * (1 - cfg.fee_pct - (cfg.tds_pct if tds else 0.0))
    return (proceeds / cost - 1.0) * 100.0


class Evaluator:
    def __init__(self, cfg: Config, path: Path, on_label: Callable[[int, list[float], bool], None] | None = None):
        self.cfg, self.path, self.on_label = cfg, path, on_label
        self.pending: list[Pending] = []
        self.stats: dict[tuple[str, str, int], Stat] = defaultdict(Stat)
        path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence ---------------------------------------------------------------
    def replay(self) -> int:
        """Rebuild stats from earlier runs so evidence accumulates across restarts."""
        if not self.path.exists():
            return 0
        n = 0
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line from a crash is skipped, not fatal
                if o.get("kind") != "out":
                    continue
                if o.get("fp") != self.cfg.cost_fingerprint:
                    o["ret"] = o["ret_no_tds"] = None   # different rules: keep the score, drop the trade
                    if o.get("up") is None:
                        continue
                self._apply(o["signal"], o["symbol"], o["h"], o["ts"], o["p"], o["up"],
                            o.get("ret"), o.get("ret_no_tds"))
                n += 1
        return n

    def _write(self, rec: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # -- intake --------------------------------------------------------------------
    def record(self, signal: str, f: Features, probs: dict[int, float]) -> None:
        cost = round_trip_cost(self.cfg, f.spread_pct) * self.cfg.min_edge_multiple
        for h, p in probs.items():
            if h in self.cfg.horizons_s and math.isfinite(p):
                ok = f.horizon_vol(h) * MEAN_ABS_NORMAL >= cost
                self.pending.append(Pending(signal, f.symbol, h, f.ts, f.mid, f.spread_pct / 2, p, cost_ok=ok))

    def record_features(self, f: Features) -> None:
        for h in self.cfg.horizons_s:
            self.pending.append(Pending(LABEL, f.symbol, h, f.ts, f.mid, 0.0, 0.5, f.vector()))

    # -- resolution ----------------------------------------------------------------
    def resolve(self, hist: dict[str, MidHistory], now: float) -> int:
        keep, done = [], 0
        for pd in self.pending:
            end_ts = pd.ts + pd.horizon
            if end_ts > now:
                keep.append(pd)
                continue
            mid1 = hist[pd.symbol].at_or_before(end_ts) if pd.symbol in hist else None
            if mid1 is None:
                continue
            # An unchanged price says nothing about direction, so it is not scored as a
            # forecast, but a trade over it still loses its full costs and must count.
            up = None if mid1 == pd.mid else mid1 > pd.mid
            if pd.signal == LABEL:
                if up is None:
                    continue
                if self.on_label and pd.vector is not None:
                    self.on_label(pd.horizon, pd.vector, up)
                continue
            key = (pd.signal, pd.symbol, pd.horizon)
            ret = ret_nt = None
            if pd.p >= self.cfg.entry_prob and pd.cost_ok and pd.ts >= self.stats[key].last_trade_ts + pd.horizon:
                ret = trade_return(self.cfg, pd.mid, pd.half_spread, mid1, self.cfg.apply_tds)
                ret_nt = trade_return(self.cfg, pd.mid, pd.half_spread, mid1, False)
            if up is None and ret is None:
                continue
            self._apply(pd.signal, pd.symbol, pd.horizon, pd.ts, pd.p, up, ret, ret_nt)
            self._write({"kind": "out", "fp": self.cfg.cost_fingerprint, "signal": pd.signal, "symbol": pd.symbol, "h": pd.horizon,
                         "ts": round(pd.ts, 3), "p": round(pd.p, 4), "up": up,
                         "ret": None if ret is None else round(ret, 5),
                         "ret_no_tds": None if ret_nt is None else round(ret_nt, 5)})
            done += 1
        self.pending = keep
        return done

    def _apply(self, signal, symbol, h, ts, p, up, ret, ret_nt) -> None:
        st = self.stats[(signal, symbol, h)]
        if up is not None:
            st.add(p, up)
        if ret is not None:
            st.trades.append(ret)
            st.trades_no_tds.append(ret_nt if ret_nt is not None else ret)
            st.last_trade_ts = ts

    # -- reporting -----------------------------------------------------------------
    def card(self, key: tuple[str, str, int]):
        st = self.stats.get(key)
        if not st or len(st.trades) < 2:
            return None
        try:
            return scorecard(st.trades, strategies_tested=self.cfg.strategies_tested)
        except ValueError:
            return None

    def skill(self, signal: str, symbol: str, horizon: int) -> tuple[float, int]:
        """Brier skill vs the base rate, and the sample size it rests on."""
        st = self.stats.get((signal, symbol, horizon))
        if not st or st.n == 0:
            return 0.0, 0
        base = st.ups / st.n
        ref = base * (1 - base)
        return (1 - (st.brier_sum / st.n) / ref if ref > 0 else 0.0), st.n

    def recent_skill(self, signal: str, symbol: str, horizon: int) -> tuple[float, int]:
        """Brier skill over the last 200 resolved predictions only. An edge that worked
        last month and stopped working this week shows up here first."""
        st = self.stats.get((signal, symbol, horizon))
        if not st or not st.recent:
            return 0.0, 0
        n = len(st.recent)
        base = sum(u for _, u in st.recent) / n
        ref = base * (1 - base)
        brier = sum((p - float(u)) ** 2 for p, u in st.recent) / n
        return (1 - brier / ref if ref > 0 else 0.0), n

    def usable(self, signal: str, symbol: str, horizon: int, min_n: int = 50) -> bool:
        """Skilled overall AND still skilled lately. Both, or the signal is benched."""
        skill, n = self.skill(signal, symbol, horizon)
        rskill, rn = self.recent_skill(signal, symbol, horizon)
        return n >= min_n and skill > 0 and (rn < min_n or rskill > 0)

    def calibrate(self, signal: str, symbol: str, horizon: int, p: float) -> float:
        """What a stated probability has actually been worth: the measured up-rate among
        past predictions in the same 10% bucket, shrunk toward 0.5 by calibration_k
        pseudo-samples. A signal with no record reads 0.5, so it cannot buy its way in on
        confidence alone. Uses resolved outcomes only, so there is no lookahead."""
        k = self.cfg.calibration_k
        st = self.stats.get((signal, symbol, horizon))
        if st is None:
            return 0.5
        n, _, ups = st.buckets[min(max(int(p * 10), 0), 9)]
        return (ups + 0.5 * k) / (n + k)

    def has_edge(self, signal: str, symbol: str, horizon: int) -> bool:
        c = self.card((signal, symbol, horizon))
        return c is not None and c.verdict == EDGE_PLAUSIBLE

    def rows(self) -> list[dict]:
        out = []
        for (sig, sym, h), st in sorted(self.stats.items()):
            if st.n == 0:
                continue
            base = st.ups / st.n
            ref = base * (1 - base)
            brier = st.brier_sum / st.n
            c = self.card((sig, sym, h))
            avg = lambda xs: sum(xs) / len(xs) if xs else None
            out.append({
                "signal": sig, "symbol": sym, "horizon": h, "n": st.n,
                "brier": round(brier, 4),
                "skill": round(1 - brier / ref, 4) if ref > 0 else None,
                "hit_rate": round(st.hits / st.n, 4),
                "trades": len(st.trades),
                "avg_net_pct": None if not st.trades else round(avg(st.trades), 4),
                "avg_no_tds_pct": None if not st.trades_no_tds else round(avg(st.trades_no_tds), 4),
                "verdict": c.verdict if c else "INSUFFICIENT",
                "t_stat": None if not c or not math.isfinite(c.t_stat) else round(c.t_stat, 2),
                "t_required": None if not c else round(c.t_required, 2),
                "calibration": [None if b[0] == 0 else round(b[2] / b[0], 3) for b in st.buckets],
                "calibration_n": [int(b[0]) for b in st.buckets],
            })
        return out

    def best_signal(self, symbol: str, horizon: int, require_edge: bool) -> str | None:
        """For PRIMARY_SIGNAL=auto. With require_edge, only a signal whose scorecard
        says EDGE_PLAUSIBLE qualifies. Without it (paper exploration), the signal with
        the best probability skill after 100+ samples, else momentum as a placeholder."""
        cands = []
        for (sig, sym, h), st in self.stats.items():
            if sym != symbol or h != horizon or sig in ("random", LABEL):
                continue
            c = self.card((sig, sym, h))
            if require_edge:
                if c and c.verdict == EDGE_PLAUSIBLE:
                    cands.append((c.avg_r, sig))
            elif st.n >= 100 and self.usable(sig, sym, h, 100):
                cands.append((self.skill(sig, sym, h)[0], sig))
        if cands:
            return max(cands)[1]
        return None if require_edge else "momentum"
