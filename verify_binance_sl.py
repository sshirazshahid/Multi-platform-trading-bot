"""
verify_binance_sl.py — Isolate the Binance -4120 SL bug.

Places a far-from-market STOP_MARKET order on BTC/USDT:USDT perp
(small qty, will NOT trigger — we cancel immediately after).

Prints exactly what ccxt sends and what Binance replies.
"""

import os
import json
import ccxt
from dotenv import load_dotenv

load_dotenv()

ex = ccxt.binance({
    "apiKey":          os.getenv("BINANCE_API_KEY", "").strip(),
    "secret":          os.getenv("BINANCE_SECRET_KEY", "").strip(),
    "options":         {"defaultType": "future", "adjustForTimeDifference": True,
                        "recvWindow": 60000},
    "enableRateLimit": True,
    "verbose":         False,
})

ex.load_markets()
ex.load_time_difference()

symbol = "BTC/USDT:USDT"
ticker = ex.fetch_ticker(symbol)
last   = float(ticker["last"])

# Trigger price 20% BELOW market for a SELL STOP_MARKET — won't fill.
trigger = round(last * 0.80, 1)
qty     = 0.002  # ~$200 notional at $100k BTC — min is ~0.001

print(f"\n=== STOP_MARKET test | BTC last={last} | trigger={trigger} | qty={qty} ===\n")

# --- Variant A: our current code path (STOP_MARKET + stopPrice, no reduceOnly) ---
try:
    print("\n[A] STOP_MARKET + stopPrice, no reduceOnly (current one-way code path)")
    o = ex.create_order(symbol, "STOP_MARKET", "sell", qty, None,
                        {"stopPrice": trigger})
    print("  OK →", o.get("id"), o.get("status"))
    ex.cancel_order(o["id"], symbol)
    print("  Canceled.")
except Exception as e:
    print("  FAIL:", repr(e)[:300])

# --- Variant B: STOP_MARKET + stopPrice + reduceOnly=true ---
try:
    print("\n[B] STOP_MARKET + stopPrice + reduceOnly=true")
    o = ex.create_order(symbol, "STOP_MARKET", "sell", qty, None,
                        {"stopPrice": trigger, "reduceOnly": True})
    print("  OK →", o.get("id"), o.get("status"))
    ex.cancel_order(o["id"], symbol)
    print("  Canceled.")
except Exception as e:
    print("  FAIL:", repr(e)[:300])

# --- Variant C: STOP_MARKET + stopPrice + closePosition=true ---
try:
    print("\n[C] STOP_MARKET + stopPrice + closePosition=true (no qty)")
    o = ex.create_order(symbol, "STOP_MARKET", "sell", None, None,
                        {"stopPrice": trigger, "closePosition": True})
    print("  OK →", o.get("id"), o.get("status"))
    ex.cancel_order(o["id"], symbol)
    print("  Canceled.")
except Exception as e:
    print("  FAIL:", repr(e)[:300])

# --- Show account info so we can see mode flags ---
try:
    acct = ex.fapiPrivateV2GetAccount()
    print("\n=== Account flags ===")
    for k in ("multiAssetsMargin", "canTrade", "canDeposit", "canWithdraw",
              "feeTier", "tradeGroupId", "updateTime"):
        print(f"  {k}: {acct.get(k)}")
    positionMode = ex.fapiPrivateGetPositionSideDual()
    print("  positionMode (dualSidePosition):", positionMode.get("dualSidePosition"))
except Exception as e:
    print("  account info error:", repr(e)[:200])

print("\n=== Done ===")
