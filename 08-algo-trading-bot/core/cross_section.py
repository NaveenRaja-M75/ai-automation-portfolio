"""Cross-sectional walk-forward: one strategy, one parameter set, many instruments.

This exists because of a structural finding, not a preference. Trend following on a single
daily-bar instrument produced three to four trades a year, so reaching a sample size that
could distinguish edge from luck would take roughly 85 years. The strategy was not the
problem; the sampling rate was.

Breadth fixes that, and it fixes it almost for free. Testing 9 parameter sets across 29
instruments yields 29 times the trades while the multiple-testing bar stays at 9, because
the same single parameter set is applied to every symbol. That is the whole point, and it
is why per-symbol optimisation is deliberately not supported: fitting each symbol its own
parameters would multiply the search space by 29 and give back every bit of significance
the breadth just bought.

What this measures is whether the strategy has an edge. What it does NOT measure is what a
real account would have returned, because each symbol is simulated with its own capital.
The portfolio approximation at the end is a rough answer to that second question and is
labelled as rough on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from .backtest import Bar, BacktestTrade, run_backtest
from .costs import CostModel
from .walkforward import expand_grid


@dataclass
class CrossSectionResult:
    oos_trades: list[tuple[str, BacktestTrade]] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)
    combos_tested: int = 0
    symbols: int = 0

    @property
    def r_multiples(self) -> list[float]:
        return [t.r_multiple for _, t in self.oos_trades]

    def per_symbol(self) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for sym, t in self.oos_trades:
            out.setdefault(sym, []).append(t.r_multiple)
        return out

    def portfolio_curve(self, start_equity: float, risk_pct: float,
                        max_concurrent: int = 5) -> tuple[list[float], int]:
        """Rough shared-capital simulation over the pooled trades.

        Trades are replayed in entry order against one account, each risking risk_pct of
        equity at the time, and any trade that would exceed max_concurrent open positions
        is dropped. It is an approximation: real fills, margin, and the correlation gate
        are not modelled here. Returns the equity curve and the number of trades skipped.
        """
        events = sorted(self.oos_trades, key=lambda x: x[1].entry_ts)
        equity = start_equity
        curve = [equity]
        open_until: list[datetime] = []
        skipped = 0
        for _, t in events:
            open_until = [ts for ts in open_until if ts > t.entry_ts]
            if len(open_until) >= max_concurrent:
                skipped += 1
                continue
            open_until.append(t.exit_ts)
            equity *= (1.0 + risk_pct * t.r_multiple)
            curve.append(equity)
        return curve, skipped


def _slice(bars: Sequence[Bar], lo: datetime, hi: datetime) -> list[Bar]:
    return [b for b in bars if lo <= b.ts < hi]


def cross_sectional_walk_forward(
    symbol_bars: dict[str, list[Bar]],
    factory: Callable[..., Any],
    grid: dict[str, Sequence[Any]],
    costs: CostModel,
    start_equity: float,
    train_days: int,
    test_days: int,
    risk_pct: float = 0.01,
    warmup_days: int = 220,
    min_train_trades: int = 20,
) -> CrossSectionResult:
    """Roll a shared train/test split across every symbol at once.

    Windows are aligned by calendar date rather than by bar index, because symbols have
    different histories and holidays. Aligning by index would train one symbol on 2019
    while testing another on 2021 and pool the results as though that were meaningful.
    """
    combos = expand_grid(grid)
    res = CrossSectionResult(combos_tested=len(combos), symbols=len(symbol_bars))

    calendar = sorted({b.ts for bars in symbol_bars.values() for b in bars})
    if len(calendar) < train_days + test_days + warmup_days:
        raise ValueError("not enough history for even one window")

    start = warmup_days
    while start + train_days + test_days <= len(calendar):
        warm_from = calendar[start - warmup_days]
        train_from = calendar[start]
        train_to = calendar[start + train_days]
        test_to = calendar[start + train_days + test_days]

        # --- fit one parameter set on the pooled training data -------------
        best, best_score, best_n = None, float("-inf"), 0
        for params in combos:
            pooled: list[float] = []
            for bars in symbol_bars.values():
                seg = _slice(bars, warm_from, train_to)
                if len(seg) < warmup_days // 2:
                    continue
                r = run_backtest(seg, factory(**params), costs, start_equity, risk_pct)
                pooled.extend(t.r_multiple for t in r.trades
                              if t.entry_ts >= train_from)
            if len(pooled) < min_train_trades:
                continue
            score = sum(pooled)
            if score > best_score:
                best, best_score, best_n = params, score, len(pooled)

        if best is None:
            res.windows.append({"test_from": train_to, "params": None,
                                "reason": "no parameter set reached the training minimum"})
            start += test_days
            continue

        # --- trade it, untouched, on the window that follows ---------------
        window_trades: list[tuple[str, BacktestTrade]] = []
        for sym, bars in symbol_bars.items():
            seg = _slice(bars, calendar[start + train_days - warmup_days], test_to)
            if len(seg) < warmup_days // 2:
                continue
            r = run_backtest(seg, factory(**best), costs, start_equity, risk_pct)
            window_trades.extend((sym, t) for t in r.trades if t.entry_ts >= train_to)

        res.oos_trades.extend(window_trades)
        res.windows.append({
            "test_from": train_to,
            "test_to": test_to,
            "params": best,
            "train_trades": best_n,
            "train_total_r": round(best_score, 1),
            "oos_trades": len(window_trades),
            "oos_total_r": round(sum(t.r_multiple for _, t in window_trades), 2),
        })
        start += test_days

    res.oos_trades.sort(key=lambda x: x[1].entry_ts)
    return res
