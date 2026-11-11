"""Portfolio correlation and behavioural guards.

The behavioural tests are named after the failure mode they prevent, because that is what
makes them worth keeping when someone later wonders why the rule exists.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.discipline import (  # noqa: E402
    CORRELATION_GROUPS, DisciplineLimits, OpenPosition, SessionState,
    assert_stop_not_widened, atr, check_discipline, group_of, stop_from_atr,
)
from core.metrics import vs_benchmark  # noqa: E402
from core.risk_gate import OrderRejected  # noqa: E402

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
EQUITY = 100_000.0


@pytest.fixture
def limits():
    return DisciplineLimits()


def check(state, limits, symbol="SPY", side="buy", risk=1_000.0, **kw):
    return check_discipline(symbol, side, risk, EQUITY, state, limits, NOW, **kw)


# --- portfolio: correlation ------------------------------------------------

def test_uncorrelated_positions_are_allowed(limits):
    state = SessionState(open_positions=[OpenPosition("SPY", "buy", 1_000.0)])
    check(state, limits, symbol="XAUUSD")   # METALS, different group
    check(state, limits, symbol="BTCUSDT")  # CRYPTO, different group


def test_spy_and_qqq_share_a_risk_budget(limits):
    """The headline case. Two 'independent' 1% index longs are one 2% bet, and a third
    correlated long is refused rather than quietly making it 3%."""
    state = SessionState(open_positions=[
        OpenPosition("SPY", "buy", 1_000.0),
        OpenPosition("QQQ", "buy", 1_000.0),
    ])
    with pytest.raises(OrderRejected) as e:
        check(state, limits, symbol="NIFTY", side="buy", risk=500.0)
    assert e.value.gate == "CORRELATION"


def test_opposite_sides_of_a_group_do_not_stack(limits):
    """Long SPY and short NIFTY are not the same directional bet, so they get separate
    budgets. Grouping without direction would reject a legitimate hedge."""
    state = SessionState(open_positions=[OpenPosition("SPY", "buy", 2_000.0)])
    check(state, limits, symbol="NIFTY", side="sell", risk=2_000.0)


def test_usd_short_basket_is_one_bet(limits):
    """Long EURUSD, GBPUSD, and AUDUSD is three tickers and one short-dollar position."""
    state = SessionState(open_positions=[
        OpenPosition("EURUSD", "buy", 1_000.0),
        OpenPosition("GBPUSD", "buy", 1_000.0),
    ])
    with pytest.raises(OrderRejected) as e:
        check(state, limits, symbol="AUDUSD", side="buy", risk=500.0)
    assert e.value.gate == "CORRELATION"


def test_unmapped_symbol_gets_its_own_group():
    assert group_of("WEIRDCO").startswith("UNMAPPED:")
    assert group_of("spy") == "EQUITY_BETA", "lookup must be case-insensitive"


def test_gold_etf_and_spot_gold_are_the_same_bet():
    assert CORRELATION_GROUPS["GOLDBEES"] == CORRELATION_GROUPS["XAUUSD"] == "METALS"


# --- portfolio: totals -----------------------------------------------------

def test_total_open_risk_is_capped(limits):
    state = SessionState(open_positions=[
        OpenPosition("SPY", "buy", 2_000.0),
        OpenPosition("BTCUSDT", "buy", 2_000.0),
        OpenPosition("XAUUSD", "buy", 2_000.0),
    ])
    with pytest.raises(OrderRejected) as e:
        check(state, limits, symbol="USO", side="buy", risk=500.0)
    assert e.value.gate == "TOTAL_EXPOSURE"


def test_max_open_positions(limits):
    state = SessionState(open_positions=[
        OpenPosition(s, "buy", 1.0) for s in
        ("SPY", "BTCUSDT", "XAUUSD", "USO", "EURUSD")
    ])
    with pytest.raises(OrderRejected) as e:
        check(state, limits, symbol="USDJPY", risk=1.0)
    assert e.value.gate == "MAX_POSITIONS"


# --- behaviour -------------------------------------------------------------

def test_cooldown_blocks_the_revenge_window(limits):
    state = SessionState(last_loss_at=NOW - timedelta(minutes=5))
    with pytest.raises(OrderRejected) as e:
        check(state, limits)
    assert e.value.gate == "COOLDOWN"


def test_trading_resumes_after_the_cooldown(limits):
    state = SessionState(last_loss_at=NOW - timedelta(minutes=16))
    check(state, limits)


def test_three_consecutive_losses_stops_the_day(limits):
    state = SessionState(consecutive_losses=3, last_loss_at=NOW - timedelta(hours=2))
    with pytest.raises(OrderRejected) as e:
        check(state, limits)
    assert e.value.gate == "LOSS_STREAK"


def test_size_may_not_increase_after_a_loss(limits):
    state = SessionState(consecutive_losses=1, last_trade_risk=1_000.0,
                         last_loss_at=NOW - timedelta(minutes=30))
    with pytest.raises(OrderRejected) as e:
        check(state, limits, risk=1_500.0)
    assert e.value.gate == "REVENGE_SIZING"
    check(state, limits, risk=1_000.0)  # same size is fine
    check(state, limits, risk=500.0)    # smaller is fine


def test_size_may_increase_after_a_win(limits):
    state = SessionState(consecutive_losses=0, last_trade_risk=1_000.0)
    check(state, limits, risk=1_500.0)


def test_overtrading_cap(limits):
    with pytest.raises(OrderRejected) as e:
        check(SessionState(trades_today=10), limits)
    assert e.value.gate == "OVERTRADING"


def test_chasing_an_extended_move_is_refused(limits):
    """Reference at 100, ATR 2. Entering a long at 105 is 2.5 ATR of chase."""
    with pytest.raises(OrderRejected) as e:
        check(SessionState(), limits, entry=105.0, reference_price=100.0, atr=2.0)
    assert e.value.gate == "CHASING"


def test_entry_near_the_reference_is_fine(limits):
    check(SessionState(), limits, entry=102.0, reference_price=100.0, atr=2.0)


def test_a_short_chases_downward(limits):
    """For a short, the chase is below the reference, so the sign must flip."""
    with pytest.raises(OrderRejected) as e:
        check(SessionState(), limits, side="sell", entry=95.0, reference_price=100.0, atr=2.0)
    assert e.value.gate == "CHASING"
    # A short entered above the reference is not chasing at all
    check(SessionState(), limits, side="sell", entry=104.0, reference_price=100.0, atr=2.0)


def test_extension_guard_is_skipped_when_no_reference_given(limits):
    check(SessionState(), limits, entry=100_000.0)


# --- session state bookkeeping ---------------------------------------------

def test_streak_resets_on_a_win():
    s = SessionState()
    s.record_close(-100.0, NOW, 1_000.0)
    s.record_close(-100.0, NOW, 1_000.0)
    assert s.consecutive_losses == 2
    s.record_close(+250.0, NOW, 1_000.0)
    assert s.consecutive_losses == 0


# --- stop widening ---------------------------------------------------------

def test_long_stop_may_rise_but_not_fall():
    assert_stop_not_widened("buy", entry=100.0, old_stop=98.0, new_stop=99.0)
    with pytest.raises(OrderRejected) as e:
        assert_stop_not_widened("buy", entry=100.0, old_stop=98.0, new_stop=97.0)
    assert e.value.gate == "STOP_WIDENED"


def test_short_stop_may_fall_but_not_rise():
    assert_stop_not_widened("sell", entry=100.0, old_stop=102.0, new_stop=101.0)
    with pytest.raises(OrderRejected):
        assert_stop_not_widened("sell", entry=100.0, old_stop=102.0, new_stop=103.0)


# --- ATR -------------------------------------------------------------------

def test_atr_captures_gaps_that_high_minus_low_misses():
    """Flat 10-wide bars, then a bar that gaps 50 above the prior close. ATR must rise."""
    highs = [110.0] * 20
    lows = [100.0] * 20
    closes = [105.0] * 20
    calm = atr(highs, lows, closes, period=14)
    highs2 = highs + [165.0]
    lows2 = lows + [155.0]
    closes2 = closes + [160.0]
    gapped = atr(highs2, lows2, closes2, period=14)
    assert calm == pytest.approx(10.0)
    assert gapped > calm


def test_atr_needs_enough_bars():
    with pytest.raises(OrderRejected):
        atr([1.0] * 5, [0.5] * 5, [0.8] * 5, period=14)


def test_atr_stop_scales_with_volatility():
    """Same 1% risk budget, different volatility: the stop widens and, downstream, the
    position gets smaller. That is the whole point of ATR sizing."""
    calm_stop = stop_from_atr(100.0, atr=1.0, multiple=2.0, side="buy")
    wild_stop = stop_from_atr(100.0, atr=5.0, multiple=2.0, side="buy")
    assert calm_stop == pytest.approx(98.0)
    assert wild_stop == pytest.approx(90.0)
    assert (100.0 - wild_stop) > (100.0 - calm_stop)


def test_atr_stop_sides():
    assert stop_from_atr(100.0, 2.0, 1.5, "sell") == pytest.approx(103.0)
    with pytest.raises(OrderRejected):
        stop_from_atr(100.0, 2.0, 1.5, "sideways")


def test_atr_stop_cannot_go_negative():
    with pytest.raises(OrderRejected):
        stop_from_atr(10.0, atr=20.0, multiple=2.0, side="buy")


# --- benchmark comparison --------------------------------------------------

def test_beating_return_while_doubling_drawdown_is_not_a_win():
    r = vs_benchmark(strategy_return_pct=30.0, benchmark_return_pct=25.0,
                     strategy_max_dd_pct=60.0, benchmark_max_dd_pct=30.0)
    assert r["verdict"] == "BEATS_ON_RETURN_ONLY"


def test_the_reference_3_case():
    """The SMA-cross-on-Bitcoin claim: higher return AND lower drawdown than holding.
    That one genuinely qualifies, which is why the honest verdict has to allow it."""
    r = vs_benchmark(strategy_return_pct=47_000.0, benchmark_return_pct=26_000.0,
                     strategy_max_dd_pct=50.0, benchmark_max_dd_pct=83.0)
    assert r["verdict"] == "BEATS_BENCHMARK"


def test_underperforming_buy_and_hold_is_named_plainly():
    r = vs_benchmark(10.0, 25.0, 40.0, 30.0)
    assert r["verdict"] == "UNDERPERFORMS"
