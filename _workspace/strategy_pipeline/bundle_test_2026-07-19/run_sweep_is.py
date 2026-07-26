#!/usr/bin/env python3
"""Else-branch: IN-SAMPLE sweep only (2023-01-01 .. 2025-06-30).
Selection fee model: bybit (middle of the three) to avoid cheapest-fee bias.
Every evaluated config is logged to sweep_log.csv. Shortlist written to shortlist.json.
NO OOS data is touched here.
"""
import itertools, json
import numpy as np
import pandas as pd
from engine import load, load_funding, bracket_backtest, summarize, rsi, ema, adx, zscore, FEES

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
IS_END = pd.Timestamp("2025-07-01", tz="UTC")
SEL_FEES = FEES["bybit"]

# ---- pre-load IS data + indicator panels ----
panels = {}
for tf in ["1h", "4h"]:
    for sym in SYMS:
        df = load("bybit", sym, tf)
        df = df[(df.ts >= "2023-01-01") & (df.ts < IS_END)].reset_index(drop=True)
        ind = {
            "rsi2": rsi(df.close, 2).values, "rsi3": rsi(df.close, 3).values,
            "rsi14": rsi(df.close, 14).values,
            "z20": zscore(df.close, 20).values,
            "ema200": ema(df.close, 200).values,
            "adx14": adx(df, 14).values,
            "close": df.close.values,
        }
        fund = load_funding("bybit", sym)
        if fund is not None:
            fund = fund[fund.ts < IS_END].reset_index(drop=True)
        panels[(tf, sym)] = (df, ind, fund)
print("panels loaded", flush=True)

def apply_filter(ind, filt):
    n = len(ind["close"])
    ok_l = np.ones(n, bool); ok_s = np.ones(n, bool)
    if filt == "ema200":
        ok_l = ind["close"] > ind["ema200"]
        ok_s = ind["close"] < ind["ema200"]
    elif filt == "adx25":
        ok = ind["adx14"] < 25
        ok_l = ok_s = ok
    return ok_l, ok_s

def entries_mrb(ind, rp, thr, filt):
    r = ind[f"rsi{rp}"]
    ok_l, ok_s = apply_filter(ind, filt)
    return (r < thr) & ok_l, (r > 100 - thr) & ok_s

def entries_mrc(ind, ze, filt):
    z = ind["z20"]
    ok_l, ok_s = apply_filter(ind, filt)
    return (z < -ze) & ok_l, (z > ze) & ok_s

# ---- grid (fixed a-priori; every config logged) ----
TSTOP = {"1h": 24, "4h": 12}
grid = []
for tf in ["1h", "4h"]:
    for (rp, thr) in [(2, 10), (2, 15), (3, 15), (3, 20), (14, 30)]:
        for filt in ["none", "ema200", "adx25"]:
            for tp in [0.8, 1.0, 1.2]:
                for sl in [1.6, 2.0, 2.4]:
                    grid.append({"family": "MR-B", "tf": tf, "rsi_p": rp, "thr": thr,
                                 "filt": filt, "tp_mult": tp, "sl_mult": sl,
                                 "tstop": TSTOP[tf]})
    for ze in [1.5, 2.0, 2.5]:
        for filt in ["none", "ema200", "adx25"]:
            for tp in [0.8, 1.0, 1.2]:
                for sl in [1.6, 2.0, 2.4]:
                    grid.append({"family": "MR-C", "tf": tf, "z_e": ze,
                                 "filt": filt, "tp_mult": tp, "sl_mult": sl,
                                 "tstop": TSTOP[tf]})
print("grid size:", len(grid), flush=True)

rows = []
for gi, cfg in enumerate(grid):
    all_tr = []
    for sym in SYMS:
        df, ind, fund = panels[(cfg["tf"], sym)]
        if cfg["family"] == "MR-B":
            el, es = entries_mrb(ind, cfg["rsi_p"], cfg["thr"], cfg["filt"])
        else:
            el, es = entries_mrc(ind, cfg["z_e"], cfg["filt"])
        tr = bracket_backtest(df, el, es, cfg, SEL_FEES, funding=fund)
        if len(tr):
            tr["symbol"] = sym
            all_tr.append(tr)
    tr = pd.concat(all_tr) if all_tr else pd.DataFrame(columns=["net", "exit_ts"])
    s = summarize(tr) if len(tr) else {"Trades": 0, "Win%": np.nan, "PF": np.nan,
                                       "NetSum%": 0, "AvgBps": np.nan, "Eq%": 0, "MaxDD%": 0}
    rows.append({"cfg_id": gi, **cfg, **s})
    if gi % 50 == 0:
        print(f"{gi}/{len(grid)}", flush=True)

log = pd.DataFrame(rows)
log.to_csv("/root/work/pipeline/sweep_log.csv", index=False)

# ---- selection: IS band [60,70], profitable, PF>=1.05, >=100 IS trades ----
sel = log[(log.Trades >= 100) & (log["Win%"] >= 60) & (log["Win%"] <= 70)
          & (log["NetSum%"] > 0) & (log.PF >= 1.05)].copy()
# rank by expectancy robustness: PF * sqrt(trades), prefer configs not on grid edge
sel["score"] = sel.PF * np.sqrt(sel.Trades)
sel = sel.sort_values("score", ascending=False)
shortlist = sel.head(10).to_dict("records")
json.dump(shortlist, open("/root/work/pipeline/shortlist.json", "w"), indent=1, default=str)
print("\nIS configs in band & profitable:", len(sel), "of", len(grid))
cols = [c for c in ["cfg_id","family","tf","rsi_p","thr","z_e","filt","tp_mult","sl_mult",
                    "Trades","Win%","PF","NetSum%","AvgBps","MaxDD%"] if c in sel.columns]
print(sel.head(15)[cols].round(2).to_string(index=False))
