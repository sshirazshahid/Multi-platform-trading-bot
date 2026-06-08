#!/usr/bin/env python3
"""market_monitor.py - self-contained live crypto market dashboard.
Import-safe: nothing runs until run() is called. Needs ccxt/pandas/numpy.
Run:  python -m quant_suite.market_monitor"""
from __future__ import annotations
from datetime import datetime, timezone
import numpy as np, pandas as pd, ccxt


def _ohlcv(ex, sym, tf, n=300):
    return pd.DataFrame(ex.fetch_ohlcv(sym, tf, limit=n), columns=["ts", "o", "h", "l", "c", "v"])
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    ru = up.ewm(alpha=1/n, adjust=False).mean(); rd = dn.ewm(alpha=1/n, adjust=False).mean()
    return (100 - 100/(1 + ru/rd.replace(0, np.nan))).fillna(50)
def _adx(d, n=14):
    h, l, c = d["h"], d["l"], d["c"]; up = h.diff(); dn = -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.); mdm = np.where((dn > up) & (dn > 0), dn, 0.)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=d.index).ewm(alpha=1/n, adjust=False).mean() / a
    mdi = 100 * pd.Series(mdm, index=d.index).ewm(alpha=1/n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().fillna(0)
def _regime(ex, sym, tf):
    d = _ohlcv(ex, sym, tf); c = d["c"]
    e20, e50, e200 = _ema(c, 20).iloc[-1], _ema(c, 50).iloc[-1], _ema(c, 200).iloc[-1]
    a = float(_adx(d).iloc[-1]); r = float(_rsi(c).iloc[-1])
    stack = "bull" if e20 > e50 > e200 else "bear" if e20 < e50 < e200 else "mixed"
    lab = ("TREND_" + ("UP" if e20 > e50 else "DOWN")) if a >= 25 else "RANGE/CHOP"
    return dict(price=float(c.iloc[-1]), label=lab, adx=round(a, 1), rsi=round(r, 1), stack=stack)


def run():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True}); ex.load_markets()
    t = ex.fetch_tickers()
    rows = [(s.split("/")[0], tk["last"], tk["percentage"], tk["quoteVolume"])
            for s, tk in t.items() if ":USDT" in s and tk.get("quoteVolume") and tk.get("last") is not None and tk.get("percentage") is not None]
    liq = [r for r in rows if r[3] > 5e7]
    gain = sorted(liq, key=lambda r: -r[2])[:8]; loss = sorted(liq, key=lambda r: r[2])[:8]
    print("=" * 60); print("LIVE MARKET MONITOR - binance USDT perps")
    print("as of", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")); print("=" * 60)
    for tf in ["4h", "1h"]:
        rg = _regime(ex, "BTC/USDT:USDT", tf)
        print(f"BTC {tf:>2}: {rg['label']:11} px {rg['price']:>10,.0f}  ADX {rg['adx']:>4}  RSI {rg['rsi']:>4}  stack {rg['stack']}")
    print("\nMAJORS  last | 24h% | funding/8h:")
    for m in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
        sym = f"{m}/USDT:USDT"; tk = t.get(sym)
        if not tk: continue
        try: fr = ex.fetch_funding_rate(sym).get("fundingRate")
        except Exception: fr = None
        print(f"  {m:4} {tk['last']:>12,.4f}  {tk['percentage']:+6.2f}%  {(f'{fr*100:+.4f}%' if fr is not None else 'n/a')}")
    print("\nTOP GAINERS 24h:"); [print(f"  {g[0]:9}{g[2]:+6.2f}%  ${g[3]/1e6:,.0f}M") for g in gain]
    print("\nTOP LOSERS 24h:");  [print(f"  {g[0]:9}{g[2]:+6.2f}%  ${g[3]/1e6:,.0f}M") for g in loss]
    print("=" * 60)


if __name__ == "__main__":
    run()
