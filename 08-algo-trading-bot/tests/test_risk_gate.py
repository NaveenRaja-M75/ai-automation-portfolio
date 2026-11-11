"""Every risk rule ships with a test that proves the rejection actually fires.

A risk control with no failing-case test is a comment, not a control.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.risk_gate import (  # noqa: E402
    EquityQuote,
    OrderIntent,
    OrderRejected,
    RiskLimits,
    validate_order,
)

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
EQUITY = 25_000.0


@pytest.fixture
def limits(tmp_path):
    # tmp_path keeps each test's kill switch isolated from every other test
    return RiskLimits(kill_switch_path=tmp_path / "KILL_SWITCH")


@pytest.fixture
def equity():
    return EquityQuote(value=EQUITY, fetched_at=NOW, source="test-broker")


def intent(**over) -> OrderIntent:
    """A long that risks exactly 1% of 25k: 100 shares x $2.50 of stop distance."""
    base = dict(
        symbol="SPY",
        side="buy",
        qty=100.0,
        entry=500.00,
        stop_loss=497.50,
        signal_ts=NOW,
        idempotency_key="alert-001",
    )
    base.update(over)
    return OrderIntent(**base)


def call(o, equity, limits, day_pnl=0.0, day_start_equity=EQUITY, now=NOW, audit=None):
    return validate_order(o, equity, limits, day_pnl, day_start_equity, now=now, audit=audit)


# --- happy path ------------------------------------------------------------

def test_valid_order_passes(equity, limits):
    o = intent()
    assert call(o, equity, limits) is o


def test_audit_records_a_pass(equity, limits):
    seen = []
    call(intent(), equity, limits, audit=seen.append)
    assert seen[0]["decision"] == "PASS"
    assert seen[0]["idempotency_key"] == "alert-001"


# --- gate 1: kill switch ---------------------------------------------------

def test_kill_switch_blocks_everything(equity, limits):
    limits.kill_switch_path.write_text("halted by operator")
    with pytest.raises(OrderRejected) as e:
        call(intent(), equity, limits)
    assert e.value.gate == "KILL_SWITCH"


# --- gate 2: daily loss circuit breaker ------------------------------------

def test_daily_loss_limit_rejects_and_trips_kill_switch(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(), equity, limits, day_pnl=-751.0)  # limit is 3% = 750
    assert e.value.gate == "DAILY_LOSS"
    assert limits.kill_switch_path.exists(), "breaching the daily limit must trip the switch"


def test_daily_loss_exactly_at_limit_rejects(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(), equity, limits, day_pnl=-750.0)
    assert e.value.gate == "DAILY_LOSS"


def test_loss_inside_the_limit_passes(equity, limits):
    call(intent(), equity, limits, day_pnl=-749.0)


# --- gate 3: signal freshness ----------------------------------------------

def test_stale_signal_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(signal_ts=NOW - timedelta(seconds=5.1)), equity, limits)
    assert e.value.gate == "FRESHNESS"


def test_future_signal_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(signal_ts=NOW + timedelta(seconds=30)), equity, limits)
    assert e.value.gate == "FRESHNESS"


def test_naive_timestamp_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(signal_ts=NOW.replace(tzinfo=None)), equity, limits)
    assert e.value.gate == "SCHEMA"


# --- gate 4: equity quote freshness ----------------------------------------

def test_stale_equity_quote_rejected(limits):
    stale = EquityQuote(value=EQUITY, fetched_at=NOW - timedelta(seconds=31), source="test-broker")
    with pytest.raises(OrderRejected) as e:
        call(intent(), stale, limits)
    assert e.value.gate == "EQUITY_STALE"


# --- gate 5: stop-loss -----------------------------------------------------

def test_missing_stop_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(stop_loss=None), equity, limits)
    assert e.value.gate == "STOP_LOSS"


def test_long_stop_above_entry_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(stop_loss=502.50), equity, limits)
    assert e.value.gate == "STOP_LOSS"


def test_short_stop_below_entry_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(side="sell", stop_loss=497.50), equity, limits)
    assert e.value.gate == "STOP_LOSS"


def test_valid_short_passes(equity, limits):
    call(intent(side="sell", stop_loss=502.50), equity, limits)


def test_absurdly_wide_stop_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(qty=1.0, stop_loss=400.00), equity, limits)  # 20% away
    assert e.value.gate == "STOP_LOSS"


def test_near_zero_stop_distance_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(qty=1.0, stop_loss=499.999), equity, limits)
    assert e.value.gate == "STOP_LOSS"


# --- gate 6: position sizing cap -------------------------------------------

def test_risk_above_cap_rejected_not_resized(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(qty=101.0), equity, limits)  # 101 x 2.50 = 252.50 > 250
    assert e.value.gate == "SIZING"


def test_risk_cap_cannot_be_raised_above_two_percent(equity, limits):
    """A config asking for 50% risk is silently clamped to the 2% ceiling."""
    greedy = RiskLimits(risk_pct=0.50, kill_switch_path=limits.kill_switch_path)
    with pytest.raises(OrderRejected) as e:
        call(intent(qty=250.0), equity, greedy)  # 625 risk > 2% of 25k = 500
    assert e.value.gate == "SIZING"


def test_two_percent_config_allows_double_the_default(equity, limits):
    wider = RiskLimits(risk_pct=0.02, kill_switch_path=limits.kill_switch_path)
    call(intent(qty=200.0), equity, wider)  # 200 x 2.50 = 500 = exactly 2%


# --- gate 7 and schema hardening -------------------------------------------

def test_missing_idempotency_key_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(idempotency_key="  "), equity, limits)
    assert e.value.gate == "IDEMPOTENCY"


def test_nan_entry_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(entry=float("nan")), equity, limits)
    assert e.value.gate == "SCHEMA"


def test_string_qty_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(qty="100"), equity, limits)
    assert e.value.gate == "SCHEMA"


def test_negative_qty_rejected(equity, limits):
    with pytest.raises(OrderRejected) as e:
        call(intent(qty=-100.0), equity, limits)
    assert e.value.gate == "SCHEMA"


def test_unexpected_error_fails_secure(equity, limits, monkeypatch):
    """Even a bug inside the gate must reject rather than pass the order through."""
    import core.risk_gate as rg

    monkeypatch.setattr(rg.Path, "exists", lambda self: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OrderRejected) as e:
        call(intent(), equity, limits)
    assert e.value.gate == "UNEXPECTED"
