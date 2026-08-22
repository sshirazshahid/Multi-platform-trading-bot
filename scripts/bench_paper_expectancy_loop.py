#!/usr/bin/env python3
"""Replay PAPER expectancy variants. Read-only. Never places orders.

    python scripts/bench_paper_expectancy_loop.py
    python scripts/bench_paper_expectancy_loop.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paper_expectancy_bench import evaluate_variants, summarize  # noqa: E402

OUT_JSON = ROOT / "_workspace/strategy_pipeline/72_bench_opt_loop.json"
DB = ROOT / "data/warehouse.sqlite"


def load_closed_paper_futures(db: Path) -> list[dict]:
    if not db.exists():
        raise SystemExit(f"missing {db}")
    uri = f"file:{db.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT realized_pnl, mcp_score, fill_type, side, hold_sec, fee, slippage,
               status, mode, market_type, strategy_family
        FROM trades
        WHERE UPPER(COALESCE(status,'')) = 'CLOSED'
          AND LOWER(COALESCE(mode,'')) IN ('paper', '')
          AND LOWER(COALESCE(market_type,'')) IN ('futures', 'swap', 'future', '')
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="PAPER expectancy benchmark loop")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db", type=Path, default=DB)
    args = parser.parse_args()
    trades = load_closed_paper_futures(args.db)
    rows = evaluate_variants(trades)
    report = summarize(rows)
    report["n_loaded"] = len(trades)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"n_loaded={len(trades)}")
    print(
        f"{'variant':<18} {'n':>6} {'WR':>8} {'net':>12} {'gross':>12} "
        f"{'E[pnl]':>10} {'PF':>8} {'entry?':>6}"
    )
    for r in rows:
        wr = f"{r['win_rate']:.3f}" if r["win_rate"] is not None else "-"
        pf = r["profit_factor"]
        pf_s = f"{pf:.3f}" if isinstance(pf, float) and pf == pf and pf != float("inf") else str(pf)
        print(
            f"{r['id']:<18} {r['n_kept']:6d} {wr:>8} {r['net_pnl']:12.4f} "
            f"{r['gross_pnl']:12.4f} {r['expectancy']:10.4f} {pf_s:>8} "
            f"{'Y' if r['entry_time_only'] else 'N':>6}"
        )
    print(f"winner={report['winner_id']} delta_vs_baseline={report['delta_vs_baseline']}")
    print(report["honesty"])
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
