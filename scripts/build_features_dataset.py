"""Backfill the warehouse `features` table from candidates + historical OHLCV.

For every (symbol, ts) in the `candidates` table that has a populated
features_json snapshot, we:
  1. Reconstruct 4h / 1h / 15m OHLCV windows around `ts` from a parquet
     cache (download from Binance public endpoints on first miss).
  2. Compute the full extended FEATURE_SCHEMA via
     `core.feature_store.compute_features_from_ohlcv` (incl. BTC context,
     funding z-score when present, time bucket).
  3. Persist one row per (ts, symbol, timeframe, feature_name) into the
     `features` table via `FeatureStore.write`.

This is a one-shot backfill — running it twice updates feature values in
place (idempotent on the `features` PK).

Usage:
    python scripts/build_features_dataset.py
    python scripts/build_features_dataset.py --since 2026-01-01 --max-rows 5000
    python scripts/build_features_dataset.py --symbols BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ccxt: public Binance OHLCV doesn't require API keys.
import ccxt  # noqa: E402
import pandas as pd  # noqa: E402

from core.feature_store import (  # noqa: E402
    _TF_TO_SECONDS,
    FeatureStore,
    compute_features_from_ohlcv,
    load_ohlcv_window,
)
from core.warehouse import get_warehouse  # noqa: E402

# Bars of *history* we keep around each candidate ts. Enough for
# EMA(50) on 4h (~200 bars) and 30d corr on 1h (~720 bars) without
# dragging entire histories through memory.
WINDOW_BARS = {"4h": 240, "1h": 800, "15m": 400}

# When `now_ts` lands inside a still-open bar we still want to compute
# features against the most recent CLOSED bar — so we trim by one bar.
TRIM_LAST_BAR = True


def _binance_public() -> ccxt.binance:
    """Spot endpoint is enough for OHLCV — same data as USDT-perp."""
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 15_000})
    return ex


def _fetcher(ex):
    """Return a fetcher closure compatible with `load_ohlcv_window`."""

    def f(symbol, timeframe, since_ms, limit):
        # ccxt expects 'BTC/USDT' (no :USDT). Strip the perp suffix if present.
        base = symbol.split(":")[0]
        return ex.fetch_ohlcv(base, timeframe, since=int(since_ms), limit=int(limit))

    return f


def _coin_to_pair(symbol: str) -> str:
    """Normalize 'BTC/USDT' or 'BTC/USDT:USDT' to 'BTC/USDT' for fetcher."""
    return symbol.split(":")[0]


def _extract_funding_series(features_json: dict) -> pd.Series | None:
    """The candidate snapshot stores a single funding_rate scalar — that's
    not enough for a z-score. Returning None falls back to the neutral
    default (0.0). We can revisit this once we ship a funding history
    fetcher."""
    return None


def _slice_around(df: pd.DataFrame, target_ts: int, n: int) -> pd.DataFrame:
    """Last n bars whose ts <= target_ts."""
    sub = df[df["ts"] <= int(target_ts)]
    if len(sub) <= n:
        return sub
    return sub.iloc[-n:]


def _process_candidate(
    *,
    cand: dict,
    btc_window: dict[str, pd.DataFrame],
    fs: FeatureStore,
    fetcher,
    cache_pad_seconds: int,
) -> int:
    """Compute features for one candidate and write them to the store.

    Returns 1 on success, 0 when not enough OHLCV could be reconstructed.
    """
    ts = int(cand["ts"])
    sym = str(cand["symbol"])
    pair = _coin_to_pair(sym)
    ohlcv: dict[str, pd.DataFrame] = {}
    for tf, n in WINDOW_BARS.items():
        tf_sec = _TF_TO_SECONDS[tf]
        start = ts - tf_sec * (n + 5)
        end = ts + tf_sec * 2
        try:
            df = load_ohlcv_window(pair, tf, start, end, fetcher=fetcher)
        except Exception:
            df = pd.DataFrame()
        if df is None or len(df) == 0:
            continue
        sub = _slice_around(df, ts, n)
        if TRIM_LAST_BAR and len(sub) > 1:
            sub = sub.iloc[:-1]
        if len(sub):
            ohlcv[tf] = sub.reset_index(drop=True)

    if "1h" not in ohlcv:
        return 0  # need at least 1h for the bulk of features

    feats = compute_features_from_ohlcv(
        ohlcv,
        btc_ohlcv=btc_window if pair != "BTC/USDT" else None,
        funding_series=None,
        regime_state=None,
        now_ts=ts,
    )
    # One persisted row per timeframe so the schema-by-timeframe story holds.
    # We aggregate the flat dict: every feat name carries its tf suffix or is
    # cross-sectional ("regime_*", "btc_*", "funding_*", "time_bucket"). Write
    # them under the natural timeframe so reads can target a slice.
    by_tf: dict[str, dict[str, float]] = {"4h": {}, "1h": {}, "15m": {}, "ctx": {}}
    for k, v in feats.items():
        if k.endswith("_4h"):
            by_tf["4h"][k] = v
        elif k.endswith("_1h"):
            by_tf["1h"][k] = v
        elif k.endswith("_15m"):
            by_tf["15m"][k] = v
        else:
            by_tf["ctx"][k] = v
    for tf, payload in by_tf.items():
        if payload:
            fs.write(ts=ts, symbol=sym, timeframe=tf, features=payload)
    return 1


def _build_btc_window(start_ts: int, end_ts: int, fetcher) -> dict[str, pd.DataFrame]:
    """Pull the BTC reference series once and reuse across candidates."""
    out: dict[str, pd.DataFrame] = {}
    for tf in ("4h", "1h"):
        tf_sec = _TF_TO_SECONDS[tf]
        df = load_ohlcv_window(
            "BTC/USDT",
            tf,
            start_ts - tf_sec * 50,
            end_ts + tf_sec * 5,
            fetcher=fetcher,
        )
        if df is not None and len(df):
            out[tf] = df
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--since", default=None, help="ISO date (YYYY-MM-DD); only process candidates after this ts"
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="cap number of candidates processed (smoke testing)",
    )
    parser.add_argument(
        "--symbols", default=None, help="comma-separated whitelist e.g. BTC/USDT,ETH/USDT"
    )
    parser.add_argument("--print-every", type=int, default=200)
    args = parser.parse_args()

    wh = get_warehouse()
    fs = FeatureStore(wh)
    ex = _binance_public()
    fetcher = _fetcher(ex)

    sql = (
        "SELECT id, ts, symbol, side, market_type, features_json "
        "FROM candidates "
        "WHERE features_json IS NOT NULL AND length(features_json) > 100"
    )
    params: list = []
    if args.since:
        dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        sql += " AND ts >= ?"
        params.append(dt.timestamp())
    if args.symbols:
        wl = [s.strip() for s in args.symbols.split(",") if s.strip()]
        placeholders = ",".join("?" for _ in wl)
        sql += f" AND symbol IN ({placeholders})"
        params.extend(wl)
    sql += " ORDER BY ts"
    if args.max_rows:
        sql += f" LIMIT {int(args.max_rows)}"
    rows = wh.query(sql, params)
    if not rows:
        print("no candidates match — nothing to do")
        return 0
    print(
        f"loaded {len(rows)} candidate rows; ts range: "
        f"{int(rows[0]['ts'])} -> {int(rows[-1]['ts'])}"
    )

    btc = _build_btc_window(int(rows[0]["ts"]), int(rows[-1]["ts"]), fetcher)
    if not btc:
        print("warning: BTC reference window empty — context features will be neutral")

    written = 0
    skipped = 0
    t0 = time.time()
    for i, r in enumerate(rows):
        ok = _process_candidate(
            cand=r,
            btc_window=btc,
            fs=fs,
            fetcher=fetcher,
            cache_pad_seconds=0,
        )
        written += ok
        skipped += 1 - ok
        if (i + 1) % args.print_every == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / max(1e-3, elapsed)
            print(f"  [{i + 1}/{len(rows)}] written={written} skipped={skipped} rate={rate:.1f}/s")
    print(f"done -- written={written} skipped={skipped} elapsed={time.time() - t0:.0f}s")

    counts = wh.query("SELECT COUNT(*) c FROM features")[0]["c"]
    print(f"features table now has {counts} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
