"""Tests that pin down the scorecard's refusal to flatter.

The important cases here are not the arithmetic. They are the three ways a strategy can
look good and not be good: a high win rate with negative expectancy, a small sample, and
a winner selected out of many attempts.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.metrics import (  # noqa: E402
    EDGE_PLAUSIBLE, INSUFFICIENT_DATA, NEGATIVE_EDGE, NO_EDGE_DEMONSTRATED,
    longest_losing_streak, max_drawdown_r, required_sample_size, scorecard, sharpe,
)


# --- the lesson the whole module exists to teach ---------------------------

def test_ninety_percent_win_rate_can_be_a_losing_strategy():
    """9 wins of +0.1R, 1 loss of -1.5R, repeated. Win rate 90%, expectancy negative.
    This is the single most common way a retail system looks great and loses money."""
    rs = ([0.1] * 9 + [-1.5]) * 10
    s = scorecard(rs)
    assert s.win_rate == pytest.approx(0.90)
    assert s.avg_r < 0
    assert s.verdict in (NEGATIVE_EDGE, NO_EDGE_DEMONSTRATED)
    assert "VERDICT" in s.summary().splitlines()[0]


def test_forty_percent_win_rate_can_be_a_good_strategy():
    """The mirror case: mostly losers, but the winners are large."""
    rs = ([2.5] * 4 + [-1.0] * 6) * 15
    s = scorecard(rs)
    assert s.win_rate == pytest.approx(0.40)
    assert s.avg_r == pytest.approx(0.40)
    assert s.verdict == EDGE_PLAUSIBLE


# --- sample size ------------------------------------------------------------

def test_small_sample_never_claims_an_edge():
    """Ten perfect winners is not evidence. It must not read as EDGE_PLAUSIBLE."""
    s = scorecard([1.0] * 10)
    assert s.n == 10
    assert s.verdict == INSUFFICIENT_DATA
    assert s.significant is False


def test_twenty_nine_trades_is_still_insufficient():
    assert scorecard([1.0] * 29).verdict == INSUFFICIENT_DATA


def test_thirty_trades_can_qualify():
    assert scorecard([1.0] * 30).verdict == EDGE_PLAUSIBLE


def test_required_sample_size_for_a_thin_edge_is_large():
    """0.05R expectancy against 1.0R noise needs thousands of trades, not hundreds."""
    n = required_sample_size(avg_r=0.05, r_std=1.0)
    assert 3_000 < n < 3_300


def test_required_sample_size_is_none_without_a_positive_edge():
    assert required_sample_size(avg_r=-0.1, r_std=1.0) is None


# --- multiple testing -------------------------------------------------------

def test_a_55_percent_win_rate_over_60_trades_proves_nothing():
    """Discovered while writing these tests, and worth keeping. 55% wins at 1:1 over 60
    trades looks like a working system on a dashboard. Its t-stat is about 0.8, so it is
    statistically indistinguishable from a coin. It needs roughly 790 trades."""
    s = scorecard(([1.0] * 11 + [-1.0] * 9) * 3)
    assert s.win_rate == pytest.approx(0.55)
    assert s.avg_r == pytest.approx(0.10)
    assert s.t_stat < 1.0
    assert s.verdict == NO_EDGE_DEMONSTRATED
    assert 700 < s.trades_needed < 900


def test_multiple_testing_raises_the_bar():
    """60 trades at 2:1 wins to losses: t is about 2.7, significant on its own. Admit you
    picked it out of 200 variants and the same data stops being evidence."""
    rs = ([1.0, 1.0, -1.0]) * 20
    alone = scorecard(rs, strategies_tested=1)
    searched = scorecard(rs, strategies_tested=200)
    assert searched.t_required > alone.t_required
    assert searched.t_stat == pytest.approx(alone.t_stat), "the data did not change"
    assert alone.significant and not searched.significant, (
        "the same result must stop counting as evidence once you admit you searched"
    )


def test_declared_search_count_is_floored_at_one():
    assert scorecard([1.0] * 30, strategies_tested=0).strategies_tested == 1


# --- confidence interval ----------------------------------------------------

def test_ci_spanning_zero_is_flagged_in_the_summary():
    rs = ([1.0] * 15 + [-1.0] * 15) * 2  # expectancy exactly zero
    s = scorecard(rs)
    assert s.ci_low < 0 < s.ci_high
    assert "spans zero" in s.summary()
    assert s.verdict == NO_EDGE_DEMONSTRATED


def test_ci_brackets_the_mean():
    rs = ([2.0] * 10 + [-1.0] * 20) * 2
    s = scorecard(rs)
    assert s.ci_low < s.avg_r < s.ci_high


# --- drawdown and streaks ---------------------------------------------------

def test_max_drawdown_is_peak_to_trough_not_worst_trade():
    # rises to +3, falls to -1, so the drawdown is 4R even though no trade lost 4R
    assert max_drawdown_r([1, 1, 1, -1, -1, -1, -1]) == pytest.approx(4.0)


def test_drawdown_of_a_monotonic_winner_is_zero():
    assert max_drawdown_r([1.0] * 10) == 0.0


def test_losing_streak_counts_consecutive_only():
    assert longest_losing_streak([-1, -1, 1, -1, -1, -1, 1]) == 3
    assert longest_losing_streak([1, 1, 1]) == 0


# --- arithmetic -------------------------------------------------------------

def test_profit_factor_and_payoff():
    rs = [2.0, 2.0, -1.0, -1.0]
    s = scorecard(rs)
    assert s.profit_factor == pytest.approx(2.0)
    assert s.payoff_ratio == pytest.approx(2.0)
    assert s.total_r == pytest.approx(2.0)


def test_no_losses_gives_infinite_profit_factor():
    assert scorecard([1.0, 2.0]).profit_factor == math.inf


def test_empty_and_nonfinite_input_raise():
    with pytest.raises(ValueError):
        scorecard([])
    with pytest.raises(ValueError):
        scorecard([1.0, float("nan")])


def test_sharpe_annualises():
    daily = [0.001] * 200 + [-0.001] * 200
    assert sharpe(daily, periods_per_year=252) == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(ValueError):
        sharpe([0.01], periods_per_year=252)


def test_summary_leads_with_verdict_not_win_rate():
    lines = scorecard([1.0] * 30).summary().splitlines()
    assert lines[0].startswith("VERDICT:")
    assert "informational" in [l for l in lines if "win rate" in l][0]
