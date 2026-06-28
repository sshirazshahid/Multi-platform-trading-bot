"""Generate the strategy promotion-readiness report.

The report is intentionally fail-closed. It only emits PROMOTE_ELIGIBLE when
the recent after-cost paper/live series clears sample-size, expectancy, profit
factor, fold-stability, and drawdown-ratio floors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.strategy_readiness import (  # noqa: E402
    StrategyReadinessThresholds,
    evaluate_warehouse,
    write_readiness_reports,
)


def build_thresholds(args: argparse.Namespace) -> StrategyReadinessThresholds:
    return StrategyReadinessThresholds(
        min_trades=args.min_trades,
        min_profit_factor=args.min_profit_factor,
        min_avg_pnl=args.min_avg_pnl,
        min_win_rate=args.min_win_rate,
        min_positive_folds=args.min_positive_folds,
        min_fold_trades=args.min_fold_trades,
        max_drawdown_to_gross_profit=args.max_drawdown_to_gross_profit,
        folds=args.folds,
        lookback_days=args.lookback_days,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/warehouse.sqlite")
    parser.add_argument("--mode", default="PAPER")
    parser.add_argument("--strategy-family", default="")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-profit-factor", type=float, default=1.20)
    parser.add_argument("--min-avg-pnl", type=float, default=0.0)
    parser.add_argument("--min-win-rate", type=float, default=0.35)
    parser.add_argument("--min-positive-folds", type=int, default=2)
    parser.add_argument("--min-fold-trades", type=int, default=10)
    parser.add_argument("--max-drawdown-to-gross-profit", type=float, default=1.50)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--fail-if-not-ready", action="store_true")
    args = parser.parse_args()

    thresholds = build_thresholds(args)
    report = evaluate_warehouse(
        ROOT / args.db,
        thresholds=thresholds,
        mode=args.mode or None,
        strategy_family=args.strategy_family or None,
    )
    json_path, md_path, latest_path = write_readiness_reports(
        report,
        out_dir=ROOT / args.output_dir,
    )
    print(f"verdict={report['verdict']}")
    print(f"json={json_path}")
    print(f"md={md_path}")
    print(f"latest={latest_path}")
    for reason in report.get("reasons") or []:
        print(f"FAIL {reason}")
    return 1 if args.fail_if_not_ready and not report["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
