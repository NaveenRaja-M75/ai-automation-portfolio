"""Trade journal and append-only audit log.

This module is the substrate for everything the operator asked for: learning from wins
and losses, finding which setups work in which regime, and knowing which days to sit out.
None of that is possible retroactively, so the design rule is:

    Market context is captured at DECISION time, never reconstructed afterwards.

Reconstructing "what the market looked like" after you know the outcome is how a strategy
gets talked into looking better than it was. Every row here is written before the outcome
is known, except the explicit close fields.

The audit log is append-only by convention and by trigger: an UPDATE or DELETE against
gate_decisions raises. That satisfies the harness requirement that the bot cannot rewrite
its own audit trail. A determined process with filesystem access can still replace the
file, so production keeps this database on a volume the trading process cannot unlink.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS gate_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    idempotency_key TEXT    NOT NULL,
    decision        TEXT    NOT NULL CHECK (decision IN ('PASS', 'REJECT')),
    gate            TEXT    NOT NULL,
    reason          TEXT,
    payload         TEXT    NOT NULL
);

CREATE TRIGGER IF NOT EXISTS gate_decisions_no_update
BEFORE UPDATE ON gate_decisions
BEGIN SELECT RAISE(ABORT, 'gate_decisions is append-only'); END;

CREATE TRIGGER IF NOT EXISTS gate_decisions_no_delete
BEFORE DELETE ON gate_decisions
BEGIN SELECT RAISE(ABORT, 'gate_decisions is append-only'); END;

CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key  TEXT    NOT NULL UNIQUE,
    stage            TEXT    NOT NULL,   -- BACKTEST | PAPER | LIVE_MINIMUM | LIVE
    symbol           TEXT    NOT NULL,
    asset_class      TEXT    NOT NULL,   -- IN_EQUITY | IN_INDEX | GOLD | CRYPTO
    side             TEXT    NOT NULL,
    setup            TEXT    NOT NULL,   -- named strategy setup, e.g. 'ema_pullback'
    regime           TEXT    NOT NULL,   -- TREND_UP | TREND_DOWN | RANGE | HIGH_VOL
    session          TEXT    NOT NULL,   -- OPEN | MIDDAY | CLOSE | OVERNIGHT
    event_risk       TEXT    NOT NULL,   -- NONE | NEAR_HIGH_IMPACT | IN_BLACKOUT
    opened_at        TEXT    NOT NULL,
    entry            REAL    NOT NULL,
    stop_loss        REAL    NOT NULL,
    take_profit      REAL,
    qty              REAL    NOT NULL,
    planned_risk     REAL    NOT NULL,   -- account currency, at open
    equity_at_open   REAL    NOT NULL,
    context          TEXT    NOT NULL,   -- JSON snapshot of every feature at decision time
    closed_at        TEXT,
    exit_price       REAL,
    exit_reason      TEXT,               -- STOP | TARGET | TIMEOUT | KILL_SWITCH | MANUAL
    gross_pnl        REAL,
    costs            REAL,               -- brokerage, STT, TDS, slippage, all in
    net_pnl          REAL,
    r_multiple       REAL                -- net_pnl / planned_risk, the only comparable unit
);

CREATE INDEX IF NOT EXISTS idx_trades_setup_regime ON trades (setup, regime);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades (closed_at);
"""


@dataclass(frozen=True)
class TradeOpen:
    idempotency_key: str
    stage: str
    symbol: str
    asset_class: str
    side: str
    setup: str
    regime: str
    session: str
    event_risk: str
    opened_at: datetime
    entry: float
    stop_loss: float
    qty: float
    planned_risk: float
    equity_at_open: float
    context: dict[str, Any]
    take_profit: Optional[float] = None


class Journal:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    # --- audit log ---------------------------------------------------------

    def record_gate_decision(self, record: dict[str, Any]) -> None:
        """Wire this directly into validate_order(audit=...) so every pass and every
        reject lands here, with no way for the caller to skip the reject path."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO gate_decisions (ts, symbol, idempotency_key, decision, gate,"
                " reason, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.get("ts", datetime.now(timezone.utc).isoformat()),
                    str(record.get("symbol", "?")),
                    str(record.get("idempotency_key", "?")),
                    record["decision"],
                    record.get("gate", "?"),
                    record.get("reason"),
                    json.dumps(record, default=str),
                ),
            )

    # --- trade lifecycle ---------------------------------------------------

    def open_trade(self, t: TradeOpen) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO trades (idempotency_key, stage, symbol, asset_class, side,"
                " setup, regime, session, event_risk, opened_at, entry, stop_loss,"
                " take_profit, qty, planned_risk, equity_at_open, context)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.idempotency_key, t.stage, t.symbol, t.asset_class, t.side, t.setup,
                    t.regime, t.session, t.event_risk, t.opened_at.isoformat(), t.entry,
                    t.stop_loss, t.take_profit, t.qty, t.planned_risk, t.equity_at_open,
                    json.dumps(t.context, default=str),
                ),
            )
            return int(cur.lastrowid)

    def close_trade(
        self,
        idempotency_key: str,
        closed_at: datetime,
        exit_price: float,
        exit_reason: str,
        costs: float,
    ) -> None:
        """Costs are mandatory, not optional. A journal that records gross P&L teaches
        the wrong lesson: in Indian crypto especially, costs are what decide the outcome."""
        with self._conn() as c:
            row = c.execute(
                "SELECT side, entry, qty, planned_risk FROM trades WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no open trade with key {idempotency_key}")
            direction = 1.0 if row["side"] == "buy" else -1.0
            gross = (exit_price - row["entry"]) * row["qty"] * direction
            net = gross - costs
            r = net / row["planned_risk"] if row["planned_risk"] else None
            c.execute(
                "UPDATE trades SET closed_at=?, exit_price=?, exit_reason=?, gross_pnl=?,"
                " costs=?, net_pnl=?, r_multiple=? WHERE idempotency_key=?",
                (closed_at.isoformat(), exit_price, exit_reason, gross, costs, net, r,
                 idempotency_key),
            )

    # --- reading state back out --------------------------------------------

    def open_trades(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def closed_r_multiples(self, stage: Optional[str] = None) -> list[float]:
        q = ("SELECT r_multiple FROM trades WHERE closed_at IS NOT NULL"
             " AND r_multiple IS NOT NULL")
        args: tuple = ()
        if stage:
            q += " AND stage = ?"
            args = (stage,)
        with self._conn() as c:
            return [r["r_multiple"] for r in c.execute(q + " ORDER BY closed_at", args)]

    def last_closed(self, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent closes first. Used to rebuild the loss streak and cooldown after a
        restart, so that restarting the process cannot clear a discipline lockout."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM trades WHERE closed_at IS NOT NULL"
                " ORDER BY closed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_opened_on(self, day: str) -> int:
        """Trades opened on an ISO date (YYYY-MM-DD), for the overtrading cap."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM trades WHERE substr(opened_at, 1, 10) = ?", (day,)
            ).fetchone()
        return int(row["n"])

    # --- learning surface --------------------------------------------------

    def edge_by_slice(self, dimension: str, min_trades: int = 20) -> list[dict[str, Any]]:
        """Expectancy in R, grouped by one dimension. This is the loss analysis the
        harness and the operator both asked for, expressed as a query rather than as a
        conversation with a model.

        min_trades guards against the main failure mode of this kind of analysis: reading
        a story into six trades. Slices below the threshold are returned with
        significant=0 so they cannot be silently acted on.
        """
        if dimension not in ("setup", "regime", "session", "event_risk", "asset_class", "symbol"):
            raise ValueError(f"refusing to group by untrusted column {dimension!r}")
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {dimension} AS slice, COUNT(*) AS n,"
                "  ROUND(AVG(r_multiple), 3) AS avg_r,"
                "  ROUND(SUM(net_pnl), 2) AS net,"
                "  ROUND(100.0 * SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_pct"
                f" FROM trades WHERE closed_at IS NOT NULL GROUP BY {dimension}"
                " ORDER BY avg_r ASC",
            ).fetchall()
        return [
            {**dict(r), "significant": 1 if r["n"] >= min_trades else 0} for r in rows
        ]

    def losing_slices(self, dimension: str, min_trades: int = 20) -> list[dict[str, Any]]:
        """Slices with a statistically meaningful negative expectancy. These are the
        candidates for a hard filter in the strategy layer."""
        return [
            s for s in self.edge_by_slice(dimension, min_trades)
            if s["significant"] and s["avg_r"] is not None and s["avg_r"] < 0
        ]
