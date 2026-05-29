"""What actually ships in production on the first Binance SL?
order_manager.build_sl_tp_order_params sends:
  STOP_MARKET + stopPrice + reduceOnly=True + positionSide=LONG/SHORT
BinanceClient only strips positionSide AFTER seeing -4061 once (self._is_oneway
starts False). So the FIRST live order on a fresh process sends positionSide
against a one-way account.

Does that combo produce -4120, -4061, or something else?"""
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

for sym in ("BNB/USDT:USDT", "BTC/USDT:USDT"):
    t = ex.fetch_ticker(sym)
    last = float(t["last"])
    trigger = float(ex.price_to_precision(sym, last * 0.80))
    mkt = ex.market(sym)
    min_amt = float(mkt.get("limits", {}).get("amount", {}).get("min") or 1)
    min_cost = float(mkt.get("limits", {}).get("cost", {}).get("min") or 5)
    qty = max(min_amt, min_cost / last * 1.5)
    qty = float(ex.amount_to_precision(sym, qty))

    # Exact prod shape on a FRESH process (oneway flag not yet set).
    params = {"stopPrice": trigger, "reduceOnly": True, "positionSide": "LONG"}
    print(f"\n--- {sym} qty={qty} trigger={trigger} ---")
    print(f"  params = {params}")
    try:
        o = ex.create_order(sym, "STOP_MARKET", "sell", qty, None, params)
        print(f"  OK id={o.get('id')}")
        try:
            ex.cancel_order(o["id"], sym, {"stop": True})
            print("  canceled.")
        except Exception as ce:
            print(f"  cancel err: {repr(ce)[:180]}")
    except Exception as e:
        print(f"  FAIL: {repr(e)[:300]}")
