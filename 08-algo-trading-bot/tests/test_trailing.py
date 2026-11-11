"""Trailing-stop mechanics: the ratchet, and the ordering that prevents look-ahead."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtest import Bar, Signal, run_backtest  # noqa: E402
from core.costs import ZeroCost  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(*ohlc):
    return [Bar(T0 + timedelta(days=i), o, h, l, c, 1000)
            for i, (o, h, l, c) in enumerate(ohlc)]


class Trailer:
    """Enters long on bar 0, then proposes whatever stop the test dictates."""
    def __init__(self, stop, proposals):
        self.stop = stop
        self.proposals = proposals
        self.seen_stops = []

    def on_bar(self, i, bs):
        return Signal("buy", self.stop, "trail") if i == 0 else None

    def should_exit(self, i, bs, position):
        self.seen_stops.append((i, position.stop))
        return False

    def update_stop(self, i, bs, position):
        return self.proposals.get(i)


def test_trailing_stop_tightens_and_triggers():
    """Stop starts at 90, is raised to 104 at the end of bar 2, and bar 3 dips to 103."""
    b = bars((100, 101, 99, 100),    # 0 signal
             (100, 101, 99, 100),    # 1 entry at 100
             (105, 110, 104, 108),   # 2 stop raised to 104 at close
             (106, 107, 103, 104))   # 3 low 103 takes out the 104 stop
    s = Trailer(stop=90.0, proposals={2: 104.0})
    r = run_backtest(b, s, ZeroCost(), 100_000.0)
    assert r.trades[0].exit_reason == "STOP"
    assert r.trades[0].exit == pytest.approx(104.0)
    assert r.trades[0].net_pnl > 0, "a trailed stop above entry must book a profit"


def test_the_engine_refuses_to_widen_a_stop():
    """A strategy proposing a looser stop is ignored. The ratchet is enforced by the
    engine, not trusted to the strategy, so a bug cannot move a stop away from a loss."""
    b = bars((100, 101, 99, 100),
             (100, 101, 99, 100),
             (100, 101, 99, 100),
             (100, 101, 94, 96))     # dips to 94, below the original 95 stop
    s = Trailer(stop=95.0, proposals={2: 80.0})   # tries to loosen to 80
    r = run_backtest(b, s, ZeroCost(), 100_000.0)
    assert r.trades[0].exit_reason == "STOP"
    assert r.trades[0].exit == pytest.approx(95.0), "the 95 stop must have survived"


def test_stop_set_on_bar_i_governs_bar_i_plus_one_not_bar_i():
    """The ordering guarantee. The stop proposed at the end of bar 2 must NOT be tested
    against bar 2's own low, or the backtest is reading the future inside the bar."""
    b = bars((100, 101, 99, 100),
             (100, 101, 99, 100),
             (105, 110, 103, 108),   # 2: low is 103; a 104 stop set here must not fire
             (108, 109, 107, 108))   # 3: never reaches 104, so no exit at all
    s = Trailer(stop=90.0, proposals={2: 104.0})
    r = run_backtest(b, s, ZeroCost(), 100_000.0)
    assert r.trades[0].exit_reason == "OPEN_AT_END", (
        "a stop set at the close of bar 2 must not be applied to bar 2's own range"
    )


def test_a_strategy_without_update_stop_still_works():
    """update_stop is optional; strategies predating it must be unaffected."""
    class Plain:
        def on_bar(self, i, bs):
            return Signal("buy", 95.0, "plain") if i == 0 else None

        def should_exit(self, i, bs, position):
            return False

    b = bars(*[(100, 101, 99, 100)] * 4)
    r = run_backtest(b, Plain(), ZeroCost(), 100_000.0)
    assert len(r.trades) == 1


def test_chandelier_ratchets_with_the_running_high():
    from strategies.chandelier_trend import ChandelierTrend

    s = ChandelierTrend(fast=2, slow=4, atr_period=3, trail_mult=1.0)
    rising = bars(*[(100 + i, 102 + i, 99 + i, 101 + i) for i in range(30)])

    class P:
        entry_i = 10
    stops = [s.update_stop(i, rising[: i + 1], P()) for i in range(12, 30)]
    assert all(b is not None for b in stops)
    assert stops == sorted(stops), "in a rising market the chandelier must never fall"
