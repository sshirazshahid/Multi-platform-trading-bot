"""Honest PAPER futures goal-progress reporting.

The owner's 63-67% daily win-rate request is a target, never a promised or
manufactured result. This reporter counts deduplicated, fully closed PAPER
futures outcomes with entry provenance, uses whole-trade after-cost PnL
(runner plus partial realization), and requires both a mature sample and
positive economics before declaring the target met.

Read-only inputs: ``data/warehouse.sqlite``, ``data/heartbeat.json``, and the
F1 carry state. Outputs are ``data/goal_progress.json`` plus an optional daily
journal entry. It cannot place orders or grant promotion.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOAL_LINE = (
    "Target: mature PAPER futures WR 63-67% with positive after-cost "
    "expectancy and profit factor > 1.0 (not guaranteed)"
)
RESOLVED_FLOOR = 30
TARGET_WR_LOW = 0.63
TARGET_WR_HIGH = 0.67

_DIRECTIONAL_FAMILIES = (
    "algo",
    "algo_det",
    "claude",
    "claude_portfolio",
)
_NON_BOT_FAMILIES = ("manual", "reconcile", "reconciled_exchange")


def atomic_write_json(path: Path, payload, *, indent: int = 2) -> None:
    """Write a complete JSON document and atomically replace the prior one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _wilson_interval(wins: int, n: int, z: float = 1.959963984540054) -> tuple:
    """Two-sided Wilson score interval for a Bernoulli win rate."""

    if n <= 0:
        return (None, None)
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denom
    return (max(0.0, centre - radius), min(1.0, centre + radius))


def _target_status(n: int, wr: float | None, net: float, expectancy: float,
                   profit_factor: float | None, gross_loss: float) -> str:
    if n < RESOLVED_FLOOR:
        return "INSUFFICIENT_SAMPLE"
    profitable = (
        net > 0
        and expectancy > 0
        and (gross_loss == 0 or (profit_factor is not None and profit_factor > 1.0))
    )
    if not profitable:
        return "NEGATIVE_AFTER_COST_ECONOMICS"
    if wr is None or wr < TARGET_WR_LOW:
        return "WIN_RATE_BELOW_TARGET"
    if wr > TARGET_WR_HIGH:
        return "WIN_RATE_ABOVE_TARGET"
    return "TARGET_MET"


def performance_summary(
    conn: sqlite3.Connection,
    *,
    lane: str,
    since_epoch: float,
    until_epoch: float | None = None,
    strategy_families: tuple[str, ...] | None = None,
    window_column: str = "ts_exit",
) -> dict:
    """Aggregate mature, deduplicated PAPER futures outcomes.

    ``realized_pnl`` is already net of modeled execution costs; partial PnL is
    added exactly once to form the whole-trade outcome. Reconciled/manual rows
    and rows without a decision ID are excluded because they are not attributable
    bot entries. A trade ID is counted once even if a damaged database exposes a
    duplicate through a future join.
    """

    out = {
        "lane": lane,
        "available": False,
        "since_epoch": float(since_epoch),
        "until_epoch": float(until_epoch) if until_epoch is not None else None,
        "mature_sample_floor": RESOLVED_FLOOR,
        "target_wr_low": TARGET_WR_LOW,
        "target_wr_high": TARGET_WR_HIGH,
    }
    if window_column not in {"ts_entry", "ts_exit"}:
        raise ValueError("window_column must be ts_entry or ts_exit")
    where = [
        "status='CLOSED'",
        "mode='PAPER'",
        "market_type='futures'",
        "ts_exit IS NOT NULL",
        "realized_pnl IS NOT NULL",
        "decision_id IS NOT NULL",
        f"{window_column} >= ?",
        "COALESCE(strategy_family, '') NOT IN (?,?,?)",
    ]
    params: list = [float(since_epoch), *_NON_BOT_FAMILIES]
    if until_epoch is not None:
        where.append(f"{window_column} < ?")
        params.append(float(until_epoch))
    if strategy_families:
        marks = ",".join("?" for _ in strategy_families)
        where.append(f"COALESCE(strategy_family, '') IN ({marks})")
        params.extend(strategy_families)
    sql = (
        "SELECT id, realized_pnl + COALESCE(partial_realized_pnl, 0) AS net_pnl "
        "FROM trades WHERE " + " AND ".join(where) + " ORDER BY ts_exit, id"
    )
    try:
        unique: dict[int, float] = {}
        for row in conn.execute(sql, tuple(params)):
            unique[int(row["id"])] = float(row["net_pnl"])
        pnls = list(unique.values())
        n = len(pnls)
        wins = sum(value > 0 for value in pnls)
        losses = sum(value < 0 for value in pnls)
        ties = n - wins - losses
        net = sum(pnls)
        gross_profit = sum(value for value in pnls if value > 0)
        gross_loss = abs(sum(value for value in pnls if value < 0))
        wr = wins / n if n else None
        expectancy = net / n if n else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        ci_low, ci_high = _wilson_interval(wins, n)
        out.update(
            {
                "available": True,
                "closed_outcomes": n,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "win_rate": round(wr, 6) if wr is not None else None,
                "win_rate_ci95": [
                    round(ci_low, 6) if ci_low is not None else None,
                    round(ci_high, 6) if ci_high is not None else None,
                ],
                "net_after_cost_pnl": round(net, 6),
                "expectancy_per_outcome": round(expectancy, 6),
                "gross_profit": round(gross_profit, 6),
                "gross_loss": round(gross_loss, 6),
                "profit_factor": round(profit_factor, 6)
                if profit_factor is not None
                else None,
                "no_losses": bool(n and gross_loss == 0),
                "sample_mature": n >= RESOLVED_FLOOR,
                "target_status": _target_status(
                    n, wr, net, expectancy, profit_factor, gross_loss
                ),
            }
        )
    except sqlite3.Error as exc:
        out["note"] = f"trades unreadable ({exc})"
    return out


def probe_summary(conn: sqlite3.Connection) -> dict:
    """Listing-short shadow probe: proposals and actually resolved outcomes."""

    out: dict = {"lane": "listing_short_probe", "available": False}
    try:
        rows = conn.execute(
            "SELECT decision, COUNT(*) AS n FROM shadow_listing_probe GROUP BY decision"
        ).fetchall()
        out["proposals_by_decision"] = {row["decision"]: row["n"] for row in rows}
        res = conn.execute(
            "SELECT COUNT(DISTINCT p.proposal_id) AS n, "
            "SUM(CASE WHEN o.net_pnl > 0 THEN 1 ELSE 0 END) AS wins "
            "FROM shadow_listing_probe p JOIN shadow_outcomes o "
            "ON o.proposal_id=p.proposal_id"
        ).fetchone()
        n, wins = int(res["n"] or 0), int(res["wins"] or 0)
        out.update(
            {
                "available": True,
                "resolved": n,
                "wins": wins,
                "forward_wr": round(wins / n, 6) if n else None,
                "resolved_floor": RESOLVED_FLOOR,
                "floor_progress": f"{n}/{RESOLVED_FLOOR}",
            }
        )
    except sqlite3.Error as exc:
        out["note"] = f"no probe data yet ({exc})"
    return out


def excursion_telemetry_summary(
    conn: sqlite3.Connection, *, since_epoch: float
) -> dict:
    """Report whether post-deploy PAPER trades carry real MFE/MAE telemetry.

    Legacy excursions cannot be reconstructed honestly from exit rows, so the
    runtime cohort is reported separately from lifetime coverage. A single
    populated close verifies the write path; 30 closes are required before
    excursion-conditioned exit research is considered mature.
    """

    out: dict = {
        "lane": "paper_futures_excursion_telemetry",
        "available": False,
        "since_epoch": float(since_epoch),
        "wiring_verification_floor": 1,
        "analysis_sample_floor": RESOLVED_FLOOR,
        "required_coverage": 0.95,
    }
    base_where = (
        "status='CLOSED' AND mode='PAPER' AND market_type='futures' "
        "AND ts_exit IS NOT NULL AND realized_pnl IS NOT NULL "
        "AND decision_id IS NOT NULL "
        "AND COALESCE(strategy_family, '') NOT IN (?,?,?)"
    )
    sql = (
        "SELECT COUNT(DISTINCT id) AS eligible, "
        "COUNT(DISTINCT CASE WHEN mfe IS NOT NULL AND mae IS NOT NULL THEN id END) "
        "AS captured FROM trades WHERE "
    )
    try:
        legacy = conn.execute(sql + base_where, _NON_BOT_FAMILIES).fetchone()
        current = conn.execute(
            sql + base_where + " AND ts_entry >= ?",
            (*_NON_BOT_FAMILIES, float(since_epoch)),
        ).fetchone()
        legacy_n = int(legacy["eligible"] or 0)
        legacy_captured = int(legacy["captured"] or 0)
        current_n = int(current["eligible"] or 0)
        current_captured = int(current["captured"] or 0)
        coverage = current_captured / current_n if current_n else None
        if current_n == 0:
            status = "AWAITING_POST_FIX_CLOSED_TRADES"
        elif current_captured == 0:
            status = "CAPTURE_MISSING"
        elif coverage < out["required_coverage"]:
            status = "INCOMPLETE_CAPTURE"
        elif current_captured < RESOLVED_FLOOR:
            status = "WIRING_VERIFIED_SAMPLE_IMMATURE"
        else:
            status = "COVERAGE_OK_FOR_RESEARCH"
        out.update({
            "available": True,
            "legacy_closed_trades": legacy_n,
            "legacy_captured_trades": legacy_captured,
            "legacy_coverage": round(legacy_captured / legacy_n, 6)
            if legacy_n else None,
            "post_fix_closed_trades": current_n,
            "post_fix_captured_trades": current_captured,
            "post_fix_coverage": round(coverage, 6)
            if coverage is not None else None,
            "runtime_wiring_verified": current_captured >= 1,
            "analysis_sample_mature": current_captured >= RESOLVED_FLOOR,
            "status": status,
            "note": (
                "Legacy null excursions are not backfilled; only organic "
                "post-fix monitor samples count."
            ),
        })
    except sqlite3.Error as exc:
        out["note"] = f"excursion telemetry unreadable ({exc})"
    return out


def carry_summary(positions_path: Path) -> dict:
    """F1 carry forward cycles, kept separate from directional trade rows."""

    out: dict = {"lane": "f1_funding_carry", "available": False}
    try:
        state = json.loads(positions_path.read_text(encoding="utf-8"))
        cycles = state.get("cycles") or []
        wins = sum(
            1 for cycle in cycles
            if float(cycle.get("net_pnl", cycle.get("pnl", 0)) or 0) > 0
        )
        net = sum(
            float(cycle.get("net_pnl", cycle.get("pnl", 0)) or 0)
            for cycle in cycles
        )
        out.update(
            {
                "available": True,
                "cycles": len(cycles),
                "wins": wins,
                "forward_wr": round(wins / len(cycles), 6) if cycles else None,
                "net_after_cost_pnl": round(net, 6),
                "open_positions": len(state.get("positions") or {}),
                "sample_mature": len(cycles) >= RESOLVED_FLOOR,
            }
        )
    except (OSError, ValueError, TypeError) as exc:
        out["note"] = f"carry state unreadable ({exc})"
    return out


def _boot_epoch(root: Path) -> float | None:
    """Legacy fallback: newest startup marker in today's local-time bot log."""

    log = root / "logs" / f"bot_{datetime.now():%Y-%m-%d}.log"
    best = None
    try:
        with log.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "Signal source:" not in line:
                    continue
                try:
                    best = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    continue
    except OSError:
        return None
    return best


def _runtime_context(root: Path) -> dict:
    """Resolve the active profile and stable cohort start from the heartbeat."""

    context = {
        "profile": "UNKNOWN",
        "operating_mode": "UNKNOWN",
        "dry_run": None,
        "entry_policy": "UNKNOWN",
        "cohort_started_at": None,
        "cohort_start_source": "unavailable",
    }
    try:
        heartbeat = json.loads((root / "data" / "heartbeat.json").read_text(
            encoding="utf-8"
        ))
        context.update(
            {
                "profile": heartbeat.get("paper_trading_profile") or "STANDARD",
                "operating_mode": heartbeat.get("operating_mode") or "UNKNOWN",
                "dry_run": heartbeat.get("dry_run"),
                "entry_policy": heartbeat.get("entry_policy") or "UNKNOWN",
            }
        )
        raw_start = heartbeat.get("paper_profile_started_at")
        if raw_start not in (None, ""):
            context["cohort_started_at"] = float(raw_start)
            context["cohort_start_source"] = "heartbeat_profile_start"
            return context
        stamp = datetime.fromisoformat(
            str(heartbeat["timestamp"]).replace("Z", "+00:00")
        ).timestamp()
        context["cohort_started_at"] = stamp - float(heartbeat["uptime_seconds"])
        context["cohort_start_source"] = "heartbeat_uptime"
        return context
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    boot = _boot_epoch(root)
    context["cohort_started_at"] = boot if boot is not None else time.time() - 6 * 3600
    context["cohort_start_source"] = "boot_log" if boot is not None else "fallback_6h"
    return context


def directional_summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    lane = performance_summary(
        conn,
        lane="directional_paper_30d",
        since_epoch=time.time() - days * 86400,
        strategy_families=_DIRECTIONAL_FAMILIES,
    )
    lane["closed_trades"] = lane.get("closed_outcomes", 0)
    lane["wr"] = (
        round(lane["win_rate"], 4) if lane.get("win_rate") is not None else None
    )
    lane["net_pnl"] = lane.get("net_after_cost_pnl", 0.0)
    return lane


def current_boot_summary(conn: sqlite3.Connection, since_epoch=None,
                         root: Path = ROOT) -> dict:
    """Compatibility name for the active directional profile cohort."""

    context = _runtime_context(root)
    since = float(since_epoch) if since_epoch is not None else float(
        context["cohort_started_at"]
    )
    lane = performance_summary(
        conn,
        lane="current_profile_directional",
        since_epoch=since,
        strategy_families=_DIRECTIONAL_FAMILIES,
        window_column="ts_entry",
    )
    lane.update(context)
    if since_epoch is not None:
        lane["cohort_started_at"] = since
        lane["cohort_start_source"] = "explicit"
    # Backward-compatible keys for read-only tools while they migrate.
    lane["closed_trades"] = lane.get("closed_outcomes", 0)
    lane["wr"] = (
        round(lane["win_rate"], 4) if lane.get("win_rate") is not None else None
    )
    lane["net_pnl"] = lane.get("net_after_cost_pnl", 0.0)
    lane["since_epoch"] = since
    lane["since_source"] = lane["cohort_start_source"]
    return lane


def _utc_day_bounds(now: datetime, offset_days: int = 0) -> tuple[float, float]:
    start = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=offset_days)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def build_report(root: Path = ROOT, now: datetime | None = None) -> dict:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report: dict = {
        "goal": GOAL_LINE,
        "generated_utc": now_utc.isoformat(timespec="seconds"),
        "target": {
            "win_rate_band": [TARGET_WR_LOW, TARGET_WR_HIGH],
            "minimum_mature_outcomes": RESOLVED_FLOOR,
            "profitability_requirements": {
                "net_after_cost_pnl_gt": 0.0,
                "expectancy_per_outcome_gt": 0.0,
                "profit_factor_gt": 1.0,
            },
            "guaranteed": False,
        },
        "runtime": _runtime_context(root),
        "lanes": [],
    }
    db = root / "data" / "warehouse.sqlite"
    if db.exists():
        conn = _ro_conn(db)
        try:
            report["lanes"].append(probe_summary(conn))
            today_start, today_end = _utc_day_bounds(now_utc)
            prior_start, prior_end = _utc_day_bounds(now_utc, -1)
            report["lanes"].extend(
                [
                    performance_summary(
                        conn, lane="paper_futures_current_utc_day",
                        since_epoch=today_start, until_epoch=today_end,
                    ),
                    performance_summary(
                        conn, lane="paper_futures_previous_utc_day",
                        since_epoch=prior_start, until_epoch=prior_end,
                    ),
                    performance_summary(
                        conn, lane="paper_futures_rolling_24h",
                        since_epoch=now_utc.timestamp() - 86400,
                    ),
                    performance_summary(
                        conn, lane="paper_futures_7d",
                        since_epoch=now_utc.timestamp() - 7 * 86400,
                    ),
                    performance_summary(
                        conn, lane="paper_futures_30d",
                        since_epoch=now_utc.timestamp() - 30 * 86400,
                    ),
                    directional_summary(conn),
                    current_boot_summary(conn, root=root),
                    excursion_telemetry_summary(
                        conn,
                        since_epoch=float(report["runtime"]["cohort_started_at"]),
                    ),
                ]
            )
        finally:
            conn.close()
    report["lanes"].append(carry_summary(root / "data" / "carry_positions.json"))
    return report


def _fmt_wr(wr) -> str:
    return f"{wr * 100:.1f}%" if wr is not None else "n/a"


def render_journal(report: dict, funnel_path: Path | None = None) -> str:
    lines = [f"\n## PAPER goal progress — {report['generated_utc']}", GOAL_LINE, ""]
    for lane in report["lanes"]:
        name = lane["lane"]
        if not lane.get("available"):
            lines.append(f"- **{name}**: no data ({lane.get('note', 'not started')})")
        elif "closed_outcomes" in lane:
            pf = lane.get("profit_factor")
            pf_text = "∞" if lane.get("no_losses") else (f"{pf:.3f}" if pf is not None else "n/a")
            lines.append(
                f"- **{name}**: n={lane['closed_outcomes']}, WR "
                f"{_fmt_wr(lane.get('win_rate'))}, net "
                f"{lane['net_after_cost_pnl']:+.4f} USDT, PF {pf_text}, "
                f"expectancy {lane['expectancy_per_outcome']:+.6f}; "
                f"{lane['target_status']}"
            )
        elif name == "listing_short_probe":
            lines.append(
                f"- **listing-short shadow**: resolved {lane['floor_progress']}, "
                f"WR {_fmt_wr(lane['forward_wr'])}; {lane.get('proposals_by_decision', {})}"
            )
        elif name == "paper_futures_excursion_telemetry":
            coverage = lane.get("post_fix_coverage")
            coverage_text = f"{coverage:.1%}" if coverage is not None else "n/a"
            lines.append(
                "- **PAPER MFE/MAE telemetry**: "
                f"{lane['post_fix_captured_trades']}/"
                f"{lane['post_fix_closed_trades']} post-fix closes captured "
                f"({coverage_text}); {lane['status']}"
            )
        elif name == "f1_funding_carry":
            lines.append(
                f"- **F1 carry PAPER**: cycles={lane['cycles']}, WR "
                f"{_fmt_wr(lane['forward_wr'])}, net "
                f"{lane['net_after_cost_pnl']:+.4f} USDT"
            )
    lines.append(
        "- A daily WR is not considered evidence below 30 closed outcomes; "
        "WR alone never overrides negative after-cost economics."
    )
    path = funnel_path or ROOT / "data" / "promotion_funnel.json"
    try:
        funnel = json.loads(path.read_text(encoding="utf-8"))
        lines.extend(["", "### Promotion funnel"])
        for lane in funnel.get("lanes", []):
            # F7 (2026-07-20 audit): eta_days=None used to render "eta=Noned".
            eta = lane["eta_days"]
            eta_txt = f"{eta}d" if eta is not None else "n/a"
            lines.append(
                f"- {lane['lane']}: {lane['state']} "
                f"({lane['floor_progress']}, eta={eta_txt})"
            )
    except (OSError, json.JSONDecodeError, KeyError):
        lines.append("- promotion funnel: no current data")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report honest PAPER futures goal progress")
    parser.add_argument(
        "--json-only", action="store_true",
        help="refresh data/goal_progress.json without appending the daily journal",
    )
    args = parser.parse_args(argv)
    report = build_report()
    data_out = ROOT / "data" / "goal_progress.json"
    atomic_write_json(data_out, report, indent=2)
    if not args.json_only:
        journal_dir = ROOT / "journal"
        journal_dir.mkdir(exist_ok=True)
        day_file = journal_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.md"
        with day_file.open("a", encoding="utf-8") as handle:
            handle.write(render_journal(report))
        print(f"goal progress -> {data_out} + {day_file}")
    else:
        print(f"goal progress -> {data_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
