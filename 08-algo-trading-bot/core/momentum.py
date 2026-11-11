"""Cross-sectional relative momentum: rank the basket, hold the leaders, rebalance monthly.

This is a different question from everything tested so far. Time-series trend following
asks "is this instrument going up?" and got a clear no. Cross-sectional momentum asks
"which instruments are going up MORE than the others?", holds those, and is agnostic about
whether the market as a whole is rising. That difference matters here specifically,
because the previous result failed partly from signal clustering: trend signals fired
across the whole basket at once, making it one market bet rather than many. A ranking
strategy holds a fixed number of names by construction and cannot cluster that way.

The canonical academic specification is 12-1 momentum: rank on the last 12 months of
return while SKIPPING the most recent month, hold the top names, rebalance monthly. The
skip is not a detail. Short-horizon returns exhibit reversal rather than continuation, so
including the most recent month reliably degrades the result, and its presence in the
standard spec is one of the oldest findings in the literature.

Tested here as a PRE-REGISTERED specification with no parameter search, so
`strategies_tested` is 1 and the bar is t >= 1.96 rather than the inflated threshold a
grid search would earn. Sensitivity across nearby parameters is reported afterwards as a
robustness check, and is explicitly NOT used to choose anything.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from .backtest import Bar
from .costs import CostModel


@dataclass
class MomentumResult:
    dates: list[datetime] = field(default_factory=list)
    strategy_returns: list[float] = field(default_factory=list)
    benchmark_returns: list[float] = field(default_factory=list)
    holdings: list[list[str]] = field(default_factory=list)
    turnover: list[float] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    benchmark_equity: list[float] = field(default_factory=list)

    @property
    def excess_returns(self) -> list[float]:
        return [s - b for s, b in zip(self.strategy_returns, self.benchmark_returns)]

    @staticmethod
    def _total(curve: list[float]) -> float:
        return (curve[-1] / curve[0] - 1.0) * 100.0 if len(curve) > 1 else 0.0

    @staticmethod
    def _dd(curve: list[float]) -> float:
        peak, worst = -math.inf, 0.0
        for e in curve:
            peak = max(peak, e)
            if peak > 0:
                worst = max(worst, (peak - e) / peak)
        return worst * 100.0

    def summary(self) -> dict:
        n = len(self.strategy_returns)
        years = n / 12.0 if n else 0.0
        s_tot, b_tot = self._total(self.equity), self._total(self.benchmark_equity)
        cagr = ((self.equity[-1] / self.equity[0]) ** (1 / years) - 1) * 100 if years > 0.5 else 0.0
        b_cagr = ((self.benchmark_equity[-1] / self.benchmark_equity[0]) ** (1 / years) - 1) * 100 \
            if years > 0.5 else 0.0
        sd = statistics.stdev(self.strategy_returns) if n > 1 else 0.0
        return {
            "months": n,
            "years": round(years, 1),
            "strategy_total_pct": s_tot,
            "benchmark_total_pct": b_tot,
            "strategy_cagr_pct": cagr,
            "benchmark_cagr_pct": b_cagr,
            "strategy_max_dd_pct": self._dd(self.equity),
            "benchmark_max_dd_pct": self._dd(self.benchmark_equity),
            "sharpe": (statistics.fmean(self.strategy_returns) / sd * math.sqrt(12)) if sd else 0.0,
            "mean_monthly_excess_pct": statistics.fmean(self.excess_returns) * 100 if n else 0.0,
            "avg_turnover_pct": statistics.fmean(self.turnover) * 100 if self.turnover else 0.0,
        }


def month_end_prices(symbol_bars: dict[str, list[Bar]]) -> tuple[list[datetime], dict]:
    """Collapse daily bars to the last trading close of each calendar month.

    Monthly sampling is the standard frequency for this anomaly and it keeps turnover,
    and therefore cost drag, at a level a retail account can actually bear.
    """
    per_symbol: dict[str, dict[tuple[int, int], tuple[datetime, float]]] = {}
    for sym, bars in symbol_bars.items():
        month: dict[tuple[int, int], tuple[datetime, float]] = {}
        for b in bars:
            key = (b.ts.year, b.ts.month)
            if key not in month or b.ts > month[key][0]:
                month[key] = (b.ts, b.close)
        per_symbol[sym] = month

    keys = sorted({k for m in per_symbol.values() for k in m})
    dates = [max(m[k][0] for m in per_symbol.values() if k in m) for k in keys]
    prices = [{s: m[k][1] for s, m in per_symbol.items() if k in m} for k in keys]
    return dates, prices


def run_momentum(
    symbol_bars: dict[str, list[Bar]],
    costs: CostModel,
    lookback_months: int = 12,
    skip_months: int = 1,
    top_n: int = 5,
    start_equity: float = 100_000.0,
) -> MomentumResult:
    """Rank, hold the top N equal-weighted, rebalance at each month end."""
    dates, prices = month_end_prices(symbol_bars)
    res = MomentumResult()

    equity = start_equity
    bench = start_equity
    held: list[str] = []
    first = lookback_months + skip_months

    for i in range(first, len(prices) - 1):
        now, nxt = prices[i], prices[i + 1]

        # Rank on return from (i - skip - lookback) to (i - skip). The skipped month is
        # excluded precisely because recent returns mean-revert.
        ranked: list[tuple[float, str]] = []
        base, past = prices[i - skip_months], prices[i - skip_months - lookback_months]
        for sym in now:
            if sym in base and sym in past and past[sym] > 0 and sym in nxt:
                ranked.append((base[sym] / past[sym] - 1.0, sym))
        if len(ranked) < top_n:
            continue
        ranked.sort(reverse=True)
        target = [s for _, s in ranked[:top_n]]

        # Costs are computed in rupees on each order's actual traded value, never from a
        # per-rupee rate. The cost model contains a flat per-order DP charge, and dividing
        # a flat fee by a notional to get a "rate" produces a number that explodes as the
        # notional shrinks. Doing exactly that is what made the first run of this strategy
        # report -98%: a Rs 15.93 flat fee over a Rs 100 reference notional became a 16%
        # sell cost, which at 52% monthly turnover compounds to near-total loss.
        old_w = {s: 1.0 / len(held) for s in held} if held else {}
        new_w = {s: 1.0 / top_n for s in target}
        names = set(old_w) | set(new_w)

        cost_rupees = 0.0
        sold = bought = 0.0
        for s in names:
            delta = new_w.get(s, 0.0) - old_w.get(s, 0.0)
            value = abs(delta) * equity
            if value <= 0:
                continue
            if delta < 0:
                sold += -delta
                cost_rupees += costs.exit_cost(value, 1.0) + value * costs.slippage_pct
            else:
                bought += delta
                cost_rupees += costs.entry_cost(value, 1.0) + value * costs.slippage_pct
        cost_drag = cost_rupees / equity if equity > 0 else 0.0

        holding_ret = statistics.fmean(nxt[s] / now[s] - 1.0 for s in target)
        net = holding_ret - cost_drag

        eligible = [s for s in now if s in nxt]
        bench_ret = statistics.fmean(nxt[s] / now[s] - 1.0 for s in eligible)

        equity *= (1.0 + net)
        bench *= (1.0 + bench_ret)
        held = target

        res.dates.append(dates[i])
        res.strategy_returns.append(net)
        res.benchmark_returns.append(bench_ret)
        res.holdings.append(target)
        res.turnover.append(sold + bought)
        res.equity.append(equity)
        res.benchmark_equity.append(bench)

    return res
