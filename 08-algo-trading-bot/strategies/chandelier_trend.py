"""Trend following with a chandelier exit. Candidate 2 for GOLDBEES daily.

Exactly one thing changed from `TrendFollow`: how the position is exited.

The diagnosis from candidate 1 was that a 22% win rate paired with only a 3.94 payoff is a
trend system whose winners are being cut. Its `close < fast SMA` exit fires on the first
pullback, which in a trend is noise rather than a reversal, so the system paid for many
small losses and never held anything long enough to collect the rare large move that
makes trend following work.

A chandelier exit trails from the highest high SINCE ENTRY, minus a multiple of ATR. It
ratchets up and never loosens, so it gives a trend room to breathe while still locking in
progress. The tradeoff is deliberate and it will show up in the numbers: giving back more
of each move at the end, in exchange for staying in the moves that matter.

The entry is untouched, on purpose. Changing two things at once means learning nothing
from either.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from core.backtest import Bar, Position, Signal
from core.discipline import atr
from strategies.trend_follow import _sma


class ChandelierTrend:
    def __init__(self, fast: int = 20, slow: int = 50, atr_period: int = 14,
                 atr_mult: float = 2.0, trail_mult: float = 4.0) -> None:
        if fast >= slow:
            raise ValueError("fast period must be shorter than slow")
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.atr_mult = atr_mult      # initial stop distance
        self.trail_mult = trail_mult  # trailing distance from the running high

    def __repr__(self) -> str:
        return (f"ChandelierTrend(fast={self.fast}, slow={self.slow}, "
                f"atr_mult={self.atr_mult}, trail_mult={self.trail_mult})")

    @property
    def warmup(self) -> int:
        return max(self.slow, self.atr_period + 1) + 1

    def _atr(self, bars: Sequence[Bar], i: int) -> Optional[float]:
        window = bars[max(0, i - self.atr_period - 5): i + 1]
        try:
            return atr([b.high for b in window], [b.low for b in window],
                       [b.close for b in window], period=self.atr_period)
        except Exception:
            return None

    def _in_trend(self, bars: Sequence[Bar], i: int) -> Optional[bool]:
        fast = _sma(bars, i, self.fast)
        slow = _sma(bars, i, self.slow)
        if fast is None or slow is None:
            return None
        return bars[i].close > slow and fast > slow

    def on_bar(self, i: int, bars: Sequence[Bar]) -> Optional[Signal]:
        if i < self.warmup:
            return None
        now = self._in_trend(bars, i)
        prev = self._in_trend(bars, i - 1)
        if not now or prev is None or prev:
            return None

        a = self._atr(bars, i)
        if a is None:
            return None
        stop = bars[i].close - self.atr_mult * a
        if stop <= 0:
            return None
        return Signal(side="buy", stop=stop, setup="chandelier_trend",
                      reference=_sma(bars, i, self.fast))

    def should_exit(self, i: int, bars: Sequence[Bar], position: Position) -> bool:
        """No discretionary exit. The trailing stop is the only way out, which is the
        whole point of the change being tested."""
        return False

    def update_stop(self, i: int, bars: Sequence[Bar], position: Position) -> Optional[float]:
        """Highest high since entry, minus trail_mult x ATR.

        The engine discards any level that would loosen the stop, so the ratchet is
        enforced by the engine rather than trusted to this method.
        """
        a = self._atr(bars, i)
        if a is None:
            return None
        start = max(0, position.entry_i)
        if start > i:
            return None
        running_high = max(b.high for b in bars[start: i + 1])
        proposed = running_high - self.trail_mult * a
        return proposed if math.isfinite(proposed) and proposed > 0 else None
