"""Cancel orphaned stop/conditional orders on exchanges.

Background — 2026-05-02
-----------------------
Bybit has a per-account limit on active conditional/stop orders. When
positions close without their associated SL/TP conditional orders being
cancelled, those orders accumulate over time. Eventually new entries
fail with retCode=110009 ("already had 10 working normal stop orders"),
which triggers the bot's fail-closed policy → trade closed at break-even
minus fees. This script is the cleanup tool to recover from that state.

What counts as an orphan
========================
A stop/conditional order is an "orphan" if the exchange has NO open
position matching its (symbol, opposite_side) — meaning there's nothing
left for the conditional order to protect or take profit on.

Example: a Sell-side conditional order with reduceOnly=True is meant to
close a long position. If there's no long position open, it's orphaned.

Usage
=====
    python scripts/cleanup_orphan_stop_orders.py             # dry-run preview
    python scripts/cleanup_orphan_stop_orders.py --commit    # actually cancel
    python scripts/cleanup_orphan_stop_orders.py --exchange bybit --commit  # one venue

Safety
======
  * Dry-run is the default; --commit required for any cancel.
  * Per-order try/except — one failure doesn't stop the rest.
  * Only cancels orders where the matching position is FLAT (size=0).
  * Cross-checks per-symbol open positions before cancelling.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
from loguru import logger  # noqa: E402


def _build_exchanges(filter_name: str | None = None):
    """Connect to selected exchanges. Returns dict[name → client]."""
    from exchanges.binance_client import BinanceClient
    from exchanges.bybit_client import BybitClient
    from exchanges.bitget_client import BitgetClient

    out: dict = {}
    candidates = (
        ("binance", BinanceClient),
        ("bybit", BybitClient),
        ("bitget", BitgetClient),
    )
    for name, klass in candidates:
        if filter_name and name != filter_name:
            continue
        try:
            ex = klass()
            if getattr(ex, "_connected", False):
                out[name] = ex
                logger.info(f"[setup] connected: {name}")
        except Exception as e:
            logger.error(f"[setup] init failed: {name}: {e}")
    return out


def _bybit_open_stop_orders(ex):
    """Return all open stop/conditional orders on Bybit linear (USDT-margined).

    Bybit v5 API requires settleCoin or symbol filter — we use settleCoin=USDT
    to get all linear stop orders in one call.
    """
    try:
        resp = ex.exchange.privateGetV5OrderRealtime({
            "category": "linear",
            "orderFilter": "StopOrder",
            "settleCoin": "USDT",
            "limit": 50,
        })
        return resp.get("result", {}).get("list", [])
    except Exception as e:
        logger.error(f"[bybit] fetch stop orders failed: {e}")
        return []


def _bybit_open_positions_by_symbol(ex, symbols: list[str]):
    """Return {bybit_native_symbol: float_size} for OPEN positions on the
    given list of native symbols (e.g. 'DOGEUSDT'). Bybit's fetch_positions
    rejects bulk fetches with category-mode errors — we iterate per-symbol."""
    out: dict = {}
    for native in set(symbols):
        try:
            # Convert DOGEUSDT → DOGE/USDT:USDT for ccxt
            base = native[:-4] if native.endswith("USDT") else native
            ccxt_sym = f"{base}/USDT:USDT"
            positions = ex.exchange.fetch_positions([ccxt_sym], params={"category": "linear"})
            for p in positions:
                sz = abs(float(p.get("contracts") or p.get("info", {}).get("size") or 0))
                if sz > 0:
                    out[native] = sz
                    break
        except Exception as e:
            logger.warning(
                f"[bybit] fetch position for {native} failed: {str(e)[:80]} — "
                f"treating as 'unknown' (will NOT cancel its stop orders)")
            # Defensive: mark as "unknown" so we don't cancel its orders
            out[native] = -1.0  # sentinel for "couldn't verify"
    return out


def _cleanup_bybit(ex, commit: bool):
    """Find and (optionally) cancel orphan stop orders on Bybit."""
    orders = _bybit_open_stop_orders(ex)
    symbols_with_orders = list({o.get("symbol", "") for o in orders if o.get("symbol")})
    positions = _bybit_open_positions_by_symbol(ex, symbols_with_orders)

    print(f"\n=== Bybit orphan-stop scan ===")
    print(f"  Total stop orders open: {len(orders)}")
    open_syms = [s for s, sz in positions.items() if sz > 0]
    unverified = [s for s, sz in positions.items() if sz == -1.0]
    print(f"  Symbols with positions: {open_syms}")
    if unverified:
        print(f"  Symbols unverified:     {unverified} (will NOT cancel — fetch failed)")
    print()

    orphans = []
    keepers = []
    skipped = []
    for o in orders:
        sym = o.get("symbol", "?")
        side = o.get("side", "?")
        qty = o.get("qty", "?")
        trigger = o.get("triggerPrice", "?")
        oid = o.get("orderId", "")
        pos_sz = positions.get(sym, 0.0)
        if pos_sz == -1.0:
            # Couldn't verify — defensive skip.
            skipped.append((sym, side, qty, trigger, oid))
        elif pos_sz > 0:
            keepers.append((sym, side, qty, trigger, oid, pos_sz))
        else:
            orphans.append((sym, side, qty, trigger, oid))

    if keepers:
        print(f"  Keepers ({len(keepers)} — symbols with open positions, NOT cancelling):")
        for sym, side, qty, trigger, oid, pos_sz in keepers:
            print(f"    KEEP {sym:14} {side:5} qty={qty:>8} trig={trigger:>10} (pos size={pos_sz})")
        print()

    if skipped:
        print(f"  Unverified ({len(skipped)} — fetch failed, defensive skip):")
        for sym, side, qty, trigger, oid in skipped:
            print(f"    SKIP {sym:14} {side:5} qty={qty:>8} trig={trigger:>10}")
        print()

    if not orphans:
        print("  No verifiable orphans found.")
        return 0, 0

    print(f"  Orphans ({len(orphans)} — symbol has no open position):")
    for sym, side, qty, trigger, oid in orphans:
        print(f"    ORPHAN {sym:14} {side:5} qty={qty:>8} trig={trigger:>10} id={oid[:12]}...")

    if not commit:
        print(f"\n  DRY RUN — re-run with --commit to cancel {len(orphans)} orphan(s).")
        return len(orphans), 0

    print(f"\n  CANCELLING {len(orphans)} orphan(s)...")
    cancelled = 0
    failed = 0
    for sym, side, qty, trigger, oid in orphans:
        try:
            ex.exchange.privatePostV5OrderCancel({
                "category": "linear",
                "symbol": sym,
                "orderId": oid,
            })
            cancelled += 1
            logger.info(f"  ✓ cancelled {sym} {side} {oid[:12]}...")
        except Exception as e:
            failed += 1
            logger.error(f"  ✗ {sym} {oid[:12]}: {str(e)[:120]}")
    return cancelled + failed, cancelled


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="Actually cancel orders. Default is dry-run.")
    ap.add_argument("--exchange", default=None, choices=("binance", "bybit", "bitget"),
                    help="Limit to one exchange. Default: all connected.")
    args = ap.parse_args()

    exchanges = _build_exchanges(args.exchange)
    if not exchanges:
        raise SystemExit("no exchanges connected")

    total_orphans = 0
    total_cancelled = 0
    if "bybit" in exchanges:
        n, c = _cleanup_bybit(exchanges["bybit"], args.commit)
        total_orphans += n
        total_cancelled += c

    # binance/bitget cleanup TODO if needed — not the immediate problem.

    print(f"\n=== SUMMARY ===")
    print(f"  Total orphans found: {total_orphans}")
    if args.commit:
        print(f"  Cancelled:           {total_cancelled}")
    else:
        print(f"  (dry-run — no cancellations performed)")


if __name__ == "__main__":
    main()
