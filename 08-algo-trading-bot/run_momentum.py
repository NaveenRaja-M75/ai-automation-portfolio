#!/usr/bin/env python3
"""Cross-sectional momentum on the NSE basket, tested as a pre-registered specification.

The spec is fixed BEFORE seeing the result: 12-month lookback, 1-month skip, top 5 of 29,
equal weight, monthly rebalance. No parameter search, so strategies_tested is 1 and the
significance bar is t >= 1.96 rather than the inflated threshold a grid would earn.

The sensitivity grid printed afterwards exists to answer "is this a knife-edge?", not to
choose parameters. If the headline spec fails, a neighbouring cell that passes is noise,
and picking it would be exactly the mistake this whole project is built to prevent.

    python run_momentum.py
"""

from __future__ import annotations

import statistics
import sys
import warnings

warnings.filterwarnings("ignore")

from core.costs import IndianEquityDelivery
from core.data import load_clean
from core.metrics import scorecard
from core.momentum import run_momentum
from run_basket import UNIVERSE

START_EQUITY = 100_000.0
LOOKBACK, SKIP, TOP_N = 12, 1, 5   # pre-registered, not tuned


def rule(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def main() -> int:
    costs = IndianEquityDelivery()
    bars = {}
    for s in UNIVERSE:
        try:
            b = load_clean(f"{s}.NS", verbose=False)
            if len(b) > 1500:
                bars[s] = b
        except Exception:
            pass
    print(f"universe: {len(bars)} NSE symbols")
    print(f"pre-registered spec: {LOOKBACK}-month lookback, {SKIP}-month skip, "
          f"top {TOP_N}, equal weight, monthly rebalance")

    res = run_momentum(bars, costs, LOOKBACK, SKIP, TOP_N, START_EQUITY)
    s = res.summary()

    rule("HEADLINE (no parameter search — one specification, fixed in advance)")
    print(f"  period            {res.dates[0].date()} to {res.dates[-1].date()} "
          f"({s['months']} months, {s['years']} years)")
    print(f"  strategy          {s['strategy_total_pct']:+8.1f}% total   "
          f"{s['strategy_cagr_pct']:+6.1f}% CAGR   max drawdown {s['strategy_max_dd_pct']:5.1f}%")
    print(f"  equal-weight bmk  {s['benchmark_total_pct']:+8.1f}% total   "
          f"{s['benchmark_cagr_pct']:+6.1f}% CAGR   max drawdown {s['benchmark_max_dd_pct']:5.1f}%")
    print(f"  Sharpe            {s['sharpe']:.2f}")
    print(f"  mean monthly edge {s['mean_monthly_excess_pct']:+.3f}% over the benchmark")
    print(f"  avg turnover      {s['avg_turnover_pct']:.0f}% per month")

    rule("SIGNIFICANCE OF THE EXCESS RETURN OVER THE BENCHMARK")
    print("  Each observation is one month of strategy return minus benchmark return.")
    print("  'expectancy' below reads as mean monthly excess; 'win rate' as months ahead.\n")
    print(scorecard(res.excess_returns, strategies_tested=1).summary())

    rule("SENSITIVITY (robustness check — NOT used to select anything)")
    print(f"  {'lookback':>9} {'top_n':>6} {'total %':>10} {'CAGR %':>8} "
          f"{'excess/mo %':>12} {'t':>6}")
    for lb in (6, 9, 12):
        for tn in (3, 5, 8):
            r = run_momentum(bars, costs, lb, SKIP, tn, START_EQUITY)
            sc = scorecard(r.excess_returns, strategies_tested=1)
            su = r.summary()
            mark = "  <-- headline" if (lb, tn) == (LOOKBACK, TOP_N) else ""
            print(f"  {lb:>9} {tn:>6} {su['strategy_total_pct']:>10.1f} "
                  f"{su['strategy_cagr_pct']:>8.1f} {su['mean_monthly_excess_pct']:>12.3f} "
                  f"{sc.t_stat:>6.2f}{mark}")

    beat = sum(1 for e in res.excess_returns if e > 0)
    print(f"\n  months ahead of benchmark: {beat}/{len(res.excess_returns)} "
          f"({beat / len(res.excess_returns):.0%})")
    print("  Survivorship caveat still applies: the universe is today's surviving large")
    print("  caps, which flatters any long-only result on this basket.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
