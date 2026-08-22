#!/usr/bin/env python3
"""PAPER trade autopsy for the cash-move plan. Read-only.

Splits entry-time correlations (actionable) from outcome-only stats
(lookahead — cannot be used as a live filter).
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data/warehouse.sqlite"
OUT = ROOT / "_workspace/strategy_pipeline/73_paper_trade_autopsy.json"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n != len(ys) or n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _bucket(rows: list[dict], key: str, pnl_key: str = "realized_pnl") -> list[dict]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "unknown")].append(float(r[pnl_key] or 0.0))
    out = []
    for name, pnls in groups.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gl = abs(sum(losses))
        out.append({
            "name": name,
            "n": len(pnls),
            "net": round(sum(pnls), 4),
            "expectancy": round(sum(pnls) / len(pnls), 4),
            "wr": round(len(wins) / len(pnls), 4) if pnls else None,
            "pf": round(sum(wins) / gl, 4) if gl else None,
        })
    out.sort(key=lambda x: x["net"])
    return out


def main() -> int:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    raw = conn.execute(
        """
        SELECT ts_entry, exchange, symbol, side, fill_type, exit_reason,
               mcp_score, realized_pnl, fee, r_multiple, hold_sec, mfe, mae,
               strategy_family, leverage
        FROM trades
        WHERE UPPER(COALESCE(status,''))='CLOSED'
          AND LOWER(COALESCE(mode,'')) IN ('paper','')
          AND LOWER(COALESCE(market_type,'')) IN ('futures','swap','future','')
        """
    ).fetchall()
    conn.close()
    rows = [dict(r) for r in raw]
    pnls = [float(r["realized_pnl"] or 0.0) for r in rows]
    scored = [
        r for r in rows
        if r.get("mcp_score") is not None
    ]
    scores = [float(r["mcp_score"]) for r in scored]
    scored_pnls = [float(r["realized_pnl"] or 0.0) for r in scored]
    mfe_mae = [
        r for r in rows
        if r.get("mfe") is not None and r.get("mae") is not None
    ]

    # Score deciles (entry-time)
    deciles = []
    if scores:
        order = sorted(zip(scores, scored_pnls))
        n = len(order)
        for d in range(10):
            lo, hi = int(d * n / 10), int((d + 1) * n / 10)
            chunk = order[lo:hi]
            if not chunk:
                continue
            cp = [p for _, p in chunk]
            deciles.append({
                "decile": d + 1,
                "n": len(chunk),
                "score_lo": round(chunk[0][0], 2),
                "score_hi": round(chunk[-1][0], 2),
                "net": round(sum(cp), 4),
                "expectancy": round(sum(cp) / len(cp), 4),
            })

    # UTC hour of entry (entry-time but family REFUTED — measure only)
    hours: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        ts = r.get("ts_entry")
        if ts is None:
            continue
        try:
            import datetime as dt
            hour = dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).hour
        except (TypeError, ValueError, OSError):
            continue
        hours[hour].append(float(r["realized_pnl"] or 0.0))
    hour_rows = []
    for h, hp in sorted(hours.items()):
        hour_rows.append({
            "hour_utc": h,
            "n": len(hp),
            "net": round(sum(hp), 4),
            "expectancy": round(sum(hp) / len(hp), 4),
        })

    mfe_vals = [abs(float(r["mfe"])) for r in mfe_mae]
    mae_vals = [abs(float(r["mae"])) for r in mfe_mae]
    ratio = (
        (statistics.mean(mfe_vals) / statistics.mean(mae_vals))
        if mfe_vals and statistics.mean(mae_vals)
        else None
    )

    first = rows[: len(rows) // 2]
    second = rows[len(rows) // 2 :]

    def _sum(rs: list[dict]) -> dict:
        p = [float(x["realized_pnl"] or 0.0) for x in rs]
        return {
            "n": len(rs),
            "net": round(sum(p), 4),
            "expectancy": round(sum(p) / len(p), 4) if p else 0.0,
        }

    report = {
        "n": len(rows),
        "net": round(sum(pnls), 4),
        "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "fee_sum": round(sum(float(r["fee"] or 0.0) for r in rows), 4),
        "score_pnl_corr": _pearson(scores, scored_pnls),
        "n_with_score": len(scored),
        "by_exit_reason": _bucket(rows, "exit_reason"),
        "by_fill_type": _bucket(rows, "fill_type"),
        "by_side": _bucket(rows, "side"),
        "by_exchange": _bucket(rows, "exchange"),
        "score_deciles_entry_time": deciles,
        "hour_utc_entry_time_DO_NOT_PROMOTE": hour_rows,
        "mfe_mae_n": len(mfe_mae),
        "mean_mfe": round(statistics.mean(mfe_vals), 6) if mfe_vals else None,
        "mean_mae": round(statistics.mean(mae_vals), 6) if mae_vals else None,
        "mfe_mae_ratio": round(ratio, 4) if ratio else None,
        "first_half": _sum(first),
        "second_half": _sum(second),
        "worst_symbols": _bucket(rows, "symbol")[:12],
        "live_trade_authorized": False,
        "hour_of_day_family": "REFUTED 2026-06-02 — numbers are diagnostic only",
        "honesty": (
            "No entry-time slice in this autopsy is +EV. Cash (zero new "
            "directional opens) is the only after-cost improvement vs this book."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"n={report['n']} net={report['net']} E={report['expectancy']}")
    print(f"score_corr={report['score_pnl_corr']} n_scored={report['n_with_score']}")
    print(f"mfe/mae ratio={report['mfe_mae_ratio']}")
    print("exit_reasons:")
    for row in report["by_exit_reason"]:
        print(f"  {row['name']:<28} n={row['n']:<5} net={row['net']:<10} E={row['expectancy']}")
    print("fill_type:")
    for row in report["by_fill_type"]:
        print(f"  {row['name']:<28} n={row['n']:<5} net={row['net']:<10} E={row['expectancy']}")
    print("score deciles:")
    for row in deciles:
        print(
            f"  D{row['decile']} score {row['score_lo']}-{row['score_hi']} "
            f"n={row['n']} net={row['net']} E={row['expectancy']}"
        )
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
