"""Binance ETH quarterly-futures basis history backfill — keyless, public REST.

Unblocks screen 08c (quarterly basis leg-swap, ETH variant only — BTC is
INFEASIBLE-AT-CURRENT-CAPITAL and is NOT re-probed). Fetches:

  * GET https://fapi.binance.com/fapi/v1/continuousKlines
      pair=ETHUSDT contractType=CURRENT_QUARTER interval=1h (history to 2021-02-04,
      audit 08d verified) -> data/ohlcv_cache/ETH-USDT_qtrcont_1h.parquet
      and contractType=NEXT_QUARTER -> ETH-USDT_qtrnext_1h.parquet (completeness;
      the frozen screen uses CURRENT_QUARTER only).
  * GET https://api.binance.com/api/v3/klines symbol=ETHUSDT interval=1h from
      2021-02-04 -> data/ohlcv_cache/ETH-USDT_qtrspot_1h.parquet (spot leg; the
      shared ETH-USDT_1h.parquet cache starts 2023-05-26 and its first-ts is a
      listing-date heuristic elsewhere, so it is NOT touched).

Columns: ts (bar OPEN, unix seconds), open, high, low, close. Idempotent merge on
ts. Degrades gracefully on errors — never fabricates bars.

Usage:
    venv\\Scripts\\python.exe scripts\\backfill_quarterly_basis.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "data", "ohlcv_cache")

CONT_URL = "https://fapi.binance.com/fapi/v1/continuousKlines"
SPOT_URL = "https://api.binance.com/api/v3/klines"
LIMIT = 1500
SPOT_LIMIT = 1000  # api/v3/klines clamps to 1000; a 1500 request would trip the
# short-page early exit after one page
BAR_MS = 3600 * 1000
SINCE = int(datetime(2021, 2, 4, tzinfo=timezone.utc).timestamp() * 1000)


def parse_klines(raw: list) -> list[tuple[int, float, float, float, float]]:
    """Binance kline rows -> [(ts_open_sec, open, high, low, close)]; skips junk."""
    out = []
    for row in raw or []:
        try:
            out.append(
                (int(row[0]) // 1000, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            )
        except (TypeError, ValueError, IndexError):
            continue
    return out


def merge_parquet(path: str, bars: list[tuple]) -> tuple[int, int]:
    """Idempotently merge bars into ``path``; returns (total_after, added)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close"])
    new["ts"] = new["ts"].astype("int64")
    if os.path.exists(path):
        old = pd.read_parquet(path)
        before = set(old["ts"].astype("int64").tolist())
        combined = pd.concat([old, new], ignore_index=True)
    else:
        before = set()
        combined = new
    combined = combined.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    combined.to_parquet(path, index=False)
    added = len({int(t) for t in new["ts"].tolist()} - before)
    return len(combined), added


def _get_json(url: str, params: dict, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


def fetch_paginated(
    url: str,
    base_params: dict,
    since_ms: int = SINCE,
    max_pages: int = 80,
    sleep_s: float = 0.35,
    limit: int = LIMIT,
) -> list[tuple]:
    """Forward-paginate klines/continuousKlines from since_ms to now."""
    bars: dict[int, tuple] = {}
    cursor = since_ms
    for _ in range(max_pages):
        params = dict(base_params, startTime=cursor, limit=limit)
        page = parse_klines(_get_json(url, params))
        if not page:
            break
        for b in page:
            bars[b[0]] = b
        new_cursor = (page[-1][0] * 1000) + BAR_MS
        if new_cursor <= cursor:
            break
        cursor = new_cursor
        time.sleep(sleep_s)
        if len(page) < limit:
            break
    return [bars[t] for t in sorted(bars)]


def main() -> None:
    jobs = [
        (
            "ETH-USDT_qtrcont_1h.parquet",
            CONT_URL,
            {"pair": "ETHUSDT", "contractType": "CURRENT_QUARTER", "interval": "1h"},
            LIMIT,
        ),
        (
            "ETH-USDT_qtrnext_1h.parquet",
            CONT_URL,
            {"pair": "ETHUSDT", "contractType": "NEXT_QUARTER", "interval": "1h"},
            LIMIT,
        ),
        (
            "ETH-USDT_qtrspot_1h.parquet",
            SPOT_URL,
            {"symbol": "ETHUSDT", "interval": "1h"},
            SPOT_LIMIT,
        ),
    ]
    for fname, url, params, limit in jobs:
        try:
            bars = fetch_paginated(
                url, params, limit=limit, max_pages=80 if limit == LIMIT else 120
            )
        except RuntimeError as e:
            print(f"{fname}: FETCH ERROR {e}", flush=True)
            continue
        if not bars:
            print(f"{fname}: no history served", flush=True)
            continue
        path = os.path.join(OUT_DIR, fname)
        total, added = merge_parquet(path, bars)
        print(
            f"{fname}: fetched={len(bars)} total={total} added={added} "
            f"first={datetime.fromtimestamp(bars[0][0], tz=timezone.utc):%Y-%m-%d} "
            f"last={datetime.fromtimestamp(bars[-1][0], tz=timezone.utc):%Y-%m-%d}",
            flush=True,
        )


if __name__ == "__main__":
    main()
