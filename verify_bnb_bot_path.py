"""Replay the bot's SL-placement code path with verbose=True.

Purpose: verify_bnb_clientid.py already proved plain-hex clientOrderId is NOT
the -4120 trigger (raw ccxt succeeded). This script puts the same payload
through BinanceClient -> BaseExchange.create_order -- the exact path the bot
uses -- and prints the ccxt request body for comparison.

If this succeeds:   the code path is fine; the discriminator is state
                    (open position, one-way vs hedge, timing).
If this fails -4120: the wrapper (switch_to_futures, position check, clientId
                    injection) is introducing a difference; verbose output
                    shows exactly what.
"""
import os, sys
import ccxt
from dotenv import load_dotenv

load_dotenv()

# Tee stdout to a file so the verbose dump is preserved even if console truncates.
_log_path = "logs/verify_bnb_bot_path.log"
os.makedirs("logs", exist_ok=True)
_log_fh = open(_log_path, "w", encoding="utf-8")

class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, s):
        for st in self.streams:
            try: st.write(s)
            except Exception: pass
    def flush(self):
        for st in self.streams:
            try: st.flush()
            except Exception: pass

sys.stdout = _Tee(sys.__stdout__, _log_fh)
sys.stderr = _Tee(sys.__stderr__, _log_fh)

print(f"ccxt version: {ccxt.__version__}")
print(f"log file:     {_log_path}")

# ─── Path A: raw ccxt (known good per verify_bnb_clientid.py) ─────────
print("\n" + "=" * 60)
print("PATH A — raw ccxt (should succeed per prior run)")
print("=" * 60)

ex_raw = ccxt.binance({
    "apiKey":  os.getenv("BINANCE_API_KEY", "").strip(),
    "secret":  os.getenv("BINANCE_SECRET_KEY", "").strip(),
    "options": {"defaultType": "future", "adjustForTimeDifference": True,
                "recvWindow": 60000},
    "enableRateLimit": True,
})
ex_raw.verbose = True
ex_raw.load_markets()
ex_raw.load_time_difference()

sym = "BNB/USDT:USDT"
last = float(ex_raw.fetch_ticker(sym)["last"])
trigger_a = float(ex_raw.price_to_precision(sym, last * 0.80))

import uuid
params_a = {
    "stopPrice": trigger_a,
    "reduceOnly": True,
    "clientOrderId": uuid.uuid4().hex[:24],
}
print(f"\n[A] {sym} trigger={trigger_a} params={params_a}")
try:
    oa = ex_raw.create_order(sym, "STOP_MARKET", "sell", 0.01, None, params_a)
    print(f"[A] OK id={oa.get('id')}")
    try:
        ex_raw.cancel_order(oa["id"], sym, {"stop": True})
        print("[A] canceled.")
    except Exception as ce:
        print(f"[A] cancel: {repr(ce)[:200]}")
except Exception as e:
    print(f"[A] FAIL: {repr(e)[:400]}")

# ─── Path B: bot wrapper (BinanceClient -> BaseExchange) ──────────────
print("\n" + "=" * 60)
print("PATH B — BinanceClient wrapper (bot code path)")
print("=" * 60)

from exchanges.binance_client import BinanceClient

client = BinanceClient()
if not getattr(client, "_connected", False):
    print("[B] BinanceClient not connected — aborting.")
    sys.exit(1)

# Flip verbose on the underlying ccxt instance so we see the HTTP round-trip
# that the wrapper + base.create_order produces.
client.exchange.verbose = True

# Fresh trigger so we don't collide with cancelled order from Path A.
last_b = float(client.exchange.fetch_ticker(sym)["last"])
trigger_b = float(client.exchange.price_to_precision(sym, last_b * 0.80))

# Pass the SAME param shape the bot's build_sl_tp_order_params produces.
# Note: base.create_order will add a clientOrderId if one isn't present.
params_b = {
    "stopPrice":  trigger_b,
    "reduceOnly": True,
}
print(f"\n[B] {sym} trigger={trigger_b} params={params_b} (clientOrderId added by base)")

try:
    ob = client.create_order(
        symbol      = sym,
        order_type  = "STOP_MARKET",
        side        = "sell",
        amount      = 0.01,
        price       = None,
        params      = params_b,
        market_type = "futures",
    )
    print(f"[B] OK id={ob.get('id')}")
    try:
        # Cancel via wrapper if there is one, else direct
        client.exchange.options["defaultType"] = "future"
        client.exchange.cancel_order(ob["id"], sym, {"stop": True})
        print("[B] canceled.")
    except Exception as ce:
        print(f"[B] cancel: {repr(ce)[:200]}")
    finally:
        client.exchange.options["defaultType"] = "spot"
except Exception as e:
    print(f"[B] FAIL: {repr(e)[:400]}")
    # Surface ccxt's last request on failure
    raw = client.exchange
    print(f"[B] last_request_url:  {getattr(raw, 'last_request_url', None)}")
    print(f"[B] last_request_body: {getattr(raw, 'last_request_body', None)}")
    print(f"[B] last_http_response:{getattr(raw, 'last_http_response', None)}")

print("\nDone. Review logs/verify_bnb_bot_path.log for full verbose dump.")
_log_fh.flush()
_log_fh.close()
