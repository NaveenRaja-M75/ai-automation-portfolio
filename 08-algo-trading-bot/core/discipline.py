"""Constraints that a single order cannot see: portfolio correlation and operator behaviour.

`risk_gate` validates one order's arithmetic in isolation. That is not enough, for two
reasons this module addresses.

PORTFOLIO. Three "independent" 1% bets on SPY long, QQQ long, and BTC long is not three
bets. It is one risk-on bet at 3%, and it loses 3% on the same afternoon. Risk has to be
budgeted per correlation group, not per ticker.

BEHAVIOUR. The documented failure modes of discretionary traders are revenge trading after
a loss, chasing extended moves, widening stops to avoid booking a loss, and overtrading. A
bot does not feel any of these, but its operator does, and the operator is the one who
restarts it, overrides it, and bumps the size after a bad morning. Encoding the rules here
means the discipline survives the operator's worst afternoon, which is the only time it
matters.

Every rule below is a hard rejection, not a warning. A warning is something a person
overrides at exactly the moment they should not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .risk_gate import OrderRejected

# Instruments that tend to move together. Risk is budgeted per group. These are coarse on
# purpose: a rough grouping applied consistently beats a precise correlation matrix that
# silently goes stale, and correlations converge to 1 in exactly the crisis where a
# fine-grained estimate would have told you they were 0.4.
CORRELATION_GROUPS: dict[str, str] = {
    "SPY": "EQUITY_BETA", "QQQ": "EQUITY_BETA", "NIFTY": "EQUITY_BETA",
    "BANKNIFTY": "EQUITY_BETA", "USO": "ENERGY",
    "BTCUSDT": "CRYPTO", "ETHUSDT": "CRYPTO",
    "XAUUSD": "METALS", "XAGUSD": "METALS", "GOLDBEES": "METALS", "GLD": "METALS",
    "EURUSD": "USD_SHORT", "GBPUSD": "USD_SHORT", "AUDUSD": "USD_SHORT",
    "USDJPY": "USD_LONG", "USDCHF": "USD_LONG",
    "EURGBP": "FX_CROSS", "EURJPY": "FX_CROSS",
}


def group_of(symbol: str) -> str:
    """Unknown symbols get their own group. Fail-safe direction: an unmapped symbol is
    treated as uncorrelated, so add new instruments to the map deliberately."""
    return CORRELATION_GROUPS.get(symbol.upper(), f"UNMAPPED:{symbol.upper()}")


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    side: str
    risk: float  # account currency still at risk between price and stop

    @property
    def group(self) -> str:
        return group_of(self.symbol)

    @property
    def signed_group(self) -> str:
        """USD_SHORT long and USD_LONG short are the same bet. Direction is part of the
        exposure, so the group key carries the side."""
        return f"{self.group}:{'L' if self.side.lower() == 'buy' else 'S'}"


@dataclass(frozen=True)
class DisciplineLimits:
    max_total_risk_pct: float = 0.06       # all open risk combined
    max_group_risk_pct: float = 0.02       # per correlated, directional group
    max_open_positions: int = 5
    max_trades_per_day: int = 10           # overtrading guard
    loss_cooldown_minutes: float = 15.0    # the 15-minute rule, enforced
    max_consecutive_losses: int = 3        # stop for the day and review
    max_entry_extension_atr: float = 1.5   # FOMO guard, in ATRs past the reference


@dataclass
class SessionState:
    """Mutable per-session state. Persisted to the journal so a restart does not reset
    the operator's cooldown, which is the obvious way to defeat every rule here."""
    open_positions: list[OpenPosition] = field(default_factory=list)
    trades_today: int = 0
    consecutive_losses: int = 0
    last_loss_at: Optional[datetime] = None
    last_trade_risk: Optional[float] = None

    def record_close(self, net_pnl: float, closed_at: datetime, risk: float) -> None:
        if net_pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_at = closed_at
        else:
            self.consecutive_losses = 0
        self.last_trade_risk = risk


def check_discipline(
    symbol: str,
    side: str,
    risk: float,
    equity: float,
    state: SessionState,
    limits: DisciplineLimits,
    now: datetime,
    entry: Optional[float] = None,
    reference_price: Optional[float] = None,
    atr: Optional[float] = None,
) -> None:
    """Raise OrderRejected if any portfolio or behavioural rule blocks this order.

    reference_price and atr enable the extension guard: pass the level the setup was
    built around (a moving average, a breakout level, the range the price left) and the
    current ATR. Omitting them skips that check, so a strategy that wants the guard must
    supply the reference deliberately.
    """
    if equity <= 0 or not math.isfinite(equity):
        raise OrderRejected("DISCIPLINE", "equity must be positive and finite")
    if risk < 0 or not math.isfinite(risk):
        raise OrderRejected("DISCIPLINE", "risk must be non-negative and finite")

    # --- behaviour ---------------------------------------------------------

    # Cooldown after a loss. The window where revenge trades happen is the few minutes
    # right after the loss, so the fix is simply to make trading impossible then.
    if state.last_loss_at is not None:
        elapsed = (now - state.last_loss_at).total_seconds() / 60.0
        if elapsed < limits.loss_cooldown_minutes:
            raise OrderRejected(
                "COOLDOWN",
                f"{elapsed:.1f} min since last loss, {limits.loss_cooldown_minutes:.0f} required",
            )

    if state.consecutive_losses >= limits.max_consecutive_losses:
        raise OrderRejected(
            "LOSS_STREAK",
            f"{state.consecutive_losses} consecutive losses; stop and review before resuming",
        )

    # Revenge sizing. After a loss, the next trade may not be larger than the one that
    # lost. This is the single most destructive pattern in retail trading and it is one
    # comparison to prevent.
    if (state.consecutive_losses > 0 and state.last_trade_risk is not None
            and risk > state.last_trade_risk + 1e-9):
        raise OrderRejected(
            "REVENGE_SIZING",
            f"risk {risk:.2f} exceeds the losing trade's {state.last_trade_risk:.2f}; "
            "size may not increase after a loss",
        )

    if state.trades_today >= limits.max_trades_per_day:
        raise OrderRejected(
            "OVERTRADING", f"{state.trades_today} trades today, cap {limits.max_trades_per_day}"
        )

    # Extension guard. Entering far past the level the setup was built on is chasing,
    # and it is the entry with the worst reward-to-risk in any strategy.
    if entry is not None and reference_price is not None and atr is not None:
        if not (math.isfinite(atr) and atr > 0):
            raise OrderRejected("DISCIPLINE", f"atr must be positive and finite, got {atr!r}")
        extension = (entry - reference_price) / atr
        signed = extension if side.lower() == "buy" else -extension
        if signed > limits.max_entry_extension_atr:
            raise OrderRejected(
                "CHASING",
                f"entry is {signed:.2f} ATR past the reference, "
                f"cap {limits.max_entry_extension_atr:.2f}",
            )

    # --- portfolio ---------------------------------------------------------

    if len(state.open_positions) >= limits.max_open_positions:
        raise OrderRejected(
            "MAX_POSITIONS",
            f"{len(state.open_positions)} open, cap {limits.max_open_positions}",
        )

    incoming = OpenPosition(symbol, side, risk)
    group_key = incoming.signed_group
    group_risk = sum(p.risk for p in state.open_positions if p.signed_group == group_key)
    if group_risk + risk > equity * limits.max_group_risk_pct + 1e-9:
        raise OrderRejected(
            "CORRELATION",
            f"{group_key} would hold {group_risk + risk:.2f} of risk, cap "
            f"{equity * limits.max_group_risk_pct:.2f} ({limits.max_group_risk_pct:.1%} of equity)",
        )

    total_risk = sum(p.risk for p in state.open_positions)
    if total_risk + risk > equity * limits.max_total_risk_pct + 1e-9:
        raise OrderRejected(
            "TOTAL_EXPOSURE",
            f"open risk would reach {total_risk + risk:.2f}, cap "
            f"{equity * limits.max_total_risk_pct:.2f} ({limits.max_total_risk_pct:.1%} of equity)",
        )


def assert_stop_not_widened(side: str, entry: float, old_stop: float, new_stop: float) -> None:
    """A stop may move toward profit and never away from it.

    This is loss aversion expressed as one comparison. Moving a stop further from entry
    to avoid booking a loss is the behaviour that converts a planned 1% loss into an
    account-ending one, and it always feels reasonable at the time.
    """
    if side.lower() == "buy":
        if new_stop < old_stop - 1e-12:
            raise OrderRejected("STOP_WIDENED", f"long stop moved down {old_stop} -> {new_stop}")
    elif side.lower() == "sell":
        if new_stop > old_stop + 1e-12:
            raise OrderRejected("STOP_WIDENED", f"short stop moved up {old_stop} -> {new_stop}")
    else:
        raise OrderRejected("STOP_WIDENED", f"unknown side {side!r}")


# --- ATR-based sizing ------------------------------------------------------

def stop_from_atr(entry: float, atr: float, multiple: float, side: str) -> float:
    """Stop placed a volatility-scaled distance from entry.

    A fixed percentage stop is too tight in a volatile market and too wide in a quiet
    one, so it produces a position size that varies with volatility in exactly the wrong
    direction. Scaling the stop by ATR keeps the money at risk constant while letting the
    position breathe as much as the instrument currently requires.
    """
    for label, v in (("entry", entry), ("atr", atr), ("multiple", multiple)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise OrderRejected("ATR", f"{label} must be a positive finite number, got {v!r}")
    dist = atr * multiple
    if side.lower() == "buy":
        stop = entry - dist
    elif side.lower() == "sell":
        stop = entry + dist
    else:
        raise OrderRejected("ATR", f"side must be buy or sell, got {side!r}")
    if stop <= 0:
        raise OrderRejected("ATR", f"computed stop {stop:.6f} is not positive")
    return stop


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> float:
    """Wilder's Average True Range over the last `period` bars.

    True range uses the previous close, so it captures overnight and weekend gaps that a
    simple high-minus-low would miss. That matters most for the instruments where gaps
    are the actual risk.
    """
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise OrderRejected("ATR", "highs, lows, and closes must be the same length")
    if n < period + 1:
        raise OrderRejected("ATR", f"need {period + 1} bars for ATR({period}), got {n}")
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, n)
    ]
    # Wilder smoothing, seeded with the simple mean of the first `period` true ranges.
    val = math.fsum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    if not math.isfinite(val) or val <= 0:
        raise OrderRejected("ATR", f"computed ATR is not usable: {val!r}")
    return val
