#!/usr/bin/env python3
"""Command-line front end for the trading system, manual stage.

No broker is connected and nothing here places an order. What it does is put every risk
and discipline rule in front of a trade you are about to take by hand, record the ones you
take, and score the results honestly.

That is deliberately the right tool for right now. Before a strategy has an evidence base,
the useful thing is not automated execution, it is a referee: something that says no to
the trade you should not take and keeps a record you cannot flatter.

    python bot.py status
    python bot.py check --symbol GOLDBEES --side buy --entry 82.50 --stop 80.85 --qty 100
    python bot.py open  --symbol GOLDBEES --side buy --entry 82.50 --stop 80.85 --qty 100 \
                        --setup ema_pullback --regime TREND_UP
    python bot.py close --key <key> --exit 85.20 --reason TARGET --costs 32.50
    python bot.py report
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.discipline import DisciplineLimits, OpenPosition, SessionState, check_discipline
from core.journal import Journal, TradeOpen
from core.metrics import MIN_SAMPLE_FOR_SIGNIFICANCE, scorecard
from core.risk_gate import EquityQuote, OrderIntent, OrderRejected, RiskLimits, validate_order
from core.sizing import INSTRUMENTS, FxRates

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
JOURNAL_PATH = ROOT / "data" / "journal.db"

DEFAULT_CONFIG = {
    "stage": "PAPER",
    "account_ccy": "INR",
    "equity": 100000.0,
    "risk_pct": 0.01,
    "_comment": (
        "equity is operator-supplied at this stage and is recorded in the audit log as "
        "source=manual. Live stages must replace it with a broker fetch: the harness "
        "forbids sizing against a figure the system cannot verify."
    ),
    "fx_rates": {"USDINR": 87.50, "EURUSD": 1.0850, "USDJPY": 150.00},
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        print(f"Created {CONFIG_PATH.name} with defaults. Set your equity, then re-run.\n")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def build_state(jrn: Journal, now: datetime) -> SessionState:
    """Rebuild discipline state from the journal rather than from memory.

    Deriving it from the recorded trades means a restart cannot clear a cooldown or reset
    a losing streak, which is otherwise the obvious way to defeat every behavioural rule.
    """
    state = SessionState()
    for t in jrn.open_trades():
        risk = abs(t["entry"] - t["stop_loss"]) * t["qty"]
        state.open_positions.append(OpenPosition(t["symbol"], t["side"], risk))

    for t in jrn.last_closed(limit=50):
        if t["net_pnl"] is None:
            continue
        if t["net_pnl"] < 0:
            state.consecutive_losses += 1
            if state.last_loss_at is None:
                state.last_loss_at = datetime.fromisoformat(t["closed_at"])
        else:
            break  # a win ends the streak; last_closed is newest-first
        if state.last_trade_risk is None:
            state.last_trade_risk = t["planned_risk"]

    if state.last_trade_risk is None:
        recent = jrn.last_closed(limit=1)
        if recent:
            state.last_trade_risk = recent[0]["planned_risk"]

    state.trades_today = jrn.count_opened_on(now.date().isoformat())
    return state


def run_gates(cfg: dict, jrn: Journal, args, now: datetime) -> float:
    """Run every gate. Returns the computed risk, or raises OrderRejected."""
    symbol = args.symbol.upper()
    inst = INSTRUMENTS.get(symbol)
    fx = FxRates(cfg.get("fx_rates", {}), as_of=now)
    equity = EquityQuote(float(cfg["equity"]), now, "manual")
    limits = RiskLimits(risk_pct=float(cfg.get("risk_pct", 0.01)),
                        kill_switch_path=ROOT / "KILL_SWITCH")

    intent = OrderIntent(
        symbol=symbol, side=args.side, qty=float(args.qty), entry=float(args.entry),
        stop_loss=float(args.stop), signal_ts=now,
        idempotency_key=args.key or f"{now:%Y%m%d-%H%M%S}-{symbol}-{args.side}",
        take_profit=float(args.target) if args.target else None,
    )

    validate_order(
        intent, equity, limits, day_pnl=0.0, day_start_equity=float(cfg["equity"]),
        now=now, audit=jrn.record_gate_decision, instrument=inst, fx=fx,
        account_ccy=cfg["account_ccy"],
    )

    risk = abs(intent.entry - intent.stop_loss) * intent.qty
    if inst is not None:
        from core.sizing import risk_in_account
        risk = risk_in_account(inst, intent.entry, intent.stop_loss, intent.qty,
                               fx, cfg["account_ccy"])

    check_discipline(
        symbol, args.side, risk, float(cfg["equity"]), build_state(jrn, now),
        DisciplineLimits(), now,
        entry=float(args.entry),
        reference_price=float(args.reference) if args.reference else None,
        atr=float(args.atr) if args.atr else None,
    )
    return risk


# --- commands --------------------------------------------------------------

def cmd_status(cfg: dict, jrn: Journal, args) -> int:
    now = datetime.now(timezone.utc)
    state = build_state(jrn, now)
    kill = ROOT / "KILL_SWITCH"
    print(f"stage            {cfg['stage']}")
    print(f"equity           {cfg['equity']:,.2f} {cfg['account_ccy']}  (manual)")
    print(f"risk per trade   {cfg.get('risk_pct', 0.01):.2%}")
    print(f"kill switch      {'ENGAGED — all routing halted' if kill.exists() else 'clear'}")
    print(f"open positions   {len(state.open_positions)}")
    for p in state.open_positions:
        print(f"    {p.symbol:<10} {p.side:<5} risk {p.risk:>10,.2f}  group {p.group}")
    print(f"trades today     {state.trades_today}")
    print(f"losing streak    {state.consecutive_losses}")
    if state.last_loss_at:
        mins = (now - state.last_loss_at).total_seconds() / 60
        cool = DisciplineLimits().loss_cooldown_minutes
        print(f"last loss        {mins:.0f} min ago"
              + (f"  COOLDOWN ACTIVE, {cool - mins:.0f} min left" if mins < cool else ""))
    n = len(jrn.closed_r_multiples())
    print(f"closed trades    {n}"
          + ("" if n >= MIN_SAMPLE_FOR_SIGNIFICANCE
             else f"   ({MIN_SAMPLE_FOR_SIGNIFICANCE - n} more before any verdict is possible)"))
    return 0


def cmd_check(cfg: dict, jrn: Journal, args) -> int:
    now = datetime.now(timezone.utc)
    try:
        risk = run_gates(cfg, jrn, args, now)
    except OrderRejected as exc:
        print(f"REJECTED  [{exc.gate}]  {exc.reason}")
        return 1
    equity = float(cfg["equity"])
    print(f"PASSED all gates")
    print(f"  risk        {risk:,.2f} {cfg['account_ccy']}  ({risk / equity:.2%} of equity)")
    if args.target:
        reward = abs(float(args.target) - float(args.entry)) * float(args.qty)
        print(f"  reward:risk {reward / (abs(float(args.entry) - float(args.stop)) * float(args.qty)):.2f} : 1")
    print("\nThis is a validation only. Nothing was ordered and nothing was logged.")
    print("Run the same command with 'open' to record it after you place it yourself.")
    return 0


def cmd_open(cfg: dict, jrn: Journal, args) -> int:
    now = datetime.now(timezone.utc)
    try:
        risk = run_gates(cfg, jrn, args, now)
    except OrderRejected as exc:
        print(f"REJECTED  [{exc.gate}]  {exc.reason}")
        print("\nNot logged. The rule exists for a reason; do not place this trade.")
        return 1

    symbol = args.symbol.upper()
    key = args.key or f"{now:%Y%m%d-%H%M%S}-{symbol}-{args.side}"
    inst = INSTRUMENTS.get(symbol)
    jrn.open_trade(TradeOpen(
        idempotency_key=key, stage=cfg["stage"], symbol=symbol,
        asset_class=inst.asset_class if inst else "EQUITY", side=args.side,
        setup=args.setup, regime=args.regime, session=args.session,
        event_risk=args.event_risk, opened_at=now, entry=float(args.entry),
        stop_loss=float(args.stop), qty=float(args.qty), planned_risk=risk,
        equity_at_open=float(cfg["equity"]),
        take_profit=float(args.target) if args.target else None,
        context={"atr": args.atr, "reference": args.reference, "note": args.note},
    ))
    print(f"LOGGED  {key}")
    print(f"  risk {risk:,.2f} {cfg['account_ccy']} ({risk / float(cfg['equity']):.2%})")
    print(f"\nClose it later with:\n  python bot.py close --key {key} --exit <price> "
          f"--reason STOP|TARGET|MANUAL --costs <total costs>")
    return 0


def cmd_close(cfg: dict, jrn: Journal, args) -> int:
    try:
        jrn.close_trade(args.key, datetime.now(timezone.utc), float(args.exit),
                        args.reason, float(args.costs))
    except KeyError as exc:
        print(f"error: {exc}")
        return 1
    row = [t for t in jrn.last_closed(5) if t["idempotency_key"] == args.key][0]
    print(f"CLOSED  {args.key}")
    print(f"  gross {row['gross_pnl']:+,.2f}   costs {row['costs']:,.2f}   "
          f"net {row['net_pnl']:+,.2f}   =  {row['r_multiple']:+.2f}R")
    if row["net_pnl"] < 0:
        print(f"\n  Cooldown active for {DisciplineLimits().loss_cooldown_minutes:.0f} minutes.")
    return 0


def cmd_report(cfg: dict, jrn: Journal, args) -> int:
    rs = jrn.closed_r_multiples(stage=args.stage)
    if not rs:
        print("No closed trades yet. Nothing to score.")
        return 0
    print(scorecard(rs, strategies_tested=args.strategies_tested).summary())
    for dim in ("setup", "regime", "session", "event_risk"):
        rows = jrn.edge_by_slice(dim, min_trades=args.min_trades)
        if not rows:
            continue
        print(f"\nBy {dim}:")
        for r in rows:
            mark = "" if r["significant"] else "   (too few trades to mean anything)"
            print(f"  {str(r['slice']):<18} n={r['n']:<4} avgR={r['avg_r']:+.3f} "
                  f"win={r['win_pct']:.0f}%{mark}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Manual-stage trading referee and journal.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def trade_args(sp, need_meta: bool):
        sp.add_argument("--symbol", required=True)
        sp.add_argument("--side", required=True, choices=["buy", "sell"])
        sp.add_argument("--entry", required=True, type=float)
        sp.add_argument("--stop", required=True, type=float)
        sp.add_argument("--qty", required=True, type=float)
        sp.add_argument("--target", type=float, help="take profit, optional but recommended")
        sp.add_argument("--atr", type=float, help="current ATR, enables the chasing guard")
        sp.add_argument("--reference", type=float,
                        help="the level the setup was built on, enables the chasing guard")
        sp.add_argument("--key", help="idempotency key; generated if omitted")
        if need_meta:
            sp.add_argument("--setup", required=True, help="named setup, e.g. ema_pullback")
            sp.add_argument("--regime", default="UNKNOWN",
                            choices=["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "UNKNOWN"])
            sp.add_argument("--session", default="UNKNOWN")
            sp.add_argument("--event-risk", dest="event_risk", default="NONE",
                            choices=["NONE", "NEAR_HIGH_IMPACT", "IN_BLACKOUT"])
            sp.add_argument("--note", default="")

    sub.add_parser("status")
    trade_args(sub.add_parser("check"), need_meta=False)
    trade_args(sub.add_parser("open"), need_meta=True)

    c = sub.add_parser("close")
    c.add_argument("--key", required=True)
    c.add_argument("--exit", required=True, type=float)
    c.add_argument("--reason", required=True,
                   choices=["STOP", "TARGET", "TIMEOUT", "KILL_SWITCH", "MANUAL"])
    c.add_argument("--costs", required=True, type=float,
                   help="all-in: brokerage, taxes, slippage, TDS. Required, not optional.")

    r = sub.add_parser("report")
    r.add_argument("--stage")
    r.add_argument("--min-trades", dest="min_trades", type=int, default=20)
    r.add_argument("--strategies-tested", dest="strategies_tested", type=int, default=1,
                   help="honest count of every variant tried before settling on this one")

    args = p.parse_args(argv)
    cfg = load_config()
    jrn = Journal(JOURNAL_PATH)
    return {
        "status": cmd_status, "check": cmd_check, "open": cmd_open,
        "close": cmd_close, "report": cmd_report,
    }[args.cmd](cfg, jrn, args)


if __name__ == "__main__":
    sys.exit(main())
