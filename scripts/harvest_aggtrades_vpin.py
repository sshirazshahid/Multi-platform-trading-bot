#!/usr/bin/env python3
"""Harvest Binance USDT-M aggTrades → VPIN time series for frozen prereg 27.

Streams Vision monthly/daily zips; does NOT retain raw trades. Writes:
  data/aggtrades_vpin/{SYMBOL}_vpin.parquet
  data/aggtrades_vpin/manifest.json

Construction (frozen 27_prereg_vpin_jump_veto):
  - Bucket vol = trailing_24h_volume / 50, recalculated each UTC day
  - Lee-Ready: is_buyer_maker True → sell aggressor volume
  - VPIN = rolling mean of |Vb−Vs|/V_bucket over last N=50 closed buckets
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "aggtrades_vpin"
VISION = "https://data.binance.vision/data/futures/um"
PREREG_JSON = ROOT / "_workspace" / "strategy_pipeline" / "27_prereg_vpin_jump_veto.json"
N_BUCKETS = 50
BUCKET_DIV = 50


def _fetch_to_temp(url: str, retries: int = 4) -> tuple[Path, str, int] | None:
    """Download URL to temp file; return (path, sha256, nbytes)."""
    for attempt in range(retries):
        tmp: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix="vpin_", suffix=".zip")
            tmp = Path(name)
            h = hashlib.sha256()
            nbytes = 0
            with urllib.request.urlopen(url, timeout=300) as resp, open(fd, "wb") as out:
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    nbytes += len(chunk)
            return tmp, h.hexdigest(), nbytes
        except urllib.error.HTTPError as exc:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            if exc.code == 404:
                return None
            time.sleep(2 + attempt)
        except Exception:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            time.sleep(2 + attempt)
    return None


def _stream_trades_path(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8")
            reader = csv.reader(text)
            header = next(reader, None)
            if not header:
                return
            cols = [h.strip().lower() for h in header]
            i_ts = cols.index("transact_time")
            i_qty = cols.index("quantity")
            i_bm = cols.index("is_buyer_maker")
            for row in reader:
                if len(row) <= max(i_ts, i_qty, i_bm):
                    continue
                ts_ms = int(row[i_ts])
                qty = float(row[i_qty])
                bm = row[i_bm].strip().lower() in ("true", "1", "yes")
                yield ts_ms, qty, bm


class VpinBuilder:
    """Online VPIN from aggressor-classified trade stream (memory-safe).

    Aggregates to 1-second bars before bucketing so the 24h volume window and
    emitted VPIN series stay O(seconds) not O(trades).
    """

    def __init__(self, n_buckets: int = N_BUCKETS):
        self.n = n_buckets
        # Per-second volume for trailing 24h (ts_sec -> qty)
        self.sec_vol: dict[int, float] = {}
        self.vol_24h_sum = 0.0
        self.bucket_size = None
        self.bucket_day = None
        self.buy = 0.0
        self.sell = 0.0
        self.filled = 0.0
        self.imbalances: deque[float] = deque(maxlen=n_buckets)
        self.rows: list[dict] = []
        # Second bar accumulator
        self.cur_sec: int | None = None
        self.cur_buy = 0.0
        self.cur_sell = 0.0

    def _trim_24h(self, ts_sec: int) -> None:
        cutoff = ts_sec - 86_400
        stale = [s for s in self.sec_vol if s < cutoff]
        for s in stale:
            self.vol_24h_sum -= self.sec_vol.pop(s)

    def _maybe_recalc_bucket(self, ts_sec: int) -> None:
        day = datetime.fromtimestamp(ts_sec, tz=timezone.utc).date()
        if self.bucket_day == day and self.bucket_size is not None:
            return
        self._trim_24h(ts_sec)
        if self.vol_24h_sum <= 0:
            return
        self.bucket_size = self.vol_24h_sum / BUCKET_DIV
        self.bucket_day = day

    def _close_second(self, ts_sec: int) -> None:
        qty = self.cur_buy + self.cur_sell
        if qty <= 0:
            return
        prev = self.sec_vol.get(ts_sec, 0.0)
        self.sec_vol[ts_sec] = prev + qty
        self.vol_24h_sum += qty
        self._maybe_recalc_bucket(ts_sec)
        if self.bucket_size is None or self.bucket_size <= 0:
            self.cur_buy = self.cur_sell = 0.0
            return

        # Feed second's buy/sell into volume-clock buckets
        buy_left, sell_left = self.cur_buy, self.cur_sell
        while buy_left + sell_left > 1e-12:
            room = self.bucket_size - self.filled
            take_total = min(buy_left + sell_left, room)
            if buy_left + sell_left <= 0:
                break
            # Pro-rate take across buy/sell residual
            tot = buy_left + sell_left
            take_buy = take_total * (buy_left / tot)
            take_sell = take_total - take_buy
            self.buy += take_buy
            self.sell += take_sell
            self.filled += take_total
            buy_left -= take_buy
            sell_left -= take_sell
            if self.filled + 1e-12 >= self.bucket_size:
                tot_b = self.buy + self.sell
                imb = abs(self.buy - self.sell) / tot_b if tot_b > 0 else 0.0
                self.imbalances.append(imb)
                vpin = sum(self.imbalances) / len(self.imbalances)
                # At most one emit per closing second
                ts_ms = ts_sec * 1000 + 999
                if self.rows and self.rows[-1]["ts_ms"] // 1000 == ts_sec:
                    self.rows[-1] = {
                        "ts_ms": ts_ms,
                        "vpin": float(vpin),
                        "imbalance": float(imb),
                        "n_closed": len(self.imbalances),
                        "bucket_size": float(self.bucket_size),
                    }
                else:
                    self.rows.append(
                        {
                            "ts_ms": ts_ms,
                            "vpin": float(vpin),
                            "imbalance": float(imb),
                            "n_closed": len(self.imbalances),
                            "bucket_size": float(self.bucket_size),
                        }
                    )
                self.buy = self.sell = self.filled = 0.0
        self.cur_buy = self.cur_sell = 0.0

    def on_trade(self, ts_ms: int, qty: float, buyer_maker: bool) -> None:
        ts_sec = ts_ms // 1000
        if self.cur_sec is None:
            self.cur_sec = ts_sec
        if ts_sec != self.cur_sec:
            self._close_second(self.cur_sec)
            self.cur_sec = ts_sec
        if buyer_maker:
            self.cur_sell += qty
        else:
            self.cur_buy += qty

    def flush(self) -> None:
        if self.cur_sec is not None:
            self._close_second(self.cur_sec)
            self.cur_sec = None


def _iter_sources(symbol: str, months: list[str], daily: list[str]):
    for m in months:
        yield (
            f"{VISION}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{m}.zip",
            f"monthly:{m}",
        )
    for d in daily:
        yield (
            f"{VISION}/daily/aggTrades/{symbol}/{symbol}-aggTrades-{d}.zip",
            f"daily:{d}",
        )


def harvest_symbol(symbol: str, months: list[str], daily: list[str]) -> Path:
    builder = VpinBuilder()
    sources = []
    for url, label in _iter_sources(symbol, months, daily):
        print(f"  [{symbol}] fetch {label} ...", flush=True)
        got = _fetch_to_temp(url)
        if got is None:
            print(f"  [{symbol}] SKIP missing {label}")
            continue
        tmp, sha, nbytes = got
        try:
            sources.append({"label": label, "bytes": nbytes, "sha256": sha})
            print(f"  [{symbol}] stream {label} ({nbytes/1e6:.1f} MB) ...", flush=True)
            n = 0
            for ts_ms, qty, bm in _stream_trades_path(tmp):
                builder.on_trade(ts_ms, qty, bm)
                n += 1
                if n % 5_000_000 == 0:
                    print(
                        f"    ... {n:,} trades, {len(builder.rows):,} VPIN points",
                        flush=True,
                    )
            builder.flush()
            print(
                f"  [{symbol}] done {label}: {n:,} trades → {len(builder.rows):,} VPIN pts",
                flush=True,
            )
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{symbol}_vpin.parquet"
    df = pd.DataFrame(builder.rows)
    if df.empty:
        raise RuntimeError(f"{symbol}: no VPIN rows produced")
    df.to_parquet(out, index=False)
    man_path = OUT_DIR / "manifest.json"
    man = {}
    if man_path.exists():
        man = json.loads(man_path.read_text(encoding="utf-8"))
    man[symbol] = {
        "path": str(out.relative_to(ROOT)).replace("\\", "/"),
        "n_rows": int(len(df)),
        "ts_min_ms": int(df["ts_ms"].iloc[0]),
        "ts_max_ms": int(df["ts_ms"].iloc[-1]),
        "sources": sources,
        "n_buckets": N_BUCKETS,
        "bucket_div": BUCKET_DIV,
        "prereg_sha256_md": json.loads(PREREG_JSON.read_text(encoding="utf-8")).get(
            "sha256_md"
        ),
        "harvested_utc": datetime.now(timezone.utc).isoformat(),
    }
    man_path.write_text(json.dumps(man, indent=2), encoding="utf-8")
    print(f"  [{symbol}] wrote {out} ({len(df):,} rows)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--months", default="2026-06", help="comma YYYY-MM monthly zips")
    ap.add_argument(
        "--daily",
        default="2026-07-01,2026-07-02,2026-07-03,2026-07-04,2026-07-05,2026-07-06,"
        "2026-07-07,2026-07-08,2026-07-09,2026-07-10,2026-07-11,2026-07-12",
        help="comma YYYY-MM-DD daily zips (July monthly not yet published)",
    )
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    daily = [d.strip() for d in args.daily.split(",") if d.strip()]

    print("=" * 70)
    print("VPIN aggTrades harvest (frozen prereg 27)")
    print("=" * 70)
    print(f"symbols={symbols} months={months} daily_n={len(daily)}")

    for sym in symbols:
        harvest_symbol(sym, months, daily)
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
