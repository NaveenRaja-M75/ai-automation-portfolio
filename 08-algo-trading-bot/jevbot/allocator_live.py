"""Daily paper-trading of the allocation rule that held up best in the backtest.

Rule (sma200_hyst_to_half on an equal-weight BTC+ETH index):
  100% crypto normally; step down to 50% after a daily close more than 6% below the
  200-day average; back to 100% after a close more than 3% above it. About 3 trades a year.

Once per UTC day, after the daily candle closes:
  1. read the closed daily candles (free Binance data) and update the regime
  2. if the target weight changed, trade the paper book: stepping down sells every coin
     pro rata, sized so the weight lands on 50% after the 31.2% tax; stepping up only
     spends cash
  3. append every sale, lot by lot, to schedule_vda.csv (acquisition date, transfer date,
     cost, consideration, gain, tax, TDS) for the income tax return
  4. log what the copy-traders and the news signal said, so their value as a regime filter
     can be measured forward instead of argued about

Paper only. This module never touches an exchange account.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import history
from .allocate import FEE, GAIN_TAX, SLIP, Book, rebalance

log = logging.getLogger("jevbot.alloc")

MA, EXIT_BUF, ENTER_BUF, OFF_W = 200, -0.06, 0.03, 0.5
VDA_FIELDS = ["asset", "date_of_acquisition", "date_of_transfer", "quantity", "cost_of_acquisition_usdt",
              "consideration_received_usdt", "fees_usdt", "gain_usdt", "tax_31_2pct_usdt", "tds_1pct_withheld_usdt"]


def regime_from_history(index: np.ndarray, on: bool | None = None, flips: list | None = None) -> tuple[bool, float]:
    """Replay the hysteresis over the index (closed candles only) and return the current
    state and today's distance from the 200-day average. Starting 'on' matches the backtest.
    The first evaluated bar is MA-1, the first with a full 200-close window. Every flip's
    position is appended to `flips` when given."""
    state = True if on is None else on
    ratio = float("nan")
    for t in range(MA - 1, len(index)):
        ratio = index[t] / index[t - MA + 1:t + 1].mean() - 1
        if state and ratio < EXIT_BUF:
            state = False
            if flips is not None:
                flips.append((t, False))
        elif not state and ratio > ENTER_BUF:
            state = True
            if flips is not None:
                flips.append((t, True))
    return state, ratio


def equal_weight_index(closes: pd.DataFrame) -> np.ndarray:
    return np.exp(np.log(closes / closes.iloc[0]).mean(axis=1)).to_numpy()


class LiveAllocator:
    def __init__(self, symbols: tuple[str, ...], capital: float, data_dir: Path, books=None, copy=None, news=None,
                 loader=history.load, lot_method: str = "fifo", name: str = ""):
        self.symbols, self.capital, self.dir, self.lot_method = symbols, capital, data_dir, lot_method
        self.name = name or "+".join(s.replace("USDT", "") for s in symbols)
        # The first book (BTC+ETH, started 2026-10-28) keeps its original file names; others get a suffix.
        sfx = "" if self.name in ("", "BTC+ETH") else "_" + self.name.replace("+", "").lower()
        self.books, self.copy, self.news, self.loader = books or {}, copy, news, loader
        self.state_path = data_dir / f"alloc_state{sfx}.json"
        self.vda_path = data_dir / f"schedule_vda{sfx}.csv"
        self.events_path = data_dir / f"alloc_events{sfx}.jsonl"
        self.cache = data_dir.parent / "history"
        self.status = "starting"
        self.st = self._load()

    # -- persistence --------------------------------------------------------------
    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        k = len(self.symbols)
        return {"cash": self.capital, "coins": [0.0] * k, "basis": [0.0] * k, "lots": [[] for _ in range(k)],
                "on": None, "target": None, "last_day": None, "tax_paid": 0.0, "fees_paid": 0.0, "trades": 0,
                "started": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "last_flip": None, "ratio": None}

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def _book(self) -> Book:
        b = Book(cash=self.st["cash"], coins=np.array(self.st["coins"], dtype=float),
                 basis=np.array(self.st["basis"], dtype=float), tax_paid=self.st["tax_paid"],
                 fees_paid=self.st["fees_paid"], trades=self.st["trades"], lots=[list(map(list, l)) for l in self.st["lots"]],
                 lot_order=self.lot_method)
        b.ledger = []
        return b

    def _store(self, b: Book) -> None:
        self.st.update(cash=b.cash, coins=b.coins.tolist(), basis=b.basis.tolist(), lots=b.lots,
                       tax_paid=b.tax_paid, fees_paid=b.fees_paid, trades=b.trades)

    def _append_vda(self, rows: list[dict]) -> None:
        if not rows:
            return
        new = not self.vda_path.exists()
        with self.vda_path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=VDA_FIELDS)
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({"asset": r["asset"], "date_of_acquisition": r["acquired"], "date_of_transfer": r["transferred"],
                            "quantity": round(r["qty"], 10), "cost_of_acquisition_usdt": round(r["cost_of_acquisition"], 6),
                            "consideration_received_usdt": round(r["consideration"], 6), "fees_usdt": round(r["fees"], 6),
                            "gain_usdt": round(r["gain"], 6), "tax_31_2pct_usdt": round(r["tax_31_2pct"], 6),
                            "tds_1pct_withheld_usdt": round(r["tds_1pct_withheld"], 6)})

    def _event(self, rec: dict) -> None:
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    # -- the daily step -----------------------------------------------------------
    def _prices_now(self, last_closes: np.ndarray) -> np.ndarray:
        """Live mid when the feed is fresh, otherwise the last daily close."""
        px = last_closes.copy()
        for i, s in enumerate(self.symbols):
            b = self.books.get(s)
            if b is not None and b.ready() and b.age() < 10:
                px[i] = b.mid
        return px

    def _shadow(self) -> dict:
        out = {}
        if self.copy is not None:
            for s in self.symbols:
                v = self.copy.view(s)
                out[f"copy_{s}"] = None if v is None else {"net_long_share": round(v[0], 3), "holders": v[1]}
        out["news"] = {s: self.news.words(s) for s in self.symbols} if self.news is not None else "jev off"
        return out

    def step(self, now: float | None = None) -> dict | None:
        frames = [self.loader(s, "1d", MA + 220, self.cache).set_index("open_time")["close"].rename(s)
                  for s in self.symbols]
        closes = pd.concat(frames, axis=1, join="inner").dropna()
        if len(closes) < MA + 5:
            self.status = f"waiting for {MA} days of history"
            return None
        day = datetime.fromtimestamp(closes.index[-1] / 1000, timezone.utc).strftime("%Y-%m-%d")
        if day == self.st["last_day"]:
            self.status = "ok"                       # already handled today's candle (e.g. after a restart)
            return None
        idx = equal_weight_index(closes)
        days = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d") for t in closes.index]
        if self.st["on"] is None or self.st["last_day"] not in days:
            on, ratio = regime_from_history(idx)             # first run: where would the rule be today?
        else:
            # Replay every close since the last one processed, so a few days offline never
            # skips a flip. Each step needs the MA-1 closes before it.
            p = days.index(self.st["last_day"])
            start = max(0, p + 2 - MA)            # first replayed close is p+1 (index MA-1 in the slice)
            flips: list = []
            on, ratio = (regime_from_history(idx[start:], self.st["on"], flips) if p + 1 < len(idx)
                         else (self.st["on"], self.st["ratio"]))
            # Flips that happened and reversed while the bot was off cannot be traded after the
            # fact; inventing back-dated fills would make the paper record lie. Record them instead.
            missed = [(days[start + t], "on" if f else "off") for t, f in flips if start + t < len(days) - 1]
            if missed:
                self._event({"day": days[-1], "flips_while_offline": missed,
                             "note": "the regime changed on these days while the bot was not running. They were not "
                                     "traded on those days; if the change still holds, it is traded today, late."})
        target = 1.0 if on else OFF_W
        flipped = self.st["target"] is not None and target != self.st["target"]
        px = self._prices_now(closes.iloc[-1].to_numpy(dtype=float))
        book = self._book()
        when = datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y-%m-%d")
        if target != self.st["target"]:
            rebalance(book, px, target, tax=True, tds=False, when=when, assets=list(self.symbols))
            self._append_vda(book.ledger)
        self._store(book)
        v = book.value(px)
        rec = {"day": day, "executed": when, "on": on, "target": target, "ratio_vs_sma200": round(ratio, 4),
               "weight": round(float(book.coins @ px) / v, 4) if v else 0.0, "equity": round(v, 4),
               "flip": flipped, "initial": self.st["target"] is None, **self._shadow()}
        if flipped or rec["initial"]:
            self._event(rec)
            if flipped:
                self.st["last_flip"] = when
                log.warning("allocation regime flip: target %.0f%% (ratio %.1f%%)", target * 100, ratio * 100)
        self.st.update(on=on, target=target, last_day=day, ratio=round(ratio, 4))
        self._save()
        self.status = "ok"
        return rec

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.step)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                          # network or data hiccup: try again later
                self.status = f"error: {exc!r}"[:160]
                log.warning("allocator step failed: %s", exc)
            await asyncio.sleep(900)

    # -- dashboard ----------------------------------------------------------------
    def snapshot(self) -> dict:
        px = np.array([self.books[s].mid if s in self.books and self.books[s].ready() else np.nan
                       for s in self.symbols])
        coins = np.array(self.st["coins"])
        if np.isnan(px).any():
            return {"name": self.name, "status": self.status, "target": self.st["target"], "on": self.st["on"]}
        coin_val = float(coins @ px)
        equity = self.st["cash"] + coin_val
        # India: tax is per lot with no loss offset, so a losing lot never reduces a winner's tax
        keep = (1 - SLIP) * (1 - FEE)
        tax_now = sum(max(0.0, q * px[i] * keep - c) * GAIN_TAX
                      for i, lots in enumerate(self.st["lots"]) for q, c, _ in lots)
        return {
            "name": self.name, "status": self.status, "on": self.st["on"], "target": self.st["target"],
            "weight": round(coin_val / equity, 4) if equity else 0.0, "equity": round(equity, 2),
            "capital": self.capital, "ratio_vs_sma200": self.st["ratio"],
            "exit_below": EXIT_BUF, "enter_above": ENTER_BUF, "last_day": self.st["last_day"],
            "last_flip": self.st["last_flip"], "trades": self.st["trades"], "tax_paid": round(self.st["tax_paid"], 2),
            "tax_if_sold_today": round(tax_now, 2), "started": self.st["started"],
        }
