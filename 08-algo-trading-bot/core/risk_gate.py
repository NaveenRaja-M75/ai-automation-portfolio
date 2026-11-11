"""Deterministic pre-transmission order gate.

Every order intent passes through validate_order() immediately before it reaches a
broker adapter. There is no LLM, no network call, and no clock ambiguity inside the
decision path, so the whole module is unit-testable and its behaviour is reproducible.

The gate is fail-secure: anything unexpected raises OrderRejected. There is no code
path in which an error results in a transmitted order.

Gate order (cheapest and hardest first):
  1. KILL SWITCH      file flag present -> reject everything
  2. DAILY LOSS       day P&L past the limit -> trip the kill switch, then reject
  3. FRESHNESS        signal older than max_signal_age_s -> reject
  4. EQUITY FRESHNESS equity quote older than max_equity_age_s -> reject
  5. STOP-LOSS        missing, wrong side, or outside the distance band -> reject
  6. SIZING           risk above the cap -> reject (never silently resized)
  7. IDEMPOTENCY      missing key -> reject
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .sizing import (
    FxRates,
    Instrument,
    SizingError,
    margin_required,
    notional_in_account,
    risk_in_account,
)

# Immutable ceilings. Config may lower these; nothing may raise them.
RISK_CAP_HARD_MAX = 0.02
MAX_STOP_DIST_PCT = 0.10      # fat-finger guard
MIN_STOP_DIST_PCT = 0.0005    # guards against a near-zero divisor in sizing


class OrderRejected(Exception):
    """Raised by any gate. Carrying the gate name makes the audit log greppable."""

    def __init__(self, gate: str, reason: str) -> None:
        self.gate = gate
        self.reason = reason
        super().__init__(f"[{gate}] {reason}")


@dataclass(frozen=True)
class EquityQuote:
    """Account equity together with the moment it was read from the broker.

    The harness forbids sizing against a cached or model-supplied equity figure. Making
    equity a timestamped value object means a stale figure cannot reach the sizing gate
    by accident: it has to carry a fetch time, and the gate checks it.
    """

    value: float
    fetched_at: datetime
    source: str  # broker adapter id, recorded in the audit log


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    entry: float
    stop_loss: Optional[float]
    signal_ts: datetime
    idempotency_key: str
    take_profit: Optional[float] = None


@dataclass(frozen=True)
class RiskLimits:
    risk_pct: float = 0.01
    daily_loss_limit_pct: float = 0.03
    max_signal_age_s: float = 5.0
    max_equity_age_s: float = 30.0
    # Gross exposure ceiling across the whole account, as a multiple of equity. 3x is
    # deliberately far below the 30x a retail forex broker will hand you: broker leverage
    # is a marketing number, not a risk limit.
    max_gross_leverage: float = 3.0
    kill_switch_path: Path = field(default_factory=lambda: Path("KILL_SWITCH"))

    def effective_risk_pct(self) -> float:
        """The 2% ceiling is immutable. Values below 1% are permitted deliberately;
        see CLAUDE.md section 5 for why the harness's 1% floor is not enforced."""
        return min(self.risk_pct, RISK_CAP_HARD_MAX)


def _finite(value: object, label: str) -> float:
    """Reject anything that is not a real, finite number before it reaches arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrderRejected("SCHEMA", f"{label} must be numeric, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise OrderRejected("SCHEMA", f"{label} must be finite, got {out}")
    return out


def _aware(ts: object, label: str) -> datetime:
    if not isinstance(ts, datetime):
        raise OrderRejected("SCHEMA", f"{label} must be a datetime")
    if ts.tzinfo is None:
        raise OrderRejected("SCHEMA", f"{label} must be timezone-aware")
    return ts


def trip_kill_switch(limits: RiskLimits, reason: str) -> None:
    """Halt all routing. Deliberately a filesystem flag, so a human can inspect it,
    and so clearing it is a conscious act rather than a process restart."""
    limits.kill_switch_path.write_text(
        f"{datetime.now(timezone.utc).isoformat()} {reason}\n", encoding="utf-8"
    )


def validate_order(
    intent: OrderIntent,
    equity: EquityQuote,
    limits: RiskLimits,
    day_pnl: float,
    day_start_equity: float,
    now: Optional[datetime] = None,
    audit: Optional[Callable[[dict], None]] = None,
    instrument: Optional["Instrument"] = None,
    fx: Optional["FxRates"] = None,
    account_ccy: str = "USD",
    free_margin: Optional[float] = None,
) -> OrderIntent:
    """Return the intent unchanged if every gate passes, else raise OrderRejected.

    day_pnl is realized plus unrealized profit and loss for the session, negative for a
    loss. now is injectable for tests; production passes None and uses the system clock,
    which must be NTP-synced.

    instrument and fx are required for anything whose quote currency differs from
    account_ccy: forex, metals, and crypto. Omitting them for such an instrument is a
    rejection rather than a fallback, because the fallback would silently compute risk in
    the wrong currency. free_margin, when supplied, enables the margin gate.
    """
    now = now or datetime.now(timezone.utc)
    record = {
        "ts": now.isoformat(),
        "symbol": getattr(intent, "symbol", "?"),
        "idempotency_key": getattr(intent, "idempotency_key", "?"),
        "equity_source": getattr(equity, "source", "?"),
    }

    try:
        decision = _run_gates(intent, equity, limits, day_pnl, day_start_equity, now,
                              instrument, fx, account_ccy, free_margin)
    except OrderRejected as exc:
        record.update(decision="REJECT", gate=exc.gate, reason=exc.reason)
        if audit:
            audit(record)
        raise
    except Exception as exc:  # fail-secure: an unexpected error is never a pass
        record.update(decision="REJECT", gate="UNEXPECTED", reason=repr(exc))
        if audit:
            audit(record)
        raise OrderRejected("UNEXPECTED", repr(exc)) from exc

    record.update(decision="PASS", gate="ALL", risk=decision)
    if audit:
        audit(record)
    return intent


def _run_gates(
    intent: OrderIntent,
    equity: EquityQuote,
    limits: RiskLimits,
    day_pnl: float,
    day_start_equity: float,
    now: datetime,
    instrument: Optional["Instrument"] = None,
    fx: Optional["FxRates"] = None,
    account_ccy: str = "USD",
    free_margin: Optional[float] = None,
) -> float:
    """All gates in order. Returns the computed risk in account currency."""

    # 1 — KILL SWITCH
    if limits.kill_switch_path.exists():
        raise OrderRejected("KILL_SWITCH", "kill switch engaged; routing halted")

    # 2 — DAILY LOSS CIRCUIT BREAKER
    day_start_equity = _finite(day_start_equity, "day_start_equity")
    day_pnl = _finite(day_pnl, "day_pnl")
    if day_start_equity <= 0:
        raise OrderRejected("DAILY_LOSS", "day_start_equity must be positive")
    loss_limit = day_start_equity * limits.daily_loss_limit_pct
    if day_pnl <= -loss_limit:
        trip_kill_switch(limits, f"daily loss {day_pnl:.2f} breached limit {-loss_limit:.2f}")
        raise OrderRejected(
            "DAILY_LOSS",
            f"day P&L {day_pnl:.2f} at or past limit {-loss_limit:.2f}; kill switch tripped",
        )

    # 3 — SIGNAL FRESHNESS
    signal_ts = _aware(intent.signal_ts, "signal_ts")
    age = (now - signal_ts).total_seconds()
    if age > limits.max_signal_age_s:
        raise OrderRejected("FRESHNESS", f"signal {age:.2f}s old, max {limits.max_signal_age_s}s")
    if age < -1.0:
        raise OrderRejected("FRESHNESS", f"signal timestamped {-age:.2f}s in the future")

    # 4 — EQUITY QUOTE FRESHNESS
    fetched_at = _aware(equity.fetched_at, "equity.fetched_at")
    equity_value = _finite(equity.value, "equity.value")
    equity_age = (now - fetched_at).total_seconds()
    if equity_age > limits.max_equity_age_s:
        raise OrderRejected(
            "EQUITY_STALE", f"equity quote {equity_age:.2f}s old, max {limits.max_equity_age_s}s"
        )
    if equity_value <= 0:
        raise OrderRejected("EQUITY_STALE", "equity must be positive")

    # 5 — STOP-LOSS PRESENCE, SIDE, AND DISTANCE BAND
    side = str(intent.side).lower().strip()
    if side not in ("buy", "sell"):
        raise OrderRejected("SCHEMA", f"side must be buy or sell, got {intent.side!r}")
    entry = _finite(intent.entry, "entry")
    qty = _finite(intent.qty, "qty")
    if entry <= 0 or qty <= 0:
        raise OrderRejected("SCHEMA", "entry and qty must be positive")
    if intent.stop_loss is None:
        raise OrderRejected("STOP_LOSS", "missing stop-loss; naked positions are never permitted")
    stop = _finite(intent.stop_loss, "stop_loss")
    if stop <= 0:
        raise OrderRejected("STOP_LOSS", "stop-loss must be positive")
    if side == "buy" and stop >= entry:
        raise OrderRejected("STOP_LOSS", f"long entry {entry} with stop {stop} not below it")
    if side == "sell" and stop <= entry:
        raise OrderRejected("STOP_LOSS", f"short entry {entry} with stop {stop} not above it")
    # Band is per instrument when one is supplied, because a single global band cannot
    # be correct for both a 5 pip forex stop and an equity stop. See Instrument docs.
    lo = instrument.min_stop_pct if instrument else MIN_STOP_DIST_PCT
    hi = instrument.max_stop_pct if instrument else MAX_STOP_DIST_PCT
    dist_pct = abs(entry - stop) / entry
    if dist_pct > hi:
        raise OrderRejected("STOP_LOSS", f"stop {dist_pct:.2%} away, cap {hi:.2%}")
    if dist_pct < lo:
        raise OrderRejected("STOP_LOSS", f"stop {dist_pct:.4%} away, floor {lo:.4%}")

    # 6 — POSITION RISK CAP (never auto-resized; resizing is a human decision)
    cap_pct = limits.effective_risk_pct()
    if instrument is None:
        # Same-currency cash instrument only. Anything else must supply an instrument,
        # because the naive formula would report risk in the wrong currency.
        risk = abs(entry - stop) * qty
    else:
        if fx is None:
            raise OrderRejected("SIZING", f"{instrument.symbol} requires fx rates")
        try:
            risk = risk_in_account(instrument, entry, stop, qty, fx, account_ccy, now)
        except SizingError as exc:
            raise OrderRejected("SIZING", str(exc)) from exc

    if risk > equity_value * cap_pct:
        raise OrderRejected(
            "SIZING",
            f"risk {risk:.2f} {account_ccy} exceeds {cap_pct:.2%} of equity "
            f"{equity_value:.2f} ({equity_value * cap_pct:.2f})",
        )

    # 6b — MARGIN AND LEVERAGE. A position can sit inside the risk cap and still be
    # unopenable, or be one gap away from a margin call, so exposure is bounded
    # separately from stop distance.
    if instrument is not None and fx is not None:
        try:
            needed = margin_required(instrument, entry, qty, fx, account_ccy)
            exposure = notional_in_account(instrument, entry, qty, fx, account_ccy)
        except SizingError as exc:
            raise OrderRejected("MARGIN", str(exc)) from exc
        if free_margin is not None and needed > free_margin:
            raise OrderRejected(
                "MARGIN", f"needs {needed:.2f} {account_ccy}, free margin {free_margin:.2f}"
            )
        if exposure > equity_value * limits.max_gross_leverage:
            raise OrderRejected(
                "MARGIN",
                f"exposure {exposure:.2f} is {exposure / equity_value:.1f}x equity, "
                f"cap {limits.max_gross_leverage:.1f}x",
            )

    # 7 — IDEMPOTENCY
    if not str(intent.idempotency_key).strip():
        raise OrderRejected("IDEMPOTENCY", "missing idempotency key")

    return risk
