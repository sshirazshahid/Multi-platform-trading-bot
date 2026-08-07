"""Post-geometry-fix verdict agent (SP1).

Answers one question daily, in plain language: is the current exit geometry
working, not working, or still too early to call?

Spec: docs/superpowers/specs/2026-08-03-postfix-verdict-agent-design.md

It REPORTS; it never acts. No imports from order/execution paths, no writes
outside data/postfix_verdict.json, warehouse opened read-only.

Two correctness rules carried over from design review:

1. Cohort membership is by ENTRY time. ``performance_summary`` defaults to
   ts_exit windowing, which would leak pre-fix trades that merely closed
   after the epoch into the cohort. Every call here passes
   ``window_column="ts_entry"`` and a test pins it.

2. Geometry epochs are never pooled. Each live change to exit geometry opens
   a NEW cohort; earlier ones are closed and reported separately. Pooling a
   pre-change and post-change cohort would measure neither.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.report_goal_progress import (  # noqa: E402
    _ro_conn,
    atomic_write_json,
    performance_summary,
)

# ── Frozen constants ────────────────────────────────────────────────────────
# Each entry opens a geometry cohort. Provenance is the supervisor respawn
# that put the change into the running process -- code commits do not take
# effect until a restart, so the restart is the epoch, not the commit.
GEOMETRY_EPOCHS: tuple[tuple[str, float, str], ...] = (
    (
        "v1",
        1785673514.0,
        "AccBand compression disabled; tier geometry restored "
        "(supervisor respawn 2026-08-02 17:25:14 +05:00)",
    ),
    (
        "v2",
        1785778977.0,
        "tier-geometry time-exit hold active: STALE/AGE defer while planned "
        "R:R >= 1 inside 72h (supervisor respawn 2026-08-03 22:42:57 +05:00)",
    ),
    (
        "v3",
        1786022386.0,
        "blueprint ops restructure + entry-gate tightening (supervisor respawn "
        "2026-08-06 18:19:46 +05:00). The Aug-4 process had loaded its modules "
        "before 19 live files were edited on Aug 5, so this respawn is the "
        "first to actually run them: max_hold_force_flat hard close, "
        "soft-stale entry latch, orphan/warehouse reconcile — and, from the "
        "boot banner, MCP_ENTRY_MIN_SCORE 50 -> 66 with SL cooldown ENABLED. "
        "Entry frequency should drop materially; do not compare v3 trade "
        "counts to v2 without accounting for that.",
    ),
)
CURRENT_LABEL, FIX_EPOCH, CURRENT_NOTE = GEOMETRY_EPOCHS[-1]

RESOLVED_TARGET = 30          # n at which a verdict is allowed at all
TIMEOUT_FLAG_MIN_N = 10       # do not flag timeout interference below this
TIMEOUT_FLAG_SHARE = 0.40     # STALE share above which the flag fires
LANE = "paper_futures_postfix"

VERDICTS = (
    "COHORT_CONTAMINATED",
    "TOO_EARLY",
    "WORKING",
    "NOT_WORKING",
    "MIXED",
)

# Exit reasons that mean "time ran out", not "a barrier was hit".
# MAX_HOLD_FORCE_FLAT added 2026-08-07: the blueprint restructure's hard
# time-close (live in v3) — omitting it would blind the timeout-interference
# flag to the newest time-exit path. Note it often fires on positions whose
# stop has ratcheted past breakeven (tier hold intentionally releases them),
# so its trades can be PROFITABLE time exits; share, not sign, is the signal.
TIME_EXIT_REASONS = {"STALE", "SCALP_STALE_CLOSE", "AGE_LIMIT", "AGE_LOSS",
                     "SCALP_TIME_WALL", "MAX_HOLD_FORCE_FLAT"}


def exit_path_breakdown(
    conn: sqlite3.Connection,
    *,
    since_epoch: float,
    until_epoch: float | None = None,
) -> dict[str, int]:
    """Count resolved cohort trades by exit reason.

    Mirrors performance_summary's provenance filters exactly so the two
    numbers can never disagree about which trades are in the cohort.
    """
    where = [
        "status='CLOSED'",
        "mode='PAPER'",
        "market_type='futures'",
        "ts_exit IS NOT NULL",
        "realized_pnl IS NOT NULL",
        "decision_id IS NOT NULL",
        "ts_entry >= ?",
        "COALESCE(strategy_family, '') NOT IN ('manual','reconcile','reconciled_exchange')",
    ]
    params: list = [float(since_epoch)]
    if until_epoch is not None:
        where.append("ts_entry < ?")
        params.append(float(until_epoch))
    sql = (
        "SELECT id, COALESCE(exit_reason, 'unknown') AS reason "
        "FROM trades WHERE " + " AND ".join(where)
    )
    seen: dict[int, str] = {}
    try:
        for row in conn.execute(sql, tuple(params)):
            seen[int(row["id"])] = str(row["reason"])
    except sqlite3.Error:
        return {}
    out: dict[str, int] = {}
    for reason in seen.values():
        out[reason] = out.get(reason, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def contamination_check(
    conn: sqlite3.Connection,
    *,
    since_epoch: float,
    until_epoch: float | None = None,
) -> dict:
    """Guard against a regression re-enabling compressed geometry mid-cohort.

    HONEST LIMITATION: the trades table persists ``entry_px`` and
    ``entry_stop_px`` but NO planned target price, so planned R:R
    (reward/risk) is not computable from the warehouse. The design assumed it
    was. Rather than assert a clean cohort the data cannot support, this
    returns status UNAVAILABLE and the verdict layer treats the guard as
    INERT -- reported loudly, never silently passed.

    Persisting a planned-target column on entry would make this guard live.
    """
    where = [
        "status='CLOSED'",
        "mode='PAPER'",
        "market_type='futures'",
        "decision_id IS NOT NULL",
        "ts_entry >= ?",
        "COALESCE(strategy_family, '') NOT IN ('manual','reconcile','reconciled_exchange')",
    ]
    params: list = [float(since_epoch)]
    if until_epoch is not None:
        where.append("ts_entry < ?")
        params.append(float(until_epoch))
    sql = (
        "SELECT id, entry_px, entry_stop_px FROM trades WHERE "
        + " AND ".join(where)
    )
    rows_with_stop = 0
    rows_total = 0
    try:
        for row in conn.execute(sql, tuple(params)):
            rows_total += 1
            entry = row["entry_px"]
            stop = row["entry_stop_px"]
            if entry and stop and float(entry) > 0 and float(stop) > 0:
                rows_with_stop += 1
    except sqlite3.Error as exc:
        return {
            "status": "UNAVAILABLE",
            "reason": f"trades unreadable ({exc})",
            "sub_1_rr_trade_ids": [],
        }
    return {
        "status": "UNAVAILABLE",
        "reason": (
            "planned R:R needs a target price; trades persists entry_px and "
            "entry_stop_px only. Guard is INERT until a planned-target column "
            "exists -- it is not asserting the cohort is clean."
        ),
        "rows_examined": rows_total,
        "rows_with_planned_stop": rows_with_stop,
        "sub_1_rr_trade_ids": [],
    }


def classify(summary: dict, contamination: dict, exit_paths: dict) -> dict:
    """Frozen verdict rules, evaluated in order; first match wins."""
    n = int(summary.get("closed_outcomes") or 0)
    net = float(summary.get("net_after_cost_pnl") or 0.0)
    w = summary.get("decisive_win_rate")
    gross_profit = float(summary.get("gross_profit") or 0.0)
    gross_loss = float(summary.get("gross_loss") or 0.0)
    wins = int(summary.get("wins") or 0)
    losses = int(summary.get("losses") or 0)

    payoff = (
        (gross_profit / wins) / (gross_loss / losses)
        if wins and losses and gross_loss > 0
        else None
    )
    required_payoff = (
        (1.0 - float(w)) / float(w) if w not in (None, 0) else None
    )

    time_exits = sum(
        count
        for reason, count in exit_paths.items()
        if reason.upper() in TIME_EXIT_REASONS
    )
    total_exits = sum(exit_paths.values())
    stale_share = (time_exits / total_exits) if total_exits else None
    timeout_flag = bool(
        n >= TIMEOUT_FLAG_MIN_N
        and stale_share is not None
        and stale_share > TIMEOUT_FLAG_SHARE
    )

    if contamination.get("sub_1_rr_trade_ids"):
        verdict = "COHORT_CONTAMINATED"
    elif n < RESOLVED_TARGET:
        verdict = "TOO_EARLY"
    elif (
        net > 0
        and payoff is not None
        and required_payoff is not None
        and payoff >= required_payoff
    ):
        verdict = "WORKING"
    elif net <= 0:
        verdict = "NOT_WORKING"
    else:
        verdict = "MIXED"

    return {
        "verdict": verdict,
        "resolved_n": n,
        "target_n": RESOLVED_TARGET,
        "net_after_cost_pnl": round(net, 6),
        "decisive_win_rate": w,
        "win_rate_ci95": summary.get("win_rate_ci95"),
        "realized_payoff": round(payoff, 6) if payoff is not None else None,
        "required_payoff": round(required_payoff, 6)
        if required_payoff is not None
        else None,
        "time_exit_share": round(stale_share, 6) if stale_share is not None else None,
        "timeout_interference": timeout_flag,
        "contamination_guard": contamination.get("status"),
    }


def build_report(conn: sqlite3.Connection, *, now: float | None = None) -> dict:
    """Report the CURRENT cohort, plus every closed prior cohort separately."""
    now_ts = float(now if now is not None else time.time())
    cohorts = []
    for idx, (label, epoch, note) in enumerate(GEOMETRY_EPOCHS):
        # A cohort ends where the next epoch begins; the last one is open.
        until = GEOMETRY_EPOCHS[idx + 1][1] if idx + 1 < len(GEOMETRY_EPOCHS) else None
        summary = performance_summary(
            conn,
            lane=f"{LANE}_{label}",
            since_epoch=epoch,
            until_epoch=until,
            window_column="ts_entry",   # entry-time cohort; never ts_exit
        )
        exit_paths = exit_path_breakdown(conn, since_epoch=epoch, until_epoch=until)
        contamination = contamination_check(
            conn, since_epoch=epoch, until_epoch=until
        )
        cohorts.append(
            {
                "epoch_label": label,
                "epoch": epoch,
                "epoch_note": note,
                "closed": until is not None,
                "until_epoch": until,
                "exit_paths": exit_paths,
                "contamination": contamination,
                **classify(summary, contamination, exit_paths),
            }
        )
    current = cohorts[-1]
    return {
        "schema_version": 1,
        "generated_at": now_ts,
        "current_epoch_label": CURRENT_LABEL,
        "current_epoch": FIX_EPOCH,
        "current_epoch_note": CURRENT_NOTE,
        "verdict": current["verdict"],
        "headline": _headline(current),
        "cohorts": cohorts,
    }


def _headline(cohort: dict) -> str:
    v = cohort["verdict"]
    n, target = cohort["resolved_n"], cohort["target_n"]
    if v == "TOO_EARLY":
        base = (
            f"Too early to call: {n} of {target} resolved trades. "
            "No verdict is meaningful yet."
        )
    elif v == "WORKING":
        base = (
            f"Working: {n} resolved, net {cohort['net_after_cost_pnl']:+.2f} "
            f"with payoff {cohort['realized_payoff']} clearing the "
            f"{cohort['required_payoff']} its win rate requires."
        )
    elif v == "NOT_WORKING":
        base = (
            f"Not working: {n} resolved, net "
            f"{cohort['net_after_cost_pnl']:+.2f} after cost."
        )
    elif v == "MIXED":
        base = (
            f"Mixed: {n} resolved and net positive, but realized payoff "
            f"{cohort['realized_payoff']} is below the "
            f"{cohort['required_payoff']} its win rate requires."
        )
    else:
        base = f"Cohort contaminated: {v}."
    if cohort.get("timeout_interference"):
        base += (
            f" Timeout interference: {cohort['time_exit_share']:.0%} of exits "
            "are time-based, so targets may not be getting reached."
        )
    if cohort.get("contamination_guard") == "UNAVAILABLE":
        base += (
            " (Geometry-contamination guard is INERT: no planned target "
            "price stored.)"
        )
    return base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse.sqlite"))
    ap.add_argument("--out", default=str(ROOT / "data" / "postfix_verdict.json"))
    ap.add_argument("--json-only", action="store_true",
                    help="print JSON only; do not write the artifact")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(json.dumps({"available": False, "note": f"no warehouse at {db}"}))
        return 0
    conn = _ro_conn(db)
    try:
        conn.execute("PRAGMA query_only=ON")
        report = build_report(conn)
    finally:
        conn.close()

    if args.json_only:
        print(json.dumps(report, indent=2))
        return 0

    out = Path(args.out)
    previous = None
    if out.exists():
        try:
            previous = json.loads(out.read_text(encoding="utf-8")).get("verdict")
        except (OSError, json.JSONDecodeError):
            previous = None
    report["verdict_changed"] = previous is not None and previous != report["verdict"]
    report["previous_verdict"] = previous
    atomic_write_json(out, report)
    print(report["headline"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
