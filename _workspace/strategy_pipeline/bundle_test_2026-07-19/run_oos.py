#!/usr/bin/env python3
"""ONE-SHOT out-of-sample evaluation of the pre-selected shortlist.
OOS: 2025-07-01 -> 2026-07-18. Indicators warm up on full history; only trades
whose ENTRY falls in the OOS window count. Gates per prereg.md (G1-G5).
"""
import json
import numpy as np
import pandas as pd
from engine import (load, load_funding, bracket_backtest, summarize, bootstrap,
                    rsi, ema, adx, zscore, FEES)

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
OOS_START = pd.Timestamp("2025-07-01", tz="UTC")
shortlist = json.load(open("/root/work/pipeline/shortlist.json"))

def build_entries(df, cfg):
    ind_close = df.close
    if cfg["family"] == "MR-B":
        r = rsi(ind_close, int(float(cfg["rsi_p"]))).values
        thr = float(cfg["thr"])
        el = r < thr
        es = r > 100 - thr
    else:
        z = zscore(ind_close, 20).values
        ze = float(cfg["z_e"])
        el = z < -ze
        es = z > ze
    if cfg["filt"] == "ema200":
        e = ema(ind_close, 200).values
        el &= ind_close.values > e
        es &= ind_close.values < e
    elif cfg["filt"] == "adx25":
        a = adx(df, 14).values
        ok = a < 25
        el &= ok; es &= ok
    return el, es

rows, trade_store = [], {}
for cfg in shortlist:
    cid = cfg["cfg_id"]
    for ex in ["binance", "bybit", "bitget"]:
        all_tr = []
        for sym in SYMS:
            df = load(ex, sym, cfg["tf"])
            df = df[df.ts >= "2023-01-01"].reset_index(drop=True)
            el, es = build_entries(df, cfg)
            oos_mask = (df.ts >= OOS_START).values
            el = el & oos_mask
            es = es & oos_mask
            fund = load_funding(ex, sym)
            c = {"tp_mult": float(cfg["tp_mult"]), "sl_mult": float(cfg["sl_mult"]),
                 "tstop": int(float(cfg["tstop"]))}
            tr = bracket_backtest(df, el, es, c, FEES[ex], funding=fund)
            if len(tr):
                tr["symbol"] = sym
                all_tr.append(tr)
        tr = pd.concat(all_tr) if all_tr else pd.DataFrame(columns=["net", "exit_ts"])
        s = summarize(tr)
        b = bootstrap(tr) if len(tr) else {"P(net>0)": np.nan, "WR_lo": np.nan, "WR_hi": np.nan}
        gates = {
            "G1_n>=30": s["Trades"] >= 30,
            "G2_WR_in_band": (s["Win%"] >= 63) and (s["Win%"] <= 67) if s["Trades"] else False,
            "G3_profit": (s["NetSum%"] > 0) and (s["PF"] >= 1.05) if s["Trades"] else False,
            "G4_MC": (b["P(net>0)"] >= 0.90) if np.isfinite(b.get("P(net>0)", np.nan)) else False,
        }
        rows.append({"cfg_id": cid, "family": cfg["family"], "tf": cfg["tf"],
                     "rsi_p": cfg.get("rsi_p"), "thr": cfg.get("thr"), "z_e": cfg.get("z_e"),
                     "filt": cfg["filt"], "tp": cfg["tp_mult"], "sl": cfg["sl_mult"],
                     "exchange": ex, **s, **b, **gates,
                     "PASS_all": all(gates.values())})
        trade_store[(cid, ex)] = tr

res = pd.DataFrame(rows)
res.to_csv("/root/work/pipeline/oos_results.csv", index=False)

# G5: pass on >= 2 of 3 exchanges
verdicts = []
for cid, grp in res.groupby("cfg_id"):
    n_pass = int(grp.PASS_all.sum())
    verdicts.append({"cfg_id": cid, "exchanges_passing": n_pass, "G5_2of3": n_pass >= 2})
ver = pd.DataFrame(verdicts).sort_values("exchanges_passing", ascending=False)
print(res[["cfg_id","family","tf","filt","tp","sl","exchange","Trades","Win%","PF",
           "NetSum%","AvgBps","MaxDD%","P(net>0)","WR_lo","WR_hi","PASS_all"]]
      .round(2).to_string(index=False))
print("\n--- G5 verdicts ---")
print(ver.to_string(index=False))

# persist trades of full-pass configs for the report/paper phase
import pickle
winners = [int(c) for c in ver[ver.G5_2of3].cfg_id]
with open("/root/work/pipeline/oos_trades_winners.pkl", "wb") as f:
    pickle.dump({k: v for k, v in trade_store.items() if k[0] in winners}, f)
print("\nwinners:", winners)
