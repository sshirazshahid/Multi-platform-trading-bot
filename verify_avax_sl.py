"""Reproduce -4120 on AVAX (one of the five broken symbols)."""
import os
import ccxt
from dotenv import load_dotenv
load_dotenv()

ex = ccxt.binance({
    "apiKey":          os.getenv("BINANCE_API_KEY", "").strip(),
    "secret":          os.getenv("BINANCE_SECRET_KEY", "").strip(),
    "options":         {"defaultType": "future", "adjustForTimeDifference": True,
                        "recvWindow": 60000},
    "enableRateLimit": True,
})
ex.load_markets()
ex.load_time_difference()

for sym in ("AVAX/USDT:USDT", "ARB/USDT:USDT", "ALGO/USDT:USDT",
            "AAVE/USDT:USDT", "TRX/USDT:USDT"):
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
        try:
            o = ex.create_order(sym, "STOP_MARKET", "sell", qty, None,
                                {"stopPrice": trigger, "reduceOnly": True})
            print(f"  A+reduceOnly OK {o.get('id')}")
            try:
                ex.cancel_order(o["id"], sym, {"stop": True})
                print("  cancel OK")
            except Exception as ce:
                print(f"  cancel ERR: {repr(ce)[:150]}")
        except Exception as e:
            print(f"  A FAIL: {repr(e)[:250]}")
    except Exception as se:
        print(f"  setup err: {repr(se)[:200]}")
