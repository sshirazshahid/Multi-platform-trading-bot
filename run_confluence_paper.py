#!/usr/bin/env python3
"""
run_confluence_paper.py - standalone PAPER forward-test of the confluence engine.

Isolated from the live bot (imports nothing from core/; cannot affect bot_engine).
Pure-price (QS_PURE_PRICE), simulated fills only, NEVER places real orders, and it
only trades its own confluence signals (it does not block the live bot's trades).

State : data/confluence_paper_state.json   |  Trade log: reports/confluence_paper_trades.csv
Run   : python run_confluence_paper.py            (loop, default every 5 min)
        python run_confluence_paper.py --once     (single cycle, for testing)
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

from quant_suite import bot_adapter as A

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "confluence_paper_state.json"
LOG = ROOT / "reports" / "confluence_paper_trades.csv"
START_BAL = float(os.getenv("PAPER_BALANCE", "5000"))
SCAN_TOP = int(os.getenv("PAPER_SCAN_TOP", "30"))
MAX_OPEN = int(os.getenv("PAPER_MAX_OPEN", "5"))
INTERVAL = int(os.getenv("PAPER_INTERVAL_SEC", "300"))
FEE = 0.0005
# Slippage mirrors quant_suite/engine.py so the forward test costs trades the SAME way
# as the backtest that justified the strategy (otherwise the paper P&L is optimistically
# biased: losers were being booked at the exact stop with zero slippage).
SLIP_OPEN = 0.0005
SLIP_CLOSE = 0.0005
SLIP_SL = 0.0010


def realized_pnl(side, entry, exit_px, kind, notional):
    """Paper fill P&L with entry/exit slippage + round-trip fees (matches engine.py).
    kind is 'SL' or 'TP'; SL fills are worse (SLIP_SL) than TP fills (SLIP_CLOSE)."""
    if side == "buy":
        ef = entry * (1 + SLIP_OPEN)
        xf = exit_px * (1 - (SLIP_SL if kind == "SL" else SLIP_CLOSE))
        gross = (xf - ef) / ef
    else:
        ef = entry * (1 - SLIP_OPEN)
        xf = exit_px * (1 + (SLIP_SL if kind == "SL" else SLIP_CLOSE))
        gross = (ef - xf) / ef
    return (gross - 2 * FEE) * notional


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {
        "balance": START_BAL,
        "start_balance": START_BAL,
        "open": [],
        "closed_count": 0,
        "wins": 0,
        "realized_pnl": 0.0,
        "created": _now(),
    }


def save_state(st):
    STATE.parent.mkdir(exist_ok=True)
    st["updated"] = _now()
    # Atomic write: a concurrent reader (or a 2nd process) never sees a partial file,
    # which the bare-except in load_state would otherwise silently reset to START_BAL.
    tmp = STATE.parent / (STATE.name + ".tmp")
    tmp.write_text(json.dumps(st, indent=2, default=str))
    os.replace(tmp, STATE)


def log_trade(rec):
    LOG.parent.mkdir(exist_ok=True)
    new = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(
                [
                    "opened",
                    "closed",
                    "symbol",
                    "side",
                    "entry",
                    "exit",
                    "outcome",
                    "pnl_usd",
                    "balance",
                ]
            )
        w.writerow(
            [
                rec["opened"],
                rec["closed"],
                rec["symbol"],
                rec["side"],
                rec["entry"],
                rec["exit"],
                rec["outcome"],
                rec["pnl_usd"],
                rec["balance"],
            ]
        )


def last_price(ex, sym):
    try:
        return float(ex.fetch_ticker(sym)["last"])
    except Exception:
        return None


def cycle(once_note=""):
    op = os.getenv("OPERATING_MODE", "PAPER").upper()
    if op == "CONTROLLED_LIVE":
        print("refusing to run under CONTROLLED_LIVE (paper forward-test only).")
        return
    st = load_state()
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    ex.load_markets()

    # 1) manage open positions: close on SL/TP (poll-price)
    for pos in list(st["open"]):
        p = last_price(ex, pos["symbol"])
        if p is None:
            continue
        hit = None
        if pos["side"] == "buy":
            hit = (
                ("SL", pos["sl"])
                if p <= pos["sl"]
                else (("TP", pos["tp"]) if p >= pos["tp"] else None)
            )
        else:
            hit = (
                ("SL", pos["sl"])
                if p >= pos["sl"]
                else (("TP", pos["tp"]) if p <= pos["tp"] else None)
            )
        if hit:
            outc, ex_px = hit
            pnl = realized_pnl(pos["side"], pos["entry"], ex_px, outc, pos["notional"])
            st["balance"] += pnl
            st["realized_pnl"] += pnl
            st["closed_count"] += 1
            st["wins"] += 1 if pnl > 0 else 0
            rec = {
                "opened": pos["opened"],
                "closed": _now(),
                "symbol": pos["symbol"],
                "side": pos["side"],
                "entry": round(pos["entry"], 8),
                "exit": round(ex_px, 8),
                "outcome": outc,
                "pnl_usd": round(pnl, 2),
                "balance": round(st["balance"], 2),
            }
            log_trade(rec)
            st["open"].remove(pos)
            print(
                f"  CLOSE {outc} {pos['symbol']} {pos['side']} pnl ${pnl:+.2f} -> bal ${st['balance']:.2f}"
            )

    # 2) open new confluence setups (paper) if room
    open_syms = {p["symbol"] for p in st["open"]}
    if len(st["open"]) < MAX_OPEN:
        res = A.scan_top(SCAN_TOP, account_equity=st["balance"])
        for s in res.get("setups", []):
            if len(st["open"]) >= MAX_OPEN:
                break
            if s["symbol"] in open_syms:
                continue
            st["open"].append(
                {
                    "symbol": s["symbol"],
                    "side": s["side"],
                    "entry": s["entry"],
                    "sl": s["stop_loss"],
                    "tp": s["take_profit"],
                    "notional": s["notional_usd"],
                    "tf": s.get("tf"),
                    "tier": s.get("conviction_tier"),
                    "opened": _now(),
                }
            )
            open_syms.add(s["symbol"])
            print(
                f"  OPEN  {s['side'].upper()} {s['symbol']} entry {s['entry']} SL {s['stop_loss']} TP {s['take_profit']} (${s['notional_usd']})"
            )

    wr = (st["wins"] / st["closed_count"] * 100) if st["closed_count"] else 0.0
    save_state(st)
    print(
        f"[{_now()}] open={len(st['open'])} closed={st['closed_count']} winrate={wr:.0f}% "
        f"realizedPnL=${st['realized_pnl']:+.2f} balance=${st['balance']:.2f} (PAPER){once_note}"
    )


if __name__ == "__main__":
    once = "--once" in sys.argv
    print(
        f"Confluence PAPER forward-test | start ${START_BAL} | scan top {SCAN_TOP} | max {MAX_OPEN} open | pure-price | NO real orders"
    )
    if once:
        cycle(once_note=" [--once]")
    else:
        while True:
            try:
                cycle()
            except Exception as e:
                print("cycle error:", str(e)[:160])
            time.sleep(INTERVAL)
