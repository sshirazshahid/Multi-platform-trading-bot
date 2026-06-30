"""Daily shadow-vs-live compare report.

Reads warehouse.shadow_decisions and warehouse.trades over a configurable
window. Emits markdown to reports/shadow_compare_<date>.md.

Wired into bot_engine._daily_self_check via subprocess.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _stats(rows: list, pnl_key: str) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "sum": 0.0, "mean": 0.0, "wr": 0.0, "wins": 0, "losses": 0}
    pnls = [(r[pnl_key] or 0.0) for r in rows]
    wins = sum(1 for x in pnls if x > 0)
    return {
        "n": n,
        "sum": sum(pnls),
        "mean": sum(pnls) / n,
        "wr": wins / n,
        "wins": wins,
        "losses": n - wins,
        "best": max(pnls),
        "worst": min(pnls),
    }


def build_report(db_path: Path, window_hours: int = 24) -> str:
    con = _connect(db_path)
    cutoff = int(time.time() - window_hours * 3600)

    # Phase 0b: report forward-RESOLVED net PnL (after-cost, SL-first) from
    # shadow_outcomes joined to the decision rows — NOT the projected-at-TP
    # shadow_decisions.sim_pnl, which never feeds a promotion decision.
    shadow_rows = list(
        con.execute(
            "SELECT d.agent_id AS agent_id, o.net_pnl AS net_pnl "
            "FROM shadow_decisions d JOIN shadow_outcomes o "
            "ON o.proposal_id = d.proposal_id "
            "WHERE d.ts >= ? AND d.decision='ALLOW' "
            "AND o.label_status='RESOLVED'",
            (cutoff,),
        ).fetchall()
    )
    live_rows = list(
        con.execute(
            "SELECT * FROM trades WHERE ts_exit >= ? AND status='CLOSED'",
            (cutoff,),
        ).fetchall()
    )

    s_stats = _stats(shadow_rows, "net_pnl")
    l_stats = _stats(live_rows, "realized_pnl")

    by_agent: dict[str, list] = {}
    for r in shadow_rows:
        agent = r["agent_id"] or "unknown"
        by_agent.setdefault(agent, []).append(r)

    lines: list[str] = []
    lines.append(f"# Shadow vs Live — last {window_hours}h\n")
    lines.append(f"_generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}_\n")
    lines.append("\n## Live (current notional)\n")
    lines.append(
        f"- n={l_stats['n']} sum=${l_stats['sum']:.2f} mean=${l_stats['mean']:.3f} WR={l_stats['wr']:.1%}"
    )
    lines.append("\n## Shadow (RESOLVED net PnL, after-cost SL-first)\n")
    lines.append(
        f"- n={s_stats['n']} sum=${s_stats['sum']:.2f} mean=${s_stats['mean']:.3f} WR={s_stats['wr']:.1%}"
    )
    lines.append("\n## Per-agent shadow breakdown\n")
    if by_agent:
        for agent, rows in sorted(by_agent.items()):
            st = _stats(rows, "net_pnl")
            lines.append(f"- **{agent}**: n={st['n']} sum=${st['sum']:.2f} WR={st['wr']:.1%}")
    else:
        lines.append("- (no shadow decisions in window)")

    return "\n".join(lines)


def write_report(db_path: Path, out_dir: Path, window_hours: int = 24) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"shadow_compare_{time.strftime('%Y%m%d')}.md"
    report_path.write_text(build_report(db_path, window_hours), encoding="utf-8")
    return report_path


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/warehouse.sqlite")
    ap.add_argument("--out-dir", default="reports")
    ap.add_argument("--window-hours", type=int, default=24)
    ns = ap.parse_args(argv)
    p = write_report(Path(ns.db), Path(ns.out_dir), ns.window_hours)
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
