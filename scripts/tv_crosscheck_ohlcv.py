"""TV-vs-exchange OHLCV cross-validation (data-quality check, no trading claim).

Compares TradingView 1h closes (data/tv_cache, pulled by backfill_tv_cache.py
--set crosscheck) against the bot's exchange-sourced cache
(data/ohlcv_cache/{BASE}-USDT_1h.parquet) for the top majors: relative close
difference (bps), bars missing on either side within the overlap window, and
bar-boundary alignment. TradingView's BINANCE:XXXUSDT series and our ccxt
Binance spot pulls should be near-identical — material divergence flags a
data-quality problem in one of the pipelines.

Run: venv\\Scripts\\python.exe scripts\\tv_crosscheck_ohlcv.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TV = ROOT / "data" / "tv_cache"
OHLCV = ROOT / "data" / "ohlcv_cache"
REPORTS = ROOT / "reports"
BASES = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "LTC", "AVAX"]
OK_MEDIAN_BPS = 5.0  # majors should agree to within a few bps


def compare_frames(tv: pd.DataFrame, ex: pd.DataFrame) -> dict:
    """Diff two [ts, ..., close] frames over their common time window."""
    t = tv[["ts", "close"]].dropna().drop_duplicates("ts")
    e = ex[["ts", "close"]].dropna().drop_duplicates("ts")
    lo = max(t["ts"].min(), e["ts"].min())
    hi = min(t["ts"].max(), e["ts"].max())
    t_w = t[(t["ts"] >= lo) & (t["ts"] <= hi)]
    e_w = e[(e["ts"] >= lo) & (e["ts"] <= hi)]
    m = t_w.merge(e_w, on="ts", suffixes=("_tv", "_ex"))
    n = len(m)
    out = {
        "n_overlap": n,
        "missing_in_tv": len(e_w) - n,
        "missing_in_ex": len(t_w) - n,
        "median_rel_diff_bps": None,
        "p95_rel_diff_bps": None,
        "max_rel_diff_bps": None,
        "aligned": n > 0 and n >= 0.5 * max(len(t_w), len(e_w)),
    }
    if n:
        rel = (m["close_tv"] - m["close_ex"]).abs() / m["close_ex"] * 1e4
        out["median_rel_diff_bps"] = float(rel.median())
        out["p95_rel_diff_bps"] = float(rel.quantile(0.95))
        out["max_rel_diff_bps"] = float(rel.max())
    return out


def run() -> dict:
    rows = []
    for base in BASES:
        tv_p = TV / f"BINANCE_{base}USDT_1h.parquet"
        ex_p = OHLCV / f"{base}-USDT_1h.parquet"
        if not tv_p.exists() or not ex_p.exists():
            rows.append({"base": base, "verdict": "SKIP (missing cache)"})
            continue
        r = compare_frames(pd.read_parquet(tv_p), pd.read_parquet(ex_p))
        r["base"] = base
        med = r["median_rel_diff_bps"]
        r["verdict"] = (
            "OK" if r["aligned"] and med is not None and med <= OK_MEDIAN_BPS else "CHECK"
        )
        rows.append(r)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md = [
        f"# TradingView vs exchange OHLCV cross-check — {date}",
        "\nTV BINANCE:XXXUSDT 1h closes vs bot ohlcv_cache (ccxt Binance spot), "
        f"common window per symbol. OK = aligned and median |rel diff| <= {OK_MEDIAN_BPS} bps.\n",
        "| base | overlap | miss TV | miss EX | median bps | p95 bps | max bps | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "n_overlap" not in r:
            md.append(f"| {r['base']} | - | - | - | - | - | - | {r['verdict']} |")
            continue

        def _f(x):
            return "-" if x is None else round(x, 2)

        md.append(
            f"| {r['base']} | {r['n_overlap']} | {r['missing_in_tv']} | {r['missing_in_ex']} | "
            f"{_f(r['median_rel_diff_bps'])} | {_f(r['p95_rel_diff_bps'])} | "
            f"{_f(r['max_rel_diff_bps'])} | {r['verdict']} |"
        )
    REPORTS.mkdir(exist_ok=True)
    out_p = REPORTS / f"tv_crosscheck_{date}.md"
    out_p.write_text("\n".join(md), encoding="utf-8")
    ok = sum(1 for r in rows if r.get("verdict") == "OK")
    print(f"crosscheck: {ok}/{len(rows)} OK -> {out_p}")
    return {"rows": rows, "report": str(out_p)}


if __name__ == "__main__":
    run()
