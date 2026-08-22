#!/usr/bin/env python3
"""Check mature-cohort gates for TP-exit-geometry replay (queue #6).

Read-only. Does not run replay or change TP/SL.
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

from core.paper_exit_geometry import run_diagnostic  # noqa: E402

MATURE_N = 30
INTERIM_WL_RATIO = 1.2
TARGET_WL_RATIO = 1.5
OUT_PATH = Path("data/mature_cohort_gates_latest.json")


def check_mature_cohort(root: Path) -> dict:
    goals_path = root / "data" / "goal_progress.json"
    paper_day: dict = {}
    if goals_path.exists():
        try:
            goals = json.loads(goals_path.read_text(encoding="utf-8"))
            for lane in goals.get("lanes") or []:
                if isinstance(lane, dict) and lane.get("lane") == "paper_futures_current_utc_day":
                    paper_day = lane
                    break
        except (OSError, json.JSONDecodeError):
            pass

    exit_geo = run_diagnostic(root, write_path=False)
    n = int(paper_day.get("closed_outcomes") or exit_geo.get("n_closed") or 0)
    sample_mature = bool(paper_day.get("sample_mature")) or n >= MATURE_N
    ev = paper_day.get("expectancy_per_outcome")
    wl = exit_geo.get("win_loss_ratio")

    tp_replay_unlocked = sample_mature
    ev_target_met = ev is not None and float(ev) > 0
    wl_interim = wl is not None and float(wl) >= INTERIM_WL_RATIO
    wl_target = wl is not None and float(wl) >= TARGET_WL_RATIO

    actions: list[str] = []
    if not sample_mature:
        actions.append(f"Accrue PAPER outcomes (n={n}/{MATURE_N}) before TP-exit-geometry prereg")
    else:
        actions.append(
            "Write NEW hashed prereg for TP-exit-geometry replay (queue #6) — documentation-only if negative-best"
        )
    if wl is not None and float(wl) < INTERIM_WL_RATIO:
        actions.append(
            f"Exit geometry bleed: win/loss ratio {wl:.2f} < {INTERIM_WL_RATIO} — prioritize R asymmetry fix"
        )
    if ev is not None and float(ev) <= 0:
        actions.append(f"PAPER EV still negative ({float(ev):.3f}R) — EV-first metric not met")

    return {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "paper_day_n": n,
        "sample_mature": sample_mature,
        "mature_floor": MATURE_N,
        "expectancy_r": ev,
        "ev_positive": ev_target_met,
        "win_loss_ratio": wl,
        "wl_interim_met": wl_interim,
        "wl_target_met": wl_target,
        "tp_exit_replay_unlocked": tp_replay_unlocked,
        "live_trade_authorized": False,
        "next_actions": actions,
        "honesty": "Gate check only. Does not auto-run replay or change live geometry.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    report = check_mature_cohort(args.root)
    if args.write:
        out = args.root / OUT_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
