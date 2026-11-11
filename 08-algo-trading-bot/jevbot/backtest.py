"""Walk-forward backtest of candle-only strategies on real Binance history.

    python -m jevbot.backtest                 (3 years, BTC/ETH/SOL, 1h and 4h)

What makes this honest, rule by rule:
  causal features   every feature at bar t uses bars <= t only; a test proves it
  next-bar entry    a signal on bar t's close enters at bar t+1's open, plus slippage
  pessimistic path  if one candle touches stop and target, the stop wins; a gap
                    through the stop fills at the (worse) open, never at the stop
  real costs        0.1% fee each side, slippage, and 1% TDS on the sale (india_spot)
  walk-forward      choose the rule on the past year only, trade it on the next 90
                    days, roll forward; training trades that end inside the test
                    window are purged
  abstain           if no rule made money in training, that window trades nothing
  controls          the same exits at random entry times (200 runs) and buy-and-hold
  statistics        core.metrics.scorecard with every rule tested counted, plus the
                    Deflated Sharpe Ratio using the real spread of Sharpes across rules

Pass = edge plausible on the scorecard AND DSR >= 0.95 AND beats random entries
(p < 0.05). Win rate is reported but is never part of the verdict.

LLM, Jev, news and copy-trader signals are NOT tested here on purpose: there is no
point-in-time archive of what they would have said, and an LLM already knows how
2023-2026 played out. Those stay forward-tested in the live bot.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from core.metrics import EDGE_PLAUSIBLE, scorecard

from . import history
from .config import DATA_DIR

OUT_DIR = DATA_DIR.parent / "backtest"
EULER = 0.5772156649


# ---- costs -------------------------------------------------------------------------

@dataclass(frozen=True)
class Costs:
    name: str
    fee: float      # per side
    tds: float      # on gross sale value
    slip: float     # per side
    gain_tax: float = 0.0   # share of each winning trade taken as tax; losses are not offset

    def net(self, entry_raw: np.ndarray, exit_raw: np.ndarray) -> np.ndarray:
        buy = entry_raw * (1 + self.slip) * (1 + self.fee)
        sell = exit_raw * (1 - self.slip) * (1 - self.fee - self.tds)
        r = sell / buy - 1.0
        return np.where(r > 0, r * (1 - self.gain_tax), r) if self.gain_tax else r


COSTS = {
    # The primary edge test: does the rule beat the exchange at all?
    "fees_only": Costs("fees_only", fee=0.001, tds=0.0, slip=0.0002),
    # Indian resident, tax view: 30% on every winning trade, losses never offset
    # (Sec 115BBH). The 1% TDS is withheld but claimable as credit, so it is left out here.
    "india_tax": Costs("india_tax", fee=0.001, tds=0.0, slip=0.0002, gain_tax=0.30),
    # Indian resident, cash-flow view: the 1% TDS treated as lost on every sale. It is
    # refundable at filing, but for a compounding account it is capital locked for a year.
    "india_tds_cash": Costs("india_tds_cash", fee=0.001, tds=0.01, slip=0.0002),
}


# ---- causal features ---------------------------------------------------------------

def yang_zhang(df: pd.DataFrame, n: int) -> pd.Series:
    """Per-bar Yang-Zhang volatility over the last n bars (ending at t)."""
    o = np.log(df["open"] / df["close"].shift(1))
    c = np.log(df["close"] / df["open"])
    h, l = np.log(df["high"] / df["open"]), np.log(df["low"] / df["open"])
    rs = h * (h - c) + l * (l - c)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = o.rolling(n).var() + k * c.rolling(n).var() + (1 - k) * rs.rolling(n).mean()
    return np.sqrt(var.clip(lower=1e-12))


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman: net move / path length over n bars. 1 = straight line, 0 = pure chop."""
    return (close - close.shift(n)).abs() / close.diff().abs().rolling(n).sum()


def ffd_zscore(close: pd.Series, d: float = 0.4, thresh: float = 1e-4, z_win: int = 24) -> pd.Series:
    """Fixed-window fractional differentiation of log price, then a rolling z-score."""
    w = [1.0]
    while True:
        nxt = -w[-1] * (d - len(w) + 1) / len(w)
        if abs(nxt) < thresh or len(w) > 200:
            break
        w.append(nxt)
    w = np.array(w)                                   # w[0] applies to x_t
    x = np.log(close.to_numpy())
    fd = np.full(len(x), np.nan)
    if len(x) >= len(w):
        fd[len(w) - 1:] = np.convolve(x, w, mode="valid")
    s = pd.Series(fd, index=close.index)
    return (s - s.rolling(z_win).mean()) / s.rolling(z_win).std()


# ---- entry rules (long only: an Indian spot account cannot short) ------------------

def entry_rules(df: pd.DataFrame, sigma: pd.Series, daily: bool = False) -> dict[str, np.ndarray]:
    c = df["close"]
    er = efficiency_ratio(c, 20)
    ffd = ffd_zscore(c)
    rules: dict[str, pd.Series] = {}
    lookbacks = (20, 60) if daily else (24, 72)      # 20/60 days on daily candles
    for L, thr in itertools.product(lookbacks, (0.5, 1.0)):
        rules[f"tsmom_L{L}_z{thr}"] = (np.log(c / c.shift(L)) / (sigma * math.sqrt(L))) > thr
    for N in (20, 55):
        rules[f"breakout_N{N}"] = c > df["high"].shift(1).rolling(N).max()
    for z in (1.5, 2.0):
        sma, sd = c.rolling(20).mean(), c.rolling(20).std()
        rules[f"meanrev_z{z}"] = (c - sma) / sd < -z
    for thr in (1.0, 1.5):
        rules[f"ffd_z{thr}"] = ffd > thr
    for name in [k for k in rules if not k.startswith("meanrev")]:
        rules[name + "_er"] = rules[name] & (er > 0.3)   # trend rules, only in trending tape
    return {k: v.fillna(False).to_numpy(dtype=bool) for k, v in rules.items()}


# (max hold bars, stop_k, tgt_k, trail_k). Fixed exits put stop and target at k x sigma x sqrt(hold).
# Trailing exits (trail_k > 0) have no target: a chandelier stop trail_k x per-bar sigma below the
# highest close so far, so a trend can run as far as it goes. That matters under India's 30% tax
# on wins with no loss set-off, which punishes capped winners.
INF = float("inf")
EXITS = {"1h": [(12, 1.0, 2.0, 0), (12, 1.5, 1.5, 0), (48, 1.0, 2.0, 0), (48, 1.5, 1.5, 0), (96, 0, INF, 4.0)],
         "4h": [(6, 1.0, 2.0, 0), (6, 1.5, 1.5, 0), (18, 1.0, 2.0, 0), (18, 1.5, 1.5, 0),
                (60, 0, INF, 3.0), (60, 0, INF, 4.5)],
         "1d": [(10, 1.0, 2.0, 0), (20, 1.0, 2.0, 0), (60, 0, INF, 3.0), (120, 0, INF, 4.0)]}
BARS_PER_DAY = {"1h": 24, "4h": 6, "1d": 1}
TRAIN_DAYS = {"1h": 365, "4h": 365, "1d": 730}   # daily needs two years to see 15+ trades


# ---- trade simulator ---------------------------------------------------------------

def simulate(o, h, l, c, sigma, entries, H, stop_k, tgt_k, trail_k=0.0, start=0, end=None):
    """Non-overlapping long trades with entries in [start, end); a trade may run past end.
    Returns arrays: signal bar, exit bar, raw entry, raw exit."""
    n = len(c)
    end = n if end is None else min(end, n)
    out_t, out_j, out_e, out_x = [], [], [], []
    next_free = start
    for t in np.flatnonzero(entries[start:end]) + start:
        if t < next_free or t + 1 >= n:
            continue
        s = sigma[t]
        if not (s > 0 and math.isfinite(s)):
            continue
        e = o[t + 1]
        if trail_k > 0:
            stop, tgt = e * (1 - trail_k * s), INF
        else:
            band = s * math.sqrt(H)
            stop, tgt = e * (1 - stop_k * band), e * (1 + tgt_k * band)
        last = min(n - 1, t + H)
        x, j = c[last], last
        for jj in range(t + 1, last + 1):
            if jj > t + 1 and o[jj] <= stop:      # gapped through the stop: a stop order fills at the open
                x, j = o[jj], jj
                break
            if l[jj] <= stop:                       # stop checked before target: pessimistic
                x, j = stop, jj
                break
            if h[jj] >= tgt:                        # a resting limit sell fills at its limit, never better
                x, j = tgt, jj
                break
            if trail_k > 0:                         # ratchet on the closed bar; applies from the next bar
                sj = sigma[jj] if sigma[jj] > 0 and math.isfinite(sigma[jj]) else s
                stop = max(stop, c[jj] * (1 - trail_k * sj))
        out_t.append(t); out_j.append(j); out_e.append(e); out_x.append(x)
        # The next signal can come at bar j's close and enters at bar j+1's open: no overlap.
        next_free = j
    return (np.array(out_t, dtype=int), np.array(out_j, dtype=int),
            np.array(out_e, dtype=float), np.array(out_x, dtype=float))


def spaced_random_entries(rng: np.random.Generator, lo: int, hi: int, k: int, gap: int, n: int) -> np.ndarray:
    """k random entry bars in [lo, hi) at least `gap` apart, drawn uniformly over all such
    placements, so every one of them becomes a trade (none is skipped for overlap)."""
    span = hi - lo
    k = int(min(k, (span - 1) // gap + 1)) if span > 0 else 0
    ent = np.zeros(n, dtype=bool)
    if k <= 0:
        return ent
    # y_i = p_i - i*(gap-1) is any k distinct values in [0, span - (k-1)*(gap-1)); mapping back
    # gives positions at least `gap` apart, uniformly over all valid placements.
    picks = np.sort(rng.choice(span - (k - 1) * (gap - 1), size=k, replace=False)) + np.arange(k) * (gap - 1) + lo
    ent[picks] = True
    return ent


# ---- statistics --------------------------------------------------------------------

def sharpe(r: np.ndarray) -> float:
    return float(r.mean() / r.std(ddof=1)) if len(r) > 2 and r.std(ddof=1) > 0 else 0.0


def deflated_sharpe(r: np.ndarray, trial_sharpes: np.ndarray) -> float:
    """Bailey & Lopez de Prado (2014). SR0 is the expected best Sharpe among K trials of
    pure noise, using the measured variance of the trials' Sharpes."""
    T, K = len(r), len(trial_sharpes)
    if T < 10 or K < 2:
        return 0.0
    sr = sharpe(r)
    sd = float(np.std(trial_sharpes, ddof=1))
    sr0 = sd * ((1 - EULER) * stats.norm.ppf(1 - 1 / K) + EULER * stats.norm.ppf(1 - 1 / (K * math.e)))
    g3, g4 = float(stats.skew(r)), float(stats.kurtosis(r, fisher=False))
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(T - 1) / denom))


def max_drawdown(r: np.ndarray) -> float:
    if not len(r):
        return 0.0
    eq = np.cumprod(1 + r)
    return float(np.max(1 - eq / np.maximum.accumulate(eq)))


# ---- one series: symbol x timeframe ------------------------------------------------

def run_series(df: pd.DataFrame, tf: str, k_total: int, rng: np.random.Generator,
               random_runs: int = 200, test_days: int = 90) -> dict:
    train_days = TRAIN_DAYS[tf]
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    sigma_s = yang_zhang(df, 20)
    sigma = sigma_s.to_numpy()
    rules = entry_rules(df, sigma_s, daily=tf == "1d")
    configs = [(r, *ex) for r in rules for ex in EXITS[tf]]

    # Simulate every config once over the whole history; windows select from it.
    sims = {cfg: simulate(o, h, l, c, sigma, rules[cfg[0]], *cfg[1:]) for cfg in configs}

    full_table = []
    trial_sr_by_cost = {k: np.array([sharpe(cst.net(e, x)) for (t, j, e, x) in sims.values()])
                        for k, cst in COSTS.items()}
    for cfg, (t, j, e, x) in sims.items():
        r = COSTS["fees_only"].net(e, x)
        full_table.append({"rule": cfg[0], "hold": cfg[1], "stop_k": cfg[2], "tgt_k": cfg[3], "trail_k": cfg[4],
                           "trades": len(r),
                           "avg_fees_only_pct": round(float(r.mean() * 100), 4) if len(r) else None,
                           "avg_india_tax_pct": round(float(COSTS["india_tax"].net(e, x).mean() * 100), 4) if len(r) else None})

    bpd = BARS_PER_DAY[tf]
    train, test = train_days * bpd, test_days * bpd
    warm = 5 * bpd          # same calendar start on every timeframe, so results line up
    results = {}
    for cost in COSTS.values():
        oos_r, oos_cfgs, folds, rand_means = [], [], [], np.zeros(random_runs)
        rand_counts = np.zeros(random_runs)
        a = warm
        first_b = last_z = None
        busy_until = 0          # no new entry while the previous window's last trade is still open
        while a + train + 30 * bpd <= len(c):        # the final window may be shorter (>= 30 days)
            b, z = a + train, min(a + train + test, len(c))
            first_b = b if first_b is None else first_b
            last_z = z
            best, best_score = None, 0.0
            for cfg, (t, j, e, x) in sims.items():
                m = (t >= a) & (j < b)                       # purge: training trades must end before b
                if m.sum() < 15:
                    continue
                r = cost.net(e[m], x[m])
                score = r.mean() / (r.std(ddof=1) / math.sqrt(len(r))) if r.std(ddof=1) > 0 else 0
                if r.mean() > 0 and score > best_score:
                    best, best_score = cfg, score
            fold = {"train_from": int(df["open_time"][a]), "test_from": int(df["open_time"][b]),
                    "rule": None, "trades": 0}
            if best is not None:
                # Trade the chosen rule fresh from the window start (nothing carried over from
                # how it behaved in training), never while the previous window's trade is open.
                t, j, e, x = simulate(o, h, l, c, sigma, rules[best[0]], *best[1:], start=max(b, busy_until), end=z)
                if len(t):
                    busy_until = int(j.max())
                r = cost.net(e, x)
                oos_r.extend(r.tolist())
                oos_cfgs.append(best)
                label = (f"{best[0]} trail={best[4]} maxhold={best[1]}" if best[4]
                         else f"{best[0]} hold={best[1]} stop={best[2]} tgt={best[3]}")
                fold.update(rule=label, trades=len(t), avg_pct=round(float(r.mean() * 100), 4) if len(r) else None)
                k = len(t)
                if k:   # control: same exits, exactly k random non-overlapping entries in the same window
                    for run in range(random_runs):
                        ent = spaced_random_entries(rng, b, min(z, len(c) - 1), k, best[1] + 1, len(c))
                        rt, rj, re_, rx = simulate(o, h, l, c, sigma, ent, *best[1:], start=b, end=z)
                        if len(rt):
                            rand_means[run] += cost.net(re_, rx).sum()
                            rand_counts[run] += len(rt)
            folds.append(fold)
            a += test

        r = np.array(oos_r)
        # Buy-and-hold: one purchase at the first test window, one sale at the end, same costs
        bh = cost.net(np.array([o[first_b]]), np.array([c[last_z - 1]]))[0] if first_b is not None else 0.0
        res = {"folds": folds, "oos_trades": len(r), "buy_hold_pct": round(float(bh * 100), 2),
               "oos_from": int(df["open_time"][first_b]) if first_b is not None else None}
        if len(r) >= 2:
            card = scorecard(r * 100, strategies_tested=k_total)
            strat_mean = float(r.mean())
            rand_avg = np.divide(rand_means, np.maximum(rand_counts, 1))
            p_random = float((np.sum(rand_avg >= strat_mean) + 1) / (random_runs + 1))
            dsr = deflated_sharpe(r, trial_sr_by_cost[cost.name])
            res.update({
                "avg_trade_pct": round(strat_mean * 100, 4),
                "win_rate": round(float((r > 0).mean()), 3),
                "total_return_pct": round(float((np.prod(1 + r) - 1) * 100), 2),
                "max_drawdown_pct": round(max_drawdown(r) * 100, 2),
                "t_stat": round(card.t_stat, 2) if math.isfinite(card.t_stat) else None,
                "t_required": round(card.t_required, 2),
                "scorecard": card.verdict,
                "deflated_sharpe": round(dsr, 4),
                "random_entry_avg_pct": round(float(rand_avg.mean() * 100), 4),
                "p_vs_random": round(p_random, 4),
            })
            res["PASS"] = bool(card.verdict == EDGE_PLAUSIBLE and dsr >= 0.95 and p_random < 0.05)
        else:
            res.update({"PASS": False, "scorecard": "NO_TRADES (abstained: nothing worked in training)"})
        results[cost.name] = res

    best_full = sorted([row for row in full_table if row["trades"] >= 30],
                       key=lambda row: row["avg_fees_only_pct"] or -1e9, reverse=True)[:5]
    return {"bars": len(c), "configs": len(configs), "top5_full_period_fees_only": best_full, "walk_forward": results}


# ---- report ------------------------------------------------------------------------

def write_report(report: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = [f"# Walk-forward backtest — {report['generated']}", "",
             f"Real Binance candles: {report['days']}. {report['k_total']} rule/exit combinations tested in total "
             f"(the scorecard's multiple-testing count). Train on the past 365 days (730 for daily), trade the next 90, roll.", "",
             "Cost profiles: **fees_only** is the main edge test (0.1% fee each side + 0.02% slippage). "
             "**india_tax** adds 30% tax on every winning trade with no loss set-off (Sec 115BBH); TDS is left "
             "out because it is claimable as credit. **india_tds_cash** instead treats the 1% TDS as lost on "
             "every sale (cash-flow view). Both India rows are per-trade approximations of annual tax rules.", "",
             "| Series | Cost | OOS trades | Avg/trade | Win | Total | Max DD | Buy&hold | t / needed | DSR | vs random p | Verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, s in report["series"].items():
        for cost, r in s["walk_forward"].items():
            lines.append(
                f"| {key} | {cost} | {r['oos_trades']} | {r.get('avg_trade_pct', '–')}% | {r.get('win_rate', '–')} | "
                f"{r.get('total_return_pct', '–')}% | {r.get('max_drawdown_pct', '–')}% | {r['buy_hold_pct']}% | "
                f"{r.get('t_stat', '–')} / {r.get('t_required', '–')} | {r.get('deflated_sharpe', '–')} | "
                f"{r.get('p_vs_random', '–')} | {'**PASS**' if r['PASS'] else r['scorecard']} |")
    lines += ["", "## Best single rules over the whole period (fees only, IN-SAMPLE: optimistic by design)", ""]
    for key, s in report["series"].items():
        top = s["top5_full_period_fees_only"][:3]
        lines.append(f"- **{key}:** " + "; ".join(
            (f"{t['rule']} trail={t['trail_k']} maxhold={t['hold']}" if t.get('trail_k') else
             f"{t['rule']} hold={t['hold']} stop={t['stop_k']} tgt={t['tgt_k']}") + f" ({t['trades']} trades, "
                                                    f"{t['avg_fees_only_pct']}%/trade)" for t in top))
    lines += ["", "PASS needs all three: scorecard EDGE_PLAUSIBLE, Deflated Sharpe >= 0.95, and beating random "
                  "entries with the same exits (p < 0.05). Win rate is shown but never decides.", ""]
    path = OUT_DIR / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest on real Binance candles")
    ap.add_argument("--days", type=int, default=1095, help="history for 1h and 4h")
    ap.add_argument("--days-1d", type=int, default=2920, help="history for daily candles (8 years)")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--tf", default="1h,4h,1d")
    ap.add_argument("--random-runs", type=int, default=200)
    args = ap.parse_args()
    symbols, tfs = args.symbols.split(","), args.tf.split(",")
    rng = np.random.default_rng(7)

    data = {(s, tf): history.load(s, tf, args.days_1d if tf == "1d" else args.days, DATA_DIR.parent / "history")
            for s in symbols for tf in tfs}
    k_total = sum(len(entry_rules(df, yang_zhang(df, 20), daily=tf == "1d")) * len(EXITS[tf])
                  for (s, tf), df in data.items())
    days = f"{args.days} days of 1h/4h" + (f", {args.days_1d} days of daily" if "1d" in tfs else "")
    report = {"generated": time.strftime("%Y-%m-%d %H:%M"), "days": days, "k_total": k_total, "series": {}}
    for (s, tf), df in data.items():
        t0 = time.time()
        report["series"][f"{s} {tf}"] = run_series(df, tf, k_total, rng, args.random_runs)
        print(f"{s} {tf}: {len(df)} bars done in {time.time() - t0:.0f}s", flush=True)
    path = write_report(report)
    print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
