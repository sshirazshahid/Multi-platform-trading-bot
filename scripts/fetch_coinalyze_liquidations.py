"""Fetch hourly liquidation history from Coinalyze (free key) -> canonical jsonl.

Pulls long/short liquidation history (USD) for the named coins at 1-hour granularity and
writes data/liquidations_history.jsonl in the SAME schema the WS harvester uses, so
scripts/run_liquidation_edge_screen.py can consume it directly.

Coinalyze free tier keeps only ~1500-2000 intraday datapoints (≈60-80 days of hourly,
rolling), so this is a one-shot ~2-month snapshot. Key is read from .env (COINALYZE_API_KEY)
and NEVER printed. Defensive about the exact response field names (l/s vs long/short).

Usage: python scripts/fetch_coinalyze_liquidations.py
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "liquidations_history.jsonl"
BASE = "https://api.coinalyze.net/v1"
KEY = os.getenv("COINALYZE_API_KEY", "").strip()

COINS = ["BTC", "ETH", "BNB", "SOL", "LINK", "TRX", "SUI", "ATOM", "DOGE",
         "ALGO", "ZEC", "ADA", "XRP", "LTC", "GRT"]
DAYS = 80
INTERVAL = "1hour"


def _get(path: str, params: dict) -> object:
    p = dict(params)
    p["api_key"] = KEY
    r = requests.get(f"{BASE}{path}", params=p, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} on {path}: {r.text[:200]}")
    return r.json()


def _pick(d: dict, *names, default=0.0):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def main() -> None:
    if not KEY:
        print("COINALYZE_API_KEY not set in .env — aborting."); return
    print(f"[coinalyze] resolving perpetual symbols for {len(COINS)} coins...")
    markets = _get("/future-markets", {})
    sym_for: dict[str, str] = {}
    sample_market = markets[0] if isinstance(markets, list) and markets else {}
    cands: dict[str, list] = {}
    for m in (markets if isinstance(markets, list) else []):
        base = str(m.get("base_asset") or m.get("baseAsset") or "")
        is_perp = m.get("is_perpetual", m.get("isPerpetual", True))
        if not m.get("symbol") or base not in COINS or not is_perp:
            continue
        cands.setdefault(base, []).append(m)
    for base, ms in cands.items():
        # prefer USDT-quote, then USDC, then Binance (.A) — most-liquid mainline perp
        best = max(ms, key=lambda m: (
            (str(m.get("quote_asset") or "").upper() == "USDT"),
            (str(m.get("quote_asset") or "").upper() == "USDC"),
            str(m.get("symbol", "")).endswith(".A")))
        sym_for[base] = best["symbol"]
    print(f"[coinalyze] market keys seen: {sorted(sample_market.keys())}")
    print(f"[coinalyze] resolved {len(sym_for)}/{len(COINS)}: "
          f"{ {k: sym_for[k] for k in list(sym_for)[:5]} } ...")
    missing = [c for c in COINS if c not in sym_for]
    if missing:
        print(f"[coinalyze] no perp symbol for: {missing}")

    now = int(time.time())
    frm = now - DAYS * 86400
    rows_out = []
    schema_printed = False
    syms = list(sym_for.items())
    for i in range(0, len(syms), 5):
        batch = syms[i:i + 5]
        symstr = ",".join(s for _, s in batch)
        try:
            data = _get("/liquidation-history", {
                "symbols": symstr, "interval": INTERVAL,
                "from": frm, "to": now, "convert_to_usd": "true"})
        except Exception as e:
            print(f"[coinalyze] batch {symstr} failed: {e}"); continue
        sym_to_coin = {s: c for c, s in batch}
        for series in (data if isinstance(data, list) else []):
            sym = series.get("symbol")
            coin = sym_to_coin.get(sym, str(sym).split("USDT")[0])
            hist = series.get("history") or series.get("data") or []
            if hist and not schema_printed:
                print(f"[coinalyze] history point keys: {sorted(hist[0].keys())}")
                schema_printed = True
            for pt in hist:
                t = int(_pick(pt, "t", "time", "timestamp"))
                hour = t // 3600 * 3600
                longu = float(_pick(pt, "l", "long", "long_usd", "longLiquidationUsd"))
                shortu = float(_pick(pt, "s", "short", "short_usd", "shortLiquidationUsd"))
                rows_out.append({"hour": hour, "symbol": coin,
                                 "long_usd": round(longu, 2), "short_usd": round(shortu, 2),
                                 "count": 0})
        time.sleep(1.6)  # stay under 40/min

    if not rows_out:
        print("[coinalyze] no liquidation rows returned — nothing written."); return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows_out:
            f.write(json.dumps(r) + "\n")
    by_coin: dict[str, int] = {}
    for r in rows_out:
        by_coin[r["symbol"]] = by_coin.get(r["symbol"], 0) + 1
    span_h = (max(r["hour"] for r in rows_out) - min(r["hour"] for r in rows_out)) / 3600
    print(f"[coinalyze] wrote {len(rows_out)} rows -> {OUT.name} "
          f"({len(by_coin)} coins, ~{span_h:.0f}h span)")
    print(f"[coinalyze] per-coin hours: { {k: by_coin[k] for k in sorted(by_coin)} }")


if __name__ == "__main__":
    main()
