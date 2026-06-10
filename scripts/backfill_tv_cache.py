"""Backfill data/tv_cache/ from TradingView (keyless websocket, research-only).

Thin CLI over quant_suite/tv_client.py. Overwrites existing cache files (each
run is a fresh research pull; the cache is gitignored and regenerable).

Sets:
  --set regime      5 CRYPTOCAP aggregates + BINANCE:BTCUSDT at 1D (screen inputs)
  --set alts        deep-history majors (from data/ohlcv_cache) as BINANCE perp-spot
                    tickers at 1D (rotation/alt-panel legs for the regime screen)
  --set crosscheck  top majors at 1h (TV-vs-exchange data verification)

Run: venv\\Scripts\\python.exe scripts\\backfill_tv_cache.py --set regime --set alts
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant_suite import tv_client as tvc  # noqa: E402

OHLCV = ROOT / "data" / "ohlcv_cache"
REGIME_SYMBOLS = [
    "CRYPTOCAP:USDT.D",
    "CRYPTOCAP:BTC.D",
    "CRYPTOCAP:TOTAL",
    "CRYPTOCAP:TOTAL2",
    "CRYPTOCAP:TOTAL3",
    "BINANCE:BTCUSDT",
]
CROSSCHECK_BASES = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "LTC", "AVAX"]
MIN_BARS_1H = 20_000  # deep-history majors gate (mirrors run_funding_carry_screen)


def deep_history_bases() -> list[str]:
    bases = []
    for p in sorted(OHLCV.glob("*-USDT_1h.parquet")):
        base = p.name.replace("-USDT_1h.parquet", "")
        if base == "USDT":  # a USDT-USDT cache would make the invalid BINANCE:USDTUSDT
            continue
        try:
            n = len(pd.read_parquet(p, columns=["ts"]))
        except Exception:
            continue
        if n >= MIN_BARS_1H:
            bases.append(base)
    return bases


def pull(symbol: str, resolution: str, countback: int) -> str:
    df = tvc.fetch_ohlcv(symbol, resolution, countback)
    if not len(df):
        return f"  {symbol} {resolution}: EMPTY"
    tvc.save_cache(df, symbol, resolution)
    f = datetime.fromtimestamp(int(df["ts"].iloc[0]), tz=timezone.utc).strftime("%Y-%m-%d")
    last = datetime.fromtimestamp(int(df["ts"].iloc[-1]), tz=timezone.utc).strftime("%Y-%m-%d")
    return f"  {symbol} {resolution}: {len(df)} bars  {f} -> {last}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", choices=["regime", "alts", "crosscheck"], default=[])
    ap.add_argument("--symbols", nargs="*", default=[])
    ap.add_argument("--resolution", default="1D")
    ap.add_argument("--countback", type=int, default=2600)
    args = ap.parse_args()

    jobs: list[tuple[str, str, int]] = []
    if "regime" in args.set:
        jobs += [(s, "1D", 2600) for s in REGIME_SYMBOLS]
    if "alts" in args.set:
        jobs += [(f"BINANCE:{b}USDT", "1D", 2600) for b in deep_history_bases() if b != "BTC"]
    if "crosscheck" in args.set:
        jobs += [(f"BINANCE:{b}USDT", "1h", 2000) for b in CROSSCHECK_BASES]
    jobs += [(s, args.resolution, args.countback) for s in args.symbols]
    if not jobs:
        ap.error("nothing to do: pass --set and/or --symbols")

    failures = 0
    for i, (sym, res, cb) in enumerate(jobs):
        try:
            print(pull(sym, res, cb), flush=True)
        except Exception as e:
            failures += 1
            print(f"  {sym} {res}: FAILED ({type(e).__name__}: {e})", flush=True)
        if i < len(jobs) - 1:
            time.sleep(1.0)  # politeness: one fetch per second
    print(f"done: {len(jobs) - failures}/{len(jobs)} ok -> {tvc.CACHE_DIR}")
    return 1 if failures == len(jobs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
