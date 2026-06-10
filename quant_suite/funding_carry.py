"""funding_carry.py - funding/carry data utilities (Pine #4 port support).

Pine #4 (pine_strategies/04_funding_carry_strategy.pine) used a perp-vs-spot
premium PROXY because Pine cannot read exchange funding. Python reads REAL 8h
funding via ccxt, so the proxy is not ported. The vote itself lives in
engine.prep() (fc_vote column, "carry" mode) so it shares the causal
alignment, cost model, and backtest harness of the other votes.

This module holds only data plumbing: paginated funding-history backfill and a
parquet cache beside data/ohlcv_cache. It never places orders.

Prior: time-series funding/carry screened NO_EDGE on a ~70d window (2026-05-26
prereg, scripts/run_funding_edge_search.py). The 3y re-screen verdict is
recorded in the strategies.py registry edge note — keep it honest.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "funding_cache"


def fetch_funding_history(
    ex,
    symbol: str,
    since_ms: int,
    until_ms: int | None = None,
    limit: int = 1000,
    throttle_s: float = 0.4,
    max_pages: int = 40,
) -> pd.DataFrame:
    """Paginated real funding history -> DataFrame {ts: unix SECONDS, rate: decimal/8h}.

    Binance fapi/v1/fundingRate caps 1000 rows/call (~333d at 8h), so a 3y window
    is ~4 pages. Cursor advances to last timestamp + 1ms; duplicates dropped.
    """
    rows, cursor = [], since_ms
    for _ in range(max_pages):
        batch = ex.fetch_funding_rate_history(symbol, since=cursor, limit=limit)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1]["timestamp"]
        if (until_ms is not None and last >= until_ms) or len(batch) < limit:
            break
        cursor = last + 1
        if throttle_s:
            time.sleep(throttle_s)
    if not rows:
        return pd.DataFrame(columns=["ts", "rate"])
    if len(rows) >= max_pages * limit:
        warnings.warn(
            f"{symbol}: fetch_funding_history hit max_pages={max_pages}; history may be truncated",
            stacklevel=2,
        )
    f = pd.DataFrame(
        {
            "ts": [int(r["timestamp"]) // 1000 for r in rows],
            "rate": [float(r["fundingRate"]) for r in rows],
        }
    )
    f = f.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if until_ms is not None:
        f = f[f["ts"] <= until_ms // 1000].reset_index(drop=True)
    return f


def cache_path(symbol: str) -> Path:
    return CACHE / f"{symbol.replace('/', '-').replace(':', '')}_8h.parquet"


def load_or_backfill(ex, symbol: str, since_ms: int) -> pd.DataFrame:
    """Cached funding history; extends forward when the cache is >1 day stale."""
    p = cache_path(symbol)
    if p.exists():
        f = pd.read_parquet(p)
        if not len(f):  # empty cache is not authoritative — retry
            f = fetch_funding_history(ex, symbol, since_ms)
            if len(f):
                f.to_parquet(p, index=False)
            return f
        if time.time() * 1000 - f["ts"].iloc[-1] * 1000 > 86_400_000:
            new = fetch_funding_history(ex, symbol, int(f["ts"].iloc[-1]) * 1000 + 1)
            if len(new):
                f = (
                    pd.concat([f, new])
                    .drop_duplicates("ts")
                    .sort_values("ts")
                    .reset_index(drop=True)
                )
                CACHE.mkdir(parents=True, exist_ok=True)
                f.to_parquet(p, index=False)
        return f
    f = fetch_funding_history(ex, symbol, since_ms)
    if len(f):
        CACHE.mkdir(parents=True, exist_ok=True)
        f.to_parquet(p, index=False)
    return f


def to_frame(f: pd.DataFrame) -> pd.DataFrame:
    """ts-seconds history -> {dt, rate} frame for engine.prep(funding=...)."""
    return pd.DataFrame(
        {"dt": pd.to_datetime(f["ts"], unit="s", utc=True), "rate": f["rate"].astype(float)}
    )
