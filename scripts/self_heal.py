"""Run bounded self-healing and machine-strategy adaptation.

This script is safe to schedule. It repairs runtime support processes, invokes
existing stale-model retraining, and writes a PAPER-only adaptive machine config
only when replay evidence passes conservative thresholds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.self_healing_supervisor import SelfHealingSupervisor, config_from_mapping  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--no-retrain", action="store_true")
    parser.add_argument("--no-adapt", action="store_true")
    parser.add_argument("--restart-main", action="store_true")
    parser.add_argument("--min-interval-sec", type=int, default=15 * 60)
    parser.add_argument("--replay-max-files", type=int, default=8)
    parser.add_argument("--replay-max-bars", type=int, default=1500)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-profit-factor", type=float, default=1.05)
    parser.add_argument("--min-positive-folds", type=int, default=2)
    args = parser.parse_args()

    cfg = config_from_mapping(
        {
            "dry_run": args.dry_run,
            "repair_enabled": not args.no_repair,
            "retrain_enabled": not args.no_retrain,
            "adapt_enabled": not args.no_adapt,
            "restart_main": args.restart_main,
            "min_interval_sec": args.min_interval_sec,
            "replay_max_files": args.replay_max_files,
            "replay_max_bars": args.replay_max_bars,
            "min_trades": args.min_trades,
            "min_profit_factor": args.min_profit_factor,
            "min_positive_folds": args.min_positive_folds,
        }
    )
    report = SelfHealingSupervisor(cfg).tick(force=args.force)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("verdict") != "ACTION_FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
