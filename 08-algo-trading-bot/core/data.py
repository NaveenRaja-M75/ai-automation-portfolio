"""Historical bar loading, with an on-disk cache.

Data is cached to CSV on first fetch so that every later run tests against byte-identical
input. That is not an optimization, it is a correctness requirement: a backtest whose
data silently changes between runs cannot be compared to its own previous result, and
you will spend a week chasing a strategy change that was actually a data revision.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .backtest import Bar

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "bars"


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00").split(" ")[0])
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def load_csv(path: Path) -> list[Bar]:
    """Read OHLCV from CSV. Expects columns: date, open, high, low, close, volume."""
    bars: list[Bar] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            low = {k.strip().lower(): v for k, v in row.items() if k}
            try:
                bar = Bar(
                    ts=_parse_ts(low["date"]),
                    open=float(low["open"]), high=float(low["high"]),
                    low=float(low["low"]), close=float(low["close"]),
                    volume=float(low.get("volume") or 0.0),
                )
            except (KeyError, ValueError, TypeError):
                continue  # skip malformed or blank rows rather than fail the whole load
            if bar.high < bar.low or min(bar.open, bar.high, bar.low, bar.close) <= 0:
                continue
            bars.append(bar)
    bars.sort(key=lambda b: b.ts)
    return bars


def save_csv(bars: list[Bar], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.ts.date().isoformat(), b.open, b.high, b.low, b.close, b.volume])


def fetch(symbol: str, start: str = "2015-01-01", refresh: bool = False) -> list[Bar]:
    """Load bars for a symbol, from cache when available.

    Uses yfinance, which is free and adequate for daily bars on a liquid ETF. It is not
    adequate for intraday or for anything where exact fills matter; the broker's own
    historical endpoint replaces this before live deployment.
    """
    cache = CACHE_DIR / f"{symbol.replace('.', '_')}_1d.csv"
    if cache.exists() and not refresh:
        return load_csv(cache)

    import yfinance as yf
    df = yf.download(symbol, start=start, progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data returned for {symbol}")
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    bars = [
        Bar(ts=idx.to_pydatetime().replace(tzinfo=timezone.utc),
            open=float(r["Open"]), high=float(r["High"]), low=float(r["Low"]),
            close=float(r["Close"]), volume=float(r.get("Volume", 0) or 0))
        for idx, r in df.iterrows()
        if r[["Open", "High", "Low", "Close"]].notna().all()
    ]
    save_csv(bars, cache)
    return bars


def find_anomalies(bars: list[Bar], window: int = 11, ratio: float = 2.0) -> list[dict]:
    """Flag bars whose close is implausible against their local neighbourhood.

    Free data feeds contain bad ticks. GOLDBEES.NS on Yahoo carries two bars in December
    2019 priced at 0.34 against a true level near 33.60, a 100x error that reverts the
    next session. Left in, it manufactures a 99% drawdown in any buy-and-hold benchmark
    and fires phantom stops in any strategy that crosses it.

    A rolling median is used rather than a simple day-over-day change filter, because the
    latter flags both the drop and the recovery and cannot tell which one is the error.
    Compared against the median of its neighbours, only the genuinely wrong bar stands out.

    This reports; it does not repair. Silent repair of price data is how a backtest ends
    up testing something that never happened.
    """
    from statistics import median

    out: list[dict] = []
    half = window // 2
    for i, b in enumerate(bars):
        lo, hi = max(0, i - half), min(len(bars), i + half + 1)
        neighbours = [x.close for j, x in enumerate(bars[lo:hi], start=lo) if j != i]
        if len(neighbours) < 4:
            continue
        med = median(neighbours)
        if med <= 0:
            continue
        r = b.close / med
        if r > ratio or r < 1.0 / ratio:
            out.append({"index": i, "date": b.ts.date().isoformat(), "close": b.close,
                        "local_median": round(med, 4), "ratio": round(r, 4)})
    return out


def drop_anomalies(bars: list[Bar], anomalies: list[dict]) -> list[Bar]:
    """Remove flagged bars. Dropping is safer than interpolating: a missing day is an
    honest gap, while an invented price is a fact the market never produced."""
    bad = {a["index"] for a in anomalies}
    return [b for i, b in enumerate(bars) if i not in bad]


def load_clean(symbol: str, start: str = "2015-01-01", refresh: bool = False,
               verbose: bool = True) -> list[Bar]:
    """Fetch, validate, and drop bad ticks. This is the loader backtests should use."""
    bars = fetch(symbol, start=start, refresh=refresh)
    anomalies = find_anomalies(bars)
    if anomalies and verbose:
        print(f"  DATA WARNING: {len(anomalies)} implausible bar(s) dropped from {symbol}:")
        for a in anomalies[:5]:
            print(f"    {a['date']}  close {a['close']:.4f} vs local median "
                  f"{a['local_median']:.2f}  ({a['ratio']:.4f}x)")
    return drop_anomalies(bars, anomalies)


def slice_by_year(bars: list[Bar], start_year: Optional[int] = None,
                  end_year: Optional[int] = None) -> list[Bar]:
    return [b for b in bars
            if (start_year is None or b.ts.year >= start_year)
            and (end_year is None or b.ts.year <= end_year)]
