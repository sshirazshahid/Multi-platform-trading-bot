"""Full-universe 1h OHLCV backfill for the alpha-search research sweep.

Expands data/ohlcv_cache/ from the ~34 majors to the UNION of liquid USDT
markets across all connected venues (Binance/Bybit/Bitget), so
scripts/run_alpha_search.py can screen the full universe.

DESIGN (deliberately gentle — the LIVE paper bot shares this IP):
  * SERIAL. No parallelism. One symbol at a time.
  * Hard inter-symbol throttle (--throttle, default 0.6s) on top of ccxt's
    own rate limiter, so the backfill stays well under the venue weight cap
    and never starves the running bot's trading/health calls.
  * UNIQUE bases only (OHLCV is ~venue-invariant) — fetch each coin once,
    not once per venue.
  * Common time window for every symbol (--days) so the IS/OOS split in the
    screen is not dominated by the few deep-history majors.
  * Idempotent: load_ohlcv_window only fills missing bars; re-runnable.
  * Spot OHLCV, symbol form 'BASE/USDT' -> cache 'BASE-USDT_1h.parquet'
    (matches the existing cache + run_alpha_search's reader).

Usage:
    python scripts/backfill_universe_ohlcv.py --list-only          # enumerate, no fetch
    python scripts/backfill_universe_ohlcv.py --days 365 --min-vol 3000000 --max-symbols 300
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.feature_store import load_ohlcv_window


def _make_spot_fetcher(client):
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


def enumerate_liquid_bases(clients: dict, min_vol_usd: float) -> dict:
    """Return {base: {'vol': max_quote_vol, 'venues': set, 'spot_on': set}}."""
    universe: dict[str, dict] = {}
    for name, client in clients.items():
        ex = client.exchange
        try:
            markets = ex.markets or ex.load_markets()
        except Exception as e:
            print(f"  [{name}] load_markets failed: {str(e)[:120]}")
            continue
        try:
            tickers = ex.fetch_tickers()
        except Exception as e:
            print(f"  [{name}] fetch_tickers failed: {str(e)[:120]} (vol filter skipped)")
            tickers = {}
        spot_count = 0
        for sym, m in markets.items():
            if not m.get("active", True) or m.get("quote") != "USDT":
                continue
            if not m.get("spot", False):
                continue  # spot markets only (clean naming + longest history)
            base = m.get("base")
            if not base:
                continue
            qv = float((tickers.get(sym) or {}).get("quoteVolume") or 0)
            if qv < min_vol_usd:
                continue
            spot_count += 1
            rec = universe.setdefault(base, {"vol": 0.0, "venues": set(), "spot_on": set()})
            rec["vol"] = max(rec["vol"], qv)
            rec["venues"].add(name)
            rec["spot_on"].add(name)
        print(f"  [{name}] {spot_count} liquid USDT spot markets (>= ${min_vol_usd:,.0f} 24h vol)")
    return universe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=365.0)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument(
        "--min-vol", type=float, default=3_000_000.0, help="min 24h quote volume (USDT)"
    )
    ap.add_argument("--max-symbols", type=int, default=300, help="safety cap on universe size")
    ap.add_argument(
        "--throttle",
        type=float,
        default=0.6,
        help="inter-symbol sleep (s) — keep the live bot's API access healthy",
    )
    ap.add_argument(
        "--list-only", action="store_true", help="enumerate the universe and exit (no OHLCV fetch)"
    )
    args = ap.parse_args()

    print("Connecting to venues...")
    clients = _connected_clients()
    if not clients:
        print("No connected venues — aborting.")
        return 1

    print(f"Enumerating liquid USDT spot universe (min 24h vol ${args.min_vol:,.0f})...")
    universe = enumerate_liquid_bases(clients, args.min_vol)
    ranked = sorted(universe.items(), key=lambda kv: kv[1]["vol"], reverse=True)
    capped = ranked[: args.max_symbols]
    print(
        f"\nUNIVERSE: {len(universe)} unique liquid bases; using top {len(capped)} (cap {args.max_symbols})."
    )
    print("Top 20 by 24h vol: " + ", ".join(b for b, _ in capped[:20]))
    if args.list_only:
        print(
            f"\n--list-only: {len(capped)} symbols would be backfilled to {args.days:.0f}d {args.timeframe}."
        )
        return 0

    # Fetch source priority: prefer Binance (deepest history), else the venue that lists it.
    fetchers = {n: _make_spot_fetcher(c) for n, c in clients.items()}
    now = int(time.time())
    start = now - int(args.days * 24 * 3600)
    done = 0
    total_bars = 0
    failed = []
    for i, (base, rec) in enumerate(capped, 1):
        symbol = f"{base}/USDT"
        src = "binance" if "binance" in rec["spot_on"] else sorted(rec["spot_on"])[0]
        try:
            df = load_ohlcv_window(symbol, args.timeframe, start, now, fetcher=fetchers[src])
            n = len(df)
            total_bars += n
            done += 1
            if i % 10 == 0 or n == 0:
                print(f"  [{i}/{len(capped)}] {symbol:16s} via {src:8s} {n:6d} bars")
        except Exception as e:
            failed.append((symbol, str(e)[:80]))
            print(f"  [{i}/{len(capped)}] {symbol:16s} FAILED: {str(e)[:80]}")
        time.sleep(args.throttle)  # gentle: protect the live bot's API access

    print(
        f"\nDONE: {done}/{len(capped)} symbols cached, {total_bars:,} total bars. Failures: {len(failed)}"
    )
    if failed:
        print("Failed: " + ", ".join(s for s, _ in failed[:20]))
    print("Next: python scripts/run_alpha_search.py --timeframe 1h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
