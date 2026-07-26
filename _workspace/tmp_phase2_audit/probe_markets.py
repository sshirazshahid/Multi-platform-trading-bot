import ccxt

fut = ccxt.binanceusdm({"enableRateLimit": True})
markets = fut.load_markets()
dated = [m for m in markets.values() if m.get("expiry") and m.get("quote") == "USDT" and m.get("linear")]
print(f"USDT-margined dated futures on binanceusdm: {len(dated)}")
for m in sorted(dated, key=lambda x: x["symbol"]):
    t = fut.fetch_ticker(m["symbol"])
    px = t.get("last") or t.get("mark")
    amin = m["limits"]["amount"]["min"]
    cmin = (m["limits"].get("cost") or {}).get("min")
    step = m["precision"].get("amount")
    minleg = max(amin * px, cmin or 0)
    # smallest amount multiple clearing cost floor
    import math
    amt = amin
    if cmin and amt * px < cmin:
        amt = math.ceil(cmin / px / amin) * amin
    print(f"{m['symbol']}  expiry={m.get('expiryDatetime','')[:10]} px={px} amount.min={amin} step={step} cost.min={cmin} -> min-leg notional ~= {amt*px:.2f} USDT (amount={amt})")

spot = ccxt.binance({"enableRateLimit": True})
sm = spot.load_markets()
for s in ["BTC/USDT", "ETH/USDT"]:
    m = sm[s]
    t = spot.fetch_ticker(s)
    px = t["last"]
    amin = m["limits"]["amount"]["min"]
    cmin = (m["limits"].get("cost") or {}).get("min")
    minleg = max(amin * px, cmin or 0)
    print(f"SPOT {s} px={px} amount.min={amin} cost.min={cmin} -> min-leg notional ~= {minleg:.2f} USDT")
