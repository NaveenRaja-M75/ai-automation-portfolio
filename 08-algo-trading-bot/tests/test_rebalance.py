"""Rebalancing mechanics, the never-rebalance control, and Indian capital gains tax."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtest import Bar  # noqa: E402
from core.costs import IndianEquityDelivery, ZeroCost  # noqa: E402
from core.rebalance import (  # noqa: E402
    RebalanceConfig, financial_year, orders_for_today, run_rebalance,
)

T0 = datetime(2015, 4, 1, tzinfo=timezone.utc)


def ramp(daily_growth: float, n: int = 1500) -> list[Bar]:
    out, p = [], 100.0
    for i in range(n):
        p *= (1 + daily_growth)
        out.append(Bar(T0 + timedelta(days=i), p, p, p, p, 1000))
    return out


def test_indian_financial_year_boundary():
    assert financial_year(datetime(2026, 3, 31, tzinfo=timezone.utc)) == 2025
    assert financial_year(datetime(2026, 4, 1, tzinfo=timezone.utc)) == 2026


def test_never_rebalance_lets_weight_drift():
    """The control. In a rising market the equity share must climb well past target."""
    r = run_rebalance(ramp(0.0008), ZeroCost(),
                      RebalanceConfig("never", 0.75, never=True), 100_000.0)
    assert r.rebalances == 0
    assert max(r.weights) > 0.85, "a rising market must push the untouched weight up"


def test_rebalancing_holds_the_weight_near_target():
    r = run_rebalance(ramp(0.0008), ZeroCost(),
                      RebalanceConfig("q", 0.75, calendar_months=3), 100_000.0)
    assert r.rebalances > 5
    assert max(r.weights) < 0.85, "rebalancing must keep the drift bounded"


def test_drift_band_only_fires_on_breach():
    """A wide band must trade less often than a narrow one on the same data."""
    wide = run_rebalance(ramp(0.0008), ZeroCost(), RebalanceConfig("w", 0.75, band=0.15))
    tight = run_rebalance(ramp(0.0008), ZeroCost(), RebalanceConfig("t", 0.75, band=0.02))
    assert tight.rebalances > wide.rebalances


def test_flat_market_never_breaches_the_band():
    flat = [Bar(T0 + timedelta(days=i), 100.0, 100.0, 100.0, 100.0, 10) for i in range(900)]
    r = run_rebalance(flat, ZeroCost(), RebalanceConfig("b", 0.75, band=0.05),
                      100_000.0, rf_annual=0.0)
    assert r.rebalances == 0, "nothing moved, so nothing should have traded"


def test_tax_is_zero_below_the_exemption():
    """A small portfolio's realised gains sit under the Rs 1.25 lakh annual exemption."""
    r = run_rebalance(ramp(0.0008), ZeroCost(),
                      RebalanceConfig("a", 0.75, calendar_months=12), 100_000.0)
    assert r.total_tax == 0.0


def test_tax_fires_above_the_exemption():
    """The same strategy on a large portfolio must realise taxable gains."""
    small = run_rebalance(ramp(0.0008), ZeroCost(),
                          RebalanceConfig("a", 0.75, calendar_months=12), 100_000.0)
    large = run_rebalance(ramp(0.0008), ZeroCost(),
                          RebalanceConfig("a", 0.75, calendar_months=12), 50_000_000.0)
    assert small.total_tax == 0.0
    assert large.total_tax > 0.0, "a large portfolio must breach the exemption"


def test_tax_can_be_switched_off_for_comparison():
    cfg = RebalanceConfig("a", 0.75, calendar_months=12)
    taxed = run_rebalance(ramp(0.0008), ZeroCost(), cfg, 50_000_000.0, apply_tax=True)
    free = run_rebalance(ramp(0.0008), ZeroCost(), cfg, 50_000_000.0, apply_tax=False)
    assert free.equity_curve[-1] > taxed.equity_curve[-1]


def test_falling_market_realises_no_taxable_gain():
    """Selling at a loss must never create a tax liability."""
    r = run_rebalance(ramp(-0.0005), ZeroCost(),
                      RebalanceConfig("q", 0.75, calendar_months=3), 50_000_000.0)
    assert r.total_tax == 0.0


def test_real_costs_reduce_the_result():
    cfg = RebalanceConfig("q", 0.75, calendar_months=3)
    free = run_rebalance(ramp(0.0008), ZeroCost(), cfg, 100_000.0)
    real = run_rebalance(ramp(0.0008), IndianEquityDelivery(), cfg, 100_000.0)
    assert real.total_costs > 0
    assert real.equity_curve[-1] < free.equity_curve[-1]


def test_hundred_percent_equity_holds_no_cash():
    r = run_rebalance(ramp(0.0008), ZeroCost(),
                      RebalanceConfig("all", 1.0, never=True), 100_000.0)
    assert all(w > 0.99 for w in r.weights)


def test_invalid_target_rejected():
    with pytest.raises(ValueError):
        run_rebalance(ramp(0.0), ZeroCost(), RebalanceConfig("x", 1.5))


# --- the live instruction tool ---------------------------------------------

def test_orders_hold_inside_the_band():
    o = orders_for_today(770_000, 230_000, 0.75, 0.05)
    assert o["action"] == "HOLD" and o["amount"] == 0.0


def test_orders_sell_when_equity_runs_hot():
    o = orders_for_today(900_000, 100_000, 0.75, 0.05)
    assert o["action"] == "SELL"
    assert o["amount"] == pytest.approx(150_000.0)


def test_orders_buy_when_equity_lags():
    o = orders_for_today(600_000, 400_000, 0.75, 0.05)
    assert o["action"] == "BUY"
    assert o["amount"] == pytest.approx(150_000.0)


def test_orders_reject_empty_portfolio():
    with pytest.raises(ValueError):
        orders_for_today(0.0, 0.0)
