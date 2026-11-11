"""Event-driven backtester with the pessimistic assumptions built in.

Most retail backtests are wrong in the same four ways, and each one flatters the result.
This engine takes the opposite side of every one of them:

1. LOOK-AHEAD. A strategy sees bars[0..i] and its order fills at bars[i+1].open. It can
   never act on the close of the bar it is trading, which is the single most common way a
   backtest invents an edge that does not exist.

2. INTRABAR AMBIGUITY. When one bar's range contains both the stop and the target, OHLC
   data cannot say which came first. This engine always assumes the STOP. A vectorized
   backtest that assumes the target instead can turn a losing system into a winner.

3. GAPS. If price opens beyond the stop, the fill is at the open, not at the stop price.
   Stops are not guarantees, and pretending they are removes exactly the losses that
   matter most.

4. COSTS. Entry and exit costs plus adverse slippage on both legs, from a real cost model.

The engine is intentionally slow and explicit. Correctness here is worth more than speed,
because a fast wrong answer is what everyone else already has.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Protocol, Sequence

from .costs import CostModel


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Signal:
    """A strategy's intent, expressed at bar i and filled at bar i+1's open."""
    side: str                        # "buy" or "sell"
    stop: float
    setup: str = "unnamed"
    target: Optional[float] = None
    reference: Optional[float] = None


class Strategy(Protocol):
    def on_bar(self, i: int, bars: Sequence[Bar]) -> Optional[Signal]:
        """Return a Signal to open a position, or None. Only bars[0..i] may be read."""
        ...

    def should_exit(self, i: int, bars: Sequence[Bar], position: "Position") -> bool:
        """Discretionary exit, checked after stop and target. Only bars[0..i] readable."""
        ...

    def update_stop(self, i: int, bars: Sequence[Bar], position: "Position") -> Optional[float]:
        """Optional trailing stop. Return the new stop level, or None to leave it.

        Called at the END of bar i, after that bar's exit check has already run, so the
        level it returns governs bar i+1 onward. Doing it in this order matters: setting
        a stop from bar i's own high and then testing bar i's low against it would be
        intrabar look-ahead, and it is how trailing-stop backtests quietly invent money.

        The engine only ever tightens. A returned level that would widen the stop is
        discarded, so a buggy strategy cannot move its stop away from a losing position.
        """
        ...


@dataclass
class Position:
    side: str
    entry: float
    stop: float
    target: Optional[float]
    qty: float
    entry_i: int
    entry_ts: datetime
    setup: str
    planned_risk: float


@dataclass(frozen=True)
class BacktestTrade:
    setup: str
    side: str
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    qty: float
    planned_risk: float
    gross_pnl: float
    costs: float
    net_pnl: float
    r_multiple: float
    exit_reason: str
    bars_held: int


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    start_equity: float = 0.0

    @property
    def r_multiples(self) -> list[float]:
        return [t.r_multiple for t in self.trades]

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.start_equity

    @property
    def return_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return (self.final_equity / self.start_equity - 1.0) * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = -math.inf
        worst = 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            if peak > 0:
                worst = max(worst, (peak - e) / peak)
        return worst * 100.0

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def buy_and_hold(bars: Sequence[Bar], start_equity: float) -> tuple[float, float]:
    """Benchmark: return percent and max drawdown percent from holding throughout.

    Every strategy is measured against this. A strategy that cannot beat simply owning
    the thing has not earned the complexity, the execution risk, or the costs.
    """
    if len(bars) < 2:
        return 0.0, 0.0
    qty = start_equity / bars[0].open
    peak = -math.inf
    worst = 0.0
    for b in bars:
        eq = qty * b.close
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak)
    return (qty * bars[-1].close / start_equity - 1.0) * 100.0, worst * 100.0


def run_backtest(
    bars: Sequence[Bar],
    strategy: Strategy,
    costs: CostModel,
    start_equity: float,
    risk_pct: float = 0.01,
    long_only: bool = True,
    max_bars_held: Optional[int] = None,
) -> BacktestResult:
    """Walk the bars once, in order, with no knowledge of the future at any point."""
    if start_equity <= 0:
        raise ValueError("start_equity must be positive")
    if not 0 < risk_pct <= 0.02:
        raise ValueError("risk_pct must be in (0, 0.02]; the 2% ceiling is not negotiable")

    res = BacktestResult(start_equity=start_equity)
    equity = start_equity
    pos: Optional[Position] = None

    for i, bar in enumerate(bars):
        # --- 1. manage an open position against THIS bar -------------------
        if pos is not None:
            exit_price, reason = _check_exit(pos, bar)

            if exit_price is None and max_bars_held and (i - pos.entry_i) >= max_bars_held:
                exit_price, reason = bar.close, "TIMEOUT"

            if exit_price is None and strategy.should_exit(i, bars[: i + 1], pos):
                # A discretionary exit decided on bar i fills at bar i's close. This is
                # slightly generous versus filling at i+1's open, and is the one place
                # the engine is not maximally pessimistic; flagged rather than hidden.
                exit_price, reason = bar.close, "SIGNAL"

            if exit_price is not None:
                filled = costs.slip(exit_price, pos.side, entering=False)
                direction = 1.0 if pos.side == "buy" else -1.0
                gross = (filled - pos.entry) * pos.qty * direction
                c = costs.entry_cost(pos.entry, pos.qty) + costs.exit_cost(filled, pos.qty)
                net = gross - c
                equity += net
                res.trades.append(BacktestTrade(
                    setup=pos.setup, side=pos.side, entry_ts=pos.entry_ts, exit_ts=bar.ts,
                    entry=pos.entry, exit=filled, qty=pos.qty, planned_risk=pos.planned_risk,
                    gross_pnl=gross, costs=c, net_pnl=net,
                    r_multiple=net / pos.planned_risk if pos.planned_risk else 0.0,
                    exit_reason=reason, bars_held=i - pos.entry_i,
                ))
                pos = None

        res.equity_curve.append(equity)

        # --- 1b. trail the stop for the NEXT bar, never for this one --------
        if pos is not None:
            updater = getattr(strategy, "update_stop", None)
            if updater is not None:
                proposed = updater(i, bars[: i + 1], pos)
                if proposed is not None and math.isfinite(proposed) and proposed > 0:
                    tighter = (proposed > pos.stop) if pos.side == "buy" else (proposed < pos.stop)
                    if tighter:
                        pos.stop = proposed

        # --- 2. look for a new entry, decided on bar i, filled on bar i+1 ---
        if pos is not None or i + 1 >= len(bars):
            continue

        sig = strategy.on_bar(i, bars[: i + 1])
        if sig is None:
            continue
        if long_only and sig.side != "buy":
            res._skip("short_blocked")
            continue

        nxt = bars[i + 1]
        entry = costs.slip(nxt.open, sig.side, entering=True)

        # The gap between the signal and the fill can invalidate the setup entirely.
        if sig.side == "buy" and entry <= sig.stop:
            res._skip("gapped_past_stop")
            continue
        if sig.side == "sell" and entry >= sig.stop:
            res._skip("gapped_past_stop")
            continue

        per_unit = abs(entry - sig.stop)
        if per_unit <= 0:
            res._skip("zero_stop_distance")
            continue

        qty = math.floor((equity * risk_pct) / per_unit)
        if qty < 1:
            res._skip("size_below_one_unit")
            continue
        if qty * entry > equity:
            qty = math.floor(equity / entry)   # cash account, no leverage
            if qty < 1:
                res._skip("insufficient_cash")
                continue

        pos = Position(
            side=sig.side, entry=entry, stop=sig.stop, target=sig.target, qty=qty,
            entry_i=i + 1, entry_ts=nxt.ts, setup=sig.setup,
            planned_risk=per_unit * qty,
        )

    # An open position at the end is closed at the last close, marked so it can be
    # excluded from analysis rather than silently counted as a completed trade.
    if pos is not None and bars:
        last = bars[-1]
        filled = costs.slip(last.close, pos.side, entering=False)
        direction = 1.0 if pos.side == "buy" else -1.0
        gross = (filled - pos.entry) * pos.qty * direction
        c = costs.entry_cost(pos.entry, pos.qty) + costs.exit_cost(filled, pos.qty)
        net = gross - c
        equity += net
        res.trades.append(BacktestTrade(
            setup=pos.setup, side=pos.side, entry_ts=pos.entry_ts, exit_ts=last.ts,
            entry=pos.entry, exit=filled, qty=pos.qty, planned_risk=pos.planned_risk,
            gross_pnl=gross, costs=c, net_pnl=net,
            r_multiple=net / pos.planned_risk if pos.planned_risk else 0.0,
            exit_reason="OPEN_AT_END", bars_held=len(bars) - 1 - pos.entry_i,
        ))
        res.equity_curve[-1] = equity

    return res


def _check_exit(pos: Position, bar: Bar) -> tuple[Optional[float], str]:
    """Stop and target resolution for one bar, resolving every ambiguity against us.

    Order of checks matters and is deliberate:
      a) gap through the stop  -> fill at the open, worse than the stop price
      b) gap through the target -> fill at the open, better; gaps cut both ways
      c) both levels inside the bar's range -> assume the STOP was hit first
      d) otherwise whichever level the range touched
    """
    long = pos.side == "buy"

    if long:
        if bar.open <= pos.stop:
            return bar.open, "STOP_GAP"
        if pos.target is not None and bar.open >= pos.target:
            return bar.open, "TARGET_GAP"
        hit_stop = bar.low <= pos.stop
        hit_target = pos.target is not None and bar.high >= pos.target
    else:
        if bar.open >= pos.stop:
            return bar.open, "STOP_GAP"
        if pos.target is not None and bar.open <= pos.target:
            return bar.open, "TARGET_GAP"
        hit_stop = bar.high >= pos.stop
        hit_target = pos.target is not None and bar.low <= pos.target

    if hit_stop:
        return pos.stop, "STOP"        # wins ties against us, by design
    if hit_target:
        return pos.target, "TARGET"
    return None, ""
