"""Pair-dossier data-gap backfill (keyless public ccxt bybit).

Closes the two BACKFILL_FIRST gap classes the per-pair dossier
(`scripts/pair_indicator_dossier.py` -> reports/pair_indicator_dossier_*.json)
reports for the spec-derived probe universe:

  * funding : bases with NO data/funding_history/bybit_{BASE}.csv — paginated
    bybit v5 funding-rate history (200/page) via the proven helpers in
    scripts/backfill_funding_history.py (idempotent CSV merge, dedupe on ts).
  * ohlcv   : data/ohlcv_cache/{BASE}-USDT_{1h,4h}.parquet refresh — APPENDS
    bars from the last cached bar to now via core.feature_store
    .load_ohlcv_window (the canonical cache writer; schema ts/open/high/low/
    close/volume, ts unix-seconds int64, never truncates history). Absent
    files are created with --days-new of history. Fetch source is keyless
    bybit spot BASE/USDT, falling back to the linear perp BASE/USDT:USDT
    when no spot market exists (source recorded in the run log).

Universe: the pairs list of the newest reports/pair_indicator_dossier_*.json
(or --bases). Read-only against exchanges; writes ONLY local artifacts under
data/ (public repo — data/ is never committed). Research-grade; no bot state,
no live-path code touched.

Usage:
    venv\\Scripts\\python.exe scripts\\backfill_pair_gaps.py --leg all
    venv\\Scripts\\python.exe scripts\\backfill_pair_gaps.py --leg funding
    venv\\Scripts\\python.exe scripts\\backfill_pair_gaps.py --leg ohlcv --bases 1INCH,UNI
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNDING_DIR = os.path.join(_REPO, "data", "funding_history")
FUNDING_SINCE = "2021-01-01"
TF_SECONDS = {"1h": 3600, "4h": 4 * 3600}


def latest_dossier_bases() -> list[str]:
    paths = sorted(glob.glob(os.path.join(_REPO, "reports", "pair_indicator_dossier_*.json")))
    if not paths:
        raise SystemExit("no reports/pair_indicator_dossier_*.json found; pass --bases")
    with open(paths[-1], encoding="utf-8") as f:
        doc = json.load(f)
    bases = [p["base"] for p in doc.get("pairs", [])]
    if not bases:
        raise SystemExit(f"dossier {paths[-1]} has no pairs; pass --bases")
    print(f"universe: {len(bases)} bases from {os.path.basename(paths[-1])}", flush=True)
    return bases


def backfill_funding(bases: list[str], *, force: bool = False) -> list[dict]:
    """Bybit funding-history CSVs for bases missing them (or all with force)."""
    from scripts.backfill_funding_history import (
        PAGE_LIMIT,
        _ms,
        _perp_symbol,
        fetch_funding_history_paginated,
        make_exchange,
        write_history_csv,
    )

    todo = [b for b in bases
            if force or not os.path.exists(os.path.join(FUNDING_DIR, f"bybit_{b}.csv"))]
    print(f"[funding] {len(todo)}/{len(bases)} bases need bybit funding CSVs: "
          f"{', '.join(todo) or 'none'}", flush=True)
    if not todo:
        return []
    ex = make_exchange("bybit")
    ex.load_markets()
    since_ms = _ms(FUNDING_SINCE)
    report = []
    for base in todo:
        sym = _perp_symbol(base)
        if sym not in ex.markets:
            print(f"[funding] bybit {base}: NOT LISTED ({sym})", flush=True)
            report.append({"base": base, "status": "not_listed", "rows": 0, "added": 0})
            continue
        try:
            rows = fetch_funding_history_paginated(ex, sym, since_ms, limit=PAGE_LIMIT["bybit"])
        except RuntimeError as e:
            print(f"[funding] bybit {base}: FETCH ERROR {e}", flush=True)
            report.append({"base": base, "status": "fetch_error", "rows": 0, "added": 0,
                           "err": str(e)})
            continue
        total, added = write_history_csv("bybit", base, sym, rows) if rows else (0, 0)
        print(f"[funding] bybit {base}: fetched={len(rows)} total={total} added={added}",
              flush=True)
        report.append({"base": base, "status": "ok" if rows else "empty",
                       "rows": total, "added": added})
        time.sleep(0.2)
    return report


def _make_bybit_fetcher(ex):
    """fetcher(symbol, tf, since_ms, limit) for load_ohlcv_window.

    `symbol` arrives as spot form BASE/USDT (that is the cache filename key);
    resolve to bybit spot, else the linear perp BASE/USDT:USDT.
    """
    def _fetch(symbol, timeframe, since_ms, limit):
        actual = symbol if symbol in ex.markets else f"{symbol}:USDT"
        if actual not in ex.markets:
            raise RuntimeError(f"no bybit spot or linear market for {symbol}")
        return ex.fetch_ohlcv(actual, timeframe, since=int(since_ms),
                              limit=min(int(limit), 1000)) or []
    return _fetch


def refresh_ohlcv(bases: list[str], *, days_new: float, throttle: float) -> list[dict]:
    """Append 1h+4h bars up to now for every base; create absent files."""
    import ccxt

    from core.feature_store import _cache_path, load_ohlcv_window

    ex = ccxt.bybit({"enableRateLimit": True})
    ex.load_markets()
    fetcher = _make_bybit_fetcher(ex)
    now = int(time.time())
    report = []
    for i, base in enumerate(bases, 1):
        symbol = f"{base}/USDT"
        src = "spot" if symbol in ex.markets else (
            "perp" if f"{symbol}:USDT" in ex.markets else "none")
        if src == "none":
            print(f"[ohlcv] [{i}/{len(bases)}] {base}: no bybit market", flush=True)
            report.append({"base": base, "status": "no_market"})
            continue
        rec = {"base": base, "status": "ok", "src": src}
        for tf in ("1h", "4h"):
            p = _cache_path(symbol, tf)
            last_ts = None
            if p.exists():
                try:
                    old = pd.read_parquet(p)
                    if len(old):
                        last_ts = int(old["ts"].max())
                except Exception:
                    last_ts = None
            before = 0 if last_ts is None else len(old)
            # Append-only: start at the last cached bar (re-fetched + deduped),
            # or --days-new back for absent files. History is never truncated —
            # load_ohlcv_window merges old + new and dedupes on ts.
            start = last_ts if last_ts is not None else now - int(days_new * 86400)
            try:
                load_ohlcv_window(symbol, tf, start, now, fetcher=fetcher)
                after = len(pd.read_parquet(p)) if p.exists() else 0
            except Exception as e:
                rec["status"] = "fetch_error"
                rec[f"{tf}_err"] = str(e)[:120]
                print(f"[ohlcv] [{i}/{len(bases)}] {base} {tf}: ERROR {str(e)[:120]}",
                      flush=True)
                continue
            rec[f"{tf}_rows"] = after
            rec[f"{tf}_added"] = after - before
            print(f"[ohlcv] [{i}/{len(bases)}] {base} {tf} via {src}: "
                  f"rows={after} (+{after - before})", flush=True)
            time.sleep(throttle)
        report.append(rec)
    return report


def main() -> None:
    try:  # windows cp1252 stdout guard (same as backfill_funding_history)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - best-effort
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=["funding", "ohlcv", "all"], default="all")
    ap.add_argument("--bases", default=None,
                    help="comma-separated bases (default: latest dossier universe)")
    ap.add_argument("--days-new", type=float, default=365.0,
                    help="history depth for ohlcv files that do not exist yet")
    ap.add_argument("--throttle", type=float, default=0.25,
                    help="inter-request sleep (s) — the live paper bot shares this IP")
    ap.add_argument("--funding-force", action="store_true",
                    help="re-fetch funding even when the CSV already exists")
    args = ap.parse_args()

    bases = ([b.strip().upper() for b in args.bases.split(",") if b.strip()]
             if args.bases else latest_dossier_bases())

    if args.leg in ("funding", "all"):
        rep = backfill_funding(bases, force=args.funding_force)
        ok = sum(1 for r in rep if r["status"] == "ok")
        print(f"[funding] done: {ok}/{len(rep)} fetched ok", flush=True)
    if args.leg in ("ohlcv", "all"):
        rep = refresh_ohlcv(bases, days_new=args.days_new, throttle=args.throttle)
        ok = sum(1 for r in rep if r["status"] == "ok")
        print(f"[ohlcv] done: {ok}/{len(rep)} bases refreshed ok", flush=True)


if __name__ == "__main__":
    main()
