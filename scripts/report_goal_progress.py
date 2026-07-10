"""Daily goal-progress reporter — "65-70% accuracy in FUTURES" (owner goal, 2026-07-09).

Read-only: opens the warehouse in sqlite mode=ro and reads data/carry_positions.json.
Measures the forward evidence the promotion gate needs, per lane:

  * listing-short shadow probe (rev3 CONFIRMED_GO): proposals, resolved count,
    forward win rate so far, progress toward the >=30-resolved evaluation floor
  * F1 funding carry (validated family): forward cycles + wins
  * directional (mcp) lane, 30d closed PAPER trades: the honest contrast line

Appends a journal section to journal/YYYY-MM-DD.md (CLAUDE.md output rule) and
writes data/goal_progress.json for tooling. Never raises on missing tables —
each lane degrades to "no data yet" so the report survives schema evolution.

Scheduled daily via Windows Task Scheduler (TradingBot-GoalProgress).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOAL_LINE = "Goal: autonomous FUTURES accuracy 65-70% (owner directive 2026-07-09)"
RESOLVED_FLOOR = 30  # auditor's evaluation-time minimum before gates are computable


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def probe_summary(conn: sqlite3.Connection) -> dict:
    """Listing-short probe: proposals by decision + resolved forward WR."""
    out: dict = {"lane": "listing_short_probe", "available": False}
    try:
        rows = conn.execute(
            "SELECT decision, COUNT(*) AS n FROM shadow_listing_probe GROUP BY decision"
        ).fetchall()
        out["proposals_by_decision"] = {r["decision"]: r["n"] for r in rows}
        res = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN o.net_pnl > 0 THEN 1 ELSE 0 END) AS wins"
            "  FROM shadow_outcomes o"
            "  JOIN shadow_listing_probe p ON p.proposal_id = o.proposal_id"
        ).fetchone()
        n, wins = int(res["n"] or 0), int(res["wins"] or 0)
        out.update(
            {
                "available": True,
                "resolved": n,
                "wins": wins,
                "forward_wr": round(wins / n, 4) if n else None,
                "resolved_floor": RESOLVED_FLOOR,
                "floor_progress": f"{n}/{RESOLVED_FLOOR}",
            }
        )
    except sqlite3.Error as e:
        out["note"] = f"no probe data yet ({e})"
    return out


def carry_summary(positions_path: Path) -> dict:
    """F1 carry: forward cycles + wins from data/carry_positions.json."""
    out: dict = {"lane": "f1_funding_carry", "available": False}
    try:
        state = json.loads(positions_path.read_text(encoding="utf-8"))
        cycles = state.get("cycles") or []
        wins = sum(1 for c in cycles if float(c.get("net_pnl", c.get("pnl", 0)) or 0) > 0)
        out.update(
            {
                "available": True,
                "cycles": len(cycles),
                "wins": wins,
                "forward_wr": round(wins / len(cycles), 4) if cycles else None,
                "open_positions": len(state.get("positions") or {}),
            }
        )
    except (OSError, ValueError) as e:
        out["note"] = f"carry state unreadable ({e})"
    return out


def directional_summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    """Directional (mcp) lane, closed PAPER trades over the window — the contrast."""
    out: dict = {"lane": "directional_30d", "available": False}
    try:
        since = time.time() - days * 86400
        res = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,"
            "       SUM(realized_pnl) AS pnl"
            "  FROM trades WHERE ts_exit IS NOT NULL AND ts_exit >= ?",
            (since,),
        ).fetchone()
        n, wins = int(res["n"] or 0), int(res["wins"] or 0)
        out.update(
            {
                "available": True,
                "closed_trades": n,
                "wins": wins,
                "wr": round(wins / n, 4) if n else None,
                "net_pnl": round(float(res["pnl"] or 0.0), 4),
            }
        )
    except sqlite3.Error as e:
        out["note"] = f"trades unreadable ({e})"
    return out


def _boot_epoch(root: Path) -> float | None:
    """Epoch of the live process boot: the NEWEST "Signal source:" line in
    today's bot log (bot_engine.run() logs it once per engine start). Loguru
    writes local time; returns None when the log/line is missing."""
    log = root / "logs" / f"bot_{datetime.now():%Y-%m-%d}.log"
    best = None
    try:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "Signal source:" not in line:
                    continue
                try:
                    best = datetime.strptime(
                        line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    continue
    except OSError:
        return None
    return best


def current_boot_summary(conn: sqlite3.Connection, since_epoch=None,
                         root: Path = ROOT) -> dict:
    """Current-boot cohort (2026-07-10): WR/n/pnl of trades ENTERED since the
    live process boot — the honest "what is the bot doing NOW" lane. The 30d
    aggregate blends dead-regime cohorts (pre-band geometry, old cutoffs) and
    reads ~44% while the current cohort can sit in the target band."""
    if since_epoch is not None:
        since, src = float(since_epoch), "explicit"
    else:
        boot = _boot_epoch(root)
        since = boot if boot is not None else time.time() - 6 * 3600
        src = "boot_log" if boot is not None else "fallback_6h"
    out: dict = {
        "lane": "current_boot", "available": False,
        "since_epoch": since, "since_source": src,
    }
    try:
        res = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,"
            "       SUM(realized_pnl) AS pnl"
            "  FROM trades WHERE ts_exit IS NOT NULL AND ts_entry >= ?",
            (since,),
        ).fetchone()
        n, wins = int(res["n"] or 0), int(res["wins"] or 0)
        out.update(
            {
                "available": True,
                "closed_trades": n,
                "wins": wins,
                "wr": round(wins / n, 4) if n else None,
                "net_pnl": round(float(res["pnl"] or 0.0), 4),
            }
        )
    except sqlite3.Error as e:
        out["note"] = f"trades unreadable ({e})"
    return out


def build_report(root: Path = ROOT) -> dict:
    report: dict = {
        "goal": GOAL_LINE,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lanes": [],
    }
    db = root / "data" / "warehouse.sqlite"
    if db.exists():
        conn = _ro_conn(db)
        try:
            report["lanes"].append(probe_summary(conn))
            report["lanes"].append(directional_summary(conn))
            report["lanes"].append(current_boot_summary(conn, root=root))
        finally:
            conn.close()
    report["lanes"].append(carry_summary(root / "data" / "carry_positions.json"))
    return report


def _fmt_wr(wr) -> str:
    return f"{wr * 100:.1f}%" if wr is not None else "n/a (no resolutions yet)"


def render_journal(report: dict) -> str:
    lines = [f"\n## Goal progress — {report['generated_utc']}", GOAL_LINE, ""]
    for lane in report["lanes"]:
        name = lane["lane"]
        if not lane.get("available"):
            lines.append(f"- **{name}**: no data yet ({lane.get('note', 'lane not started')})")
        elif name == "listing_short_probe":
            lines.append(
                f"- **listing-short probe** (shadow, log-only): resolved "
                f"{lane['floor_progress']} toward gate floor, forward WR "
                f"{_fmt_wr(lane['forward_wr'])}; proposals {lane.get('proposals_by_decision', {})}"
            )
        elif name == "f1_funding_carry":
            lines.append(
                f"- **F1 carry** (validated, PAPER): {lane['cycles']} forward cycles, "
                f"WR {_fmt_wr(lane['forward_wr'])}, open positions {lane['open_positions']}"
            )
        elif name == "current_boot":
            lines.append(
                f"- **current-boot cohort** ({lane['since_source']}): "
                f"{lane['closed_trades']} closed, WR {_fmt_wr(lane.get('wr'))}, "
                f"net {lane['net_pnl']} USDT — trades entered since the live "
                f"process boot; the 30d line blends dead-regime cohorts"
            )
        else:
            lines.append(
                f"- **directional lane, 30d** (contrast): {lane['closed_trades']} closed, "
                f"WR {_fmt_wr(lane.get('wr'))}, net {lane['net_pnl']} USDT"
            )
    lines.append(
        "- Promotion bar: >=30 resolved probes then DSR>=0.10, PBO<=0.5, "
        "OOS-WR>=0.55, AUC>=0.60 + owner sign-off. Probe stays unlevered."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    report = build_report()
    data_out = ROOT / "data" / "goal_progress.json"
    data_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    journal_dir = ROOT / "journal"
    journal_dir.mkdir(exist_ok=True)
    day_file = journal_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.md"
    with day_file.open("a", encoding="utf-8") as fh:
        fh.write(render_journal(report))
    print(f"goal progress -> {data_out} + {day_file}")


if __name__ == "__main__":
    main()
