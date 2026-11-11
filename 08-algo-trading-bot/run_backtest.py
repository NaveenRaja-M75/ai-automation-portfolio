#!/usr/bin/env python3
"""Backtest a strategy candidate on GOLDBEES, honestly.

Two runs, on purpose:

  NAIVE        best parameters fitted on all of history, scored on that same history.
               This is what almost every published backtest is, and it measures hindsight.
  WALK-FORWARD parameters fitted on a training window and traded on the untouched window
               that follows, rolled forward. Only out-of-sample trades are counted.

CUMULATIVE SEARCH ACCOUNTING. Every parameter set ever evaluated on an instrument raises
the bar for everything tested on it afterwards. Test forty candidates and the best one
looks good by luck alone. PRIOR_COMBOS carries that history forward, so a later candidate
cannot quietly reset the count and promote noise.

    python run_backtest.py                 # the current candidate
    python run_backtest.py --strategy trend
"""

from __future__ import annotations

import argparse
import sys

from core.backtest import buy_and_hold, run_backtest
from core.costs import IndianEquityDelivery, breakeven_move_pct
from core.data import load_clean
from core.metrics import scorecard, vs_benchmark
from core.walkforward import expand_grid, walk_forward
from strategies.chandelier_trend import ChandelierTrend
from strategies.trend_follow import TrendFollow

SYMBOL = "GOLDBEES.NS"
START_EQUITY = 100_000.0
RISK_PCT = 0.01
TRAIN_BARS = 500   # about two years of daily bars
TEST_BARS = 125    # about six months

# Parameter sets already spent on this instrument in earlier sessions. See CLAUDE.md 6e.
PRIOR_COMBOS = {"trend": 0, "chandelier": 27}

CANDIDATES = {
    "trend": (TrendFollow, {
        "fast": [10, 20, 30], "slow": [50, 100, 150], "atr_mult": [2.0, 2.5, 3.0],
    }),
    # One deliberate change from `trend`: the exit. Entry held fixed at fast=20 so the
    # result is attributable to the exit and nothing else.
    "chandelier": (ChandelierTrend, {
        "fast": [20], "slow": [50, 100, 150], "trail_mult": [3.0, 4.0, 5.0],
    }),
}


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="chandelier", choices=list(CANDIDATES))
    args = ap.parse_args(argv)
    factory, grid = CANDIDATES[args.strategy]

    costs = IndianEquityDelivery()
    bars = load_clean(SYMBOL)
    combos = expand_grid(grid)
    cumulative = PRIOR_COMBOS[args.strategy] + len(combos)

    print(f"{SYMBOL}: {len(bars)} daily bars, {bars[0].ts.date()} to {bars[-1].ts.date()}")
    print(f"candidate: {args.strategy}  ({len(combos)} combos this run, "
          f"{cumulative} cumulative on this instrument)")
    print(f"round-trip cost hurdle: {breakeven_move_pct(costs, bars[-1].close, 500):.3%}")

    bh_ret, bh_dd = buy_and_hold(bars, START_EQUITY)
    print(f"buy and hold: {bh_ret:+.1f}% return, {bh_dd:.1f}% max drawdown")

    rule("NAIVE (fitted and scored on the same data — the misleading number)")
    best, best_res = None, None
    for p in combos:
        r = run_backtest(bars, factory(**p), costs, START_EQUITY, RISK_PCT)
        if best_res is None or r.return_pct > best_res.return_pct:
            best, best_res = p, r
    print(f"best of {len(combos)}: {best}")
    print(f"  return {best_res.return_pct:+.1f}%   drawdown {best_res.max_drawdown_pct:.1f}%"
          f"   trades {len(best_res.trades)}")

    rule("WALK-FORWARD (out-of-sample only — the number to act on)")
    wf = walk_forward(bars, factory, grid, costs, START_EQUITY,
                      TRAIN_BARS, TEST_BARS, RISK_PCT)
    print(f"  {'train ends':<12} {'params':<30} {'n':>3} {'oos R':>8} {'equity':>12}")
    for w in wf.windows:
        if w["params"] is None:
            print(f"  {str(w['train_end'].date()):<12} (no qualifying parameter set)")
            continue
        p = " ".join(f"{k[:4]}{v}" for k, v in w["params"].items())
        print(f"  {str(w['train_end'].date()):<12} {p:<30} {w['oos_trades']:>3} "
              f"{w['oos_total_r']:>+8.2f} {w['equity_after']:>12,.0f}")

    if not wf.oos_trades:
        print("\nNo out-of-sample trades. Nothing to score.")
        return 0

    rule(f"SCORECARD (out-of-sample, adjusted for {cumulative} cumulative variants)")
    print(scorecard(wf.r_multiples, strategies_tested=cumulative).summary())

    holds = [t.bars_held for t in wf.oos_trades]
    wins = [t for t in wf.oos_trades if t.net_pnl > 0]
    print(f"\n  median bars held  {sorted(holds)[len(holds) // 2]}"
          f"   longest {max(holds)}")
    if wins:
        print(f"  best trade        {max(t.r_multiple for t in wins):+.2f}R")

    rule("VERSUS SIMPLY OWNING THE ETF")
    cmp = vs_benchmark(wf.return_pct, bh_ret, wf.max_drawdown_pct, bh_dd)
    print(f"  strategy   {cmp['strategy_return_pct']:+8.1f}%   "
          f"drawdown {cmp['strategy_max_dd_pct']:5.1f}%")
    print(f"  buy & hold {cmp['benchmark_return_pct']:+8.1f}%   "
          f"drawdown {cmp['benchmark_max_dd_pct']:5.1f}%")
    print(f"  VERDICT: {cmp['verdict']}")

    gross = sum(t.gross_pnl for t in wf.oos_trades)
    spent = sum(t.costs for t in wf.oos_trades)
    print(f"\n  gross {gross:+,.0f}   costs {spent:,.0f}   net {gross - spent:+,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
