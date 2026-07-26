#!/usr/bin/env python3
"""Download real market data for the bundle test.
- Binance vision: monthly+daily USDT-M klines (1h,4h,1d) + funding, 2023-01..2026-07-18
- Bitget API: native 1h/4h candles + funding history, as far back as available
- Bybit archive: one 2024 MT4 kline file to validate the Binance-price proxy
Writes parquet files under /root/work/pipeline/data/
"""
import io, os, sys, time, zipfile, json, gzip
import urllib.request, urllib.error, socket
import pandas as pd

socket.setdefaulttimeout(30)
BASE = "/root/work/pipeline/data"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
TFS = ["1h", "4h", "1d"]
os.makedirs(BASE, exist_ok=True)

def fetch(url, retries=3):
    for a in range(retries):
        try:
            return urllib.request.urlopen(url).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1 + a)
        except Exception:
            time.sleep(1 + a)
    return None

KCOLS = ["open_time","open","high","low","close","volume","close_time",
         "qvol","count","tb_vol","tb_qvol","ignore"]

def parse_kline_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name), header=None)
    # newer files carry a header row
    if isinstance(df.iloc[0, 0], str) and not str(df.iloc[0, 0]).isdigit():
        df = df.iloc[1:].reset_index(drop=True)
    df.columns = KCOLS[: df.shape[1]]
    for c in ["open_time","open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    return df[["open_time","open","high","low","close","volume"]]

def binance_klines():
    months = pd.period_range("2023-01", "2026-06", freq="M").strftime("%Y-%m")
    for sym in SYMS:
        for tf in TFS:
            out = f"{BASE}/binance_{sym}_{tf}.parquet"
            if os.path.exists(out):
                print("skip", out); continue
            parts = []
            for m in months:
                raw = fetch(f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/{tf}/{sym}-{tf}-{m}.zip")
                if raw: parts.append(parse_kline_zip(raw))
            for d in pd.date_range("2026-07-01", "2026-07-18"):
                ds = d.strftime("%Y-%m-%d")
                raw = fetch(f"https://data.binance.vision/data/futures/um/daily/klines/{sym}/{tf}/{sym}-{tf}-{ds}.zip")
                if raw: parts.append(parse_kline_zip(raw))
            if parts:
                df = pd.concat(parts).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
                # some vision files use microsecond timestamps (2025+); normalize to ms
                df.loc[df.open_time > 10**14, "open_time"] //= 1000
                df["ts"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
                df.to_parquet(out)
                print("saved", out, len(df), df.ts.min(), "->", df.ts.max(), flush=True)

def binance_funding():
    months = pd.period_range("2023-01", "2026-07", freq="M").strftime("%Y-%m")
    for sym in SYMS:
        out = f"{BASE}/binance_funding_{sym}.parquet"
        if os.path.exists(out): continue
        parts = []
        for m in months:
            raw = fetch(f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
            if raw:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    d = pd.read_csv(z.open(z.namelist()[0]))
                parts.append(d)
        if parts:
            df = pd.concat(parts)
            df.columns = [c.strip() for c in df.columns]
            tcol = [c for c in df.columns if "calc" in c.lower() or "time" in c.lower()][0]
            rcol = [c for c in df.columns if "rate" in c.lower()][-1]
            df = df.rename(columns={tcol: "fund_time", rcol: "rate"})[["fund_time","rate"]]
            df["fund_time"] = pd.to_numeric(df.fund_time, errors="coerce")
            df = df.dropna()
            df.loc[df.fund_time > 10**14, "fund_time"] //= 1000
            df["ts"] = pd.to_datetime(df.fund_time, unit="ms", utc=True)
            df["rate"] = pd.to_numeric(df.rate)
            df.sort_values("ts").to_parquet(out)
            print("saved", out, len(df), flush=True)

def bitget_candles():
    GR = {"1h": ("1H", 3600_000), "4h": ("4H", 14400_000)}
    for sym in SYMS:
        for tf, (g, tf_ms) in GR.items():
            out = f"{BASE}/bitget_{sym}_{tf}.parquet"
            if os.path.exists(out): continue
            rows, end = [], int(time.time() * 1000)
            target_start = int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)
            for _ in range(400):
                url = (f"https://api.bitget.com/api/v2/mix/market/history-candles?symbol={sym}"
                       f"&productType=usdt-futures&granularity={g}&limit=200&endTime={end}")
                raw = fetch(url)
                if not raw: break
                d = json.loads(raw)
                data = d.get("data") or []
                if not data: break
                rows = data + rows
                oldest = int(data[0][0])
                if oldest <= target_start or len(data) < 200:
                    break
                end = oldest
                time.sleep(0.12)
            if rows:
                df = pd.DataFrame(rows, columns=["ts_ms","open","high","low","close","base_vol","quote_vol"])
                df = df.drop_duplicates("ts_ms")
                for c in ["open","high","low","close","base_vol"]:
                    df[c] = pd.to_numeric(df[c])
                df["open_time"] = pd.to_numeric(df.ts_ms)
                df["ts"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
                df = df.rename(columns={"base_vol":"volume"})
                df[["open_time","open","high","low","close","volume","ts"]].sort_values("open_time").to_parquet(out)
                print("saved", out, len(df), df.ts.min(), "->", df.ts.max(), flush=True)

def bitget_funding():
    for sym in SYMS:
        out = f"{BASE}/bitget_funding_{sym}.parquet"
        if os.path.exists(out): continue
        rows = []
        for page in range(1, 60):
            url = (f"https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol={sym}"
                   f"&productType=usdt-futures&pageSize=100&pageNo={page}")
            raw = fetch(url)
            if not raw: break
            data = (json.loads(raw).get("data")) or []
            if not data: break
            rows += data
            if len(data) < 100: break
            time.sleep(0.12)
        if rows:
            df = pd.DataFrame(rows)
            df["ts"] = pd.to_datetime(pd.to_numeric(df.fundingTime), unit="ms", utc=True)
            df["rate"] = pd.to_numeric(df.fundingRate)
            df[["ts","rate"]].sort_values("ts").to_parquet(out)
            print("saved", out, len(df), flush=True)

def bybit_2024_sample():
    """Grab Bybit MT4 1m klines for BTCUSDT 2024 to validate the Binance-price proxy."""
    idx = fetch("https://public.bybit.com/kline_for_metatrader4/BTCUSDT/2024/")
    if not idx:
        print("bybit index unavailable"); return
    import re
    names = re.findall(r'href="([^"]+)"', idx.decode())
    print("bybit 2024 files:", names[:20], flush=True)
    parts = []
    for n in names:
        if not n.endswith(".csv.gz"):
            continue
        raw = fetch(f"https://public.bybit.com/kline_for_metatrader4/BTCUSDT/2024/{n}")
        if raw:
            try:
                d = pd.read_csv(io.BytesIO(raw), compression="gzip", header=None)
                parts.append(d)
            except Exception as e:
                print("parse fail", n, e)
    if parts:
        df = pd.concat(parts)
        df.to_parquet(f"{BASE}/bybit_BTCUSDT_2024_raw.parquet")
        print("saved bybit raw", len(df), "cols:", df.shape[1], flush=True)
        print(df.head(3).to_string(), flush=True)

if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("all","binance"): binance_klines(); binance_funding()
    if step in ("all","bitget"): bitget_candles(); bitget_funding()
    if step in ("all","bybit"): bybit_2024_sample()
    print("DONE", step, flush=True)
