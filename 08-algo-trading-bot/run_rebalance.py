#!/usr/bin/env python3
"""Does rebalancing a 75/25 portfolio actually add anything?

The control is NEVER REBALANCING: buy once, let it drift, touch nothing for 17 years. If
no schedule beats that after Indian costs and capital gains tax, then rebalancing is
activity rather than strategy and the right answer is to leave it alone.

Every variant is compared against both that control and 100% equity.

    python run_rebalance.py
    python run_rebalance.py --check 780000 220000   # what to trade given current holdings
"""

from __future__ import annotations

import argparse
import sys
import warnings

warnings.filterwarnings("ignore")

from core.costs import IndianEquityDelivery
from core.data import load_clean
from core.rebalance import RebalanceConfig, orders_for_today, run_rebalance

SYMBOL = "NIFTYBEES.NS"
START = 100_000.0
RF = 0.06

VARIANTS = [
    RebalanceConfig("100% equity, never touched", target_equity=1.00, never=True),
    RebalanceConfig("75/25 NEVER rebalanced (control)", target_equity=0.75, never=True),
    RebalanceConfig("75/25 annual", target_equity=0.75, calendar_months=12),
    RebalanceConfig("75/25 semi-annual", target_equity=0.75, calendar_months=6),
    RebalanceConfig("75/25 quarterly", target_equity=0.75, calendar_months=3),
    RebalanceConfig("75/25 drift band 10%", target_equity=0.75, band=0.10),
    RebalanceConfig("75/25 drift band 5%", target_equity=0.75, band=0.05),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", nargs=2, type=float, metavar=("EQUITY", "CASH"),
                    help="current equity and cash value; prints what to trade")
    ap.add_argument("--target", type=float, default=0.75)
    ap.add_argument("--band", type=float, default=0.05)
    args = ap.parse_args(argv)

    if args.check:
        o = orders_for_today(args.check[0], args.check[1], args.target, args.band)
        print(f"portfolio      {o['total']:>14,.0f}")
        print(f"equity weight  {o['current_weight']:>14.1%}   target {o['target_weight']:.0%}")
        print(f"drift          {o['drift']:>+14.1%}")
        print(f"\n  {o['action']}" + (f"  {o['amount']:,.0f} of the index fund"
                                      if o["amount"] else ""))
        print(f"  {o['reason']}")
        if o["action"] == "SELL":
            print("\n  Note: selling equity realises capital gains. Above the Rs 1.25 lakh")
            print("  annual exemption, LTCG is taxed at 12.5%. If you are adding new money,")
            print("  direct it to the underweight side instead and skip the tax entirely.")
        return 0

    costs = IndianEquityDelivery()
    bars = load_clean(SYMBOL, start="2005-01-01")
    print(f"{SYMBOL}: {len(bars)} daily bars, {bars[0].ts.date()} to {bars[-1].ts.date()}")
    print(f"cash leg earns {RF:.0%}, LTCG 12.5% above the Rs 1.25 lakh annual exemption\n")

    print(f"  {'variant':<34}{'CAGR%':>7}{'vol%':>7}{'maxDD%':>8}{'ret/vol':>8}"
          f"{'rebal':>7}{'costs':>9}{'tax':>9}")
    print("  " + "-" * 89)
    rows = []
    for cfg in VARIANTS:
        r = run_rebalance(bars, costs, cfg, START, RF)
        s = r.summary()
        rows.append(s)
        print(f"  {s['name']:<34}{s['cagr_pct']:>7.2f}{s['vol_pct']:>7.1f}"
              f"{s['max_dd_pct']:>8.1f}{s['ret_per_vol']:>8.2f}{s['rebalances']:>7}"
              f"{s['costs']:>9,.0f}{s['tax']:>9,.0f}")

    control = rows[1]
    best = max(rows[2:], key=lambda r: r["ret_per_vol"])
    print(f"\n  Control (never rebalanced):  ret/vol {control['ret_per_vol']:.2f}, "
          f"drifted between {rows[1]['drift_min']:.0%} and {rows[1]['drift_max']:.0%} equity")
    print(f"  Best rebalanced variant:     {best['name']} at ret/vol {best['ret_per_vol']:.2f}")
    delta = best["ret_per_vol"] - control["ret_per_vol"]
    print(f"  Rebalancing added {delta:+.3f} to return-per-unit-risk, "
          f"at a cost of {best['costs'] + best['tax']:,.0f} in fees and tax.")
    print(f"\n  VERDICT: rebalancing "
          f"{'is worth doing' if delta > 0.02 else 'did NOT earn its costs'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
