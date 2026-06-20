"""Backfill OHLCV history for the analysis-only commodity/equity PERPS.

These (XAU/XAG/CL/BZ/COPPER + TSLA/NVDA/AMZN/AAPL/GOOGL/META/MSFT/MSTR/COIN) are
live, liquid perpetuals on Binance/Bybit/Bitget but only ~5 months old. They are
tracked for research and HARD-BLOCKED from live entry (config.ANALYSIS_ONLY_BASES,
bot_engine._execute_open). This accumulates their OHLCV (1d/4h/1h) into
data/ohlcv_cache/ so the frozen pre-registered edge screen can evaluate them once
there is enough history — turning the owner's observation into a falsifiable track.

DESIGN (deliberately gentle — the LIVE paper bot shares this IP):
  * SERIAL, one (symbol, timeframe) at a time, with an inter-call throttle.
  * Source priority: Binance (deepest history / most liquid), else the venue that
    lists the perp.
  * Idempotent: load_ohlcv_window only fills missing bars; re-run as history grows.
  * Cache key 'BASE-USDTUSDT_<tf>.parquet' (perp form; distinct from spot cache).

Usage:
    python scripts/backfill_perps_ohlcv.py                 # 1d/4h/1h, ~420d window
    python scripts/backfill_perps_ohlcv.py --timeframes 1h --days 200
    python scripts/backfill_perps_ohlcv.py --list-only     # show listings, no fetch
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import ANALYSIS_ONLY_BASES  # noqa: E402
from core.feature_store import load_ohlcv_window  # noqa: E402


def _make_fetcher(client):
    def _fetch(symbol, timeframe, since_ms, limit):
        return (
            client.exchange.fetch_ohlcv(
                symbol, timeframe, since=int(since_ms), limit=int(limit), params={}
            )
            or []
        )

    return _fetch


def _connected_clients() -> dict:
    import config  # noqa: F401
    from exchanges.binance_client import BinanceClient
    from exchanges.bitget_client import BitgetClient
    from exchanges.bybit_client import BybitClient

    out = {}
    for name, ctor in (
        ("binance", BinanceClient),
        ("bybit", BybitClient),
        ("bitget", BitgetClient),
    ):
        try:
            c = ctor()
            if getattr(c, "_connected", False):
                out[name] = c
        except Exception as e:
            print(f"  [{name}] connect failed: {str(e)[:120]}")
    return out


def _resolve_listings(clients: dict) -> dict:
    """base -> source venue name for its `{base}/USDT:USDT` perp (Binance preferred)."""
    listed: dict[str, str] = {}
    for name, c in clients.items():
        try:
            mk = c.exchange.markets or c.exchange.load_markets()
        except Exception as e:
            print(f"  [{name}] load_markets failed: {str(e)[:100]}")
            continue
        for base in ANALYSIS_ONLY_BASES:
            sym = f"{base}/USDT:USDT"
            m = mk.get(sym)
            if m and m.get("active", True):
                if base not in listed or name == "binance":
                    listed[base] = name
    return listed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", nargs="+", default=["1d", "4h", "1h"])
    ap.add_argument("--days", type=float, default=420.0)
    ap.add_argument(
        "--throttle", type=float, default=0.6, help="inter-call sleep (s) — protect the live bot"
    )
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    print("Connecting to venues...")
    clients = _connected_clients()
    if not clients:
        print("No connected venues — aborting.")
        return 1

    listed = _resolve_listings(clients)
    fetchers = {n: _make_fetcher(c) for n, c in clients.items()}
    bases = sorted(ANALYSIS_ONLY_BASES)
    print(f"\nAnalysis-only perps: {len(bases)} bases; {len(listed)} listed on a connected venue.")
    for b in bases:
        print(f"  {b:<8} {'-> ' + listed[b] if b in listed else 'NOT LISTED (skip)'}")
    if args.list_only:
        return 0

    now = int(time.time())
    start = now - int(args.days * 24 * 3600)
    print(f"\nBackfilling {args.timeframes} over ~{args.days:.0f}d (gentle, serial)...")
    grand = 0
    for base in bases:
        src = listed.get(base)
        if not src:
            continue
        sym = f"{base}/USDT:USDT"
        per = {}
        for tf in args.timeframes:
            try:
                df = load_ohlcv_window(sym, tf, start, now, fetcher=fetchers[src])
                per[tf] = len(df)
                grand += len(df)
            except Exception as e:
                per[tf] = f"ERR:{str(e)[:30]}"
            time.sleep(args.throttle)
        print(f"  {base:<8} via {src:<8} " + "  ".join(f"{tf}={per[tf]}" for tf in args.timeframes))

    print(f"\nDONE. {grand:,} total bars cached -> data/ohlcv_cache/<BASE>-USDTUSDT_<tf>.parquet")
    print(
        "These are research-only (hard-blocked from live entry). Screen later once history is deep enough."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
