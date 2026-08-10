#!/usr/bin/env python3
"""Fail-closed continuous research loop tick (evolve-PAPER W2).

Daily ops health for the evidence pipeline — NEVER installs strategies,
NEVER enables CONTROLLED_LIVE, NEVER mutates decision code.

Checks:
  S0 — operating mode / kill switch / soft-stale / funnel freshness
  S1 — goal_progress present
  S2 — promotion funnel age (warn if stale)
Writes a JSON summary under data/research_loop_tick_latest.json (unless --dry-run).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mission_control.state import (  # noqa: E402
    FUNNEL_PATH,
    GOAL_PATH,
    KILL_SWITCH_PATH,
    SOFT_STALE_LATCH_PATH,
    file_meta,
    paper_research_snapshot,
    read_json,
    safe_env_flags,
)

OUT_PATH = Path("data/research_loop_tick_latest.json")
FUNNEL_STALE_HOURS = 36.0


def run_tick(*, root: Path, dry_run: bool) -> dict:
    env = safe_env_flags(root)
    mode = str(env.get("OPERATING_MODE") or "").upper()
    profile = str(env.get("PAPER_TRADING_PROFILE") or "")
    kill = (root / KILL_SWITCH_PATH).exists()
    soft = (root / SOFT_STALE_LATCH_PATH).exists()
    goals_meta = file_meta(root / GOAL_PATH)
    funnel_meta = file_meta(root / FUNNEL_PATH)
    research = paper_research_snapshot(root)

    warnings: list[str] = []
    refusals: list[str] = [
        "refuse_auto_strategy_install",
        "refuse_controlled_live_enable",
        "refuse_scalp_tier_force_on",
    ]

    if mode == "CONTROLLED_LIVE":
        warnings.append("CONTROLLED_LIVE in .env — this tick will not enable or authorize live")
    if kill:
        warnings.append("KILL_SWITCH present — new entries paused")
    if soft:
        warnings.append("soft_stale_entry_latch present — NEW opens blocked")

    funnel_age_h = None
    if funnel_meta.get("age_seconds") is not None:
        funnel_age_h = float(funnel_meta["age_seconds"]) / 3600.0
        if funnel_age_h > FUNNEL_STALE_HOURS:
            warnings.append(
                f"promotion_funnel.json age {funnel_age_h:.1f}h > {FUNNEL_STALE_HOURS}h — run scripts/promotion_funnel.py"
            )
    if not goals_meta.get("exists"):
        warnings.append("goal_progress.json missing — run scripts/report_goal_progress.py")

    day = research.get("paper_futures_utc_day") or {}
    payload = {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dry_run": dry_run,
        "s0": {
            "operating_mode_env": mode,
            "paper_profile_env": profile,
            "kill_switch": kill,
            "soft_stale_entry_block": soft,
            "econ_gate_mode": research.get("econ_gate_mode"),
            "scalp_tier_enabled": research.get("scalp_tier_enabled"),
        },
        "s1_goals": {
            "exists": bool(goals_meta.get("exists")),
            "age_seconds": goals_meta.get("age_seconds"),
            "paper_day_target_status": day.get("target_status"),
            "paper_day_expectancy": day.get("expectancy_per_outcome"),
            "paper_day_wr": day.get("win_rate"),
        },
        "s2_funnel": {
            "exists": bool(funnel_meta.get("exists")),
            "age_seconds": funnel_meta.get("age_seconds"),
            "age_hours": funnel_age_h,
            "stale": bool(funnel_age_h is not None and funnel_age_h > FUNNEL_STALE_HOURS),
        },
        "probe_floor_count": len(research.get("probe_floors") or []),
        "warnings": warnings,
        "refusals": refusals,
        "honesty": (
            "Ops tick only. Does not install strategies, place orders, or enable CONTROLLED_LIVE. "
            "PAPER expectancy is measured research, not a profit guarantee."
        ),
        "next_human_actions": [
            "If funnel stale: python scripts/promotion_funnel.py",
            "If goals missing: python scripts/report_goal_progress.py",
            "Strategy work: strategy-evidence-pipeline only (prereg → screen → audit → shadow)",
        ],
    }

    if not dry_run:
        out = root / OUT_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Do not write latest JSON")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    payload = run_tick(root=args.root, dry_run=args.dry_run)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
