"""Historical Binance spot candles, free and keyless, cached to CSV.

Uses the market-data-only REST endpoint. A cache file is extended incrementally on
each run, so the first download is the only slow one.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

REST = "https://data-api.binance.vision/api/v3/klines"
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]


def _fetch(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    rows: list[list] = []
    session = requests.Session()
    while start_ms < end_ms:
        for attempt in range(5):
            try:
                r = session.get(REST, params={"symbol": symbol, "interval": interval,
                                              "startTime": start_ms, "endTime": end_ms, "limit": 1000},
                                timeout=20)
                r.raise_for_status()
                batch = r.json()
                break
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        if not batch:
            break
        rows += batch
        start_ms = batch[-1][0] + INTERVAL_MS[interval]
    return rows


def load(symbol: str, interval: str, days: int, cache_dir: Path) -> pd.DataFrame:
    """Closed candles only (the still-forming bar is dropped), oldest first."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol}_{interval}.csv"
    step = INTERVAL_MS[interval]
    now_ms = int(time.time() * 1000)
    want_from = now_ms - days * 86_400_000
    df = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=COLUMNS)

    if df.empty or df["open_time"].iloc[0] > want_from + step:
        df = pd.DataFrame(_fetch(symbol, interval, want_from, now_ms)).iloc[:, :6]
        df.columns = COLUMNS
    else:
        new = _fetch(symbol, interval, int(df["open_time"].iloc[-1]) + step, now_ms)
        if new:
            add = pd.DataFrame(new).iloc[:, :6]
            add.columns = COLUMNS
            df = pd.concat([df, add], ignore_index=True)

    df = df.astype({"open_time": "int64", "open": float, "high": float, "low": float,
                    "close": float, "volume": float})
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df = df[df["open_time"] + step <= now_ms]           # closed bars only
    df.to_csv(path, index=False)
    return df[df["open_time"] >= want_from].reset_index(drop=True)
