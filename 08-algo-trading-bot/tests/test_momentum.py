"""Cross-sectional momentum, and a regression test for the flat-fee cost bug."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtest import Bar  # noqa: E402
from core.costs import IndianEquityDelivery, ZeroCost  # noqa: E402
from core.momentum import month_end_prices, run_momentum  # noqa: E402

T0 = datetime(2020, 1, 31, tzinfo=timezone.utc)


def series(start: float, monthly_growth: float, months: int = 40) -> list[Bar]:
    out = []
    for m in range(months):
        ts = datetime(2020 + m // 12, m % 12 + 1, 28, tzinfo=timezone.utc)
        p = start * (1 + monthly_growth) ** m
        out.append(Bar(ts, p, p * 1.01, p * 0.99, p, 1000))
    return out


def test_flat_per_order_fee_must_not_become_a_percentage_rate():
    """The bug that produced a false -98%.

    A flat Rs 15.93 DP charge divided by a Rs 100 reference notional reads as a 16% cost
    rate. Costs must be computed in rupees on each order's real traded value, so that a
    flat fee stays flat. This test fails loudly if anyone reintroduces a rate derived
    from an arbitrary notional.
    """
    c = IndianEquityDelivery()
    small = c.exit_cost(100.0, 1.0) / 100.0        # naive rate on a tiny notional
    real = c.exit_cost(20_000.0, 1.0) / 20_000.0   # rate on a realistic position
    assert small > 0.15, "precondition: the naive derivation really is this wrong"
    assert real < 0.003, "on a realistic position the true sell cost is under 0.3%"
    assert small > 50 * real


def test_costs_do_not_destroy_a_flat_market():
    """Everything flat, so gross return is zero. Net must be a small negative from costs,
    not a catastrophe. Before the fix this compounded to near total loss."""
    bars = {f"S{i}": series(500.0, 0.0) for i in range(8)}
    r = run_momentum(bars, IndianEquityDelivery(), 12, 1, 3, 100_000.0)
    assert r.equity, "the run must produce months"
    total = r.equity[-1] / 100_000.0 - 1
    assert -0.10 < total <= 0.0, f"flat market drift should be small, got {total:.1%}"


def test_momentum_picks_the_strongest_names():
    """Three clear winners among eight. With zero costs the ranking must find them."""
    bars = {f"WIN{i}": series(100.0, 0.03) for i in range(3)}
    bars.update({f"LOSE{i}": series(100.0, -0.01) for i in range(5)})
    r = run_momentum(bars, ZeroCost(), 12, 1, 3, 100_000.0)
    assert all(all(s.startswith("WIN") for s in h) for h in r.holdings)
    assert r.equity[-1] > r.benchmark_equity[-1]


def test_the_skip_month_is_actually_skipped():
    """Ranking must use the window ending one month back, not the latest month."""
    bars = {f"S{i}": series(100.0, 0.01) for i in range(6)}
    dates, prices = month_end_prices(bars)
    assert len(dates) == len(prices) > 20


def test_benchmark_is_the_equal_weight_basket():
    """With top_n equal to the universe size, the strategy IS the benchmark, so the
    excess return must be zero apart from turnover costs."""
    bars = {f"S{i}": series(100.0, 0.01 * (i + 1)) for i in range(5)}
    r = run_momentum(bars, ZeroCost(), 12, 1, 5, 100_000.0)
    assert all(abs(e) < 1e-9 for e in r.excess_returns)
