#!/usr/bin/env python3
"""H1: bundle strategies AS SHIPPED, tested at scale.
5 symbols x {1d,4h,1h} x 3 exchange fee models, 2023-01 -> 2026-07-18,
bundle-faithful accounting (flip engine). Also IS/OOS sub-period views.
"""
import pandas as pd
from engine import load, flip_backtest, BUNDLE_STRATS, FEES

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
BARS_PER_DAY = {"1d": 1, "4h": 6, "1h": 24}
OOS_START = pd.Timestamp("2025-07-01", tz="UTC")

rows = []
for ex in ["binance", "bybit", "bitget"]:
    taker = FEES[ex]["taker"]
    for sym in SYMS:
        for tf in ["1d", "4h", "1h"]:
            try:
                df = load(ex, sym, tf)
            except Exception as e:
                print("no data", ex, sym, tf, e)
                continue
            df = df[df.ts >= "2023-01-01"].reset_index(drop=True)
            for name, fn in BUNDLE_STRATS.items():
                pos = fn(df)
                for period, mask in [("FULL", df.ts >= "2023-01-01"),
                                     ("IS", df.ts < OOS_START),
                                     ("OOS", df.ts >= OOS_START)]:
                    d2 = df[mask].reset_index(drop=True)
                    p2 = pos[mask.values].reset_index(drop=True)
                    if len(d2) < 60:
                        continue
                    r = flip_backtest(d2, p2, taker, bars_per_day=BARS_PER_DAY[tf])
                    rows.append({"exchange": ex, "symbol": sym, "tf": tf,
                                 "strategy": name, "period": period, **r})
res = pd.DataFrame(rows)
res.to_csv("/root/work/pipeline/h1_results.csv", index=False)

# pooled per-strategy view (sum trades, trade-weighted WR, total of per-symbol returns)
full = res[res.period == "FULL"]
for tf in ["1d", "4h", "1h"]:
    sub = full[full.tf == tf]
    if not len(sub):
        continue
    print(f"\n=== {tf} FULL 2023-01..2026-07  (per strategy x exchange, pooled over 5 symbols) ===")
    g = sub.groupby(["strategy", "exchange"]).apply(
        lambda d: pd.Series({
            "Trades": d.Trades.sum(),
            "WR%": (d["Win%"] * d.Trades).sum() / max(d.Trades.sum(), 1),
            "AvgSymbolReturn%": d["Return%"].mean(),
            "WorstSymbolDD%": d["MaxDD%"].min(),
        }), include_groups=False).round(2)
    print(g.to_string())

oos = res[res.period == "OOS"]
print("\n=== OOS (2025-07 -> 2026-07) pooled, 1h ===")
g = oos[oos.tf == "1h"].groupby(["strategy", "exchange"]).apply(
    lambda d: pd.Series({
        "Trades": d.Trades.sum(),
        "WR%": (d["Win%"] * d.Trades).sum() / max(d.Trades.sum(), 1),
        "AvgSymbolReturn%": d["Return%"].mean(),
    }), include_groups=False).round(2)
print(g.to_string())
print("\nsaved h1_results.csv")
