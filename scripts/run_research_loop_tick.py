#!/usr/bin/env python3
"""Fail-closed continuous research loop tick (evolve-PAPER W2).

Daily ops health for the evidence pipeline — NEVER installs strategies,
NEVER enables CONTROLLED_LIVE, NEVER mutates decision code.

Checks:
  S0 — operating mode / kill switch / soft-stale / funnel freshness
  S1 — goal_progress present
  S2 — promotion funnel age (warn if stale)
  S4 — paper stack structural audit
  S5 — exit-geometry diagnostic (EV / R asymmetry)
  S6 — mature-cohort gate for TP-exit replay (queue #6)
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
    max_flow_band_env,
    paper_research_snapshot,
    read_json,
    safe_env_flags,
)
from core.paper_exit_geometry import run_diagnostic  # noqa: E402
from core.paper_stack_audit import run_audit  # noqa: E402
from scripts.check_mature_cohort_gates import check_mature_cohort  # noqa: E402

OUT_PATH = Path("data/research_loop_tick_latest.json")
FUNNEL_STALE_HOURS = 36.0
WHALE_STATUS_PATH = Path("data/whale_events_status.json")
WHALE_STALE_HOURS = 48.0


def run_tick(*, root: Path, dry_run: bool) -> dict:
    env = safe_env_flags(root)
    mode = str(env.get("OPERATING_MODE") or "").upper()
    profile = str(env.get("PAPER_TRADING_PROFILE") or "")
    kill = (root / KILL_SWITCH_PATH).exists()
    soft = (root / SOFT_STALE_LATCH_PATH).exists()
    goals_meta = file_meta(root / GOAL_PATH)
    funnel_meta = file_meta(root / FUNNEL_PATH)
    research = paper_research_snapshot(root)
    mf_env = max_flow_band_env(root)

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

    if mf_env.get("max_flow_misconfig_acc_off"):
        warnings.append(
            "MAX_FLOW_BAND but ACCURACY_TARGET_MODE off — wide TP rarely hits; "
            "stop_loss dominates (set ACCURACY_TARGET_MODE=true + restart supervisor)"
        )
    elif mf_env.get("accuracy_band_enabled") and not mf_env.get("band_regime_filter_enabled"):
        warnings.append(
            "AccBand on but BAND_REGIME_FILTER_ENABLED=false — toxic regimes "
            "(ADX>30, low BTC vol) still admitted; consider true + restart"
        )

    funnel_age_h = None
    if funnel_meta.get("age_seconds") is not None:
        funnel_age_h = float(funnel_meta["age_seconds"]) / 3600.0
        if funnel_age_h > FUNNEL_STALE_HOURS:
            warnings.append(
                f"promotion_funnel.json age {funnel_age_h:.1f}h > {FUNNEL_STALE_HOURS}h — run scripts/promotion_funnel.py"
            )
    if not goals_meta.get("exists"):
        warnings.append("goal_progress.json missing — run scripts/report_goal_progress.py")

    whale_meta = file_meta(root / WHALE_STATUS_PATH)
    whale_age_h = None
    if whale_meta.get("age_seconds") is not None:
        whale_age_h = float(whale_meta["age_seconds"]) / 3600.0
        if whale_age_h > WHALE_STALE_HOURS:
            warnings.append(
                f"whale_events_status.json age {whale_age_h:.1f}h > {WHALE_STALE_HOURS}h — "
                "run scripts/harvest_whale_events.py --once (log-only)"
            )
    elif not whale_meta.get("exists"):
        warnings.append(
            "whale_events_status.json missing — optional: python scripts/harvest_whale_events.py --once"
        )

    stack_audit = run_audit(root)
    if not stack_audit.get("ok"):
        warnings.append(
            "paper_stack_audit FAILED: "
            + ", ".join(stack_audit.get("missing") or ["unknown"])
        )

    exit_geo = run_diagnostic(root)
    if exit_geo.get("warn_low_win_loss_ratio"):
        wl = exit_geo.get("win_loss_ratio")
        warnings.append(
            f"exit geometry: win/loss ratio {wl:.2f} < 1.0 at n={exit_geo.get('n_closed')} — "
            "prioritize R asymmetry fix (no TP/SL change without prereg)"
        )
    elif exit_geo.get("binding_note"):
        warnings.append(exit_geo["binding_note"])

    mature = check_mature_cohort(root)
    for action in mature.get("next_actions") or []:
        if "TP-exit-geometry" in action or "Accrue PAPER" in action:
            warnings.append(action)

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
            "accuracy_band_enabled": mf_env.get("accuracy_band_enabled"),
            "band_regime_filter_enabled": mf_env.get("band_regime_filter_enabled"),
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
        "s3_whale_accrual": {
            "exists": bool(whale_meta.get("exists")),
            "age_seconds": whale_meta.get("age_seconds"),
            "age_hours": whale_age_h,
            "log_only": True,
            "live_trade_authorized": False,
        },
        "s4_stack_audit": stack_audit,
        "s5_exit_geometry": exit_geo,
        "s6_mature_cohort": mature,
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
            "Optional whale accrual: python scripts/harvest_whale_events.py --once",
            "Stack audit: python scripts/audit_paper_stack.py",
            "Exit geometry: python scripts/diagnose_paper_exit_geometry.py",
            "Mature cohort: python scripts/check_mature_cohort_gates.py --write",
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
