"""Shadow vs live dashboard panel — returns text/dict for rendering.

Decoupled from dashboard.py so it can be unit-tested without the
dashboard's Rich live-loop. dashboard.py imports `render_shadow_panel`
and feeds the dict into its layout.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def shadow_panel_data(db_path: Path, window_hours: int = 24) -> dict:
    """Returns dict suitable for Rich rendering or JSON dashboard."""
    cutoff = int(time.time() - window_hours * 3600)
    con = _connect(db_path)
    s_rows = list(con.execute(
        "SELECT sim_pnl, projected_pnl, projected_fee, agent_id "
        "FROM shadow_decisions WHERE ts >= ? AND decision='ALLOW'",
        (cutoff,),
    ).fetchall())
    l_rows = list(con.execute(
        "SELECT realized_pnl, fee FROM trades WHERE ts_exit >= ? AND status='CLOSED'",
        (cutoff,),
    ).fetchall())
    veto_rows = list(con.execute(
        "SELECT veto_reason FROM shadow_decisions WHERE ts >= ? AND decision='VETO'",
        (cutoff,),
    ).fetchall())
    con.close()

    def _agg(rows, pnl_key):
        if not rows:
            return {"n": 0, "wr": 0.0, "sum": 0.0, "best": 0.0, "worst": 0.0}
        pnls = [(r[pnl_key] or 0.0) for r in rows]
        wins = sum(1 for x in pnls if x > 0)
        return {
            "n": len(pnls), "wr": wins / len(pnls), "sum": sum(pnls),
            "best": max(pnls), "worst": min(pnls),
        }

    by_agent: dict[str, list] = {}
    for r in s_rows:
        agent = r["agent_id"] or "unknown"
        by_agent.setdefault(agent, []).append(r["sim_pnl"] or 0.0)

    return {
        "window_hours": window_hours,
        "shadow_current": _agg(s_rows, "sim_pnl"),
        "shadow_alt": _agg(s_rows, "projected_pnl"),
        "live": _agg(l_rows, "realized_pnl"),
        "vetoes": len(veto_rows),
        "per_agent": {
            a: {"n": len(pnls),
                "sum": round(sum(pnls), 2),
                "wr": round(sum(1 for x in pnls if x > 0) / len(pnls), 3) if pnls else 0.0}
            for a, pnls in by_agent.items()
        },
    }


def render_shadow_panel(db_path: Path, window_hours: int = 24) -> str:
    """Plain-text panel for terminal display. Returns multi-line string."""
    data = shadow_panel_data(db_path, window_hours)
    s = data["shadow_current"]
    a = data["shadow_alt"]
    l = data["live"]
    lines: list[str] = []
    lines.append(f"=== Shadow vs Live (last {window_hours}h) ===")
    lines.append(f"  LIVE:        n={l['n']:>4} sum=${l['sum']:>+7.2f} WR={l['wr']:>5.1%}")
    lines.append(f"  SHADOW(cur): n={s['n']:>4} sum=${s['sum']:>+7.2f} WR={s['wr']:>5.1%}")
    lines.append(f"  SHADOW($200):n={a['n']:>4} sum=${a['sum']:>+7.2f}")
    lines.append(f"  Vetoes:      {data['vetoes']}")
    if data["per_agent"]:
        lines.append("  Per-agent:")
        for agent, st in sorted(data["per_agent"].items()):
            lines.append(f"    {agent:25s} n={st['n']:>3} sum=${st['sum']:>+6.2f} WR={st['wr']:.1%}")
    return "\n".join(lines)
