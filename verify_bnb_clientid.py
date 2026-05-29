"""Exact prod payload replay — Binance is one-way (positionSide stripped),
clientOrderId is auto-added by base.create_order. Reproduce that precise
shape on BNB to see if clientOrderId/something ccxt-side triggers -4120."""
import os, uuid
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
print(f"ccxt version: {ccxt.__version__}")

sym = "BNB/USDT:USDT"
last = float(ex.fetch_ticker(sym)["last"])
trigger = float(ex.price_to_precision(sym, last * 0.80))

# Prod shape after positionSide stripped, clientOrderId added by base.
params = {
    "stopPrice": trigger,
    "reduceOnly": True,
    "clientOrderId": uuid.uuid4().hex[:24],
}
print(f"\n{sym} trigger={trigger} params={params}")
try:
    o = ex.create_order(sym, "STOP_MARKET", "sell", 0.01, None, params)
    print(f"  OK id={o.get('id')}")
    try:
        ex.cancel_order(o["id"], sym, {"stop": True})
        print("  canceled.")
    except Exception as ce:
        print(f"  cancel: {repr(ce)[:120]}")
except Exception as e:
    print(f"  FAIL: {repr(e)[:300]}")

# Also: what does the prod SL for a LONG with side="buy" send?
# close_side = "sell"; side sent to build_sl_tp_order_params = "buy".
# One-way → no positionSide. Output = same as above. Confirmed.
