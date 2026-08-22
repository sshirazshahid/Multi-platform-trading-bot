#!/usr/bin/env python3
"""Read-only shadow probe summary from warehouse (ground truth vs TV)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.sqlite"
OUT = ROOT / "data" / "shadow_probe_summary_latest.json"

AGENTS = (
    "zfade_4h_cfg365",
    "rsi2_4h_cfg226",
    "pullback_ma20_4h",
)


def main() -> int:
    if not DB.exists():
        print(json.dumps({"error": "warehouse missing"}))
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'shadow%'"
    )]
    summary: dict = {"tables": tables, "agents": {}}
    if "shadow_outcomes" in tables and "shadow_decisions" in tables:
        for agent_pat in ("zfade", "rsi2", "pullback"):
            row = con.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN o.net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       AVG(o.net_pnl) AS avg_net_pnl,
                       AVG(o.r_multiple) AS avg_r,
                       SUM(o.net_pnl) AS total_net_pnl
                FROM shadow_outcomes o
                JOIN shadow_decisions d ON d.proposal_id = o.proposal_id
                WHERE LOWER(d.agent_id) LIKE ?
                   OR LOWER(d.model_version) LIKE ?
                """,
                (f"%{agent_pat}%", f"%{agent_pat}%"),
            ).fetchone()
            summary["agents"][agent_pat] = dict(row) if row else {}
    con.close()
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
