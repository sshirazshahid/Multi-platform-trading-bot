"""
engine.py - regime-switching ENSEMBLE of the validated strategies + backtest.

Per-bar votes (causal, look-ahead-safe):
  * multifactor (mirrors core/mcp_brain.py): 4h-trend + 1h confirm, score>=65.
  * regime-trend: 4h EMA stack (20>50>200) + ADX>=25, 1h price vs EMA20.
  * mean-reversion + rel-strength: VWAP/ATR bands + RSI extreme + outperformance.

Ensemble (the accuracy lever):
  * TREND market (4h ADX>=25): require multifactor AND regime-trend to AGREE
    (2-of-2 confluence) -> fewer, higher-quality trend entries.
  * RANGE market (4h ADX<25): use mean-reversion sleeve instead.
  * conviction tier = how many sub-strategies align (used for sizing live).

backtest(mode=...) runs the same wick-based ATR-stop / R:R loop the Python bot
uses, so 'multifactor' reproduces the bot's current engine and 'ensemble' is the
apples-to-apples comparison.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from . import indicators as ind

TAKER_FEE, SLIP_OPEN, SLIP_CLOSE, SLIP_SL = 0.0005, 0.0005, 0.0005, 0.0010


@dataclass
class BTParams:
    atr_mult: float = 1.5
    rr: float = 2.5
    sl_min: float = 0.015
    sl_max: float = 0.035
    max_hold: int = 72
    cooldown: int = 2
    # mean-rev sleeve
    mr_dev: float = 2.0
    mr_rsi_lo: float = 32.0
    mr_rsi_hi: float = 68.0
    mr_rr: float = 1.5
    rs_len: int = 48


def prep(df1h: pd.DataFrame, df_btc1h: pd.DataFrame | None = None,
         is_benchmark: bool = False, p: BTParams = BTParams(), htf_rule: str = "4h") -> pd.DataFrame:
    """Compute all indicator + per-strategy vote columns on the 1h frame."""
    d = df1h.copy().reset_index(drop=True)
    c = d["close"]
    d["ema20"] = ind.ema(c, 20); d["ema50"] = ind.ema(c, 50)
    d["rsi"] = ind.rsi(c, 14); d["adx"] = ind.adx(d, 14); d["atr"] = ind.atr(d, 14)
    vwap = ind.vwap_rolling(d, 24)
    d["vol_ratio"] = d["volume"] / d["volume"].rolling(20).mean()
    d["pvw"] = (c - vwap) / vwap * 100
    d["lower"] = vwap - p.mr_dev * d["atr"]
    d["upper"] = vwap + p.mr_dev * d["atr"]

    # 4h context (last CLOSED 4h bar -> shift 1), ffilled onto 1h
    e20_4h = e50_4h = e200_4h = adx_4h = slope_4h = None
    if "dt" in d:
        s = d.set_index(pd.to_datetime(d["dt"], utc=True))
        o4 = s["open"].resample(htf_rule).first(); h4 = s["high"].resample(htf_rule).max()
        l4 = s["low"].resample(htf_rule).min(); c4 = s["close"].resample(htf_rule).last()
        f4 = pd.DataFrame({"open": o4, "high": h4, "low": l4, "close": c4}).dropna()
        if len(f4) > 60:
            E20 = ind.ema(f4["close"], 20); E50 = ind.ema(f4["close"], 50)
            E200 = ind.ema(f4["close"], 200); A = ind.adx(f4, 14)
            use = pd.DataFrame({"e20": E20, "e50": E50, "e200": E200, "adx": A,
                                "slope": E20.diff(3)}).shift(1).reindex(s.index, method="ffill")
            d["ema20_4h"] = use["e20"].values; d["ema50_4h"] = use["e50"].values
            d["ema200_4h"] = use["e200"].values; d["adx_4h"] = use["adx"].values
            d["slope_4h"] = use["slope"].values
    for col in ["ema20_4h", "ema50_4h", "ema200_4h", "adx_4h", "slope_4h"]:
        if col not in d:
            d[col] = np.nan

    # relative strength vs BTC, aligned by TIMESTAMP (dt), not tail position
    if df_btc1h is not None and "dt" in df_btc1h.columns and "dt" in d.columns:
        bser = df_btc1h.set_index(pd.to_datetime(df_btc1h["dt"], utc=True))["close"]
        baln = bser.reindex(pd.to_datetime(d["dt"], utc=True), method="ffill").reset_index(drop=True)
        cc = c.reset_index(drop=True)
        d["rs"] = ((cc / cc.shift(p.rs_len) - 1) - (baln / baln.shift(p.rs_len) - 1)).values
    else:
        d["rs"] = 0.0
    d["is_bench"] = is_benchmark

    # ---- multifactor vote ----
    gap4 = (d["ema20_4h"] - d["ema50_4h"]).abs() / d["ema50_4h"].replace(0, np.nan) * 100
    side4 = np.where(d["ema20_4h"] > d["ema50_4h"], 1, np.where(d["ema20_4h"] < d["ema50_4h"], -1, 0))
    align = np.where(side4 == 1, d["ema20"] > d["ema50"], d["ema20"] < d["ema50"])
    rsi_ok = np.where(side4 == 1, (d["rsi"] >= 38) & (d["rsi"] <= 68), (d["rsi"] >= 32) & (d["rsi"] <= 62))
    req = (gap4 >= 0.15) & align & rsi_ok & (d["adx"] >= 20) & (side4 != 0)
    hh = d["high"].rolling(5).max(); ll = d["low"].rolling(5).min()
    struct_l = (hh > hh.shift(5)) & (ll > ll.shift(5))
    struct_s = (ll < ll.shift(5)) & (hh < hh.shift(5))
    score = np.where(req, 50.0, 0.0)
    score += np.where(req & (((side4 == 1) & (d["slope_4h"] > 0)) | ((side4 == -1) & (d["slope_4h"] < 0))), 3.0, 0.0)
    score += np.where(req & (d["vol_ratio"] >= 1.2), 8.0, 0.0)
    score += np.where(req & (((side4 == 1) & struct_l) | ((side4 == -1) & struct_s)), 8.0, 0.0)
    vwap_l = (side4 == 1) & (d["pvw"] > 0.05) & (d["pvw"] < 2.0)
    vwap_s = (side4 == -1) & (d["pvw"] > -2.0) & (d["pvw"] < -0.05)
    score += np.where(req & (vwap_l | vwap_s), 15.0, 0.0)
    d["mf_score"] = score
    d["mf_vote"] = np.where(req & (score >= 65) & (side4 == 1), 1,
                            np.where(req & (score >= 65) & (side4 == -1), -1, 0))

    # ---- regime-trend vote ----
    bull = (d["ema20_4h"] > d["ema50_4h"]) & (d["ema50_4h"] > d["ema200_4h"]) & (d["adx_4h"] >= 25)
    bear = (d["ema20_4h"] < d["ema50_4h"]) & (d["ema50_4h"] < d["ema200_4h"]) & (d["adx_4h"] >= 25)
    d["rg_vote"] = np.where(bull & (c > d["ema20"]) & (d["adx"] >= 20), 1,
                            np.where(bear & (c < d["ema20"]) & (d["adx"] >= 20), -1, 0))

    # ---- mean-reversion vote ----
    rs_long = (d["rs"] > 0) | d["is_bench"]
    rs_short = (d["rs"] < 0) | d["is_bench"]
    d["mr_vote"] = np.where((c < d["lower"]) & (d["rsi"] < p.mr_rsi_lo) & rs_long, 1,
                            np.where((c > d["upper"]) & (d["rsi"] > p.mr_rsi_hi) & rs_short, -1, 0))

    # ---- market regime + ensemble ok flags ----
    trend = d["adx_4h"] >= 25
    d["trend"] = trend
    long_conf = (trend & (d["mf_vote"] == 1) & (d["rg_vote"] == 1)) | (~trend & (d["mr_vote"] == 1))
    short_conf = (trend & (d["mf_vote"] == -1) & (d["rg_vote"] == -1)) | (~trend & (d["mr_vote"] == -1))
    d["ens_long"] = long_conf.fillna(False)
    d["ens_short"] = short_conf.fillna(False)
    # conviction tier: trend confluence counts mf+rg(+mr if aligned); range = 1
    tier = np.zeros(len(d))
    tl = trend & d["ens_long"]; ts = trend & d["ens_short"]
    tier = np.where(tl, 2 + (d["mr_vote"] == 1).astype(int), tier)
    tier = np.where(ts, 2 + (d["mr_vote"] == -1).astype(int), tier)
    tier = np.where((~trend) & (d["ens_long"] | d["ens_short"]), 1, tier)
    d["tier"] = tier
    return d


def _ok_arrays(d: pd.DataFrame, mode: str):
    if mode == "confluence_long":
        tr = d["trend"].values
        return (tr & (d["mf_vote"].values == 1) & (d["rg_vote"].values == 1),
                np.zeros(len(d), dtype=bool))
    if mode == "confluence":
        tr = d["trend"].values
        return (tr & (d["mf_vote"].values == 1) & (d["rg_vote"].values == 1),
                tr & (d["mf_vote"].values == -1) & (d["rg_vote"].values == -1))
    if mode == "multifactor":
        return (d["mf_vote"].values == 1), (d["mf_vote"].values == -1)
    if mode == "ensemble":
        return d["ens_long"].values, d["ens_short"].values
    if mode == "regime":
        return (d["rg_vote"].values == 1), (d["rg_vote"].values == -1)
    raise ValueError(mode)


def backtest(d: pd.DataFrame, mode: str = "ensemble", p: BTParams = BTParams()):
    long_ok, short_ok = _ok_arrays(d, mode)
    o = d["open"].values.astype(float); h = d["high"].values.astype(float)
    l = d["low"].values.astype(float); c = d["close"].values.astype(float)
    atr = d["atr"].values.astype(float); tier = d["tier"].values
    dt = d["dt"].values if "dt" in d else np.arange(len(d))
    trend = d["trend"].values
    n = len(c); i = 60; cooldown = 0; trades = []
    while i < n - 1:
        if cooldown > 0:
            cooldown -= 1; i += 1; continue
        go_l = bool(long_ok[i]); go_s = bool(short_ok[i])
        if (go_l or go_s) and atr[i] == atr[i] and atr[i] > 0 and o[i+1] > 0:
            side = 1 if go_l else -1
            entry = o[i+1]; atr_pct = atr[i]/c[i]
            sl_d = min(max(p.atr_mult*atr_pct, p.sl_min), p.sl_max)
            rr = p.mr_rr if (mode == "ensemble" and not bool(trend[i])) else p.rr
            if side == 1:
                sl = entry*(1-sl_d); tp = entry*(1+sl_d*rr); ef = entry*(1+SLIP_OPEN)
            else:
                sl = entry*(1+sl_d); tp = entry*(1-sl_d*rr); ef = entry*(1-SLIP_OPEN)
            outc, ex, bars = "TIME", None, 0
            for j in range(i+1, min(i+1+p.max_hold, n)):
                bars = j-i
                hit_sl = (l[j] <= sl) if side == 1 else (h[j] >= sl)
                hit_tp = (h[j] >= tp) if side == 1 else (l[j] <= tp)
                if hit_sl:
                    ex = sl*(1-SLIP_SL) if side == 1 else sl*(1+SLIP_SL); outc = "SL"; break
                if hit_tp:
                    ex = tp*(1-SLIP_CLOSE) if side == 1 else tp*(1+SLIP_CLOSE); outc = "TP"; break
            if ex is None:
                jb = min(i+p.max_hold, n-1)
                ex = c[jb]*(1-SLIP_CLOSE) if side == 1 else c[jb]*(1+SLIP_CLOSE)
            gross = (ex-ef)/ef if side == 1 else (ef-ex)/ef
            net = gross - 2*TAKER_FEE
            trades.append({"side": side, "outcome": outc, "ret": net,
                           "R": net/sl_d if sl_d else 0.0, "tier": int(tier[i]),
                           "bars": bars, "dt": dt[i]})
            i += bars + p.cooldown
        else:
            i += 1
    return trades


def stats(trades: list) -> dict:
    if not trades:
        return {"trades": 0}
    rets = np.array([t["ret"] for t in trades]); R = np.array([t["R"] for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    eq = (1+rets).cumprod(); dd = (eq-np.maximum.accumulate(eq))/np.maximum.accumulate(eq)
    gl = -losses.sum()
    return {
        "trades": len(trades),
        "win_rate": round(len(wins)/len(trades)*100, 1),
        "tp_rate": round(sum(t["outcome"] == "TP" for t in trades)/len(trades)*100, 1),
        "expectancy_pct": round(float(rets.mean()*100), 4),
        "avg_R": round(float(R.mean()), 3),
        "profit_factor": round(float(wins.sum()/gl), 2) if gl > 0 else float("inf"),
        "total_return_pct": round(float((eq[-1]-1)*100), 2),
        "max_dd_pct": round(float(dd.min()*100), 2),
    }


def latest_signal(d: pd.DataFrame, mode: str = "confluence", confirmed: bool = True) -> dict:
    """Signal for the last CLOSED bar (confirmed=True -> iloc[-2], non-repaint)."""
    if d is None or len(d) < 62:
        return {"side": 0, "reason": "insufficient_history"}
    i = -2 if (confirmed and len(d) > 2) else -1
    long_ok, short_ok = _ok_arrays(d, mode)
    row = d.iloc[i]
    side = 1 if bool(long_ok[i]) else (-1 if bool(short_ok[i]) else 0)
    a4 = row["adx_4h"]
    return {"side": side, "tier": int(row["tier"]), "trend": bool(row["trend"]),
            "mf_vote": int(row["mf_vote"]), "rg_vote": int(row["rg_vote"]),
            "mr_vote": int(row["mr_vote"]), "mf_score": float(row["mf_score"]),
            "close": float(row["close"]),
            "atr_pct": float(row["atr"]/row["close"]*100) if row["close"] else 0.0,
            "rsi": float(row["rsi"]), "adx_4h": (float(a4) if a4 == a4 else None)}
