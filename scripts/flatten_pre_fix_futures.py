"""Flatten any live futures positions opened before the 2026-04-27 risk-trim
fix so the new leverage/cap regime starts clean.

Reads live positions directly from each exchange (does not trust
data/positions.json snapshots, which were stale). For each LIVE futures
position it submits a reduceOnly market order on the opposite side.

Safe to run multiple times: reduceOnly orders cannot open new exposure,
and zero-size positions return harmlessly.

Usage:
    python scripts/flatten_pre_fix_futures.py            # dry-run, prints plan
    python scripts/flatten_pre_fix_futures.py --execute  # actually close
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root or scripts/ — pin the path.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from config import (  # noqa: E402
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    BITGET_API_KEY,
    BITGET_PASSPHRASE,
    BITGET_SECRET_KEY,
    BYBIT_API_KEY,
    BYBIT_SECRET_KEY,
)
from exchanges.base import BaseExchange  # noqa: E402
from exchanges.binance_client import BinanceClient  # noqa: E402
from exchanges.bitget_client import BitgetClient  # noqa: E402
from exchanges.bybit_client import BybitClient  # noqa: E402


def _build_clients() -> dict[str, BaseExchange]:
    out: dict[str, BaseExchange] = {}
    if BINANCE_API_KEY and BINANCE_SECRET_KEY:
        out["binance"] = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET_KEY)
    if BYBIT_API_KEY and BYBIT_SECRET_KEY:
        out["bybit"] = BybitClient(BYBIT_API_KEY, BYBIT_SECRET_KEY)
    if BITGET_API_KEY and BITGET_SECRET_KEY and BITGET_PASSPHRASE:
        out["bitget"] = BitgetClient(BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--execute", action="store_true", help="Actually submit closing orders. Default is dry-run."
    )
    args = ap.parse_args()

    clients = _build_clients()
    if not clients:
        print("No exchange credentials available — nothing to do.")
        return 0

    plan: list[tuple[str, str, str, float]] = []  # (exch, sym, close_side, size)
    for name, ex in clients.items():
        try:
            positions = ex.fetch_positions() or []
        except Exception as e:
            print(f"[{name}] fetch_positions failed: {e}")
            continue
        for p in positions:
            size = float(p.get("contracts") or p.get("size") or 0)
            side = (p.get("side") or "").lower()
            sym = p.get("symbol") or ""
            if size <= 0 or side not in ("long", "short", "buy", "sell") or not sym:
                continue
            close_side = "sell" if side in ("long", "buy") else "buy"
            plan.append((name, sym, close_side, size))

    if not plan:
        print("No live futures positions found across configured exchanges.")
        return 0

    print(f"Plan ({len(plan)} order(s)):")
    for exch, sym, close_side, size in plan:
        print(f"  {exch:<8} {sym:<22} CLOSE via {close_side.upper()} size={size}")

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to submit.")
        return 0

    print("\nExecuting...")
    failed = 0
    for exch, sym, close_side, size in plan:
        client = clients[exch]
        try:
            client.create_order(
                sym,
                "market",
                close_side,
                size,
                None,
                params={"reduceOnly": True},
                market_type="futures",
            )
            print(f"  {exch} {sym} {close_side} {size} — submitted")
        except Exception as e:
            failed += 1
            print(f"  {exch} {sym} {close_side} {size} — FAILED: {e}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
