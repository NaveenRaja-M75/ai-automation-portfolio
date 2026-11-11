"""Volatility-managed index exposure.

Every strategy tested so far tried to choose WHAT to own and lost to simply owning the
index. This one does not choose anything. It holds the index and varies HOW MUCH of it
is held, scaling exposure down when recent realised volatility is high and back up when
it is calm, parking the remainder in cash.

The mechanism it relies on is that volatility is strongly autocorrelated while returns
are barely autocorrelated at all. Tomorrow's volatility is highly predictable from
today's; tomorrow's return is not. So exposure can be adjusted using information that
genuinely persists, without needing to forecast direction at all.

The honest expectation, stated before running it: this should NOT raise returns. Capping
weight at 1.0 means it can only ever hold less than the index, never more, so in a rising
market it must finish behind on raw return. What it can plausibly do is cut drawdown by
more than it cuts return, which is a real improvement and the only kind on offer here.

Pre-registered specification, fixed before seeing any result:
  target volatility   15% annualised
  lookback            21 trading days of realised volatility
  rebalance           monthly
  maximum weight      1.0, because the account is cash and cannot use leverage
  cash return         6% annualised, the approximate Indian liquid-fund rate
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from .backtest import Bar
from .costs import CostModel

TRADING_DAYS = 252


@dataclass
class VolResult:
    dates: list[datetime] = field(default_factory=list)
    strategy_daily: list[float] = field(default_factory=list)
    benchmark_daily: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    benchmark_equity: list[float] = field(default_factory=list)
    rebalances: int = 0
    total_cost: float = 0.0

    @staticmethod
    def _ann_vol(rets: Sequence[float]) -> float:
        return statistics.stdev(rets) * math.sqrt(TRADING_DAYS) if len(rets) > 1 else 0.0

    @staticmethod
    def _max_dd(curve: Sequence[float]) -> float:
        peak, worst = -math.inf, 0.0
        for e in curve:
            peak = max(peak, e)
            if peak > 0:
                worst = max(worst, (peak - e) / peak)
        return worst * 100.0

    def _cagr(self, curve: Sequence[float]) -> float:
        years = len(self.strategy_daily) / TRADING_DAYS
        if years <= 0.5 or curve[0] <= 0:
            return 0.0
        return ((curve[-1] / curve[0]) ** (1 / years) - 1) * 100

    def monthly(self, which: str = "strategy") -> tuple[list[datetime], list[float]]:
        """Compound daily returns into calendar-month returns for significance testing."""
        rets = self.strategy_daily if which == "strategy" else self.benchmark_daily
        out_d: list[datetime] = []
        out_r: list[float] = []
        cur_key, acc = None, 1.0
        for d, r in zip(self.dates, rets):
            key = (d.year, d.month)
            if cur_key is None:
                cur_key = key
            if key != cur_key:
                out_d.append(d)
                out_r.append(acc - 1.0)
                cur_key, acc = key, 1.0
            acc *= (1.0 + r)
        if cur_key is not None:
            out_d.append(self.dates[-1])
            out_r.append(acc - 1.0)
        return out_d, out_r

    def summary(self) -> dict:
        s_vol = self._ann_vol(self.strategy_daily)
        b_vol = self._ann_vol(self.benchmark_daily)
        s_cagr = self._cagr(self.equity)
        b_cagr = self._cagr(self.benchmark_equity)
        return {
            "years": round(len(self.strategy_daily) / TRADING_DAYS, 1),
            "strategy_total_pct": (self.equity[-1] / self.equity[0] - 1) * 100,
            "benchmark_total_pct": (self.benchmark_equity[-1] / self.benchmark_equity[0] - 1) * 100,
            "strategy_cagr_pct": s_cagr,
            "benchmark_cagr_pct": b_cagr,
            "strategy_vol_pct": s_vol * 100,
            "benchmark_vol_pct": b_vol * 100,
            "strategy_max_dd_pct": self._max_dd(self.equity),
            "benchmark_max_dd_pct": self._max_dd(self.benchmark_equity),
            "strategy_sharpe": s_cagr / (s_vol * 100) if s_vol else 0.0,
            "benchmark_sharpe": b_cagr / (b_vol * 100) if b_vol else 0.0,
            "mean_weight": statistics.fmean(self.weights) if self.weights else 0.0,
            "min_weight": min(self.weights) if self.weights else 0.0,
            "rebalances": self.rebalances,
            "total_cost": self.total_cost,
        }

    def vol_matched_excess(self) -> list[float]:
        """Monthly strategy return, rescaled to the benchmark's volatility, minus the
        benchmark's return.

        This answers the question raw returns cannot: at the SAME risk, would the
        strategy have paid more? Scaling above 1.0 implies leverage the cash account
        cannot actually use, so this is an analytical comparison rather than a tradeable
        result, and it is reported as such.
        """
        _, s = self.monthly("strategy")
        _, b = self.monthly("benchmark")
        n = min(len(s), len(b))
        s, b = s[:n], b[:n]
        s_sd = statistics.stdev(s) if n > 1 else 0.0
        b_sd = statistics.stdev(b) if n > 1 else 0.0
        if s_sd <= 0:
            return [0.0] * n
        k = b_sd / s_sd
        return [s[i] * k - b[i] for i in range(n)]


def realised_vol(returns: Sequence[float]) -> float:
    """Annualised standard deviation of daily returns."""
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS)


def run_vol_managed(
    bars: Sequence[Bar],
    costs: CostModel,
    target_vol: float = 0.15,
    lookback_days: int = 21,
    max_weight: float = 1.0,
    rf_annual: float = 0.06,
    start_equity: float = 100_000.0,
) -> VolResult:
    """Hold the index at a weight that targets constant volatility, rebalanced monthly."""
    if not 0 < target_vol < 2:
        raise ValueError("target_vol must be a sensible annualised fraction")
    if max_weight <= 0:
        raise ValueError("max_weight must be positive")

    res = VolResult()
    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1

    rets = [bars[i].close / bars[i - 1].close - 1.0 for i in range(1, len(bars))]
    dates = [b.ts for b in bars[1:]]

    equity = start_equity
    bench = start_equity
    weight = 0.0
    cur_month: Optional[tuple[int, int]] = None

    for i, (d, r) in enumerate(zip(dates, rets)):
        # Rebalance on the first bar of a new month, using ONLY returns already observed.
        key = (d.year, d.month)
        if key != cur_month and i >= lookback_days:
            window = rets[i - lookback_days: i]
            rv = realised_vol(window)
            new_weight = min(target_vol / rv, max_weight) if rv > 0 else max_weight
            traded = abs(new_weight - weight) * equity
            if traded > 0:
                c = (costs.entry_cost(traded, 1.0) if new_weight > weight
                     else costs.exit_cost(traded, 1.0))
                c += traded * costs.slippage_pct
                equity -= c
                res.total_cost += c
                res.rebalances += 1
            weight = new_weight
            res.weights.append(weight)
            cur_month = key
        elif cur_month is None:
            cur_month = key

        port_ret = weight * r + (1.0 - weight) * rf_daily
        equity *= (1.0 + port_ret)
        bench *= (1.0 + r)

        res.dates.append(d)
        res.strategy_daily.append(port_ret)
        res.benchmark_daily.append(r)
        res.equity.append(equity)
        res.benchmark_equity.append(bench)

    return res
