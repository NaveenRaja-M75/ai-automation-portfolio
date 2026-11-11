"""Long-only trend following. The baseline candidate, chosen for GOLDBEES daily bars.

Rules, in full:

  ENTRY   Close is above the slow SMA and the fast SMA is above the slow SMA, and that
          was NOT true on the previous bar. Entering only on the transition avoids
          re-entering every single bar of a trend that is already underway.
  STOP    entry minus atr_mult x ATR(14). Volatility-scaled, so the stop is wide when
          gold is wild and tight when it is quiet, keeping money at risk constant.
  EXIT    Close falls below the fast SMA, or the stop is hit.
  TARGET  None. Trend following makes its money on the few large moves; capping them
          with a fixed target is what turns a positive-expectancy trend system into a
          negative one.

There is nothing clever here, and that is deliberate. It is a baseline to prove the
pipeline and give the scorecard something real to judge, not a claim of edge.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from core.backtest import Bar, Position, Signal
from core.discipline import atr


def _sma(bars: Sequence[Bar], end: int, period: int) -> Optional[float]:
    """Mean close over the `period` bars ending at index `end`, inclusive."""
    if end + 1 < period:
        return None
    return math.fsum(b.close for b in bars[end + 1 - period: end + 1]) / period


class TrendFollow:
    def __init__(self, fast: int = 20, slow: int = 100,
                 atr_period: int = 14, atr_mult: float = 2.5) -> None:
        if fast >= slow:
            raise ValueError("fast period must be shorter than slow")
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.atr_mult = atr_mult

    def __repr__(self) -> str:
        return (f"TrendFollow(fast={self.fast}, slow={self.slow}, "
                f"atr_mult={self.atr_mult})")

    @property
    def warmup(self) -> int:
        return max(self.slow, self.atr_period + 1) + 1

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
            return None  # only on the transition into trend

        window = bars[i - self.atr_period - 5: i + 1]
        try:
            a = atr([b.high for b in window], [b.low for b in window],
                    [b.close for b in window], period=self.atr_period)
        except Exception:
            return None

        close = bars[i].close
        stop = close - self.atr_mult * a
        if stop <= 0:
            return None
        return Signal(side="buy", stop=stop, setup="trend_follow",
                      reference=_sma(bars, i, self.fast))

    def should_exit(self, i: int, bars: Sequence[Bar], position: Position) -> bool:
        fast = _sma(bars, i, self.fast)
        return fast is not None and bars[i].close < fast
