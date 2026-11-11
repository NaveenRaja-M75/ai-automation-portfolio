"""The backtest's honesty rules, each proven by a test that would fail if broken."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevbot.backtest import COSTS, deflated_sharpe, entry_rules, ffd_zscore, simulate, yang_zhang


def bars(o, h, l, c):
    return [np.array(x, dtype=float) for x in (o, h, l, c)]


def test_entry_is_next_bar_open_and_target_fill():
    o, h, l, c = bars([100, 100, 101, 103], [100, 101, 104, 104], [100, 99.5, 100.5, 102], [100, 100.5, 103, 103])
    sigma = np.full(4, 0.01)
    ent = np.array([True, False, False, False])
    t, j, e, x = simulate(o, h, l, c, sigma, ent, H=3, stop_k=1.0, tgt_k=2.0)
    band = 0.01 * np.sqrt(3)
    assert e[0] == 100                       # open of bar 1, not close of bar 0
    assert x[0] == pytest.approx(100 * (1 + 2 * band)) and j[0] == 2


def test_same_candle_stop_and_target_counts_as_stop():
    o, h, l, c = bars([100, 100, 100], [100, 110, 100], [100, 90, 100], [100, 100, 100])
    t, j, e, x = simulate(o, h, l, c, np.full(3, 0.01), np.array([True, False, False]), H=2, stop_k=1.0, tgt_k=1.0)
    assert x[0] < e[0]                       # the pessimistic resolution


def test_gap_through_stop_fills_at_the_worse_open():
    o, h, l, c = bars([100, 100, 90], [100, 100.5, 91], [100, 99.9, 89], [100, 100, 90])
    t, j, e, x = simulate(o, h, l, c, np.full(3, 0.01), np.array([True, False, False]), H=2, stop_k=1.0, tgt_k=3.0)
    assert x[0] == 90                        # not the stop price, which the market skipped


def test_time_exit_at_close_of_last_bar_and_no_overlap():
    o, h, l, c = bars([100] * 6, [100.1] * 6, [99.9] * 6, [100, 100, 100, 100.05, 100, 100])
    ent = np.array([True, True, True, False, False, False])
    t, j, e, x = simulate(o, h, l, c, np.full(6, 0.01), ent, H=2, stop_k=1.0, tgt_k=1.0)
    assert list(t) == [0, 2] and j[0] == 2 and x[0] == 100   # bar 1's signal overlapped the open trade


def test_flat_round_trip_costs_include_tds():
    one = np.array([100.0])
    assert COSTS["india_tds_cash"].net(one, one)[0] == pytest.approx((0.9998 * 0.989) / (1.0002 * 1.001) - 1)
    assert COSTS["india_tds_cash"].net(one, one)[0] < -0.012
    assert -0.0025 < COSTS["fees_only"].net(one, one)[0] < -0.0023


def test_features_are_causal():
    rng = np.random.default_rng(1)
    n = 400
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "close": c})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.002
    df["low"] = df[["open", "close"]].min(axis=1) * 0.998
    base_sig = yang_zhang(df, 20)
    base_rules = entry_rules(df, base_sig)
    base_ffd = ffd_zscore(df["close"])
    future = df.copy()
    future.loc[300:, ["open", "high", "low", "close"]] *= 1.5     # rewrite the future
    sig2 = yang_zhang(future, 20)
    rules2 = entry_rules(future, sig2)
    assert np.allclose(base_sig[:300].dropna(), sig2[:300].dropna())
    assert np.allclose(base_ffd[:300].dropna(), ffd_zscore(future["close"])[:300].dropna())
    for k in base_rules:
        assert (base_rules[k][:300] == rules2[k][:300]).all(), k


def test_deflated_sharpe_rejects_luck_and_accepts_strength():
    rng = np.random.default_rng(3)
    trials = rng.normal(0, 0.1, 100)                  # Sharpes of 100 noise strategies
    noise = rng.normal(0.0, 1.0, 200)
    strong = rng.normal(0.5, 1.0, 200)
    assert deflated_sharpe(noise, trials) < 0.5
    assert deflated_sharpe(strong, trials) > 0.95


def test_india_tax_profile_taxes_wins_but_never_offsets_losses():
    from jevbot.backtest import COSTS
    e = np.array([100.0, 100.0])
    x = np.array([110.0, 90.0])
    fees, tax = COSTS["fees_only"].net(e, x), COSTS["india_tax"].net(e, x)
    assert tax[0] == pytest.approx(fees[0] * 0.7)     # 30% of the win goes to tax
    assert tax[1] == pytest.approx(fees[1])           # the loss is not reduced by any tax benefit


# ---- review round 3: limit target fills, trailing stops, exact random control ------

from jevbot.backtest import spaced_random_entries  # noqa: E402


def test_take_profit_fills_at_its_limit_even_when_price_opens_above_it():
    o, h, l, c = bars([100, 100, 110], [100, 100.5, 111], [100, 99.9, 109], [100, 100, 110])
    t, j, e, x = simulate(o, h, l, c, np.full(3, 0.01), np.array([True, False, False]), H=2, stop_k=1.0, tgt_k=1.0)
    assert x[0] == pytest.approx(100 * (1 + 0.01 * np.sqrt(2)))   # the limit price, not the 110 open


def test_trailing_stop_lets_a_trend_run_then_ratchets_up():
    # price climbs 100 -> 140 then drops; sigma 2%, trail 3 sigma = 6% below the best close
    closes = [100, 100, 110, 120, 130, 140, 125]
    o = [100, 100, 100, 110, 120, 130, 140]
    h = [x * 1.001 for x in closes[:6]] + [140]
    l = [100, 99.5, 100, 110, 120, 130, 120]
    o, h, l, c = bars(o, h, l, closes)
    sig = np.full(7, 0.02)
    ent = np.zeros(7, dtype=bool); ent[0] = True
    t, j, e, x = simulate(o, h, l, c, sig, ent, H=50, stop_k=0, tgt_k=np.inf, trail_k=3.0)
    assert j[0] == 6 and x[0] == pytest.approx(140 * (1 - 0.06))      # trailed from the 140 close
    assert x[0] / e[0] - 1 > 0.30                                       # the winner was not capped


def test_trailing_stop_never_moves_down():
    o, h, l, c = bars([100] * 5, [100.5, 100.5, 100.5, 100.5, 100.5], [99.9, 99.9, 99.9, 99.9, 99.9],
                      [100, 100, 99, 98, 97])
    sig = np.full(5, 0.02)
    ent = np.zeros(5, dtype=bool); ent[0] = True
    t, j, e, x = simulate(o, h, l, c, sig, ent, H=3, stop_k=0, tgt_k=np.inf, trail_k=3.0)
    assert x[0] == 98 and j[0] == 3                                     # time exit at bar 3; the stop stayed at 94


def test_random_control_places_exactly_k_non_overlapping_entries():
    rng = np.random.default_rng(0)
    for _ in range(200):
        ent = spaced_random_entries(rng, 100, 400, 20, 13, 500)
        idx = np.flatnonzero(ent)
        assert len(idx) == 20 and idx.min() >= 100 and idx.max() < 400
        assert np.all(np.diff(idx) >= 13)


def test_random_control_handles_a_fully_packed_window():
    rng = np.random.default_rng(1)
    ent = spaced_random_entries(rng, 0, 41, 5, 10, 50)      # exactly room for 5 at spacing 10
    assert list(np.flatnonzero(ent)) == [0, 10, 20, 30, 40]
    assert spaced_random_entries(rng, 0, 41, 9, 10, 50).sum() == 5   # asks for 9, only 5 fit
