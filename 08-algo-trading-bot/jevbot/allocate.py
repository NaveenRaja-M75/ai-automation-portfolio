"""Low-turnover allocation test on real daily Binance data, taxed like an Indian resident.

    python -m jevbot.allocate

Question: does any rule for HOW MUCH crypto to hold (and when to change it) beat simply
buying once and never touching it, after India's tax on every sale?

Accounting, per coin, average cost basis:
  buy   fee 0.1% + slippage 0.02%; cost basis rises by the full amount paid
  sell  fee + slippage (+1% TDS in the cash-flow view); 30% tax on the realized gain of the
        exact coins sold, charged at the sale; a loss gives no tax back (Sec 115BBH)
  end   every portfolio is liquidated on the last day under the same rules, so buy-and-hold
        pays its tax too
Signals use closes up to day t-1 and trade at day t's close (a full day of delay).

Fair comparison (the control most backtests forget):
  A rule that averages w crypto is compared with simply putting w into buy-and-hold on
  day 1 and leaving the rest in cash, never trading. Holding less crypto lowers drawdown by
  itself; a rule only shows skill if it beats that passive split.
PASS = after-tax Calmar (CAGR / max drawdown) above the exposure-matched passive split in
the full period AND in each half separately. Metrics use the daily equity curve, so every
rule is measured on the same calendar grid however often it trades.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import copy
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from . import history
from .backtest import EULER, OUT_DIR, deflated_sharpe
from .config import DATA_DIR

FEE, SLIP, TDS = 0.001, 0.0002, 0.01
GAIN_TAX = 0.312   # Sec 115BBH 30% + 4% health and education cess (surcharge for high incomes not modelled)


@dataclass
class Book:
    """Cash plus FIFO lots per coin. FIFO gives every sale an acquisition date and cost,
    which is what Schedule VDA in the ITR asks for."""
    cash: float
    coins: np.ndarray
    basis: np.ndarray          # total remaining cost basis per coin (USDT)
    tax_paid: float = 0.0
    fees_paid: float = 0.0
    trades: int = 0
    lots: list = field(default_factory=list)     # per coin: list of [qty, cost_usdt, acquired_label]
    ledger: list | None = None                   # Schedule VDA rows when recording
    lot_order: str = "fifo"                      # "fifo" (default: what practitioner guides recommend),
                                                 # "average" (average cost; some tax tools support it, ask a CA),
                                                 # "hifo" (analysis only: no Indian source mentions it)

    def __post_init__(self):
        if not self.lots:
            self.lots = [[] for _ in range(len(self.coins))]

    def value(self, px: np.ndarray) -> float:
        return self.cash + float(self.coins @ px)

    def buy(self, i: int, usdt: float, price: float, when=None) -> None:
        usdt = min(usdt, self.cash)
        if usdt <= 1e-9:
            return
        qty = usdt / (price * (1 + SLIP) * (1 + FEE))
        self.coins[i] += qty
        self.basis[i] += usdt
        self.lots[i].append([qty, usdt, when])
        self.cash -= usdt
        self.fees_paid += usdt - qty * price
        self.trades += 1

    def sell(self, i: int, qty: float, price: float, tax: bool, tds: bool, when=None, asset: str = "") -> None:
        qty = min(qty, self.coins[i])
        if qty <= 1e-12:
            return
        left, total_tax, total_net, total_gross = qty, 0.0, 0.0, 0.0
        if self.lot_order == "average":
            # Average cost: the sale carries the average cost of all coins held; every lot shrinks
            # pro rata. One ledger row per sale, dated from the earliest lot still held.
            r = qty / self.coins[i]
            cost = self.basis[i] * r
            gross = qty * price
            net = gross * (1 - SLIP) * (1 - FEE - (TDS if tds else 0.0))
            gain = net - cost
            t = GAIN_TAX * gain if (tax and gain > 0) else 0.0
            if self.ledger is not None:
                self.ledger.append({"asset": asset, "acquired": self.lots[i][0][2] if self.lots[i] else None,
                                    "transferred": when, "qty": qty, "cost_of_acquisition": cost,
                                    "consideration": gross, "fees": gross * (SLIP + FEE), "gain": gain,
                                    "tax_31_2pct": t, "tds_1pct_withheld": gross * TDS})
            for lot in self.lots[i]:
                lot[0] *= 1 - r
                lot[1] *= 1 - r
            self.lots[i] = [l for l in self.lots[i] if l[0] > 1e-15]
            self.basis[i] -= cost
            self.coins[i] -= qty
            self.cash += net - t
            self.tax_paid += t
            self.fees_paid += gross - net
            self.trades += 1
            return
        while left > 1e-15 and self.lots[i]:
            if self.lot_order == "hifo":            # highest unit cost first
                li = max(range(len(self.lots[i])), key=lambda n: self.lots[i][n][1] / self.lots[i][n][0])
            else:
                li = 0
            lot = self.lots[i][li]
            take = min(left, lot[0])
            cost = lot[1] * take / lot[0]
            gross = take * price
            net = gross * (1 - SLIP) * (1 - FEE - (TDS if tds else 0.0))
            gain = net - cost
            t = GAIN_TAX * gain if (tax and gain > 0) else 0.0      # a losing lot never offsets anything
            if self.ledger is not None:
                self.ledger.append({"asset": asset, "acquired": lot[2], "transferred": when,
                                    "qty": take, "cost_of_acquisition": cost, "consideration": gross,
                                    "fees": gross * (SLIP + FEE), "gain": gain,
                                    "tax_31_2pct": t, "tds_1pct_withheld": gross * TDS})
            lot[0] -= take
            lot[1] -= cost
            if lot[0] <= 1e-15:
                self.lots[i].pop(li)
            self.basis[i] -= cost
            left -= take
            total_tax, total_net, total_gross = total_tax + t, total_net + net, total_gross + gross
        self.coins[i] -= qty - left
        self.cash += total_net - total_tax
        self.tax_paid += total_tax
        self.fees_paid += total_gross - total_net
        self.trades += 1


def rebalance(book: Book, px: np.ndarray, want: float, tax: bool, tds: bool, when=None,
              assets: list | None = None, step_mode: str = "exact") -> None:
    """Move total crypto weight to `want` without avoidable taxable sales.
      stepping down  sell the SAME fraction of every coin (relative weights untouched), sized
                     so the weight lands on `want` AFTER the tax comes out of the proceeds
                     (step_mode="exact"), or sized on pre-tax value, e.g. "sell half the
                     coins", landing a little above `want` (step_mode="coins")
      stepping up    spend cash only, filling the most under-weight coins first; never sell a
                     winning coin to buy another one
    """
    k = len(px)
    v = book.value(px)
    cur = float(book.coins @ px) / v if v > 0 else 0.0
    names = assets or [""] * k
    if want < cur - 1e-6:
        def weight_after(f: float) -> float:
            b = copy.deepcopy(book)
            b.ledger = None
            for i in range(k):
                b.sell(i, b.coins[i] * f, px[i], tax, tds)
            vv = b.value(px)
            return float(b.coins @ px) / vv if vv > 0 else 0.0
        lo, hi = 0.0, 1.0
        if want <= 0:
            lo = 1.0
        elif step_mode == "coins":
            lo = hi = 1 - want / cur                  # pre-tax sizing: tax then leaves weight slightly above want
        else:
            for _ in range(40):                       # bisection on the sale fraction
                mid = (lo + hi) / 2
                if weight_after(mid) > want:
                    lo = mid
                else:
                    hi = mid
        f = lo if want <= 0 else (lo + hi) / 2
        for i in range(k):
            book.sell(i, book.coins[i] * f, px[i], tax, tds, when, names[i])
    elif want > cur + 1e-6:
        spend = min(book.cash, v * want - float(book.coins @ px))
        vals = book.coins * px
        target_each = (vals.sum() + spend) / k
        need = np.clip(target_each - vals, 0, None)
        if need.sum() > 0:
            for i in range(k):
                book.buy(i, spend * need[i] / need.sum(), px[i], when)


# ---- policies: (closes up to t-1, state) -> target crypto weight, or None = do nothing ----

Policy = Callable[[np.ndarray, int, dict], "float | None"]


def p_hold(w: float) -> Policy:
    return lambda px, t, st: w if t == 0 else None


def p_band(target: float, band: float) -> Policy:
    def f(px, t, st):
        if t == 0:
            return target
        return target if abs(st["w"] - target) > band else None
    return f


def p_sma(ma: int = 200, exit_buf: float = -0.06, enter_buf: float = 0.03, off_w: float = 0.0) -> Policy:
    def f(px, t, st):
        if t == 0:
            st["on"] = True
            return 1.0
        if t < ma:
            return None
        ratio = px[t - 1] / px[t - ma:t].mean() - 1
        if st["on"] and ratio < exit_buf:
            st["on"] = False
            return off_w
        if not st["on"] and ratio > enter_buf:
            st["on"] = True
            return 1.0
        return None
    return f


def p_dip(start: float = 0.5, levels=(0.35, 0.50, 0.65)) -> Policy:
    """Start partly invested; deploy the cash in equal tranches at deeper drawdowns from the
    all-time high seen so far. Never sells, so it never pays tax before the end."""
    def f(px, t, st):
        if t == 0:
            st["done"] = 0
            return start
        dd = 1 - px[t - 1] / px[:t].max()
        if st["done"] < len(levels) and dd >= levels[st["done"]]:
            st["done"] += 1
            return start + (1 - start) * st["done"] / len(levels)
        return None
    return f


def p_dip_cycles(start: float = 0.5, levels=(0.35, 0.50, 0.65), harvest_dd: float = 0.0) -> Policy:
    """Like p_dip, but after all tranches are spent and price makes a new all-time high,
    trim back to the start weight (a taxable sale) so there is cash for the next bear market."""
    def f(px, t, st):
        if t == 0:
            st["done"], st["ath"] = 0, px[0]
            return start
        st["ath"] = max(st["ath"], px[t - 1])
        dd = 1 - px[t - 1] / st["ath"]
        if st["done"] < len(levels) and dd >= levels[st["done"]]:
            st["done"] += 1
            return start + (1 - start) * st["done"] / len(levels)
        if st["done"] == len(levels) and dd <= harvest_dd and px[t - 1] >= st["ath"]:
            st["done"] = 0
            return start
        return None
    return f


def p_voltarget(target_vol: float = 0.6, band: float = 0.25, lookback: int = 60) -> Policy:
    def f(px, t, st):
        if t <= lookback:
            return 1.0 if t == 0 else None
        r = np.diff(np.log(px[t - lookback - 1:t]))
        want = float(np.clip(target_vol / (r.std(ddof=1) * math.sqrt(365)), 0, 1))
        return want if abs(st["w"] - want) > band else None
    return f


POLICIES: dict[str, Policy] = {
    "band_50pct_pm25": p_band(0.5, 0.25),
    "band_70pct_pm25": p_band(0.7, 0.25),
    "sma200_hyst_to_cash": p_sma(off_w=0.0),
    "sma200_hyst_to_half": p_sma(off_w=0.5),
    "sma150_hyst_to_cash": p_sma(ma=150, off_w=0.0),
    "dip_buy_from_50pct": p_dip(0.5),
    "dip_buy_from_30pct": p_dip(0.3, (0.30, 0.45, 0.60, 0.75)),
    "dip_buy_cycles_50pct": p_dip_cycles(0.5),
    "voltarget_60pct": p_voltarget(0.6),
    "voltarget_90pct": p_voltarget(0.9),
}


# ---- engine ---------------------------------------------------------------------------

def run(prices: np.ndarray, policy: Policy, tax: bool = True, tds: bool = False,
        step_mode: str = "exact", lot_order: str = "fifo") -> dict:
    """prices: (days, assets) closes. The crypto weight is split equally across assets."""
    n, k = prices.shape
    book = Book(cash=1.0, coins=np.zeros(k), basis=np.zeros(k), lot_order=lot_order)
    st: dict = {"w": 0.0}
    eq, weights = np.empty(n), np.empty(n)
    # Signal series: one asset's closes, or an equal-weight index (geometric mean of price
    # relatives) so a high-priced coin doesn't swamp the others.
    signal = prices[:, 0] if k == 1 else np.exp(np.log(prices / prices[0]).mean(axis=1))
    for t in range(n):
        px = prices[t]
        v = book.value(px)
        st["w"] = float(book.coins @ px) / v if v > 0 else 0.0
        want = policy(signal, t, st)
        if want is not None:
            rebalance(book, px, want, tax, tds, t, step_mode=step_mode)
        eq[t] = book.value(px)
        weights[t] = float(book.coins @ px) / eq[t] if eq[t] > 0 else 0.0
    for i in range(k):                                           # same liquidation for everyone
        book.sell(i, book.coins[i], prices[-1, i], tax, tds)
    final = book.cash
    return {"equity": eq, "final": final, "avg_w": float(weights.mean()), "trades": book.trades,
            "tax_paid": book.tax_paid, "fees_paid": book.fees_paid}


def metrics(res: dict) -> dict:
    eq = res["equity"].copy()
    eq[-1] = res["final"]                     # the last day is the after-tax liquidation value
    years = (len(eq) - 1) / 365
    cagr = res["final"] ** (1 / years) - 1 if res["final"] > 0 else -1.0
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq)))
    r = np.diff(eq) / eq[:-1]
    downside = r[r < 0].std(ddof=1) if (r < 0).sum() > 2 else float("nan")
    return {"final_x": round(res["final"], 3), "cagr_pct": round(cagr * 100, 2), "max_dd_pct": round(dd * 100, 1),
            "calmar": round(cagr / dd, 3) if dd > 0 else None,
            "sortino": round(r.mean() / downside * math.sqrt(365), 2) if downside and downside > 0 else None,
            "avg_crypto_pct": round(res["avg_w"] * 100, 1), "trades": res["trades"],
            "tax_paid_x": round(res["tax_paid"], 3), "daily_returns": r}


def evaluate(prices: np.ndarray, tax: bool) -> dict:
    out = {}
    halves = {"full": (0, len(prices)), "first_half": (0, len(prices) // 2), "second_half": (len(prices) // 2, len(prices))}
    for name, pol in {"buy_hold_100": p_hold(1.0), **POLICIES}.items():
        row = {}
        for h, (a, b) in halves.items():
            seg = prices[a:b]
            m = metrics(run(seg, pol, tax))
            passive = metrics(run(seg, p_hold(m["avg_crypto_pct"] / 100), tax))
            m["passive_same_exposure"] = {k: passive[k] for k in ("final_x", "cagr_pct", "max_dd_pct", "calmar")}
            m["beats_passive"] = (m["calmar"] or -9) > (passive["calmar"] or -9)
            row[h] = m
        row["PASS"] = name != "buy_hold_100" and all(row[h]["beats_passive"] for h in halves)
        out[name] = row
    trial = np.array([np.mean(v["full"]["daily_returns"]) / np.std(v["full"]["daily_returns"], ddof=1)
                      for k, v in out.items() if k != "buy_hold_100"])
    for k, v in out.items():
        v["deflated_sharpe_daily"] = round(deflated_sharpe(v["full"]["daily_returns"], trial), 4) if k != "buy_hold_100" else None
        for h in halves:
            v[h].pop("daily_returns")
    return out


def robustness(prices: np.ndarray, step: int = 60, min_years: int = 3) -> dict:
    """Start on every 60th day (each window running to the end, at least 3 years long) and
    count how often each rule beats the exposure-matched passive split (after-tax Calmar)
    and 100% buy-and-hold (after-tax final wealth). Windows overlap, so this is a stability
    check across entry timing, not independent evidence."""
    starts = list(range(0, len(prices) - min_years * 365, step))
    out = {}
    for name, pol in POLICIES.items():
        beat_p = beat_bh = 0
        for s0 in starts:
            seg = prices[s0:]
            m = metrics(run(seg, pol, True))
            ps = metrics(run(seg, p_hold(m["avg_crypto_pct"] / 100), True))
            bh = metrics(run(seg, p_hold(1.0), True))
            beat_p += (m["calmar"] or -9) > (ps["calmar"] or -9)
            beat_bh += m["final_x"] > bh["final_x"]
        out[name] = {"starts": len(starts), "beats_passive": round(beat_p / len(starts), 2),
                     "beats_buy_hold_wealth": round(beat_bh / len(starts), 2)}
    return out


def variants(prices: np.ndarray, step: int = 60, min_years: int = 3) -> dict:
    """sma200_hyst_to_half under each step-down sizing x lot order, scored the same way:
    share of start dates beating the exposure-matched passive split, and both halves."""
    pol = POLICIES["sma200_hyst_to_half"]
    starts = list(range(0, len(prices) - min_years * 365, step))
    halves = [(0, len(prices) // 2), (len(prices) // 2, len(prices))]
    out = {}
    for mode in ("exact", "coins"):
        for lots in ("fifo", "average", "hifo"):
            def score(seg):
                r = run(seg, pol, True, step_mode=mode, lot_order=lots)
                m = metrics(r)
                ps = metrics(run(seg, p_hold(m["avg_crypto_pct"] / 100), True))
                return m, (m["calmar"] or -9) > (ps["calmar"] or -9)
            beats = sum(score(prices[s0:])[1] for s0 in starts)
            full, _ = score(prices)
            out[f"{mode}+{lots}"] = {"beats_passive": round(beats / len(starts), 2),
                                     "halves": "/".join(str(int(score(prices[a:b])[1])) for a, b in halves),
                                     "final_x": full["final_x"], "max_dd_pct": full["max_dd_pct"],
                                     "tax_paid_x": full["tax_paid_x"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Low-turnover allocation vs exposure-matched passive holding")
    ap.add_argument("--days", type=int, default=2920)
    ap.add_argument("--variants", action="store_true", help="compare step-down sizing and lot order only")
    args = ap.parse_args()
    cache = DATA_DIR.parent / "history"
    closes = {s: history.load(s, "1d", args.days, cache).set_index("open_time")["close"]
              for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
    universes = {"BTC": ["BTCUSDT"], "ETH": ["ETHUSDT"], "SOL": ["SOLUSDT"], "BTC+ETH": ["BTCUSDT", "ETHUSDT"]}
    if args.variants:
        res = {u: variants(pd.concat([closes[s] for s in syms], axis=1, join="inner").dropna().to_numpy())
               for u, syms in universes.items()}
        print(json.dumps(res, indent=1))
        (OUT_DIR / "allocation_variants.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        return 0
    report = {"generated": time.strftime("%Y-%m-%d %H:%M"), "universes": {}}
    lines = [f"# Allocation test — {report['generated']}", "",
             "Real Binance daily closes. Every sale pays fee + slippage and 31.2% tax (30% + 4% cess) on the realized "
             "gain of each FIFO lot (a losing lot offsets nothing); every portfolio is liquidated and taxed on the last day. "
             "Stepping down sells every coin pro rata, sized so the weight lands on target after tax; stepping up only "
             "spends cash (never sells one coin to buy another). "
             "Each rule is compared with **passively holding the same average crypto share and never trading**. "
             "PASS = better after-tax Calmar than that passive split in the full period AND both halves.", ""]
    for uname, syms in universes.items():
        df = pd.concat([closes[s] for s in syms], axis=1, join="inner").dropna()
        prices = df.to_numpy()
        res = evaluate(prices, tax=True)
        rob = robustness(prices)
        for name in res:
            res[name]["robustness"] = rob.get(name)
        start = time.strftime("%Y-%m-%d", time.gmtime(df.index[0] / 1000))
        end = time.strftime("%Y-%m-%d", time.gmtime(df.index[-1] / 1000))
        report["universes"][uname] = {"from": start, "to": end, "days": len(df), "results": res}
        lines += [f"## {uname} ({start} to {end}, {len(df)} days)", "",
                  "| Rule | Final x | CAGR | Max DD | Calmar | Avg crypto | Trades | Tax paid x | Passive same-exposure Calmar | Halves beat passive | Start dates beating passive | Start dates beating B&H wealth | Verdict |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, row in res.items():
            f = row["full"]
            halves_ok = f"{int(row['first_half']['beats_passive'])}/{int(row['second_half']['beats_passive'])}"
            rb = row.get("robustness") or {}
            verdict = "benchmark" if name == "buy_hold_100" else ("**PASS**" if row["PASS"] else "no")
            rob_p = f"{rb['beats_passive']:.0%}" if rb else "–"
            rob_b = f"{rb['beats_buy_hold_wealth']:.0%}" if rb else "–"
            lines.append(f"| {name} | {f['final_x']} | {f['cagr_pct']}% | {f['max_dd_pct']}% | {f['calmar']} | "
                         f"{f['avg_crypto_pct']}% | {f['trades']} | {f['tax_paid_x']} | "
                         f"{f['passive_same_exposure']['calmar']} | {halves_ok} | {rob_p} | {rob_b} | {verdict} |")
        lines.append("")
    lines += ["Final x = ending wealth per 1.0 invested, after liquidation tax. Max DD is measured on the daily "
              "mark-to-market value. Cash earns nothing here (USDT idle), which slightly favours rules that hold more crypto."]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "allocation.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    (OUT_DIR / "allocation.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
