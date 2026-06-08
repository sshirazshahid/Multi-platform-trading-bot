"""
bull_bear.py - per-coin BULL/BEAR signal across the 3-exchange universe.
Pure price/quant/technical (crypto has no equity fundamentals). Combines:
  * 4h EMA stack (20/50/200) -> trend structure
  * confluence vote (the validated engine) -> directional edge
  * HMM regime -> statistical state
  * 30-bar momentum
into a Bull/Bear verdict + score. Read-only/advisory.
"""
from __future__ import annotations
import ccxt
import pandas as pd
from . import engine as E, hmm_regime as H


def _fetch(ex, sym, tf="1h", limit=400):
    d = pd.DataFrame(ex.fetch_ohlcv(sym, tf, limit=limit),
                     columns=["ts", "open", "high", "low", "close", "volume"])
    d["dt"] = pd.to_datetime(d["ts"], unit="ms", utc=True)
    return d


def scan(n=20, tf="1h", htf="4h"):
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True}); ex.load_markets()
    t = ex.fetch_tickers()
    liq = sorted([(s, v) for s, v in t.items() if ":USDT" in s and v.get("quoteVolume") and v.get("last") is not None],
                 key=lambda x: -x[1]["quoteVolume"])[:n]
    btc = _fetch(ex, "BTC/USDT:USDT", tf)
    rows = []
    for sym, _ in liq:
        try:
            d = _fetch(ex, sym, tf)
            if len(d) < 220:
                continue
            dd = E.prep(d, df_btc1h=btc, is_benchmark=sym.startswith("BTC"), htf_rule=htf)
            last = dd.iloc[-2]
            e20, e50, e200 = last["ema20_4h"], last["ema50_4h"], last["ema200_4h"]
            stack = "bull" if (e20 > e50 > e200) else ("bear" if (e20 < e50 < e200) else "mixed")
            sig = E.latest_signal(dd, "confluence")
            reg = H.detect_regime(d).get("label", "?")
            mom = float(d["close"].iloc[-1] / d["close"].iloc[-31] - 1) * 100 if len(d) > 31 else 0.0
            score = (1 if stack == "bull" else -1 if stack == "bear" else 0) + int(sig.get("side", 0)) + (1 if mom > 0 else -1)
            verdict = "BULL" if score >= 2 else "BEAR" if score <= -2 else "NEUTRAL"
            rows.append({"coin": sym.split("/")[0], "verdict": verdict, "score": score,
                         "stack4h": stack, "confluence": {1: "long", -1: "short", 0: "flat"}[int(sig.get("side", 0))],
                         "hmm": reg, "mom30_pct": round(mom, 2), "adx4h": sig.get("adx_4h")})
        except Exception:
            pass
    return sorted(rows, key=lambda r: -r["score"])


if __name__ == "__main__":
    rows = scan(20)
    print(f"{'coin':8}{'verdict':9}{'score':>6}{'stack4h':>9}{'confluence':>11}{'hmm':>12}{'mom30%':>8}")
    for r in rows:
        print(f"{r['coin']:8}{r['verdict']:9}{r['score']:>6}{r['stack4h']:>9}{r['confluence']:>11}{r['hmm']:>12}{r['mom30_pct']:>8}")
    b = sum(1 for r in rows if r["verdict"] == "BULL"); s = sum(1 for r in rows if r["verdict"] == "BEAR")
    print(f"\nUniverse tilt: {b} bull / {s} bear / {len(rows)-b-s} neutral (of {len(rows)})")
