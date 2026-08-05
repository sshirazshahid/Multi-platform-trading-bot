#!/usr/bin/env python3
"""Ops verify: F1 schtask + heartbeat profile (read-only)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _schtasks_list() -> str:
    try:
        out = subprocess.check_output(
            ["schtasks", "/Query", "/FO", "LIST"],
            text=True,
            errors="replace",
            timeout=60,
        )
    except Exception as exc:
        return f"schtasks_error: {exc}"
    return out


def main() -> int:
    text = _schtasks_list()
    f1_lines = [ln for ln in text.splitlines() if "F1" in ln or "f1" in ln or "Carry" in ln]
    print("=== F1-related schtasks ===")
    for ln in f1_lines[:40]:
        print(ln)
    if not f1_lines:
        print("(none matched)")

    hb = ROOT / "data" / "heartbeat.json"
    ch = ROOT / "data" / "carry_heartbeat.json"
    print("=== heartbeat.json ===")
    if hb.is_file():
        d = json.loads(hb.read_text(encoding="utf-8"))
        for k in (
            "operating_mode",
            "mode",
            "paper_trading_profile",
            "paper_profile",
            "status",
            "dry_run",
            "uptime_seconds",
            "is_halted",
        ):
            if k in d:
                print(f"  {k}={d[k]}")
        # nested
        for nest in ("bot", "config", "profile"):
            if isinstance(d.get(nest), dict):
                print(f"  [{nest}] keys={list(d[nest].keys())[:12]}")
    else:
        print("  missing")

    print("=== carry_heartbeat.json ===")
    if ch.is_file():
        d = json.loads(ch.read_text(encoding="utf-8"))
        print(json.dumps(d, indent=2)[:800])
    else:
        print("  missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
