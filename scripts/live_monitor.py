#!/usr/bin/env python3
"""
scripts/live_monitor.py — continuous bot monitor.
Prints a snapshot every 60s: live uPnL, daily PnL, open positions,
new trades detected in log, cycle timing, errors.
"""
from __future__ import annotations
import json, os, sys, time, re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dotenv; dotenv.load_dotenv()

from loguru import logger
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | {message}", level="INFO")

POSITIONS_FILE  = Path("data/positions.json")
RISK_FILE       = Path("data/risk_state.json")
LOG_FILE        = Path(f"logs/bot_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log")
POLL_SEC        = 60

# ── Exchange factory ──────────────────────────────────────────────────────────
_clients: dict = {}
def _client(name: str):
    k = name.lower()
    if k in _clients:
        return _clients[k]
    if "binance" in k:
        from exchanges.binance_client import BinanceClient
        c = BinanceClient(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET"))
    elif "bybit" in k:
        from exchanges.bybit_client import BybitClient
        c = BybitClient(os.getenv("BYBIT_API_KEY"), os.getenv("BYBIT_SECRET"))
    elif "bitget" in k:
        from exchanges.bitget_client import BitgetClient
        c = BitgetClient(os.getenv("BITGET_API_KEY"), os.getenv("BITGET_SECRET"), os.getenv("BITGET_PASSPHRASE"))
    else:
        return None
    _clients[k] = c
    return c

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_open():
    return json.loads(POSITIONS_FILE.read_text()).get("open", [])

def load_risk():
    try:
        return json.loads(RISK_FILE.read_text())
    except Exception:
        return {}

def upnl(pos, price):
    e, sz = pos["entry_price"], pos["size"]
    return (e - price) * sz if pos["side"] == "sell" else (price - e) * sz

def fetch_price(pos):
    cl = _client(pos["exchange"])
    if not cl:
        return None
    try:
        return cl.exchange.fetch_ticker(pos["symbol"])["last"]
    except Exception:
        return None

# ── Log tail tracker ──────────────────────────────────────────────────────────
_log_seen_lines = 0
_cycle_timestamps: list[float] = []

def scan_log_new_lines() -> dict:
    global _log_seen_lines, _cycle_timestamps
    if not LOG_FILE.exists():
        return {}
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    new_lines = lines[_log_seen_lines:]
    _log_seen_lines = len(lines)

    new_trades, errors, cycles = [], [], []
    for line in new_lines:
        # New cycle fired
        if "Portfolio cycle:" in line:
            cycles.append(line.strip())
            _cycle_timestamps.append(time.time())
        # Trade opened
        if "OPEN executed" in line or "Opened" in line:
            new_trades.append(("OPEN", line.strip()))
        # Trade closed
        if "CLOSE executed" in line or "Closed" in line or "mcp_take_profit" in line:
            new_trades.append(("CLOSE", line.strip()))
        # Errors (skip debug noise)
        if "| ERROR" in line and "fetch_ohlcv" not in line and "not available" not in line:
            errors.append(line.strip())
        # Halt
        if "HALTED" in line or "halt_reason" in line.lower():
            errors.append("⚠ HALT: " + line.strip())

    # Compute avg cycle interval from last 5 cycles
    avg_interval = None
    if len(_cycle_timestamps) >= 2:
        diffs = [_cycle_timestamps[i+1] - _cycle_timestamps[i]
                 for i in range(max(0, len(_cycle_timestamps)-6), len(_cycle_timestamps)-1)]
        avg_interval = sum(diffs) / len(diffs) if diffs else None

    return {
        "new_trades": new_trades,
        "errors": errors,
        "cycles_fired": len(cycles),
        "avg_cycle_s": avg_interval,
    }

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("LIVE MONITOR — 60s snapshots | Ctrl+C to stop")
    logger.info("=" * 60)

    # Prime the log pointer at current end (don't spam old history)
    if LOG_FILE.exists():
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            _log_seen_lines_init = len(f.readlines())
        global _log_seen_lines
        _log_seen_lines = _log_seen_lines_init

    iteration = 0
    while True:
        time.sleep(POLL_SEC)
        iteration += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        positions = load_open()
        risk = load_risk()
        log_info = scan_log_new_lines()

        # ── Fetch live prices in parallel ───────────────────────────────────
        from concurrent.futures import ThreadPoolExecutor
        prices = {}
        if positions:
            with ThreadPoolExecutor(max_workers=min(5, len(positions))) as pool:
                results = pool.map(
                    lambda p: (p["id"], fetch_price(p)),
                    positions
                )
                for pid, price in results:
                    if price is not None:
                        prices[pid] = price

        # ── Build report ────────────────────────────────────────────────────
        logger.info(f"{'='*60}")
        logger.info(f"[{ts}] Snapshot #{iteration}")
        logger.info(f"{'='*60}")

        # Daily PnL / halt
        daily_pnl = risk.get("daily_pnl", 0)
        is_halted  = risk.get("is_halted", False)
        balance    = risk.get("balance", 0)
        halt_icon  = "HALTED" if is_halted else "running"
        logger.info(f"  Bot: {halt_icon} | daily_pnl={daily_pnl:+.4f}U | balance=${balance:.1f}")

        # Cycle cadence
        if log_info["avg_cycle_s"]:
            logger.info(f"  Cycle cadence: avg {log_info['avg_cycle_s']:.0f}s (target 60s) | {log_info['cycles_fired']} new cycles this window")

        # Positions
        total_upnl_val = 0.0
        logger.info(f"  Positions ({len(positions)} open):")
        for p in positions:
            price = prices.get(p["id"])
            if price is not None:
                u = upnl(p, price)
                total_upnl_val += u
                icon = "+" if u > 0 else "-"
                age_min = (time.time() - p.get("open_time", time.time())) / 60
                logger.info(
                    f"    [{icon}] {p['exchange']:6} {p['symbol']:22} "
                    f"uPnL={u:+.4f}U age={age_min:.0f}m "
                    f"entry={p['entry_price']:.4f} now={price:.4f}")
            else:
                logger.info(f"    [?] {p['exchange']:6} {p['symbol']:22} price unavailable")
        logger.info(f"  TOTAL uPnL: {total_upnl_val:+.4f} USDT")

        # New trades
        if log_info["new_trades"]:
            logger.info(f"  NEW TRADES this window:")
            for kind, line in log_info["new_trades"]:
                logger.info(f"    [{kind}] {line[-120:]}")

        # Errors
        if log_info["errors"]:
            logger.info(f"  ERRORS/ALERTS:")
            for e in log_info["errors"][:5]:
                logger.info(f"    {e[-120:]}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Monitor stopped.")
