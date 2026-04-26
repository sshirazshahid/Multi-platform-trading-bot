"""Test STOP_MARKET variants on BNB/USDT:USDT (the symbol that -4120'd in
production at 2026-04-21 05:49). Compares three payload shapes head-to-head
against BTC (known-good) to isolate whether BNB rejects a specific shape."""
import os
import ccxt
from dotenv import load_dotenv

load_dotenv()

ex = ccxt.binance({
    "apiKey":  os.getenv("BINANCE_API_KEY", "").strip(),
    "secret":  os.getenv("BINANCE_SECRET_KEY", "").strip(),
    "options": {"defaultType": "future", "adjustForTimeDifference": True,
                "recvWindow": 60000},
    "enableRateLimit": True,
})
ex.load_markets()
ex.load_time_difference()


def try_shape(sym, variant, shape_params, qty):
    try:
        o = ex.create_order(sym, "STOP_MARKET", "sell", qty, None, shape_params)
        print(f"  [{variant}] OK id={o.get('id')}")
        # Cancel via algo (stop=True) — regular cancel returns -2011 for stops.
        try:
            ex.cancel_order(o["id"], sym, {"stop": True})
            print(f"  [{variant}] canceled.")
        except Exception as ce:
            try:
                ex.cancel_order(o["id"], sym)
                print(f"  [{variant}] canceled (regular).")
            except Exception as ce2:
                print(f"  [{variant}] cancel FAILED: {repr(ce2)[:180]}")
    except Exception as e:
        print(f"  [{variant}] FAIL: {repr(e)[:250]}")


for sym in ("BTC/USDT:USDT", "BNB/USDT:USDT", "AVAX/USDT:USDT",
            "ARB/USDT:USDT", "ALGO/USDT:USDT", "AAVE/USDT:USDT",
            "TRX/USDT:USDT"):
    try:
        t = ex.fetch_ticker(sym)
        last = float(t["last"])
        trigger = float(ex.price_to_precision(sym, last * 0.80))
        mkt = ex.market(sym)
        min_amt = float(mkt.get("limits", {}).get("amount", {}).get("min") or 1)
        min_cost = float(mkt.get("limits", {}).get("cost", {}).get("min") or 5)
        qty = max(min_amt, min_cost / last * 1.2)
        qty = float(ex.amount_to_precision(sym, qty))
        print(f"\n--- {sym} last={last} trigger={trigger} qty={qty} ---")
        try_shape(sym, "A: bare",
                  {"stopPrice": trigger}, qty)
        try_shape(sym, "B: +reduceOnly",
                  {"stopPrice": trigger, "reduceOnly": True}, qty)
        try_shape(sym, "C: +closePosition",
                  {"stopPrice": trigger, "closePosition": True}, None)
    except Exception as se:
        print(f"  setup err {sym}: {repr(se)[:200]}")

print("\n=== Done ===")
