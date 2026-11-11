"""Two-asset rebalancing between an index fund and cash, with Indian tax modelled.

This is the only finding that survived four rounds of strategy testing: a static split
between equity and cash produced better return per unit of risk than 100% equity. That
result came from the ALLOCATION, though, not from rebalancing it, and those are different
claims. This module exists to test the second one honestly.

The control that matters is NEVER REBALANCING. Buy 75/25 once, let it drift, touch nothing.
If periodic rebalancing cannot beat that, then rebalancing is activity rather than
strategy, and the correct action is to do nothing at all.

TAX IS MODELLED, and it is the reason most rebalancing analyses are wrong. Selling equity
to rebalance is a taxable event in India. Long-term capital gains on listed equity are
taxed at 12.5% above a Rs 1.25 lakh annual exemption. An analysis that ignores this makes
frequent rebalancing look free when it is not, and frequency is exactly the variable under
test.

Simplifications, stated rather than hidden: a single weighted-average cost basis is used
rather than per-lot accounting, and all gains are treated as long-term. Both are reasonable
here because rebalancing sells are infrequent and the equity position turns over slowly,
and both make rebalancing look slightly CHEAPER than reality, so they bias against this
module's own conclusion rather than for it.
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
LTCG_RATE = 0.125
LTCG_EXEMPTION = 125_000.0


def financial_year(d: datetime) -> int:
    """Indian financial year runs 1 April to 31 March. FY2025-26 is returned as 2025."""
    return d.year if d.month >= 4 else d.year - 1


@dataclass(frozen=True)
class RebalanceConfig:
    name: str
    target_equity: float = 0.75
    band: Optional[float] = None            # absolute drift, e.g. 0.05 for +/-5 points
    calendar_months: Optional[int] = None   # e.g. 12 for annual
    never: bool = False                     # the control: buy once, never touch it


@dataclass
class RebalanceResult:
    config: RebalanceConfig
    dates: list[datetime] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    rebalances: int = 0
    total_costs: float = 0.0
    total_tax: float = 0.0

    @property
    def daily_returns(self) -> list[float]:
        return [self.equity_curve[i] / self.equity_curve[i - 1] - 1.0
                for i in range(1, len(self.equity_curve))]

    def summary(self) -> dict:
        rets = self.daily_returns
        years = len(rets) / TRADING_DAYS
        vol = statistics.stdev(rets) * math.sqrt(TRADING_DAYS) if len(rets) > 1 else 0.0
        cagr = ((self.equity_curve[-1] / self.equity_curve[0]) ** (1 / years) - 1) * 100 \
            if years > 0.5 else 0.0
        peak, dd = -math.inf, 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            if peak > 0:
                dd = max(dd, (peak - e) / peak)
        return {
            "name": self.config.name,
            "final": self.equity_curve[-1],
            "cagr_pct": cagr,
            "vol_pct": vol * 100,
            "max_dd_pct": dd * 100,
            "ret_per_vol": cagr / (vol * 100) if vol else 0.0,
            "rebalances": self.rebalances,
            "costs": self.total_costs,
            "tax": self.total_tax,
            "drift_min": min(self.weights) if self.weights else 0.0,
            "drift_max": max(self.weights) if self.weights else 0.0,
        }


def run_rebalance(
    bars: Sequence[Bar],
    costs: CostModel,
    config: RebalanceConfig,
    start_equity: float = 100_000.0,
    rf_annual: float = 0.06,
    apply_tax: bool = True,
) -> RebalanceResult:
    """Simulate holding target_equity in the index and the rest in cash."""
    if not 0.0 <= config.target_equity <= 1.0:
        raise ValueError("target_equity must be between 0 and 1")
    if len(bars) < 2:
        raise ValueError("need at least two bars")

    res = RebalanceResult(config=config)
    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1

    # Initial purchase, priced with slippage and charged entry costs like any other buy.
    px = costs.slip(bars[0].close, "buy", entering=True)
    target_value = start_equity * config.target_equity
    units = target_value / px if px > 0 else 0.0
    buy_cost = costs.entry_cost(target_value, 1.0) if target_value > 0 else 0.0
    cash = start_equity - target_value - buy_cost
    avg_cost = px
    res.total_costs += buy_cost

    last_rebalance = bars[0].ts
    fy_gains: dict[int, float] = {}
    cur_fy = financial_year(bars[0].ts)

    for i, bar in enumerate(bars):
        if i > 0:
            cash *= (1.0 + rf_daily)

        # Settle the previous financial year's capital gains tax when it rolls over.
        fy = financial_year(bar.ts)
        if apply_tax and fy != cur_fy:
            gain = fy_gains.pop(cur_fy, 0.0)
            taxable = max(0.0, gain - LTCG_EXEMPTION)
            tax = taxable * LTCG_RATE
            cash -= tax
            res.total_tax += tax
            cur_fy = fy

        equity_value = units * bar.close
        total = equity_value + cash
        weight = equity_value / total if total > 0 else 0.0

        if not config.never and total > 0:
            due = False
            if config.band is not None and abs(weight - config.target_equity) >= config.band:
                due = True
            if config.calendar_months is not None:
                months = ((bar.ts.year - last_rebalance.year) * 12
                          + bar.ts.month - last_rebalance.month)
                if months >= config.calendar_months:
                    due = True

            if due:
                desired_value = total * config.target_equity
                delta = desired_value - equity_value
                if delta > 0:  # buy equity
                    fill = costs.slip(bar.close, "buy", entering=True)
                    c = costs.entry_cost(delta, 1.0) + delta * costs.slippage_pct
                    if delta + c <= cash:
                        new_units = delta / fill
                        avg_cost = ((avg_cost * units + fill * new_units) / (units + new_units)
                                    if units + new_units > 0 else fill)
                        units += new_units
                        cash -= (delta + c)
                        res.total_costs += c
                        res.rebalances += 1
                        last_rebalance = bar.ts
                elif delta < 0:  # sell equity
                    fill = costs.slip(bar.close, "sell", entering=False)
                    sell_value = -delta
                    sold_units = sell_value / fill if fill > 0 else 0.0
                    sold_units = min(sold_units, units)
                    if sold_units > 0:
                        proceeds = sold_units * fill
                        c = costs.exit_cost(proceeds, 1.0) + proceeds * costs.slippage_pct
                        gain = (fill - avg_cost) * sold_units
                        fy_gains[fy] = fy_gains.get(fy, 0.0) + max(0.0, gain)
                        units -= sold_units
                        cash += (proceeds - c)
                        res.total_costs += c
                        res.rebalances += 1
                        last_rebalance = bar.ts

            equity_value = units * bar.close
            total = equity_value + cash
            weight = equity_value / total if total > 0 else 0.0

        res.dates.append(bar.ts)
        res.equity_curve.append(total)
        res.weights.append(weight)

    return res


def orders_for_today(
    equity_value: float,
    cash_value: float,
    target_equity: float = 0.75,
    band: float = 0.05,
) -> dict:
    """What to trade right now, given what you currently hold.

    Returns a plain instruction. Deliberately does not place anything: rebalancing is a
    twice-a-decade decision, and a human clicking the button is not the bottleneck.
    """
    total = equity_value + cash_value
    if total <= 0:
        raise ValueError("total portfolio value must be positive")
    weight = equity_value / total
    drift = weight - target_equity
    desired = total * target_equity
    delta = desired - equity_value
    return {
        "total": total,
        "current_weight": weight,
        "target_weight": target_equity,
        "drift": drift,
        "action": ("HOLD" if abs(drift) < band else ("BUY" if delta > 0 else "SELL")),
        "amount": abs(delta) if abs(drift) >= band else 0.0,
        "reason": (f"drift {drift:+.1%} is inside the +/-{band:.0%} band"
                   if abs(drift) < band else
                   f"drift {drift:+.1%} breached the +/-{band:.0%} band"),
    }
