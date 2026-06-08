"""indicators.py - causal, vectorized TA indicators (mirrors MCP-Brain set)."""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(close, period=14):
    d = close.diff(); up = d.clip(lower=0.0); dn = -d.clip(upper=0.0)
    ru = up.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rd = dn.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = ru / rd.replace(0, np.nan)
    out = 100 - 100/(1+rs)
    out = out.mask((rd == 0) & (ru > 0), 100.0)
    return out.fillna(50)


def true_range(df):
    h, l, c = df["high"], df["low"], df["close"]; pc = c.shift(1)
    return pd.concat([(h-l), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)


def atr(df, period=14):
    return true_range(df).ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def adx(df, period=14):
    h, l = df["high"], df["low"]; up = h.diff(); dn = -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df); a = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    pdi = 100*pd.Series(pdm, index=df.index).ewm(alpha=1/period, adjust=False, min_periods=period).mean()/a
    mdi = 100*pd.Series(mdm, index=df.index).ewm(alpha=1/period, adjust=False, min_periods=period).mean()/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean().fillna(0)


def vwap_rolling(df, window=24):
    tp = (df["high"]+df["low"]+df["close"])/3
    return (tp*df["volume"]).rolling(window).sum() / df["volume"].rolling(window).sum().replace(0, np.nan)


def realized_vol(close, window=24):
    return np.log(close/close.shift(1)).rolling(window).std()


def hurst_exponent(close, max_lag=30):
    s = np.log(close.dropna().values)
    if len(s) < max_lag*2:
        return 0.5
    lags = range(2, max_lag)
    tau = [np.sqrt(np.std(s[lag:]-s[:-lag])) for lag in lags]
    try:
        return float(np.polyfit(np.log(list(lags)), np.log(tau), 1)[0]*2.0)
    except Exception:
        return 0.5
