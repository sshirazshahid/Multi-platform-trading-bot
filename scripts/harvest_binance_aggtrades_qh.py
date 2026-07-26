#!/usr/bin/env python3
"""Harvest Binance USDT-M aggTrades → quarter-hour opening-10s imbalance events.

Pilot (frozen prereg C3): BTCUSDT + ETHUSDT, 2026-04 → 2026-06 monthly zips.
Streams CSV rows and discards everything outside each opening 10s window.

Output: data/aggtrades_qh/{SYMBOL}_qh_events.parquet + manifest.json (gitignored)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "aggtrades_qh"
VISION = "https://data.binance.vision/data/futures/um/monthly/aggTrades"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
MONTHS = ("2026-04", "2026-05", "2026-06")
WINDOW_MS = 10_000
PREREG_JSON = ROOT / "_workspace" / "strategy_pipeline" / "22_prereg_c3_quarter_hour_imbalance.json"


def _fetch(url: str, retries: int = 3) -> bytes | None:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            time.sleep(1 + attempt)
        except Exception:
            time.sleep(1 + attempt)
    return None


def boundary_ms(ts_ms: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    qm = (dt.minute // 15) * 15
    b = dt.replace(minute=qm, second=0, microsecond=0)
    return int(b.timestamp() * 1000)


def in_opening_window(ts_ms: int, b_ms: int) -> bool:
    return b_ms <= ts_ms < b_ms + WINDOW_MS


def process_zip(raw: bytes, symbol: str) -> list[dict]:
    """Stream aggTrades CSV; emit one row per quarter-hour boundary with ≥1 trade."""
    events: dict[int, dict] = {}

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8")
            reader = csv.reader(text)
            header = next(reader, None)
            if not header:
                return []
            # normalize header
            cols = [h.strip().lower() for h in header]
            try:
                i_ts = cols.index("transact_time")
                i_px = cols.index("price")
                i_qty = cols.index("quantity")
                i_bm = cols.index("is_buyer_maker")
            except ValueError as exc:
                raise RuntimeError(f"{symbol}: unexpected CSV header {header}") from exc

            for row in reader:
                if len(row) <= max(i_ts, i_px, i_qty, i_bm):
                    continue
                ts_ms = int(row[i_ts])
                px = float(row[i_px])
                qty = float(row[i_qty])
                bm = row[i_bm].strip().lower() in ("true", "1", "yes")
                b_ms = boundary_ms(ts_ms)
                if not in_opening_window(ts_ms, b_ms):
                    continue
                ev = events.get(b_ms)
                if ev is None:
                    ev = {
                        "boundary_ms": b_ms,
                        "symbol": symbol,
                        "buy_qty": 0.0,
                        "sell_qty": 0.0,
                        "buy_dollar": 0.0,
                        "sell_dollar": 0.0,
                        "n_trades": 0,
                        "last_price": px,
                        "last_ts_ms": ts_ms,
                    }
                    events[b_ms] = ev
                dollar = px * qty
                if bm:
                    ev["sell_qty"] += qty
                    ev["sell_dollar"] += dollar
                else:
                    ev["buy_qty"] += qty
                    ev["buy_dollar"] += dollar
                ev["n_trades"] += 1
                if ts_ms >= ev["last_ts_ms"]:
                    ev["last_price"] = px
                    ev["last_ts_ms"] = ts_ms

    out = []
    for b_ms, ev in sorted(events.items()):
        tot_d = ev["buy_dollar"] + ev["sell_dollar"]
        tot_q = ev["buy_qty"] + ev["sell_qty"]
        ev["imbalance_dollar"] = (ev["buy_dollar"] - ev["sell_dollar"]) / (tot_d + 1e-12)
        ev["imbalance_qty"] = (ev["buy_qty"] - ev["sell_qty"]) / (tot_q + 1e-12)
        ev["boundary_ts"] = pd.Timestamp(b_ms, unit="ms", tz="UTC").isoformat()
        out.append(ev)
    return out


def harvest_symbol(symbol: str) -> pd.DataFrame:
    all_rows: list[dict] = []
    for month in MONTHS:
        url = f"{VISION}/{symbol}/{symbol}-aggTrades-{month}.zip"
        print(f"fetch {url}", flush=True)
        raw = _fetch(url)
        if raw is None:
            raise FileNotFoundError(f"missing aggTrades zip: {url}")
        print(f"  {len(raw)/1e6:.1f} MB — parsing opening windows…", flush=True)
        rows = process_zip(raw, symbol)
        print(f"  {len(rows)} boundary events", flush=True)
        all_rows.extend(rows)
    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows).drop_duplicates("boundary_ms").sort_values("boundary_ms")
    return df.reset_index(drop=True)


def write_manifest(paths: dict[str, str]) -> dict:
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": list(SYMBOLS),
        "months": list(MONTHS),
        "files": paths,
        "file_sha256": {},
    }
    for sym, rel in paths.items():
        p = ROOT / rel
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest["file_sha256"][sym] = h
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rel_paths: dict[str, str] = {}
    for symbol in SYMBOLS:
        out_path = OUT_DIR / f"{symbol}_qh_events.parquet"
        df = harvest_symbol(symbol)
        if df.empty:
            print(f"ERROR: no events for {symbol}", file=sys.stderr)
            return 1
        df.to_parquet(out_path, index=False)
        rel = str(out_path.relative_to(ROOT)).replace("\\", "/")
        rel_paths[symbol] = rel
        print(f"wrote {out_path} rows={len(df)}", flush=True)

    manifest = write_manifest(rel_paths)
    if PREREG_JSON.exists():
        prereg = json.loads(PREREG_JSON.read_text(encoding="utf-8"))
        prereg["harvest_manifest_sha256"] = manifest["manifest_sha256"]
        PREREG_JSON.write_text(json.dumps(prereg, indent=2), encoding="utf-8")
    print(json.dumps({"manifest_sha256": manifest["manifest_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
