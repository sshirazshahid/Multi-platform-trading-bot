"""Byte-compile sweep over core/ and scripts/ — a fast syntax/import-shape gate.

`compileall` parses and compiles every module without executing it, so it
catches syntax errors, indentation breaks, and stray merge markers that a
partial test run might miss. Wired into CI (Phase 0e) and runnable locally:

    python scripts/compile_sweep.py

Exit code 0 = all clean, 1 = at least one file failed to compile.
"""
from __future__ import annotations

import compileall
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("core", "scripts")


def main() -> int:
    ok = True
    for rel in TARGETS:
        target = ROOT / rel
        if not target.exists():
            print(f"skip (missing): {target}")
            continue
        # quiet=1 prints only errors; force=True recompiles regardless of cache.
        result = compileall.compile_dir(str(target), quiet=1, force=True)
        ok = ok and bool(result)
    if ok:
        print("compile_sweep: OK")
        return 0
    print("compile_sweep: FAILED — see errors above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
