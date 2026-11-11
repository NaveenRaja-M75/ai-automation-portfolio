"""Turns a probability into buy / sell / hold. Pure function, no I/O.

Three fixes over the original jev-trader loop, each aimed at a documented failure:
  - hysteresis: open at ENTRY_PROB, close only below EXIT_PROB. The public copycat
    that lost ~$31.7k flipped BUY/SELL every few blocks; a gap between the two
    thresholds makes that impossible.
  - cost filter: no entry unless the typical move over the horizon beats the full
    round-trip cost (spread + 2 fees + TDS) by MIN_EDGE_MULTIPLE.
  - one position per symbol, a time exit at the horizon, a cooldown after every exit.
  - expected value: entries need (2*p_cal - 1) * typical_move > round-trip cost, where
    p_cal is the signal's MEASURED hit rate at that confidence level, not what it claims.
  - patient exits: a "signal faded" exit needs MIN_HOLD_S held and EXIT_CONFIRM fades in a
    row. The first paper session closed two 5-minute trades after 15 s on one noisy flip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from .features import Features

MEAN_ABS_NORMAL = math.sqrt(2 / math.pi)   # E|X| for X ~ N(0, 1)


@dataclass(frozen=True)
class Decision:
    action: str            # buy | sell | hold
    reason: str
    qty: float = 0.0
    stop: float | None = None


def round_trip_cost(cfg: Config, spread_pct: float) -> float:
    return spread_pct + 2 * cfg.fee_pct + (cfg.tds_pct if cfg.apply_tds else 0.0)


def expected_value(cfg: Config, f: Features, p_cal: float) -> float:
    """Expected net return of a long over the trade horizon, as a fraction. Symmetric
    move model: win the typical move with p_cal, lose it with 1 - p_cal, pay costs always."""
    move = f.horizon_vol(cfg.trade_horizon_s) * MEAN_ABS_NORMAL
    return (2 * p_cal - 1) * move - round_trip_cost(cfg, f.spread_pct)


def decide(cfg: Config, f: Features, p_up: float | None, holding: bool, entry_ts: float | None,
           last_exit_ts: float, now: float, equity: float, cash: float,
           p_cal: float | None = None, fades: int = 0) -> Decision:
    """p_up is what the signal says; p_cal is what its track record says that is worth.
    fades is how many decisions in a row (before this one) already read as faded."""
    h = cfg.trade_horizon_s
    if holding:
        held = now - entry_ts if entry_ts is not None else 0.0
        if entry_ts is not None and held >= h:
            return Decision("sell", "time exit: horizon reached")
        if p_up is not None and p_up <= cfg.exit_prob:
            if held < cfg.min_hold_s:
                return Decision("hold", f"fade ignored: held {held:.0f}s < min {cfg.min_hold_s:.0f}s")
            if fades + 1 < cfg.exit_confirm:
                return Decision("hold", f"fade {fades + 1}/{cfg.exit_confirm}: waiting for confirmation")
            return Decision("sell", f"signal faded {cfg.exit_confirm}x: p_up {p_up:.2f} <= {cfg.exit_prob:.2f}")
        return Decision("hold", "holding")

    if now - last_exit_ts < cfg.cooldown_s:
        return Decision("hold", "cooldown")
    if p_up is None:
        return Decision("hold", "no signal this round")
    if p_up < cfg.entry_prob:
        return Decision("hold", f"p_up {p_up:.2f} below entry {cfg.entry_prob:.2f}")

    sigma = f.horizon_vol(h)
    expected_move = sigma * MEAN_ABS_NORMAL
    cost = round_trip_cost(cfg, f.spread_pct)
    if expected_move < cost * cfg.min_edge_multiple:
        return Decision("hold", f"costs {cost:.3%} exceed typical move {expected_move:.3%}"
                                f" / {cfg.min_edge_multiple}x")

    if cfg.require_positive_ev:
        pc = 0.5 if p_cal is None else p_cal
        ev = expected_value(cfg, f, pc)
        if ev <= 0:
            return Decision("hold", f"negative expected value {ev:+.3%}: track record says p={pc:.2f}, "
                                    f"not {p_up:.2f}")

    stop_dist = min(max(cfg.stop_vol_multiple * sigma, 0.001), 0.09)
    stop = f.ask * (1 - stop_dist)
    per_unit_risk = f.ask - stop
    qty = min(equity * cfg.risk_pct / per_unit_risk,
              equity * cfg.max_notional_pct / f.ask,
              cash * 0.98 / f.ask)          # 2% headroom for fees and book walk
    if qty <= 0 or not math.isfinite(qty):
        return Decision("hold", "no cash")
    return Decision("buy", f"p_up {p_up:.2f} >= {cfg.entry_prob:.2f}", qty=qty, stop=stop)
