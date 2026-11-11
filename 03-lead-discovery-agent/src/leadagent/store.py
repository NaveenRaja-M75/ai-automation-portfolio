"""SQLite store and the resumable target queue.

The queue is the reason this scales to all-India without an all-India bill:
work is broken into (city, niche) targets, each checkpointed independently.
Hitting a budget ceiling stops the run clean; the next run picks up the
remaining pending targets. Businesses are cached permanently by source key,
so re-running a city never re-pays for a business already seen.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Business, Score, SiteSignals

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    params      TEXT NOT NULL,
    stats       TEXT
);

CREATE TABLE IF NOT EXISTS targets (
    city    TEXT NOT NULL,
    niche   TEXT NOT NULL,
    status  TEXT NOT NULL DEFAULT 'pending',
    found   INTEGER DEFAULT 0,
    updated TEXT,
    error   TEXT,
    PRIMARY KEY (city, niche)
);

CREATE TABLE IF NOT EXISTS businesses (
    key         TEXT PRIMARY KEY,
    niche       TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    data        TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_signals (
    key       TEXT PRIMARY KEY,
    data      TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    key        TEXT PRIMARY KEY,
    total      INTEGER NOT NULL,
    tier       TEXT NOT NULL,
    band       INTEGER NOT NULL,
    data       TEXT NOT NULL,
    scored_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_niche_city ON businesses(niche, city);
CREATE INDEX IF NOT EXISTS idx_scores_tier ON scores(tier, band);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- runs -------------------------------------------------------------
    def start_run(self, params: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, params) VALUES (?, ?)",
            (_now(), json.dumps(params)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, stats = ? WHERE id = ?",
            (_now(), json.dumps(stats), run_id),
        )
        self.conn.commit()

    # --- target queue -----------------------------------------------------
    def enqueue(self, cities: list[str], niches: list[str]) -> int:
        added = 0
        for city in cities:
            for niche in niches:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO targets (city, niche, updated) "
                    "VALUES (?, ?, ?)",
                    (city, niche, _now()),
                )
                added += cur.rowcount
        self.conn.commit()
        return added

    def pending_targets(self) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT city, niche FROM targets WHERE status IN ('pending','failed') "
            "ORDER BY city, niche"
        ).fetchall()
        return [(r["city"], r["niche"]) for r in rows]

    def mark_target(
        self, city: str, niche: str, status: str, found: int = 0, error: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE targets SET status = ?, found = ?, updated = ?, error = ? "
            "WHERE city = ? AND niche = ?",
            (status, found, _now(), error, city, niche),
        )
        self.conn.commit()

    def reset_targets(self) -> None:
        self.conn.execute("UPDATE targets SET status = 'pending', error = NULL")
        self.conn.commit()

    # --- businesses -------------------------------------------------------
    def upsert_business(self, biz: Business) -> bool:
        """Returns True if this is the first time we have seen the business."""
        from .models import as_dict

        key = biz.key()
        existing = self.conn.execute(
            "SELECT key FROM businesses WHERE key = ?", (key,)
        ).fetchone()
        payload = json.dumps(as_dict(biz), ensure_ascii=False)
        if existing:
            self.conn.execute(
                "UPDATE businesses SET data = ?, last_seen = ? WHERE key = ?",
                (payload, _now(), key),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO businesses (key, niche, city, state, data, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, biz.niche, biz.city, biz.state, payload, _now(), _now()),
        )
        self.conn.commit()
        return True

    def businesses(
        self, niche: str | None = None, city: str | None = None
    ) -> Iterator[Business]:
        sql = "SELECT data FROM businesses WHERE 1=1"
        args: list[Any] = []
        if niche:
            sql += " AND niche = ?"
            args.append(niche)
        if city:
            sql += " AND city = ?"
            args.append(city)
        for row in self.conn.execute(sql, args):
            yield Business(**json.loads(row["data"]))

    # --- site signals -----------------------------------------------------
    def get_site(self, key: str) -> SiteSignals | None:
        row = self.conn.execute(
            "SELECT data FROM site_signals WHERE key = ?", (key,)
        ).fetchone()
        return SiteSignals(**json.loads(row["data"])) if row else None

    def put_site(self, key: str, site: SiteSignals) -> None:
        from .models import as_dict

        self.conn.execute(
            "INSERT OR REPLACE INTO site_signals (key, data, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(as_dict(site), ensure_ascii=False), _now()),
        )
        self.conn.commit()

    # --- scores -----------------------------------------------------------
    def get_score(self, key: str) -> Score | None:
        row = self.conn.execute(
            "SELECT data FROM scores WHERE key = ?", (key,)
        ).fetchone()
        return Score(**json.loads(row["data"])) if row else None

    def put_score(self, key: str, score: Score) -> None:
        from .models import as_dict

        self.conn.execute(
            "INSERT OR REPLACE INTO scores (key, total, tier, band, data, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                key,
                score.total,
                score.tier,
                score.budget_band,
                json.dumps(as_dict(score), ensure_ascii=False),
                _now(),
            ),
        )
        self.conn.commit()

    # --- reporting --------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        def scalar(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])

        tiers = {
            row["tier"]: row["n"]
            for row in self.conn.execute(
                "SELECT tier, COUNT(*) AS n FROM scores GROUP BY tier"
            )
        }
        return {
            "businesses": scalar("SELECT COUNT(*) FROM businesses"),
            "enriched": scalar("SELECT COUNT(*) FROM site_signals"),
            "scored": scalar("SELECT COUNT(*) FROM scores"),
            "tiers": tiers,
            "targets_done": scalar(
                "SELECT COUNT(*) FROM targets WHERE status = 'done'"
            ),
            "targets_pending": scalar(
                "SELECT COUNT(*) FROM targets WHERE status IN ('pending','failed')"
            ),
        }
