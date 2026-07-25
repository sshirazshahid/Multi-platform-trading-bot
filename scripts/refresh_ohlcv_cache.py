"""Atomic append-only refresh for data/ohlcv_cache/{BASE}-USDT_{tf}.parquet (keyless bybit).

Refresh-ONLY companion to the pair-dossier backfill tooling: brings EXISTING
stale OHLCV cache parquets up to now without ever rewriting history. Mirrors
the canonical cache schema (core.feature_store: columns ts/open/high/low/
close/volume, ts unix-seconds int64, RangeIndex, to_parquet index=False) and
the established source resolution (bybit spot BASE/USDT preferred, linear
perp BASE/USDT:USDT fallback). Hardening over the plain cache writer:

  * append-only from the last stored bar - existing rows are never touched
  * closed bars only - the in-progress current bar is dropped
  * hard invariants: earliest bar unchanged, row count grew
  * atomic write: tmp file + os.replace (a crash mid-write cannot corrupt
    the parquet, and concurrent readers never see a partial file)

Absent files are SKIPPED by design (creation-with-history is a different
operation with different provenance implications). Read-only against the
exchange (public endpoints, no keys); writes only under data/ (gitignored).

Usage:
    venv\\Scripts\\python.exe scripts\\refresh_ohlcv_cache.py 1h DASH ENA UNI
    venv\\Scripts\\python.exe scripts\\refresh_ohlcv_cache.py 4h GRT NEAR OP
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import ccxt
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "ohlcv_cache"
TF_SEC = {"1h": 3600, "4h": 14400}
COLS = ["ts", "open", "high", "low", "close", "volume"]


def refresh(ex, base: str, tf: str) -> None:
    """Append new closed bars to one existing cache parquet, atomically."""
    tf_sec = TF_SEC[tf]
    path = CACHE_DIR / f"{base}-USDT_{tf}.parquet"
    if not path.exists():
        print(f"[{base} {tf}] MISSING file - skipped (refresh-only tool)", flush=True)
        return
    old = pd.read_parquet(path)
    old["ts"] = old["ts"].astype("int64")
    old = old.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    first0, last0, n0 = int(old["ts"].iloc[0]), int(old["ts"].iloc[-1]), len(old)

    spot = f"{base}/USDT"
    sym = spot if spot in ex.markets else f"{spot}:USDT"
    if sym not in ex.markets:
        print(f"[{base} {tf}] no bybit market", flush=True)
        return
    src = "spot" if sym == spot else "perp"

    now = int(time.time())
    cursor_ms = (last0 + tf_sec) * 1000
    rows_all: list = []
    guard = 0
    while cursor_ms < now * 1000 and guard < 50:
        guard += 1
        batch = ex.fetch_ohlcv(sym, tf, since=cursor_ms, limit=1000)
        if not batch:
            break
        rows_all.extend(batch)
        last_ms = int(batch[-1][0])
        if last_ms < cursor_ms:
            break
        cursor_ms = last_ms + tf_sec * 1000
        if len(batch) < 1000:
            break

    new = pd.DataFrame(rows_all, columns=COLS)
    if len(new):
        new["ts"] = new["ts"].astype("int64") // 1000
        # append-only, closed bars only (bar open time + tf must be in the past)
        new = new[(new["ts"] > last0) & (new["ts"] + tf_sec <= now)]
        for c in COLS[1:]:
            new[c] = new[c].astype("float64")
    if not len(new):
        print(f"[{base} {tf}] no new closed bars (last={last0})", flush=True)
        return

    combined = pd.concat([old, new], ignore_index=True)
    combined["ts"] = combined["ts"].astype("int64")
    combined = (
        combined.sort_values("ts").drop_duplicates("ts", keep="first").reset_index(drop=True)
    )
    if int(combined["ts"].iloc[0]) != first0:
        raise RuntimeError(f"{base} {tf}: earliest bar changed - aborting write")
    if len(combined) <= n0:
        raise RuntimeError(f"{base} {tf}: row count did not grow - aborting write")

    tmp = str(path) + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    print(
        f"[{base} {tf}] via {src}: rows {n0} -> {len(combined)} (+{len(combined) - n0}) "
        f"first={first0} last={int(combined['ts'].iloc[-1])}",
        flush=True,
    )


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in TF_SEC:
        print("usage: refresh_ohlcv_cache.py {1h|4h} BASE [BASE ...]")
        sys.exit(2)
    tf = sys.argv[1]
    bases = [b.upper() for b in sys.argv[2:]]
    ex = ccxt.bybit({"enableRateLimit": True})
    ex.load_markets()
    for b in bases:
        try:
            refresh(ex, b, tf)
        except Exception as e:  # noqa: BLE001 - keep the batch going, report honestly
            print(f"[{b} {tf}] ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
