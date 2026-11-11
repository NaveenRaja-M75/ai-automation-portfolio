from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.journal import Journal, TradeOpen  # noqa: E402

T0 = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def jrn(tmp_path):
    return Journal(tmp_path / "journal.db")


def open_one(jrn, key, setup="ema_pullback", regime="TREND_UP", side="buy", entry=100.0):
    jrn.open_trade(TradeOpen(
        idempotency_key=key, stage="PAPER", symbol="GOLDBEES", asset_class="GOLD",
        side=side, setup=setup, regime=regime, session="OPEN", event_risk="NONE",
        opened_at=T0, entry=entry, stop_loss=entry - 2.0, qty=50.0,
        planned_risk=100.0, equity_at_open=10_000.0,
        context={"rsi": 41.2, "ema20": 99.4, "atr": 1.9},
    ))


def test_audit_log_rejects_updates(jrn):
    jrn.record_gate_decision({"decision": "REJECT", "gate": "STOP_LOSS", "symbol": "X",
                              "idempotency_key": "k1", "reason": "missing stop"})
    with jrn._conn() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE gate_decisions SET reason='clean' WHERE idempotency_key='k1'")


def test_audit_log_rejects_deletes(jrn):
    jrn.record_gate_decision({"decision": "PASS", "gate": "ALL", "symbol": "X",
                              "idempotency_key": "k2"})
    with jrn._conn() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM gate_decisions")


def test_duplicate_idempotency_key_rejected(jrn):
    open_one(jrn, "dup")
    with pytest.raises(sqlite3.IntegrityError):
        open_one(jrn, "dup")


def test_r_multiple_is_net_of_costs(jrn):
    open_one(jrn, "t1")
    # +4.00 x 50 = 200 gross, 50 of costs, 150 net on 100 of planned risk = 1.5R
    jrn.close_trade("t1", T0 + timedelta(hours=2), 104.0, "TARGET", costs=50.0)
    with jrn._conn() as c:
        row = c.execute("SELECT * FROM trades WHERE idempotency_key='t1'").fetchone()
    assert row["gross_pnl"] == 200.0
    assert row["net_pnl"] == 150.0
    assert row["r_multiple"] == 1.5


def test_short_pnl_sign_is_correct(jrn):
    open_one(jrn, "s1", side="sell", entry=100.0)
    jrn.close_trade("s1", T0 + timedelta(hours=1), 96.0, "TARGET", costs=0.0)
    with jrn._conn() as c:
        row = c.execute("SELECT gross_pnl FROM trades WHERE idempotency_key='s1'").fetchone()
    assert row["gross_pnl"] == 200.0, "a short that falls must profit"


def test_costs_can_flip_a_winner_to_a_loser(jrn):
    """The Indian crypto case: gross positive, net negative after 1% TDS and fees."""
    open_one(jrn, "c1")
    jrn.close_trade("c1", T0 + timedelta(hours=1), 100.5, "TARGET", costs=60.0)
    with jrn._conn() as c:
        row = c.execute("SELECT gross_pnl, net_pnl FROM trades WHERE idempotency_key='c1'").fetchone()
    assert row["gross_pnl"] > 0 and row["net_pnl"] < 0


def test_closing_unknown_trade_raises(jrn):
    with pytest.raises(KeyError):
        jrn.close_trade("ghost", T0, 100.0, "STOP", costs=0.0)


def test_small_slices_are_marked_insignificant(jrn):
    for i in range(5):
        open_one(jrn, f"small{i}", setup="rare_setup")
        jrn.close_trade(f"small{i}", T0 + timedelta(hours=1), 98.0, "STOP", costs=0.0)
    slices = jrn.edge_by_slice("setup", min_trades=20)
    assert slices[0]["significant"] == 0, "5 trades must never count as evidence"
    assert jrn.losing_slices("setup", min_trades=20) == []


def test_losing_slice_surfaces_once_it_has_enough_trades(jrn):
    for i in range(25):
        open_one(jrn, f"bad{i}", setup="fade_the_open", regime="RANGE")
        jrn.close_trade(f"bad{i}", T0 + timedelta(hours=1), 98.0, "STOP", costs=0.0)
    for i in range(25):
        open_one(jrn, f"good{i}", setup="ema_pullback", regime="TREND_UP")
        jrn.close_trade(f"good{i}", T0 + timedelta(hours=1), 103.0, "TARGET", costs=0.0)
    losers = jrn.losing_slices("setup", min_trades=20)
    assert [s["slice"] for s in losers] == ["fade_the_open"]
    assert losers[0]["avg_r"] == -1.0


def test_group_by_arbitrary_column_is_refused(jrn):
    with pytest.raises(ValueError):
        jrn.edge_by_slice("id; DROP TABLE trades")


def test_context_survives_the_round_trip(jrn):
    open_one(jrn, "ctx")
    with jrn._conn() as c:
        row = c.execute("SELECT context FROM trades WHERE idempotency_key='ctx'").fetchone()
    import json
    assert json.loads(row["context"])["rsi"] == 41.2
