"""Walk-forward analysis: the only backtest result worth acting on.

A single backtest over all history answers "what parameters would have worked", which is
a question about the past. Walk-forward answers "would choosing parameters the way I
actually choose them have worked", which is a question about the future, and it is the
one that matters.

The procedure: fit parameters on a training window, trade them on the untouched window
that follows, roll forward, repeat. Only the out-of-sample trades count. The training
results are thrown away, because they are curve fits by construction.

Crucially, this reports how many parameter combinations were evaluated, and that number
is handed to `metrics.scorecard` as `strategies_tested`. Searching 48 combinations and
reporting the winner as though it were the only candidate is the most common way a
walk-forward result is still a mirage.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .backtest import Bar, BacktestTrade, run_backtest
from .costs import CostModel


@dataclass
class WalkForwardResult:
    oos_trades: list[BacktestTrade] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)
    combos_tested: int = 0
    start_equity: float = 0.0
    final_equity: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def r_multiples(self) -> list[float]:
        return [t.r_multiple for t in self.oos_trades]

    @property
    def return_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return (self.final_equity / self.start_equity - 1.0) * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = float("-inf")
        worst = 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            if peak > 0:
                worst = max(worst, (peak - e) / peak)
        return worst * 100.0


def expand_grid(grid: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def walk_forward(
    bars: Sequence[Bar],
    factory: Callable[..., Any],
    grid: dict[str, Sequence[Any]],
    costs: CostModel,
    start_equity: float,
    train_bars: int,
    test_bars: int,
    risk_pct: float = 0.01,
    min_train_trades: int = 5,
) -> WalkForwardResult:
    """Roll a train/test split forward across the data.

    factory(**params) must return a strategy. min_train_trades rejects parameter sets
    that barely traded in the training window: a combination with two training trades
    and a spectacular total is noise being promoted to a decision.
    """
    combos = expand_grid(grid)
    res = WalkForwardResult(start_equity=start_equity, final_equity=start_equity,
                            combos_tested=len(combos))
    equity = start_equity
    start = 0

    while start + train_bars + test_bars <= len(bars):
        train = bars[start: start + train_bars]
        # The test window carries the training bars ahead of it so indicators are warm at
        # its first bar. Only trades entered inside the test span are counted.
        test_with_warmup = bars[start: start + train_bars + test_bars]
        test_begins_at = train[-1].ts

        best, best_score = None, float("-inf")
        for params in combos:
            r = run_backtest(train, factory(**params), costs, start_equity, risk_pct)
            if len(r.trades) < min_train_trades:
                continue
            score = sum(t.r_multiple for t in r.trades)
            if score > best_score:
                best, best_score = params, score

        if best is None:
            res.windows.append({"train_end": test_begins_at, "params": None,
                                "reason": "no parameter set traded enough in training"})
            start += test_bars
            continue

        run = run_backtest(test_with_warmup, factory(**best), costs, equity, risk_pct)
        oos = [t for t in run.trades if t.entry_ts > test_begins_at]
        realised = sum(t.net_pnl for t in oos)
        equity += realised
        res.oos_trades.extend(oos)
        res.equity_curve.append(equity)
        res.windows.append({
            "train_end": test_begins_at,
            "params": best,
            "train_total_r": round(best_score, 2),
            "oos_trades": len(oos),
            "oos_total_r": round(sum(t.r_multiple for t in oos), 2),
            "oos_pnl": round(realised, 2),
            "equity_after": round(equity, 2),
        })
        start += test_bars

    res.final_equity = equity
    return res
