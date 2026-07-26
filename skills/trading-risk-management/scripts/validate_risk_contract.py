#!/usr/bin/env python3
"""Validate a CONTROLLED_LIVE risk-contract manifest without runtime imports."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


BOOL_REQUIREMENTS = (
    "default_fail_closed",
    "double_latch_enforced",
    "owner_signoff_required",
    "risk_gate_on_all_entries",
    "persistent_kill_switch",
    "startup_reconciliation",
    "protection_verified_before_entries",
    "protection_pending_persisted",
    "reduce_only_exits",
    "clock_drift_pauses_entries",
)

NUMERIC_CEILINGS = {
    "max_effective_leverage": 1.0,
    "max_concurrent_positions": 1.0,
    "risk_per_trade_pct": 0.1,
    "gross_exposure_pct": 2.0,
}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_contract(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(payload.get("profile", "")).upper() != "CONTROLLED_LIVE":
        errors.append("profile must be CONTROLLED_LIVE")

    for key, ceiling in NUMERIC_CEILINGS.items():
        value = _finite_number(payload.get(key))
        if value is None or value <= 0:
            errors.append(f"{key} must be a finite positive number")
        elif value > ceiling:
            errors.append(f"{key}={value:g} exceeds ceiling {ceiling:g}")
        elif key == "max_concurrent_positions" and not value.is_integer():
            errors.append("max_concurrent_positions must be an integer")

    for key in BOOL_REQUIREMENTS:
        if payload.get(key) is not True:
            errors.append(f"{key} must be true")
    return errors


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors = [f"cannot read manifest: {exc}"]
        raw = None
    else:
        errors = validate_contract(raw) if isinstance(raw, dict) else ["manifest root must be an object"]

    report = {
        "status": "PASS_FOR_REVIEW" if not errors else "BLOCKED",
        "live_authorization": False,
        "errors": errors,
        "source": str(args.input),
    }
    _atomic_write_json(args.output_dir / "risk-contract-report.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
