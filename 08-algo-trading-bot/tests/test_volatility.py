"""Volatility-managed exposure: mechanics, and the static-weight control."""

from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtest import Bar  # noqa: E402
from core.costs import ZeroCost  # noqa: E402
from core.volatility import realised_vol, run_vol_managed  # noqa: E402

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def synth(daily_vol: float, drift: float = 0.0, n: int = 800, seed: int = 7) -> list[Bar]:
    rng = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        p *= (1 + drift + rng.gauss(0, daily_vol))
        out.append(Bar(T0 + timedelta(days=i), p, p * 1.005, p * 0.995, p, 1000))
    return out


def test_realised_vol_annualises():
    daily = [0.01, -0.01] * 50
    assert realised_vol(daily) == pytest.approx(0.01 * math.sqrt(252), rel=0.05)


def test_weight_falls_when_volatility_rises():
    """The core mechanism: a more volatile market must be held at a smaller weight."""
    calm = run_vol_managed(synth(0.005), ZeroCost(), 0.15, 21, 1.0, 0.06)
    wild = run_vol_managed(synth(0.030), ZeroCost(), 0.15, 21, 1.0, 0.06)
    assert calm.summary()["mean_weight"] > wild.summary()["mean_weight"]


def test_weight_is_capped_and_never_levered():
    r = run_vol_managed(synth(0.001), ZeroCost(), 0.15, 21, 1.0, 0.06)
    assert max(r.weights) <= 1.0 + 1e-12


def test_rebalance_uses_only_past_returns():
    """A rebalance on the first day of a month may only see returns already observed.
    Two series identical up to a point must produce identical weights up to that point."""
    a = synth(0.01, seed=1)
    b = list(a[:400]) + synth(0.05, seed=2)[400:]
    ra = run_vol_managed(a, ZeroCost(), 0.15, 21, 1.0, 0.06)
    rb = run_vol_managed(b, ZeroCost(), 0.15, 21, 1.0, 0.06)
    shared = min(len(ra.weights), len(rb.weights)) // 3
    assert ra.weights[:shared] == pytest.approx(rb.weights[:shared])


def test_cash_leg_earns_the_risk_free_rate():
    """With weight pinned near zero by an extreme lookback vol, the account must still
    grow at roughly the cash rate rather than sitting flat."""
    r = run_vol_managed(synth(0.05), ZeroCost(), 0.001, 21, 1.0, 0.06, 100_000.0)
    assert r.summary()["mean_weight"] < 0.05
    assert r.equity[-1] > 100_000.0


def test_static_weight_control_is_expressible():
    """The control that decided the real result: a target so high the weight pins at
    max_weight gives a static allocation with no timing at all."""
    r = run_vol_managed(synth(0.01), ZeroCost(), 1.99, 21, 0.74, 0.06)
    assert all(w == pytest.approx(0.74) for w in r.weights)


def test_absurd_target_is_rejected():
    with pytest.raises(ValueError):
        run_vol_managed(synth(0.01), ZeroCost(), 999.0, 21, 1.0, 0.06)
    with pytest.raises(ValueError):
        run_vol_managed(synth(0.01), ZeroCost(), 0.15, 21, 0.0, 0.06)
