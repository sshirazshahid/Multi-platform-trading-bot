#!/usr/bin/env python3
"""Diagnose PAPER exit geometry / R asymmetry from closed positions.

Read-only. Writes data/paper_exit_geometry_latest.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paper_exit_geometry import run_diagnostic  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--no-write", action="store_true", help="Print only; skip JSON artifact")
    args = ap.parse_args()
    report = run_diagnostic(
        args.root,
        write_path=False if args.no_write else None,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
