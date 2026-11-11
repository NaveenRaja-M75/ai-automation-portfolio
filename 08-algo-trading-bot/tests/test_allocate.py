"""Allocation engine: Indian tax accounting and policy causality, each proven by a test."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevbot.allocate import FEE, GAIN_TAX, SLIP, Book, metrics, p_band, p_dip, p_dip_cycles, p_hold, p_sma, rebalance, run


def test_sale_pays_30pct_on_realized_gain_of_coins_sold_only():
    b = Book(cash=1000.0, coins=np.zeros(1), basis=np.zeros(1))
    b.buy(0, 1000.0, 100.0)
    qty = b.coins[0]
    b.sell(0, qty / 2, 200.0, tax=True, tds=False)
    proceeds = qty / 2 * 200 * (1 - SLIP) * (1 - FEE)
    gain = proceeds - 500.0                                 # half the cost basis was sold
    assert b.tax_paid == pytest.approx(GAIN_TAX * gain)
    assert b.basis[0] == pytest.approx(500.0)               # remaining basis untouched


def test_loss_gives_no_tax_back():
    b = Book(cash=1000.0, coins=np.zeros(1), basis=np.zeros(1))
    b.buy(0, 1000.0, 100.0)
    b.sell(0, b.coins[0], 50.0, tax=True, tds=False)
    assert b.tax_paid == 0.0 and b.cash < 500.0


def test_buy_and_hold_pays_tax_once_at_the_end():
    prices = np.array([[100.0], [150.0], [300.0]])
    res = run(prices, p_hold(1.0), tax=True)
    assert res["trades"] == 2                                # one buy, one liquidation
    qty = 1.0 / (100 * (1 + SLIP) * (1 + FEE))
    proceeds = qty * 300 * (1 - SLIP) * (1 - FEE)
    assert res["final"] == pytest.approx(proceeds - GAIN_TAX * (proceeds - 1.0))


def test_passive_split_never_trades_after_day_one():
    rng = np.random.default_rng(0)
    prices = (100 * np.exp(np.cumsum(rng.normal(0, 0.05, 500)))).reshape(-1, 1)
    res = run(prices, p_hold(0.4), tax=True)
    assert res["trades"] == 2


def test_band_rebalances_only_outside_the_band():
    prices = np.array([[100.0], [101.0], [102.0], [400.0], [401.0]])
    res = run(prices, p_band(0.5, 0.25), tax=True)
    assert res["trades"] == 3          # initial buy, one trim when weight passes 75%, final sale


def test_sma_signal_uses_only_past_closes():
    prices = np.concatenate([np.full(250, 100.0), np.full(10, 50.0)]).reshape(-1, 1)
    pol = p_sma(ma=200, exit_buf=-0.06, enter_buf=0.03, off_w=0.0)
    res = run(prices, pol, tax=True)
    w = []
    # Re-run with a bookkeeping hook: the exit must happen on day 251 (first close at 50 is day 250)
    st = {"w": 1.0}
    pol2 = p_sma(ma=200, exit_buf=-0.06, enter_buf=0.03, off_w=0.0)
    for t in range(len(prices)):
        out = pol2(prices[:, 0], t, st)
        if out == 0.0:
            w.append(t)
    assert w[0] == 251 and res["trades"] >= 2


def test_dip_buy_never_sells_before_the_end():
    prices = np.concatenate([np.linspace(100, 200, 50), np.linspace(200, 60, 50), np.linspace(60, 150, 50)]).reshape(-1, 1)
    res = run(prices, p_dip(0.5), tax=True)
    assert res["tax_paid"] > 0                               # only the final liquidation can tax
    b_trades = res["trades"] - 1                             # minus the liquidation
    assert b_trades == 1 + 3                                 # initial buy + three tranches (35/50/65% drawdown)


def test_metrics_are_on_the_daily_grid():
    prices = np.linspace(100, 200, 366).reshape(-1, 1)
    m = metrics(run(prices, p_hold(1.0), tax=False))
    assert m["max_dd_pct"] < 1.0 and m["cagr_pct"] == pytest.approx((m["final_x"] - 1) * 100, rel=0.02)


# ---- round-4 fixes ------------------------------------------------------------------

def book_with(prices_paid, qty_usdt=1.0):
    b = Book(cash=float(len(prices_paid)) * qty_usdt, coins=np.zeros(len(prices_paid)), basis=np.zeros(len(prices_paid)))
    for i, p in enumerate(prices_paid):
        b.buy(i, qty_usdt, p, when="day0")
    return b


def test_step_down_lands_on_target_weight_after_tax():
    b = Book(cash=1.0, coins=np.zeros(1), basis=np.zeros(1))
    b.buy(0, 1.0, 100.0)
    px = np.array([300.0])                                   # +200%: a large taxable gain
    rebalance(b, px, 0.5, tax=True, tds=False)
    w = float(b.coins @ px) / b.value(px)
    assert w == pytest.approx(0.5, abs=1e-6) and b.tax_paid > 0


def test_step_up_never_sells_and_fills_the_laggard_first():
    b = book_with([100.0, 100.0])
    rebalance(b, np.array([100.0, 100.0]), 0.5, tax=True, tds=False)     # down to half
    tax_before, coins_before = b.tax_paid, b.coins.copy()
    px = np.array([180.0, 100.0])                            # BTC rallied, ETH flat
    rebalance(b, px, 1.0, tax=True, tds=False)               # back to full exposure
    added = (b.coins - coins_before) * px
    assert b.tax_paid == tax_before                          # no sale, no tax
    assert (b.coins >= coins_before - 1e-12).all()           # nothing was sold
    assert added[1] > added[0] > 0                           # the laggard (ETH) got most of the cash
    assert b.cash == pytest.approx(0.0, abs=1e-9)


def test_step_up_with_one_coin_above_its_share_buys_only_the_other():
    b = book_with([100.0, 100.0])
    rebalance(b, np.array([100.0, 100.0]), 0.5, tax=True, tds=False)
    coins_before = b.coins.copy()
    px = np.array([500.0, 100.0])                            # BTC now far above an equal share
    rebalance(b, px, 1.0, tax=True, tds=False)
    assert b.coins[0] == pytest.approx(coins_before[0])      # the winner is neither sold nor bought
    assert b.coins[1] > coins_before[1]


def test_step_down_keeps_relative_coin_weights():
    b = book_with([100.0, 100.0])
    px = np.array([200.0, 100.0])
    ratio = b.coins[0] * px[0] / (b.coins[1] * px[1])
    rebalance(b, px, 0.5, tax=True, tds=False)
    assert b.coins[0] * px[0] / (b.coins[1] * px[1]) == pytest.approx(ratio)


def test_fifo_ledger_has_acquisition_dates_and_taxes_only_winning_lots():
    b = Book(cash=2.0, coins=np.zeros(1), basis=np.zeros(1), ledger=[])
    b.buy(0, 1.0, 100.0, when="2026-01-01")
    b.buy(0, 1.0, 300.0, when="2026-02-01")
    b.sell(0, b.coins[0], 200.0, tax=True, tds=False, when="2026-03-01", asset="BTC")
    first, second = b.ledger
    assert first["acquired"] == "2026-01-01" and first["gain"] > 0 and first["tax_31_2pct"] > 0
    assert second["acquired"] == "2026-02-01" and second["gain"] < 0 and second["tax_31_2pct"] == 0
    assert b.tax_paid == pytest.approx(first["tax_31_2pct"])         # the losing lot offsets nothing


def test_dip_cycles_harvests_at_new_high_and_rearms():
    prices = np.concatenate([np.linspace(100, 30, 60), np.linspace(30, 140, 80), np.linspace(140, 40, 60)]).reshape(-1, 1)
    res = run(prices, p_dip_cycles(0.5), tax=True)
    one_shot = run(prices, p_dip(0.5), tax=True)
    assert res["trades"] > one_shot["trades"] + 3            # harvested, then bought the second bear market


def test_average_cost_method_taxes_the_blended_gain_per_sale():
    b = Book(cash=2.0, coins=np.zeros(1), basis=np.zeros(1), ledger=[], lot_order="average")
    b.buy(0, 1.0, 100.0, when="d1")
    b.buy(0, 1.0, 300.0, when="d2")
    q = b.coins[0]
    b.sell(0, q / 2, 250.0, tax=True, tds=False, when="d3", asset="BTC")
    row = b.ledger[0]
    assert row["cost_of_acquisition"] == pytest.approx(1.0)          # half of the 2.0 total basis
    net = q / 2 * 250 * (1 - SLIP) * (1 - FEE)
    assert b.tax_paid == pytest.approx(GAIN_TAX * max(0.0, net - 1.0))
    assert b.basis[0] == pytest.approx(1.0) and len(b.ledger) == 1
