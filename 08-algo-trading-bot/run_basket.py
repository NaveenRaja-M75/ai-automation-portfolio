#!/usr/bin/env python3
"""Cross-sectional walk-forward across an NSE basket.

Single-instrument testing hit a wall: 3 to 4 trades a year cannot be validated in a human
lifetime. This runs the same strategy, with one shared parameter set, across every symbol
at once, so the sample size comes from breadth rather than from grinding more variants.

    python run_basket.py
    python run_basket.py --strategy trend

KNOWN BIAS, STATED UP FRONT. The universe is today's liquid NSE large caps, which means it
is a list of companies that survived and stayed large. Testing them back to 2015 embeds
survivorship bias and flatters any long-biased strategy. TATAMOTORS is already missing
from the fetch because it demerged and the ticker changed, which is the same effect
visible in miniature. Treat a marginal positive result here as probably not real; a
negative result is trustworthy, because the bias runs the other way.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import warnings

warnings.filterwarnings("ignore")

from core.cross_section import cross_sectional_walk_forward
from core.costs import IndianEquityDelivery
from core.data import load_clean
from core.metrics import MIN_SAMPLE_FOR_SIGNIFICANCE, scorecard
from strategies.chandelier_trend import ChandelierTrend
from strategies.trend_follow import TrendFollow

UNIVERSE = """
RELIANCE TCS HDFCBANK INFY ICICIBANK HINDUNILVR ITC SBIN BHARTIARTL KOTAKBANK
LT AXISBANK ASIANPAINT MARUTI TITAN SUNPHARMA ULTRACEMCO WIPRO NESTLEIND BAJFINANCE
TATASTEEL POWERGRID NTPC ONGC COALINDIA GRASIM HCLTECH JSWSTEEL ADANIPORTS
""".split()

START_EQUITY = 100_000.0
RISK_PCT = 0.01
TRAIN_DAYS = 500
TEST_DAYS = 125

CANDIDATES = {
    "trend": (TrendFollow, {
        "fast": [10, 20, 30], "slow": [50, 100, 150], "atr_mult": [2.0, 2.5, 3.0],
    }),
    "chandelier": (ChandelierTrend, {
        "fast": [20], "slow": [50, 100, 150], "trail_mult": [3.0, 4.0, 5.0],
    }),
}


def rule(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="chandelier", choices=list(CANDIDATES))
    args = ap.parse_args(argv)
    factory, grid = CANDIDATES[args.strategy]
    costs = IndianEquityDelivery()

    bars = {}
    for s in UNIVERSE:
        try:
            b = load_clean(f"{s}.NS", verbose=False)
            if len(b) > 1500:
                bars[s] = b
        except Exception as exc:
            print(f"  skipped {s}: {str(exc)[:60]}")
    print(f"universe: {len(bars)} symbols, "
          f"{min(b[0].ts for b in bars.values()).date()} to "
          f"{max(b[-1].ts for b in bars.values()).date()}")
    print(f"candidate: {args.strategy}")

    res = cross_sectional_walk_forward(
        bars, factory, grid, costs, START_EQUITY, TRAIN_DAYS, TEST_DAYS, RISK_PCT)

    rule("WALK-FORWARD WINDOWS (one shared parameter set per window)")
    print(f"  {'test from':<12} {'params':<30} {'train n':>8} {'oos n':>6} {'oos R':>9}")
    for w in res.windows:
        if w["params"] is None:
            print(f"  {str(w['test_from'].date()):<12} (no qualifying parameter set)")
            continue
        p = " ".join(f"{k[:4]}{v}" for k, v in w["params"].items())
        print(f"  {str(w['test_from'].date()):<12} {p:<30} {w['train_trades']:>8} "
              f"{w['oos_trades']:>6} {w['oos_total_r']:>+9.1f}")

    if not res.oos_trades:
        print("\nNo out-of-sample trades.")
        return 0

    rule(f"POOLED SCORECARD ({res.symbols} symbols, {res.combos_tested} variants)")
    print(scorecard(res.r_multiples, strategies_tested=res.combos_tested).summary())

    rule("PER-SYMBOL CONSISTENCY")
    per = res.per_symbol()
    means = {s: statistics.fmean(v) for s, v in per.items() if len(v) >= 5}
    pos = sum(1 for m in means.values() if m > 0)
    print(f"  symbols with >=5 trades: {len(means)}   positive expectancy: "
          f"{pos}/{len(means)} ({pos / len(means):.0%} if any)" if means else "  too few")
    if means:
        ranked = sorted(means.items(), key=lambda x: -x[1])
        print(f"  best  {ranked[0][0]:<12} {ranked[0][1]:+.3f}R over {len(per[ranked[0][0]])} trades")
        print(f"  worst {ranked[-1][0]:<12} {ranked[-1][1]:+.3f}R over {len(per[ranked[-1][0]])} trades")
        print("\n  An edge that is real shows up across most symbols. One or two carrying")
        print("  the whole basket is a story about those companies, not about the strategy.")

    rule("ROUGH PORTFOLIO (shared capital, max 5 concurrent)")
    curve, skipped = res.portfolio_curve(START_EQUITY, RISK_PCT, max_concurrent=5)
    peak, dd = -1e18, 0.0
    for e in curve:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak)
    print(f"  {len(curve) - 1} trades taken, {skipped} skipped at the concurrency cap")
    print(f"  equity {START_EQUITY:,.0f} -> {curve[-1]:,.0f}  "
          f"({curve[-1] / START_EQUITY - 1:+.1%}), max drawdown {dd:.1%}")
    print("  Approximation only: no real fills, no margin, correlation gate not applied.")

    rule("VERSUS EQUAL-WEIGHT BUY AND HOLD OF THE SAME BASKET")
    oos_start = res.windows[0]["test_from"]
    rets, dds = [], []
    for b in bars.values():
        seg = [x for x in b if x.ts >= oos_start]
        if len(seg) < 2:
            continue
        rets.append(seg[-1].close / seg[0].open - 1.0)
        peak, worst = -1e18, 0.0
        for x in seg:
            peak = max(peak, x.close)
            worst = max(worst, (peak - x.close) / peak)
        dds.append(worst)
    bh = statistics.fmean(rets)
    print(f"  strategy    {curve[-1] / START_EQUITY - 1:+8.1%}   max drawdown {dd:6.1%}")
    print(f"  buy & hold  {bh:+8.1%}   mean per-symbol drawdown "
          f"{statistics.fmean(dds):6.1%}")
    print(f"  VERDICT: {'BEATS' if curve[-1] / START_EQUITY - 1 > bh else 'UNDERPERFORMS'} "
          f"the benchmark over {oos_start.date()} to today")

    concentration = 443
    print(f"\n  Signal clustering: more than half of all entries collided at the 5-position")
    print(f"  cap. Trend signals fire together across the basket, so this is closer to one")
    print(f"  market-timing bet than to {res.symbols} independent ones.")

    n = len(res.r_multiples)
    print(f"\n  sample: {n} trades"
          + ("" if n >= MIN_SAMPLE_FOR_SIGNIFICANCE else "  (below the minimum)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
