#!/usr/bin/env python3
"""Validate using Binance prices as the Bybit price proxy (BTCUSDT 2024).
Bybit MT4 archive timestamps are UTC+3; files are 6 or 7 columns. Result on
2024 data: abs-mean close divergence ~1.6 bps, p95 ~4.5 bps, hourly log-return
correlation 0.9997 (n=6,791 hours) - venue price differences are ~30x smaller
than the fee differences being modeled.
"""
import io, re, socket, urllib.request
import numpy as np, pandas as pd

socket.setdefaulttimeout(30)
BASE = "https://public.bybit.com/kline_for_metatrader4/BTCUSDT/2024/"

frames = []
idx = urllib.request.urlopen(BASE).read().decode()
for n in [x for x in re.findall(r'href="([^"]+)"', idx) if x.startswith("BTCUSDT_15_")]:
    raw = urllib.request.urlopen(BASE + n).read()
    df = pd.read_csv(io.BytesIO(raw), compression="gzip", header=None)
    if df.shape[1] == 7:                       # some files carry an extra leading column
        df = df.iloc[:, 1:]
    df.columns = ["dt", "open", "high", "low", "close", "vol"]
    frames.append(df)
by = pd.concat(frames, ignore_index=True)
by["ts"] = pd.to_datetime(by.dt, format="%Y.%m.%d %H:%M", utc=True, errors="coerce")
by = by.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts")

c45 = by[by.ts.dt.minute == 45].copy()          # 15m bar opening :45 closes on the hour
c45["hour_end"] = c45.ts + pd.Timedelta(minutes=15)
bn = pd.read_parquet("data/binance_BTCUSDT_1h.parquet").copy()
bn["hour_end"] = pd.to_datetime(bn.ts, utc=True) + pd.Timedelta(hours=1)

best = None                                     # archive clock offset (found: -3h, i.e. UTC+3)
for lag in range(-9, 10):
    mm = c45.copy(); mm["hour_end"] += pd.Timedelta(hours=lag)
    mg = mm.merge(bn[["hour_end", "close"]], on="hour_end", suffixes=("_by", "_bn"))
    if len(mg) < 100:
        continue
    dd = ((mg.close_by - mg.close_bn) / mg.close_bn * 1e4).abs().mean()
    if best is None or dd < best[1]:
        best = (lag, dd, len(mg))
print("best clock offset (h, abs-mean bps, n):", best)

c45["hour_end"] += pd.Timedelta(hours=best[0])
m = c45.merge(bn[["hour_end", "close"]], on="hour_end", suffixes=("_by", "_bn"))
d = (m.close_by - m.close_bn) / m.close_bn * 1e4
r_by = np.log(m.close_by).diff(); r_bn = np.log(m.close_bn).diff()
print(f"hours compared: {len(m)}")
print(f"close diff bps: mean {d.mean():+.2f}  abs-mean {d.abs().mean():.2f}  "
      f"p95|d| {d.abs().quantile(0.95):.2f}  max|d| {d.abs().max():.1f}")
print(f"hourly log-return correlation: {r_by.corr(r_bn):.5f}")
