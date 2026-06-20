"""scripts/run_signal_postmortem.py — weekly crypto signal postmortem.

Reads `data/warehouse.sqlite` for trades closed in the last N days and emits
a structured digest under `reports/signal_postmortem_<date>.json` with:

  - per-symbol P&L breakdown
  - per-side (long/short) win rate and net P&L
  - per-exit-reason aggregate (which exits make money, which lose)
  - per-mcp-score bucket WR / P&L (so we can spot non-monotonic confidence)
  - per-hour-UTC WR / P&L (feeds back into hour gate refresh)
  - a flat "actionables" list — symbols / hours / sides flagged for review

Equivalent to the equity-side `skills/signal-postmortem` output, but
keyed on crypto warehouse fields (mcp_score, exit_reason) rather than
the equity ticker / regime fields the skill expects.

Run weekly from `scripts/retrain_weekly.ps1`.

Usage:
    python scripts/run_signal_postmortem.py [--lookback-days 7]
                                            [--out reports/]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WAREHOUSE_PATH = Path("data/warehouse.sqlite")

# Flag thresholds — a row that meets ANY of these produces an actionable.
ACTIONABLE_MIN_TRADES = 5
ACTIONABLE_WR_FLOOR_PCT = 35.0
ACTIONABLE_PNL_FLOOR = -3.0


def _load_trades(db: Path, lookback_days: int) -> list[dict]:
    if not db.exists():
        return []
    cutoff = time.time() - lookback_days * 86400
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "SELECT id,ts_entry,ts_exit,exchange,symbol,market_type,side,"
            "strategy_family,realized_pnl,r_multiple,hold_sec,exit_reason,"
            "mcp_score "
            "FROM trades WHERE status='CLOSED' AND ts_exit >= ? "
            "ORDER BY ts_exit DESC",
            (cutoff,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _bucket_score(s) -> str:
    if s is None:
        return "no_score"
    if s < 60:
        return "<60"
    if s < 70:
        return "60-70"
    if s < 80:
        return "70-80"
    return "80+"


def _summarise(trades: list[dict], key_fn) -> list[dict]:
    by_key: dict = {}
    for t in trades:
        k = key_fn(t)
        if k is None:
            continue
        b = by_key.setdefault(k, {"key": k, "n": 0, "wins": 0, "pnl": 0.0})
        b["n"] += 1
        b["pnl"] += float(t.get("realized_pnl") or 0)
        if (t.get("realized_pnl") or 0) > 0:
            b["wins"] += 1
    out = []
    for b in by_key.values():
        b["wr_pct"] = round(100.0 * b["wins"] / max(1, b["n"]), 2)
        b["pnl"] = round(b["pnl"], 4)
        out.append(b)
    out.sort(key=lambda r: r["pnl"])
    return out


def build_report(trades: list[dict], lookback_days: int) -> dict:
    by_symbol = _summarise(trades, lambda t: t.get("symbol"))
    by_side = _summarise(trades, lambda t: (t.get("side") or "").lower() or None)
    by_exit = _summarise(trades, lambda t: t.get("exit_reason"))
    by_score = _summarise(trades, lambda t: _bucket_score(t.get("mcp_score")))
    by_hour = _summarise(
        trades,
        lambda t: int(datetime.fromtimestamp(t.get("ts_entry") or 0, tz=timezone.utc).hour)
        if t.get("ts_entry")
        else None,
    )

    actionables: list[dict] = []
    for grp_name, rows, key in (
        ("symbol", by_symbol, "key"),
        ("hour_utc", by_hour, "key"),
        ("side", by_side, "key"),
        ("exit_reason", by_exit, "key"),
    ):
        for r in rows:
            if (
                r["n"] >= ACTIONABLE_MIN_TRADES
                and r["wr_pct"] < ACTIONABLE_WR_FLOOR_PCT
                and r["pnl"] < ACTIONABLE_PNL_FLOOR
            ):
                actionables.append(
                    {
                        "group": grp_name,
                        "key": r[key],
                        "n": r["n"],
                        "wr_pct": r["wr_pct"],
                        "pnl": r["pnl"],
                    }
                )
    actionables.sort(key=lambda r: r["pnl"])

    total_pnl = round(sum(float(t.get("realized_pnl") or 0) for t in trades), 4)
    wins = sum(1 for t in trades if (t.get("realized_pnl") or 0) > 0)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "trade_count": len(trades),
        "wr_pct": round(100.0 * wins / max(1, len(trades)), 2),
        "net_pnl": total_pnl,
        "by_symbol": by_symbol,
        "by_side": by_side,
        "by_exit_reason": by_exit,
        "by_score_bucket": by_score,
        "by_hour_utc": by_hour,
        "actionables": actionables,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/warehouse.sqlite", type=Path)
    ap.add_argument("--out", default="reports", type=Path)
    ap.add_argument("--lookback-days", default=7, type=int)
    args = ap.parse_args()

    trades = _load_trades(args.db, args.lookback_days)
    report = build_report(trades, args.lookback_days)

    args.out.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = args.out / f"signal_postmortem_{date}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"[signal-postmortem] wrote {out_path} "
        f"(trades={report['trade_count']}, wr={report['wr_pct']}%, "
        f"net=${report['net_pnl']:.2f}, "
        f"actionables={len(report['actionables'])})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
