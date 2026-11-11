"""Live daily allocation: regime replay, paper trades, Schedule VDA ledger, restarts."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevbot.allocator_live import MA, LiveAllocator, regime_from_history


class FakeHistory:
    """Daily closes for two coins; `days` controls how much history 'exists' so far."""

    def __init__(self, path: np.ndarray):
        self.path, self.days = path, len(path)

    def __call__(self, symbol, interval, days, cache):
        n = self.days
        t0 = 1_700_000_000_000
        mult = 1.0 if symbol == "BTCUSDT" else 0.05
        return pd.DataFrame({"open_time": t0 + np.arange(n) * 86_400_000, "close": self.path[:n] * mult})


def make(tmp_path, path):
    fh = FakeHistory(path)
    return LiveAllocator(("BTCUSDT", "ETHUSDT"), 1000.0, tmp_path, loader=fh), fh


def test_regime_hysteresis_needs_the_buffer_to_flip():
    idx = np.r_[np.full(MA, 100.0), np.full(5, 95.0)]       # 5% below: inside the 6% buffer
    assert regime_from_history(idx)[0] is True
    idx = np.r_[np.full(MA, 100.0), np.full(5, 90.0)]       # clearly below: steps down
    assert regime_from_history(idx)[0] is False
    on, ratio = regime_from_history(np.r_[idx, np.full(3, 102.0)], on=False)
    assert on is False                                       # 102 vs a 99.8 average is +2.2%: not 3% above yet
    assert regime_from_history(np.r_[idx, np.full(3, 103.0)], on=False)[0] is True   # +3.2%: back on


def test_first_day_buys_full_exposure_in_an_uptrend(tmp_path):
    alloc, _ = make(tmp_path, np.linspace(100, 200, MA + 30))
    rec = alloc.step(now=0)
    assert rec["initial"] and rec["target"] == 1.0 and rec["weight"] > 0.99
    assert not (tmp_path / "schedule_vda.csv").exists()     # buying is not a taxable transfer


def test_crash_steps_down_to_half_and_logs_every_lot_for_the_tax_return(tmp_path):
    path = np.r_[np.linspace(100, 200, MA + 30), np.linspace(200, 120, 40)]
    alloc, fh = make(tmp_path, path)
    fh.days = MA + 30
    alloc.step(now=0)
    flips = []
    for d in range(MA + 31, len(path) + 1):
        fh.days = d
        rec = alloc.step(now=d * 86400)
        if rec and rec["flip"]:
            flips.append(rec)
    assert len(flips) == 1 and flips[0]["target"] == 0.5
    assert flips[0]["weight"] == pytest.approx(0.5, abs=1e-6)          # lands on 50% after tax
    rows = list(csv.DictReader((tmp_path / "schedule_vda.csv").open(encoding="utf-8")))
    assert {r["asset"] for r in rows} == {"BTCUSDT", "ETHUSDT"}
    assert all(r["date_of_acquisition"] and r["date_of_transfer"] for r in rows)
    assert all(float(r["tax_31_2pct_usdt"]) == 0 for r in rows if float(r["gain_usdt"]) <= 0)


def test_same_day_is_processed_once_and_state_survives_restart(tmp_path):
    alloc, fh = make(tmp_path, np.linspace(100, 200, MA + 30))
    assert alloc.step(now=0) is not None
    assert alloc.step(now=1) is None                         # the daily candle has not changed
    again = LiveAllocator(("BTCUSDT", "ETHUSDT"), 1000.0, tmp_path, loader=fh)
    assert again.st["target"] == 1.0 and again.step(now=2) is None


def test_days_offline_are_replayed_so_no_flip_is_missed(tmp_path):
    path = np.r_[np.linspace(100, 200, MA + 30), np.linspace(200, 110, 30), np.linspace(110, 112, 5)]
    alloc, fh = make(tmp_path, path)
    fh.days = MA + 30
    alloc.step(now=0)
    fh.days = len(path)                                      # the bot was off for the whole crash
    rec = alloc.step(now=1)
    assert rec["flip"] and rec["target"] == 0.5


# ---- round 6 ----------------------------------------------------------------------

import json  # noqa: E402

from jevbot.allocate import GAIN_TAX, Book, rebalance  # noqa: E402


def test_tax_if_sold_today_is_per_lot_with_no_loss_offset(tmp_path):
    alloc, _ = make(tmp_path, np.linspace(100, 200, MA + 30))
    alloc.st.update(cash=0.0, coins=[1.0, 1.0], basis=[100.0, 100.0],
                    lots=[[[1.0, 100.0, "d0"]], [[1.0, 100.0, "d0"]]], on=True, target=1.0)

    class B:
        def __init__(self, mid): self.mid = mid
        def ready(self): return True
        def age(self): return 0.0
    alloc.books = {"BTCUSDT": B(200.0), "ETHUSDT": B(20.0)}       # BTC +100%, ETH -80%
    snap = alloc.snapshot()
    keep = (1 - 0.0002) * (1 - 0.001)
    assert snap["tax_if_sold_today"] == pytest.approx(round((200 * keep - 100) * GAIN_TAX, 2))   # ETH's loss offsets nothing


def test_regime_is_evaluated_from_the_first_full_window():
    idx = np.r_[np.full(MA - 1, 100.0), 80.0]                # the 200th close is already a crash
    assert regime_from_history(idx)[0] is False


def test_whipsaw_while_offline_is_logged_not_back_dated(tmp_path):
    up = np.linspace(100, 200, MA + 30)
    path = np.r_[up, np.full(3, 130.0), np.full(4, 260.0)]  # crash ~17% below the average, then far above, all while off
    alloc, fh = make(tmp_path, path)
    fh.days = len(up)
    alloc.step(now=0)
    trades_before = alloc.st["trades"]
    fh.days = len(path)
    rec = alloc.step(now=1)
    assert rec["target"] == 1.0 and not rec["flip"]           # still "on" today: nothing to trade now
    assert alloc.st["trades"] == trades_before                 # no invented back-dated fills
    events = [json.loads(l) for l in (tmp_path / "alloc_events.jsonl").read_text(encoding="utf-8").splitlines()]
    offline = [e for e in events if "flips_while_offline" in e]
    assert offline and [f[1] for f in offline[0]["flips_while_offline"]] == ["off", "on"]


def test_hifo_sells_the_highest_cost_lot_first():
    b = Book(cash=2.0, coins=np.zeros(1), basis=np.zeros(1), ledger=[], lot_order="hifo")
    b.buy(0, 1.0, 100.0, when="cheap")
    b.buy(0, 1.0, 300.0, when="dear")
    b.sell(0, b.coins[0] * 0.2, 250.0, tax=True, tds=False)
    assert b.ledger[0]["acquired"] == "dear"


def test_coins_mode_sells_half_the_coins_and_lands_above_half_after_tax():
    b = Book(cash=1.0, coins=np.zeros(1), basis=np.zeros(1))
    b.buy(0, 1.0, 100.0)
    q = b.coins[0]
    px = np.array([400.0])
    rebalance(b, px, 0.5, tax=True, tds=False, step_mode="coins")
    assert b.coins[0] == pytest.approx(q / 2)
    w = float(b.coins @ px) / b.value(px)
    assert 0.5 < w < 0.6


def test_second_book_keeps_separate_files_and_first_keeps_original_names(tmp_path):
    fh = FakeHistory(np.linspace(100, 200, MA + 30))
    pair = LiveAllocator(("BTCUSDT", "ETHUSDT"), 1000.0, tmp_path, loader=fh)
    btc = LiveAllocator(("BTCUSDT",), 1000.0, tmp_path, loader=fh)
    assert pair.state_path.name == "alloc_state.json" and btc.state_path.name == "alloc_state_btc.json"
    pair.step(now=0); btc.step(now=0)
    assert btc.st["coins"][0] > 0 and len(btc.st["coins"]) == 1 and len(pair.st["coins"]) == 2
