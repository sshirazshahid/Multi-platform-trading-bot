#!/usr/bin/env python3
"""CLI for structural PAPER stack audit (Binance/Bybit/Bitget + evidence rails).

Exit 0 if ok, 1 if missing hard checks. Never places orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paper_stack_audit import run_audit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PAPER trading stack structure")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args()
    report = run_audit(ROOT)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"ok={report['ok']} venues={report['venues']}")
        if report["missing"]:
            print("missing:")
            for m in report["missing"]:
                print(f"  - {m}")
        for w in report.get("warnings") or []:
            print(f"warn: {w}")
        print(report["honesty"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
