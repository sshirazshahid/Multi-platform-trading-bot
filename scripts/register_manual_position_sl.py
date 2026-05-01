"""Register exchange-side stop-loss orders on ghost-reconciled manual positions.

Background — 2026-05-01 readiness audit
---------------------------------------
The bot's `position_tracker.import_orphans` ingests orphaned exchange
positions with `strategy="manual"`. The risk_manager later computes a
local SL for each (visible in `data/positions.json`), but the SL is
never registered as a stop order on the exchange itself
(`data/exchange_positions.json` shows `stop_loss: 0` for all manual
positions). The bot's soft-SL would only fire while it polls — if the
process crashes or loses connection, the position has zero protection
between cycles.

This script reuses `OrderManager._place_exchange_sl_tp` to register
exchange-side stop orders for each manual position. The fail-closed
policy in that method means a position whose SL placement fails is
immediately closed at market, NOT left exposed.

Usage
=====
    python scripts/register_manual_position_sl.py             # dry-run preview
    python scripts/register_manual_position_sl.py --commit    # actually place SL

Safety
======
  * Dry-run is the default; --commit required for any exchange call.
  * Per-position try/except — one failure does not stop the rest.
  * Skips if the bot already marked `_exchange_sl=True` (idempotent).
  * Skips if SL price is on the wrong side of current mark by > 5%
    (defense against stale SL values).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
from loguru import logger  # noqa: E402

from core.position_tracker import Position  # noqa: E402

POSITIONS_PATH = ROOT / "data" / "positions.json"


def _load_manual_positions() -> list[dict]:
    """Return the manual (ghost-reconciled) open positions from positions.json."""
    if not POSITIONS_PATH.exists():
        raise SystemExit(f"positions.json not found at {POSITIONS_PATH}")
    data = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
    return [
        p for p in data.get("open", [])
        if (p.get("strategy") == "manual"
            and (p.get("id") or "").startswith("MANUAL-")
            and not p.get("_exchange_sl", False))
    ]


def _build_exchanges():
    """Connect to all 3 exchanges. Mirrors BotEngine init logic."""
    from exchanges.binance_client import BinanceClient
    from exchanges.bybit_client import BybitClient
    from exchanges.bitget_client import BitgetClient

    out: dict = {}
    for name, klass in (
        ("binance", BinanceClient),
        ("bybit", BybitClient),
        ("bitget", BitgetClient),
    ):
        try:
            ex = klass()
            if getattr(ex, "_connected", False):
                out[name] = ex
                logger.info(f"[setup] connected: {name}")
            else:
                logger.warning(f"[setup] not connected: {name} (skipping)")
        except Exception as e:
            logger.error(f"[setup] init failed: {name}: {e}")
    return out


def _verify_position_still_open(exchange, symbol: str, side: str, size: float) -> bool:
    """Confirm the position still exists on exchange before placing SL.

    ccxt normalises side to 'long'/'short'; bot tracks 'buy'/'sell'. Map both.
    """
    long_short = {"buy": "long", "sell": "short"}
    expected = long_short.get(side, side)
    try:
        positions = exchange.fetch_positions([symbol]) or []
        for p in positions:
            ex_size = abs(float(p.get("contracts") or p.get("info", {}).get("size") or 0))
            ex_side = (p.get("side") or "").lower()
            if ex_size <= 0:
                continue
            if ex_side and ex_side != expected:
                continue
            # Tolerance: exchange size matches within 1%
            if abs(ex_size - abs(size)) / max(abs(size), 1e-9) < 0.01:
                return True
        return False
    except Exception as e:
        logger.warning(f"[verify] fetch_positions failed for {symbol}: {e}")
        return False  # err on the side of NOT placing if we can't verify


def _sanity_check_sl(symbol: str, side: str, sl: float, mark: float) -> tuple[bool, str]:
    """Reject SL prices that are wildly out of range vs current mark."""
    if sl <= 0:
        return False, "sl_zero"
    if mark <= 0:
        return False, "mark_zero"
    pct_dist = abs(sl - mark) / mark
    if side == "buy":
        # long: SL must be BELOW mark
        if sl >= mark:
            return False, f"sl_{sl:.6g}_above_mark_{mark:.6g}_for_long"
        if pct_dist > 0.10:
            return False, f"sl_{pct_dist:.1%}_below_mark_too_far"
    else:
        # short: SL must be ABOVE mark
        if sl <= mark:
            return False, f"sl_{sl:.6g}_below_mark_{mark:.6g}_for_short"
        if pct_dist > 0.10:
            return False, f"sl_{pct_dist:.1%}_above_mark_too_far"
    return True, f"ok({pct_dist:.2%}_from_mark)"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="Actually place SL orders. Default is dry-run.")
    args = ap.parse_args()

    positions = _load_manual_positions()
    if not positions:
        print("no manual positions need SL registration. exit.")
        return

    print(f"\nfound {len(positions)} manual position(s) without exchange-side SL:\n")
    print(f"{'#':<3} {'exchange':<10} {'symbol':<22} {'side':<5} "
          f"{'size':>12} {'entry':>10} {'sl':>10}")
    print("-" * 80)
    for i, p in enumerate(positions, 1):
        print(f"{i:<3} {p['exchange']:<10} {p['symbol']:<22} {p['side']:<5} "
              f"{p['size']:>12.6g} {p['entry_price']:>10.6g} {p['stop_loss']:>10.6g}")

    if not args.commit:
        print("\nDRY RUN — no orders placed. Re-run with --commit to register SL.")
        return

    print("\nconnecting to exchanges ...")
    exchanges = _build_exchanges()
    if not exchanges:
        raise SystemExit("no exchanges connected — abort")

    # Build a minimal OrderManager — _place_exchange_sl_tp only needs the
    # exchange registry, tracker (for SL state), and notifier (for alerts).
    from core.order_manager import OrderManager
    from core.position_tracker import PositionTracker
    from core.risk_manager import RiskManager
    from utils.notifier import TelegramNotifier
    tracker = PositionTracker()
    risk = RiskManager()
    notifier = TelegramNotifier()
    om = OrderManager(tracker, risk, notifier)
    om.set_exchanges(exchanges)

    successes = 0
    failures = 0
    for p in positions:
        ex = exchanges.get(p["exchange"])
        if ex is None:
            logger.warning(f"skip {p['id']}: exchange '{p['exchange']}' not connected")
            failures += 1
            continue

        # Recreate Position from dict for the placement helper.
        pos = Position(
            id=p["id"], exchange=p["exchange"], symbol=p["symbol"], side=p["side"],
            market_type=p.get("market_type", "futures"), strategy=p.get("strategy", "manual"),
            entry_price=p["entry_price"], size=p["size"],
            stop_loss=p["stop_loss"], take_profit=p.get("take_profit", 0),
            leverage=p.get("leverage", 1),
            paper_trade=False,
        )

        # Pull current mark for sanity-check.
        try:
            ticker = ex.fetch_ticker(p["symbol"], market_type="futures")
            mark = float(ticker.get("last") or ticker.get("close") or 0)
        except Exception as e:
            logger.error(f"{pos.id}: fetch_ticker failed: {e}")
            failures += 1
            continue

        ok, why = _sanity_check_sl(p["symbol"], p["side"], p["stop_loss"], mark)
        if not ok:
            logger.error(f"{pos.id}: SL sanity failed: {why} (mark={mark})")
            failures += 1
            continue

        if not _verify_position_still_open(ex, p["symbol"], p["side"], p["size"]):
            logger.warning(f"{pos.id}: position not found on exchange — skipping")
            failures += 1
            continue

        logger.info(
            f"placing SL: {p['exchange']} {p['symbol']} {p['side']} "
            f"size={p['size']} sl={p['stop_loss']} mark={mark} (sanity={why})")
        try:
            om._place_exchange_sl_tp(  # noqa: SLF001
                ex, pos, p["stop_loss"], p["take_profit"], p["side"],
                p["symbol"], p["size"], p.get("market_type", "futures"))
            if getattr(pos, "_exchange_sl", False):
                successes += 1
                logger.info(f"  ✓ SL registered for {pos.id}")
            else:
                logger.warning(f"  ✗ _place_exchange_sl_tp returned without _exchange_sl=True")
                failures += 1
        except Exception as e:
            logger.error(f"  ✗ placement raised: {e}")
            failures += 1

    print(f"\nresult: {successes} placed, {failures} failed/skipped")


if __name__ == "__main__":
    main()
