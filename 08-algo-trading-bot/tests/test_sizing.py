"""Currency-correct risk, checked against hand-computed pip values.

Every expected number here was derived independently from standard pip-value arithmetic,
not by running the code and recording what it printed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.risk_gate import (  # noqa: E402
    EquityQuote, OrderIntent, OrderRejected, RiskLimits, validate_order,
)
from core.sizing import (  # noqa: E402
    INSTRUMENTS, FxRates, SizingError, margin_required, notional_in_account,
    risk_in_account, suggest_qty,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

RATES = {
    "EURUSD": 1.0850,
    "GBPUSD": 1.2700,
    "USDJPY": 150.00,
    "EURGBP": 0.8500,
    "USDINR": 87.50,
}


@pytest.fixture
def fx():
    return FxRates(RATES, as_of=NOW)


# --- rate resolution -------------------------------------------------------

def test_identity_and_inverse(fx):
    assert fx.rate("USD", "USD") == 1.0
    assert fx.rate("USD", "JPY") == 150.00
    assert fx.rate("JPY", "USD") == pytest.approx(1 / 150.00)


def test_stablecoins_normalise_to_usd(fx):
    assert fx.rate("USDT", "USD") == 1.0


def test_triangulation_through_usd(fx):
    # EUR->INR has no direct rate: 1.0850 * 87.50
    assert fx.rate("EUR", "INR") == pytest.approx(1.0850 * 87.50)


def test_missing_rate_raises_rather_than_defaulting(fx):
    with pytest.raises(SizingError):
        fx.rate("EUR", "ZWL")


def test_stale_rates_raise(fx):
    with pytest.raises(SizingError):
        fx.assert_fresh(NOW + timedelta(seconds=61))


def test_nonsense_rate_raises():
    with pytest.raises(SizingError):
        FxRates({"EURUSD": 0.0}, as_of=NOW).rate("EUR", "USD")


# --- risk in account currency ---------------------------------------------

def test_eurusd_usd_account_matches_naive_case(fx):
    """Quote currency equals account currency, so the naive formula is right here.
    1 lot, 50 pip stop: 0.0050 * 100,000 = $500."""
    r = risk_in_account(INSTRUMENTS["EURUSD"], 1.0850, 1.0800, 1.0, fx, "USD")
    assert r == pytest.approx(500.0)


def test_usdjpy_is_where_naive_math_breaks(fx):
    """1 lot USD/JPY, 50 pip stop = 0.50 of price.
    Risk in JPY = 0.50 * 100,000 = 50,000 JPY. At 150 that is 333.33 USD.
    The naive formula would have reported 50,000, a 150x overstatement."""
    r = risk_in_account(INSTRUMENTS["USDJPY"], 150.00, 149.50, 1.0, fx, "USD")
    assert r == pytest.approx(333.333, rel=1e-4)
    naive = abs(150.00 - 149.50) * 1.0
    assert r != pytest.approx(naive)


def test_eurgbp_cross_converts_through_gbp(fx):
    """1 lot EUR/GBP, 50 pip stop: 0.0050 * 100,000 = 500 GBP -> 500 * 1.27 = 635 USD."""
    r = risk_in_account(INSTRUMENTS["EURGBP"], 0.8500, 0.8450, 1.0, fx, "USD")
    assert r == pytest.approx(635.0)


def test_gold_contract_is_100_ounces(fx):
    """XAU/USD, 1 lot, $10 stop: 10 * 100 oz = $1,000."""
    r = risk_in_account(INSTRUMENTS["XAUUSD"], 2400.00, 2390.00, 1.0, fx, "USD")
    assert r == pytest.approx(1000.0)


def test_risk_reported_in_inr_for_an_inr_account(fx):
    r = risk_in_account(INSTRUMENTS["EURUSD"], 1.0850, 1.0800, 1.0, fx, "INR")
    assert r == pytest.approx(500.0 * 87.50)


def test_zero_stop_distance_raises(fx):
    with pytest.raises(SizingError):
        risk_in_account(INSTRUMENTS["EURUSD"], 1.0850, 1.0850, 0.0, fx, "USD")


# --- notional and margin ---------------------------------------------------

def test_margin_uses_leverage(fx):
    """1 lot EUR/USD at 1.0850 is $108,500 notional; at 30:1 that is $3,616.67."""
    assert notional_in_account(INSTRUMENTS["EURUSD"], 1.0850, 1.0, fx, "USD") == pytest.approx(108_500.0)
    assert margin_required(INSTRUMENTS["EURUSD"], 1.0850, 1.0, fx, "USD") == pytest.approx(3_616.667, rel=1e-5)


def test_crypto_is_unleveraged_so_margin_equals_notional(fx):
    n = notional_in_account(INSTRUMENTS["BTCUSDT"], 60_000.0, 0.5, fx, "USD")
    assert n == pytest.approx(30_000.0)
    assert margin_required(INSTRUMENTS["BTCUSDT"], 60_000.0, 0.5, fx, "USD") == pytest.approx(n)


# --- qty suggestion --------------------------------------------------------

def test_suggest_qty_floors_never_rounds_up(fx):
    """$250 budget, EUR/USD 50 pip stop. Per 0.01 lot the risk is $5, so 50 steps
    is 0.50 lots exactly. A $249 budget must give 0.49, never 0.50."""
    assert suggest_qty(INSTRUMENTS["EURUSD"], 1.0850, 1.0800, 250.0, fx, "USD") == pytest.approx(0.50)
    assert suggest_qty(INSTRUMENTS["EURUSD"], 1.0850, 1.0800, 249.0, fx, "USD") == pytest.approx(0.49)


def test_suggest_qty_returns_zero_when_even_minimum_is_too_big(fx):
    assert suggest_qty(INSTRUMENTS["EURUSD"], 1.0850, 1.0800, 1.0, fx, "USD") == 0.0


def test_suggested_qty_actually_fits_the_budget(fx):
    for sym in ("EURUSD", "USDJPY", "EURGBP", "XAUUSD"):
        inst = INSTRUMENTS[sym]
        entry = 150.00 if sym == "USDJPY" else (2400.0 if sym == "XAUUSD" else 1.0850)
        stop = entry * 0.995
        q = suggest_qty(inst, entry, stop, 500.0, fx, "USD")
        if q > 0:
            assert risk_in_account(inst, entry, stop, q, fx, "USD") <= 500.0 + 1e-9


# --- gate integration ------------------------------------------------------

def intent(**over):
    base = dict(symbol="USDJPY", side="buy", qty=1.0, entry=150.00, stop_loss=149.50,
                signal_ts=NOW, idempotency_key="fx-1")
    base.update(over)
    return OrderIntent(**base)


@pytest.fixture
def limits(tmp_path):
    return RiskLimits(kill_switch_path=tmp_path / "KILL_SWITCH")


def gate(o, fx, limits, equity_val=50_000.0, inst="USDJPY", **kw):
    return validate_order(
        o, EquityQuote(equity_val, NOW, "test"), limits, 0.0, equity_val, now=NOW,
        instrument=INSTRUMENTS[inst] if inst else None, fx=fx, account_ccy="USD", **kw
    )


def test_usdjpy_passes_once_risk_is_converted(fx, limits):
    """333 USD of real risk is 0.67% of a 50k account, inside the 1% cap. Under the
    naive formula this same order would have been read as 50,000 and rejected."""
    assert gate(intent(), fx, limits) is not None


def test_forex_without_fx_rates_is_rejected(limits):
    with pytest.raises(OrderRejected) as e:
        validate_order(intent(), EquityQuote(50_000.0, NOW, "t"), limits, 0.0, 50_000.0,
                       now=NOW, instrument=INSTRUMENTS["USDJPY"], fx=None)
    assert e.value.gate == "SIZING"


def test_oversized_forex_position_rejected_on_risk(fx, limits):
    # 5 lots USD/JPY = 1,666 USD risk, over 1% of 50k = 500
    with pytest.raises(OrderRejected) as e:
        gate(intent(qty=5.0), fx, limits)
    assert e.value.gate == "SIZING"


def test_gross_leverage_cap_bites_before_broker_leverage(fx, limits):
    """1 lot EUR/USD is $108.5k of exposure. On a $50k account that is 2.17x, fine.
    On a $5k account it is 21x, which the broker permits and this gate does not."""
    o = intent(symbol="EURUSD", entry=1.0850, stop_loss=1.0845, qty=1.0)
    with pytest.raises(OrderRejected) as e:
        gate(o, fx, limits, equity_val=5_000.0, inst="EURUSD")
    assert e.value.gate == "MARGIN"


def test_insufficient_free_margin_rejected(fx, limits):
    o = intent(symbol="EURUSD", entry=1.0850, stop_loss=1.0845, qty=1.0)
    with pytest.raises(OrderRejected) as e:
        gate(o, fx, limits, equity_val=200_000.0, inst="EURUSD", free_margin=100.0)
    assert e.value.gate == "MARGIN"


def test_stale_fx_rates_reject_the_order(limits):
    stale = FxRates(RATES, as_of=NOW - timedelta(seconds=120))
    with pytest.raises(OrderRejected) as e:
        gate(intent(), stale, limits)
    assert e.value.gate == "SIZING"


def test_stop_band_is_per_instrument_not_global(fx, limits):
    """A 5 pip EUR/USD stop is 0.046% of price. Legitimate in forex, and a data error
    on an equity. The same distance must be accepted for one and rejected for the other."""
    tight_fx = intent(symbol="EURUSD", entry=1.0850, stop_loss=1.0845, qty=0.10)
    gate(tight_fx, fx, limits, equity_val=200_000.0, inst="EURUSD")  # accepted

    tight_eq = intent(symbol="GOLDBEES", entry=100.0, stop_loss=99.954, qty=10.0)
    with pytest.raises(OrderRejected) as e:
        validate_order(tight_eq, EquityQuote(50_000.0, NOW, "t"), limits, 0.0, 50_000.0,
                       now=NOW)
    assert e.value.gate == "STOP_LOSS"


def test_crypto_tolerates_a_wide_swing_stop(fx, limits):
    """20% is inside crypto's band and far outside forex's."""
    o = intent(symbol="BTCUSDT", entry=60_000.0, stop_loss=48_000.0, qty=0.01)
    gate(o, fx, limits, equity_val=200_000.0, inst="BTCUSDT")


def test_equity_instrument_still_works_without_fx(limits):
    """The Indian leg is unaffected by any of this."""
    o = intent(symbol="GOLDBEES", entry=100.0, stop_loss=98.0, qty=100.0)
    assert validate_order(o, EquityQuote(50_000.0, NOW, "t"), limits, 0.0, 50_000.0,
                          now=NOW) is o
