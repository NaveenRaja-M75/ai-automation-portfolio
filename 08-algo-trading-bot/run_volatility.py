#!/usr/bin/env python3
"""Volatility-managed NIFTY exposure, tested as a pre-registered specification.

Spec fixed before the run: 15% target volatility, 21-day realised-volatility lookback,
monthly rebalance, weight capped at 1.0 because the account is cash, remainder in cash at
6% annualised. No parameter search, so strategies_tested is 1 and the bar is t >= 1.96.

The sensitivity grid at the end is a robustness check. It is not used to select anything,
and a neighbouring cell that passes while the headline fails is noise.

    python run_volatility.py
"""

from __future__ import annotations

import statistics
import sys
import warnings

warnings.filterwarnings("ignore")

from core.costs import IndianEquityDelivery
from core.data import load_clean
from core.metrics import scorecard
from core.volatility import run_vol_managed

SYMBOL = "NIFTYBEES.NS"
START_EQUITY = 100_000.0
TARGET_VOL, LOOKBACK, MAX_W, RF = 0.15, 21, 1.0, 0.06


def rule(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def main() -> int:
    costs = IndianEquityDelivery()
    bars = load_clean(SYMBOL, start="2005-01-01")
    print(f"{SYMBOL}: {len(bars)} daily bars, {bars[0].ts.date()} to {bars[-1].ts.date()}")
    print(f"pre-registered spec: {TARGET_VOL:.0%} target vol, {LOOKBACK}-day lookback, "
          f"max weight {MAX_W:.1f}, cash at {RF:.0%}")

    res = run_vol_managed(bars, costs, TARGET_VOL, LOOKBACK, MAX_W, RF, START_EQUITY)
    s = res.summary()

    rule("HEADLINE (no parameter search)")
    print(f"  period              {res.dates[0].date()} to {res.dates[-1].date()} "
          f"({s['years']} years)")
    print(f"  {'':<18} {'strategy':>12} {'buy & hold':>12}")
    print(f"  {'total return':<18} {s['strategy_total_pct']:>11.1f}% "
          f"{s['benchmark_total_pct']:>11.1f}%")
    print(f"  {'CAGR':<18} {s['strategy_cagr_pct']:>11.2f}% "
          f"{s['benchmark_cagr_pct']:>11.2f}%")
    print(f"  {'volatility':<18} {s['strategy_vol_pct']:>11.1f}% "
          f"{s['benchmark_vol_pct']:>11.1f}%")
    print(f"  {'max drawdown':<18} {s['strategy_max_dd_pct']:>11.1f}% "
          f"{s['benchmark_max_dd_pct']:>11.1f}%")
    print(f"  {'return / vol':<18} {s['strategy_sharpe']:>12.2f} "
          f"{s['benchmark_sharpe']:>12.2f}")
    print(f"\n  average weight held {s['mean_weight']:.2f}   "
          f"lowest {s['min_weight']:.2f}   rebalances {s['rebalances']}   "
          f"total costs {s['total_cost']:,.0f}")

    dd_cut = 1 - s["strategy_max_dd_pct"] / s["benchmark_max_dd_pct"]
    ret_cut = 1 - s["strategy_cagr_pct"] / s["benchmark_cagr_pct"]
    print(f"\n  drawdown reduced by {dd_cut:.0%}, CAGR reduced by {ret_cut:.0%}")
    print(f"  VERDICT: {'favourable trade' if dd_cut > ret_cut else 'unfavourable trade'}"
          f" — it gave up {'less' if dd_cut > ret_cut else 'more'} return than risk")

    rule("SIGNIFICANCE AT MATCHED RISK")
    print("  Strategy monthly returns rescaled to the benchmark's volatility, minus the")
    print("  benchmark. Answers: at the SAME risk, would this have paid more?")
    print("  Analytical only — scaling above 1.0 implies leverage a cash account lacks.\n")
    excess = res.vol_matched_excess()
    print(scorecard(excess, strategies_tested=1).summary())

    rule("SENSITIVITY (robustness check — NOT used to select anything)")
    print(f"  {'target':>7} {'lookback':>9} {'CAGR %':>8} {'vol %':>7} {'maxDD %':>9} "
          f"{'ret/vol':>8} {'t':>6}")
    for tv in (0.10, 0.15, 0.20):
        for lb in (21, 63):
            r = run_vol_managed(bars, costs, tv, lb, MAX_W, RF, START_EQUITY)
            su = r.summary()
            sc = scorecard(r.vol_matched_excess(), strategies_tested=1)
            mark = "  <-- headline" if (tv, lb) == (TARGET_VOL, LOOKBACK) else ""
            print(f"  {tv:>7.2f} {lb:>9} {su['strategy_cagr_pct']:>8.2f} "
                  f"{su['strategy_vol_pct']:>7.1f} {su['strategy_max_dd_pct']:>9.1f} "
                  f"{su['strategy_sharpe']:>8.2f} {sc.t_stat:>6.2f}{mark}")
    print(f"\n  benchmark for comparison: CAGR {s['benchmark_cagr_pct']:.2f}%, "
          f"vol {s['benchmark_vol_pct']:.1f}%, maxDD {s['benchmark_max_dd_pct']:.1f}%, "
          f"ret/vol {s['benchmark_sharpe']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
