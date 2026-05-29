#!/usr/bin/env python3
"""
scripts/monitor_close_on_profit.py

Polls all open FUTURES positions every POLL_SEC seconds.
Closes any position the moment its unrealized PnL turns positive.
Safe to run alongside the live bot — uses reduceOnly orders;
a harmless "already closed" error is caught if the bot closes first.

Usage:
    python scripts/monitor_close_on_profit.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dotenv
dotenv.load_dotenv()

from loguru import logger

POSITIONS_FILE = Path("data/positions.json")
POLL_SEC = 20
MIN_PROFIT_USDT = 0.01   # close as soon as > 1 cent profit (covers fees)

# ── Exchange factory ─────────────────────────────────────────────────────────

_exchange_cache: dict = {}

def _get_exchange(name: str):
    key = name.lower()
    if key in _exchange_cache:
        return _exchange_cache[key]
    if "binance" in key:
        from exchanges.binance_client import BinanceClient
        client = BinanceClient(
            api_key=os.getenv("BINANCE_API_KEY"),
            secret=os.getenv("BINANCE_SECRET"),
        )
    elif "bybit" in key:
        from exchanges.bybit_client import BybitClient
        client = BybitClient(
            api_key=os.getenv("BYBIT_API_KEY"),
            secret=os.getenv("BYBIT_SECRET"),
        )
    elif "bitget" in key:
        from exchanges.bitget_client import BitgetClient
        client = BitgetClient(
            api_key=os.getenv("BITGET_API_KEY"),
            secret=os.getenv("BITGET_SECRET"),
            passphrase=os.getenv("BITGET_PASSPHRASE"),
        )
    else:
        logger.warning(f"Unknown exchange: {name}")
        return None
    _exchange_cache[key] = client
    return client


# ── Positions loader ─────────────────────────────────────────────────────────

def load_open_positions() -> list[dict]:
    try:
        data = json.loads(POSITIONS_FILE.read_text())
        return [p for p in data.get("open", []) if not p.get("paper_trade")]
    except Exception as e:
        logger.error(f"positions.json read error: {e}")
        return []


# ── Price fetch ──────────────────────────────────────────────────────────────

def fetch_price(client, symbol: str) -> float | None:
    try:
        ex = client.exchange
        ticker = ex.fetch_ticker(symbol)
        return float(ticker.get("last") or ticker.get("mark") or 0) or None
    except Exception as e:
        logger.warning(f"  price fetch failed {symbol}: {e}")
        return None


# ── PnL calc ─────────────────────────────────────────────────────────────────

def calc_upnl(pos: dict, current_price: float) -> float:
    entry = float(pos["entry_price"])
    size  = float(pos["size"])
    side  = pos["side"]          # "buy" or "sell"
    if side == "sell":           # short: profit when price falls
        return (entry - current_price) * size
    else:                        # long: profit when price rises
        return (current_price - entry) * size


# ── Close position ───────────────────────────────────────────────────────────

def close_position(client, pos: dict, upnl: float) -> bool:
    symbol = pos["symbol"]
    size   = float(pos["size"])
    side   = pos["side"]
    close_side = "buy" if side == "sell" else "sell"
    ex_name = pos["exchange"].lower()
    ex = client.exchange

    logger.info(f"  CLOSING {symbol} {side} size={size} upnl={upnl:+.4f} USDT ...")
    try:
        params: dict = {"reduceOnly": True}
        if "bybit" in ex_name:
            params["category"] = "linear"
        elif "bitget" in ex_name:
            params["productType"] = "USDT-FUTURES"

        order = ex.create_market_order(
            symbol=symbol,
            side=close_side,
            amount=size,
            params=params,
        )
        oid = order.get("id", "?")
        logger.success(f"  ✓ CLOSED {symbol} order_id={oid}  profit={upnl:+.4f} USDT")
        return True
    except Exception as e:
        err = str(e)
        if "already" in err.lower() or "position" in err.lower():
            logger.info(f"  {symbol} already closed (bot beat us): {err[:80]}")
            return True
        logger.error(f"  close FAILED {symbol}: {err[:120]}")
        return False


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("PROFIT MONITOR — close when any position turns green")
    logger.info(f"Poll every {POLL_SEC}s | Min profit threshold: {MIN_PROFIT_USDT} USDT")
    logger.info("=" * 60)

    closed_ids: set[str] = set()

    while True:
        positions = [p for p in load_open_positions() if p["id"] not in closed_ids]
        if not positions:
            logger.success("All positions closed. Monitor done.")
            break

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        logger.info(f"\n[{ts}] Checking {len(positions)} position(s)...")

        for pos in positions:
            symbol = pos["symbol"]
            entry  = float(pos["entry_price"])
            side   = pos["side"]
            tp     = float(pos.get("take_profit") or 0)

            client = _get_exchange(pos["exchange"])
            if client is None:
                continue

            price = fetch_price(client, symbol)
            if price is None:
                continue

            upnl    = calc_upnl(pos, price)
            upnl_pct = upnl / (entry * float(pos["size"])) * 100

            # Distance to TP
            if tp and side == "sell":
                pct_to_tp = (entry - price) / (entry - tp) * 100 if (entry - tp) != 0 else 0
            elif tp and side == "buy":
                pct_to_tp = (price - entry) / (tp - entry) * 100 if (tp - entry) != 0 else 0
            else:
                pct_to_tp = 0

            status_icon = "🟢" if upnl > 0 else "🔴"
            logger.info(
                f"  {status_icon} {pos['exchange']:6} {symbol:22} "
                f"entry={entry:.4f} now={price:.4f} "
                f"upnl={upnl:+.4f}U ({upnl_pct:+.2f}%) "
                f"TP_dist={pct_to_tp:.0f}%"
            )

            if upnl >= MIN_PROFIT_USDT:
                success = close_position(client, pos, upnl)
                if success:
                    closed_ids.add(pos["id"])

        remaining = len(positions) - len([p for p in positions if p["id"] in closed_ids])
        logger.info(f"  {remaining} position(s) still open — next check in {POLL_SEC}s")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
