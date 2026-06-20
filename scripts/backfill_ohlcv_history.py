"""Stage 0: one-shot 1h OHLCV backfill to ~N years for the alpha search.

Reuses core.feature_store.load_ohlcv_window (paginate + dedup + parquet
write). The bot's BaseExchange.fetch_ohlcv has no `since` parameter, so we
build a fetcher that calls the underlying ccxt client directly with `since`.

Idempotent: re-running only fills missing bars. Symbols are derived from the
existing cache filenames so the panel stays consistent (BASE-USDT_1h.parquet
-> 'BASE/USDT').

Usage:
    python scripts/backfill_ohlcv_history.py --years 3
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

CACHE = ROOT / "data" / "ohlcv_cache"


def make_fetcher(client, *, market_type: str = "spot"):
    """Return a callable(symbol, timeframe, since_ms, limit) -> ccxt rows."""
    params = client._futures_params() if market_type == "futures" else {}

    def _fetch(symbol, timeframe, since_ms, limit):
        return (
            client.exchange.fetch_ohlcv(
                symbol, timeframe, since=int(since_ms), limit=int(limit), params=params
            )
            or []
        )

    return _fetch


def symbols_from_cache(timeframe: str = "1h") -> list[str]:
    out = []
    for p in sorted(CACHE.glob(f"*_{timeframe}.parquet")):
        base = p.name[: -len(f"_{timeframe}.parquet")]  # 'BTC-USDT'
        out.append(base.replace("-", "/", 1))  # 'BTC/USDT'
    return out


def backfill(
    client, *, years: float = 3.0, timeframe: str = "1h", market_type: str = "spot"
) -> dict[str, int]:
    now = int(time.time())
    start = now - int(years * 365 * 24 * 3600)
    fetcher = make_fetcher(client, market_type=market_type)
    counts: dict[str, int] = {}
    for sym in symbols_from_cache(timeframe):
        df = load_ohlcv_window(sym, timeframe, start, now, fetcher=fetcher)
        counts[sym] = len(df)
        print(f"  {sym:14s} {len(df):6d} bars")
    return counts


def _build_binance():
    import config
    from exchanges.binance_client import BinanceClient

    return BinanceClient(config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--timeframe", default="1h")
    args = ap.parse_args()
    client = _build_binance()
    print(f"Backfilling {args.timeframe} ~{args.years}y from Binance...")
    counts = backfill(client, years=args.years, timeframe=args.timeframe)
    total = sum(counts.values())
    print(f"Done. {len(counts)} symbols, {total} total bars.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
