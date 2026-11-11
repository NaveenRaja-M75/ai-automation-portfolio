"""Proofs that the engine is pessimistic where OHLC data is ambiguous.

These tests are the reason to trust any number this engine produces. Each one pins down a
specific way that backtests lie.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtest import Bar, Position, Signal, buy_and_hold, run_backtest  # noqa: E402
from core.costs import (  # noqa: E402
    IndianCrypto, IndianEquityDelivery, IndianEquityIntraday, ZeroCost, breakeven_move_pct,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(*ohlc) -> list[Bar]:
    return [Bar(ts=T0 + timedelta(days=i), open=o, high=h, low=l, close=c, volume=1000)
            for i, (o, h, l, c) in enumerate(ohlc)]


class EnterOnce:
    """Signals a long on bar `at`, with a fixed stop and target. Never exits on signal."""
    def __init__(self, at: int, stop: float, target=None):
        self.at, self.stop, self.target = at, stop, target

    def on_bar(self, i, bs):
        return Signal("buy", self.stop, "test", self.target) if i == self.at else None

    def should_exit(self, i, bs, position):
        return False


# --- the four ways backtests lie -------------------------------------------

def test_stop_wins_ties_when_both_levels_are_inside_one_bar():
    """Bar 2's range spans BOTH the stop (95) and the target (110). OHLC cannot say
    which came first, so the engine must book the loss. Assuming the target here is how
    a losing system gets published as a winner."""
    b = bars((100, 101, 99, 100),     # 0
             (100, 101, 99, 100),     # 1: entry fills at bar 2 open
             (100, 112, 94, 100))     # 2: hits both
    r = run_backtest(b, EnterOnce(at=0, stop=95.0, target=110.0), ZeroCost(), 100_000.0)
    assert len(r.trades) == 1
    assert r.trades[0].exit_reason == "STOP"
    assert r.trades[0].net_pnl < 0


def test_gap_through_the_stop_fills_at_the_open_not_the_stop():
    """A stop at 95 does not fill at 95 when the market opens at 80. Modelling stops as
    guarantees removes exactly the losses that end accounts."""
    b = bars((100, 101, 99, 100),
             (100, 101, 99, 100),
             (80, 85, 78, 82))        # gaps far below the 95 stop
    r = run_backtest(b, EnterOnce(at=0, stop=95.0), ZeroCost(), 100_000.0)
    t = r.trades[0]
    assert t.exit_reason == "STOP_GAP"
    assert t.exit == pytest.approx(80.0)
    assert t.r_multiple < -1.0, "a gapped stop must lose MORE than the planned 1R"


def test_gaps_are_modelled_in_both_directions():
    b = bars((100, 101, 99, 100),
             (100, 101, 99, 100),
             (130, 132, 128, 130))
    r = run_backtest(b, EnterOnce(at=0, stop=95.0, target=110.0), ZeroCost(), 100_000.0)
    assert r.trades[0].exit_reason == "TARGET_GAP"
    assert r.trades[0].exit == pytest.approx(130.0)


def test_no_lookahead_entry_fills_on_the_next_bar_open():
    """A signal on bar 0 must fill at bar 1's OPEN, never bar 0's close."""
    b = bars((100, 101, 99, 100),
             (105, 130, 104, 128),    # entry here, at 105
             (128, 129, 127, 128))
    r = run_backtest(b, EnterOnce(at=0, stop=95.0, target=120.0), ZeroCost(), 100_000.0)
    assert r.trades[0].entry == pytest.approx(105.0)


def test_a_strategy_that_peeks_at_the_future_cannot_be_expressed():
    """on_bar receives only bars[0..i]. A strategy asking for more gets an IndexError,
    which is a design guarantee, not an accident."""
    seen = []

    class Peeker:
        def on_bar(self, i, bs):
            seen.append(len(bs))
            return None

        def should_exit(self, i, bs, position):
            return False

    b = bars(*[(100, 101, 99, 100)] * 6)
    run_backtest(b, Peeker(), ZeroCost(), 100_000.0)
    assert seen == [1, 2, 3, 4, 5], "each call must see exactly i+1 bars"


# --- sizing and cash -------------------------------------------------------

def test_position_is_sized_to_the_risk_budget():
    """1% of 100k = 1,000 of risk. Entry 100, stop 95 -> 5 per unit -> 200 units."""
    b = bars((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100))
    r = run_backtest(b, EnterOnce(at=0, stop=95.0), ZeroCost(), 100_000.0, risk_pct=0.01)
    assert r.trades[0].qty == 200
    assert r.trades[0].planned_risk == pytest.approx(1_000.0)


def test_no_leverage_position_capped_by_cash():
    """A very tight stop implies a huge position; a cash account cannot take it."""
    b = bars((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100))
    r = run_backtest(b, EnterOnce(at=0, stop=99.9), ZeroCost(), 10_000.0)
    assert r.trades[0].qty * r.trades[0].entry <= 10_000.0 + 1e-6


def test_risk_pct_above_two_percent_is_refused():
    with pytest.raises(ValueError):
        run_backtest(bars((1, 1, 1, 1)), EnterOnce(0, 0.5), ZeroCost(), 1000.0, risk_pct=0.05)


def test_trade_skipped_when_the_gap_invalidates_the_setup():
    """Signal on bar 0 with a stop at 95, but bar 1 opens at 90: the setup is already
    dead and must be skipped, not entered with an inverted stop."""
    b = bars((100, 101, 99, 100), (90, 91, 89, 90), (90, 91, 89, 90))
    r = run_backtest(b, EnterOnce(at=0, stop=95.0), ZeroCost(), 100_000.0)
    assert r.trades == []
    assert r.skipped.get("gapped_past_stop") == 1


# --- costs -----------------------------------------------------------------

def test_costs_are_charged_on_both_legs():
    b = bars((100, 101, 99, 100), (100, 101, 99, 100), (100, 112, 99, 110))
    free = run_backtest(b, EnterOnce(0, 95.0, 110.0), ZeroCost(), 100_000.0)
    real = run_backtest(b, EnterOnce(0, 95.0, 110.0), IndianEquityDelivery(), 100_000.0)
    assert real.trades[0].costs > 0
    assert real.trades[0].net_pnl < free.trades[0].net_pnl


def test_crypto_tds_dwarfs_equity_costs():
    """The finding that shapes the whole crypto plan, as an executable assertion."""
    eq = breakeven_move_pct(IndianEquityDelivery(), 100.0, 1000.0)
    cr = breakeven_move_pct(IndianCrypto(), 100.0, 1000.0)
    assert cr > 0.012, "crypto round trip must need more than a 1.2% gross move"
    assert cr > 4 * eq


def test_intraday_and_delivery_differ_as_expected():
    """Intraday pays brokerage but far less STT; on a large order delivery costs more."""
    d = breakeven_move_pct(IndianEquityDelivery(), 500.0, 2000.0)
    i = breakeven_move_pct(IndianEquityIntraday(), 500.0, 2000.0)
    assert d > i


def test_slippage_always_moves_against_you():
    c = ZeroCost(slippage_pct=0.01)
    assert c.slip(100.0, "buy", entering=True) == pytest.approx(101.0)
    assert c.slip(100.0, "buy", entering=False) == pytest.approx(99.0)
    assert c.slip(100.0, "sell", entering=True) == pytest.approx(99.0)
    assert c.slip(100.0, "sell", entering=False) == pytest.approx(101.0)


# --- benchmark and result plumbing -----------------------------------------

def test_buy_and_hold_benchmark():
    b = bars((100, 100, 100, 100), (100, 100, 100, 100), (200, 200, 200, 200))
    ret, dd = buy_and_hold(b, 100_000.0)
    assert ret == pytest.approx(100.0)
    assert dd == pytest.approx(0.0)


def test_buy_and_hold_drawdown_is_measured():
    b = bars((100, 100, 100, 100), (50, 50, 50, 50), (120, 120, 120, 120))
    _, dd = buy_and_hold(b, 100_000.0)
    assert dd == pytest.approx(50.0)


def test_open_position_at_the_end_is_flagged():
    b = bars((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100))
    r = run_backtest(b, EnterOnce(at=0, stop=95.0), ZeroCost(), 100_000.0)
    assert r.trades[0].exit_reason == "OPEN_AT_END"


def test_long_only_blocks_shorts():
    class Shorter:
        def on_bar(self, i, bs):
            return Signal("sell", 105.0, "test") if i == 0 else None

        def should_exit(self, i, bs, position):
            return False

    b = bars(*[(100, 101, 99, 100)] * 4)
    r = run_backtest(b, Shorter(), ZeroCost(), 100_000.0, long_only=True)
    assert r.trades == [] and r.skipped.get("short_blocked") == 1


# --- data integrity --------------------------------------------------------

def test_bad_tick_is_detected_and_dropped():
    """The real GOLDBEES case: a 100x price error that reverts the next session.
    A day-over-day change filter flags both the drop and the recovery and cannot tell
    which is wrong; comparing against the local median identifies only the bad bar."""
    from core.data import drop_anomalies, find_anomalies

    good = [Bar(T0 + timedelta(days=i), 33.0, 34.0, 32.0, 33.5, 100) for i in range(20)]
    good[10] = Bar(T0 + timedelta(days=10), 0.34, 0.34, 0.34, 0.34, 100)
    found = find_anomalies(good)
    assert [a["index"] for a in found] == [10]
    assert len(drop_anomalies(good, found)) == 19


def test_clean_series_flags_nothing():
    from core.data import find_anomalies
    steady = [Bar(T0 + timedelta(days=i), 100 + i, 101 + i, 99 + i, 100 + i, 10)
              for i in range(40)]
    assert find_anomalies(steady) == []


def test_a_genuine_trend_is_not_mistaken_for_bad_data():
    """A 3x rise over 40 bars is a bull market, not a glitch, and must survive."""
    from core.data import find_anomalies
    trend = [Bar(T0 + timedelta(days=i), 100 * 1.028 ** i, 101 * 1.028 ** i,
                 99 * 1.028 ** i, 100 * 1.028 ** i, 10) for i in range(40)]
    assert find_anomalies(trend) == []
