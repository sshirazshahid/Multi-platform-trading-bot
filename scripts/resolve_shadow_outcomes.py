"""Shadow-outcome resolver runner — wires the KEYSTONE into ops.

WHY: core/shadow_resolver.py ("Phase 0b forward resolver (KEYSTONE)") was
shipped with unit tests but never called from any runtime path — no engine
hook, no script, no scheduled task. Result (found 2026-07-05): 2,105 PENDING
shadow_decisions rows since the 2026-06-30 evidence rebuild and a permanently
empty shadow_outcomes table, which makes the resolved-only promotion gate
structurally unsatisfiable. This runner closes that gap.

Design (harvester skeleton): standalone, PAPER/sim only, read-only market data
via public ccxt OHLCV (no API keys), single process via utils.process_lock,
fail-open (a venue/fetch error skips those rows; they stay PENDING for the
next run). Candles are fetched ONCE per (venue, symbol, timeframe) group and
sliced per row — 2,000+ pending rows collapse to a handful of API calls.

Run: python scripts/resolve_shadow_outcomes.py            (one pass)
     python scripts/resolve_shadow_outcomes.py --dry-run  (count only)
Schedule: hourly Windows task "TradingBot-ShadowResolver".
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MAX_GROUP_FETCHES = 60
_TF_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _default_horizon_bars() -> int:
    """Resolver's default horizon for legacy horizon_bars=0 rows (lazy import
    so tests can load this module without core on sys.path side effects)."""
    from core.shadow_resolver import DEFAULT_HORIZON_BARS
    return int(DEFAULT_HORIZON_BARS)


def _tf_seconds(tf: str) -> int:
    """Parse a ccxt-style timeframe ('15m', '1h', '1d') into seconds."""
    tf = str(tf).strip().lower()
    try:
        return int(tf[:-1]) * _TF_UNITS[tf[-1]]
    except (KeyError, ValueError, IndexError):
        return 3600  # conservative default: 1h


def build_fetch_candles(fetch_ohlcv, *, now_ms=None, max_group_fetches=DEFAULT_MAX_GROUP_FETCHES):
    """Build the fetch_candles(row) callable for shadow_resolver.resolve_pending.

    `fetch_ohlcv(venue, symbol, timeframe, since_ms) -> [[ts_ms,o,h,l,c,v], ...]`
    is the raw candle source (ccxt in production, a stub in tests).

    Guarantees required by the resolver's no-repaint contract:
    - only bars strictly AFTER the entry moment (bar open > entry ts) are
      returned, so the bar that was forming at entry never repaints history;
    - the currently-forming bar (open + timeframe > now) is dropped;
    - per-row output is capped at horizon_bars + 2.

    One fetch per (venue, symbol, timeframe) group, cached; if a later row in
    the same group has an EARLIER entry, the cache is widened by refetching
    from that entry. New-group fetches stop after `max_group_fetches` per run
    (remaining rows stay PENDING for the next run).
    """
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    cache: dict[tuple, tuple[int, list]] = {}  # key -> (since_ms, candles)
    fetches = 0

    def fetch_candles(row: dict) -> list:
        nonlocal fetches
        try:
            entry_ms = int(row["ts"]) * 1000
            venue = str(row["venue"])
            symbol = str(row["symbol"])
            tf = str(row["timeframe"])
        except (KeyError, TypeError, ValueError):
            return []
        if not venue or not symbol or not tf or entry_ms <= 0:
            return []

        tf_ms = _tf_seconds(tf) * 1000
        horizon = int(row.get("horizon_bars") or 0)
        if horizon <= 0:
            # Match core.shadow_resolver.resolve_one: legacy rows without an
            # explicit horizon use the documented default, so the fetch cap
            # and the resolver's censoring guard always agree.
            horizon = _default_horizon_bars()
        key = (venue, symbol, tf)

        cached = cache.get(key)
        if cached is None or entry_ms < cached[0]:
            if cached is None and fetches >= max_group_fetches:
                return []  # budget spent; row stays PENDING for next run
            try:
                candles = list(fetch_ohlcv(venue, symbol, tf, entry_ms))
            except Exception:
                return []  # fail-open: unresolved rows retry next run
            fetches += 1
            cache[key] = (entry_ms, candles)
            cached = cache[key]

        out = [c for c in cached[1] if c[0] > entry_ms and c[0] + tf_ms <= now_ms]
        if horizon > 0:
            out = out[: horizon + 2]
        return out

    return fetch_candles


def _make_ccxt_fetcher():
    """Public-data ccxt OHLCV fetcher with per-venue client cache. No API keys."""
    import ccxt  # deferred: tests never import ccxt

    clients: dict[str, object] = {}

    def fetch_ohlcv(venue: str, symbol: str, timeframe: str, since_ms: int):
        client = clients.get(venue)
        if client is None:
            cls = getattr(ccxt, venue, None)
            if cls is None:
                return []
            client = cls({"enableRateLimit": True})
            client.load_markets()
            clients[venue] = client
        return client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)

    return fetch_ohlcv


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Resolve PENDING shadow decisions into shadow_outcomes.")
    parser.add_argument("--dry-run", action="store_true", help="count PENDING rows and exit")
    parser.add_argument(
        "--max-group-fetches", type=int, default=DEFAULT_MAX_GROUP_FETCHES,
        help="cap on new (venue,symbol,timeframe) candle fetches per run",
    )
    args = parser.parse_args(argv)

    # cwd-proof (2026-07-07): the hourly Task Scheduler entry used to launch
    # with cwd=System32; core.warehouse's relative WAREHOUSE_PATH then crashed
    # on mkdir("data") every run for 2 days (exit 1, zero outcomes resolved).
    # Relative-to-cwd is the repo-wide contract, so anchor the cwd here.
    import os
    os.chdir(ROOT)

    from utils.process_lock import acquire_process_lock

    lock = acquire_process_lock("resolve_shadow_outcomes", root=ROOT)
    if lock is None:
        print("[resolve_shadow] another instance holds the lock; exiting")
        return 0

    from core.warehouse import Warehouse

    wh = Warehouse()
    pending = wh.query(
        "SELECT COUNT(*) AS n FROM shadow_decisions WHERE label_status='PENDING' AND entry_px IS NOT NULL"
    )[0]["n"]
    print(f"[resolve_shadow] pending resolvable rows: {pending}")
    if args.dry_run or pending == 0:
        return 0

    from core.shadow_resolver import resolve_pending

    fetch_candles = build_fetch_candles(
        _make_ccxt_fetcher(), max_group_fetches=args.max_group_fetches
    )
    summary = resolve_pending(wh, fetch_candles)
    print(
        f"[resolve_shadow] resolved={summary['resolved']} "
        f"skipped={summary['skipped']} of n_pending={summary['n_pending']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
