#!/usr/bin/env python3
"""Validate a strategy/model evidence manifest without importing bot code."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

TRUE_FIELDS = (
    "point_in_time_data",
    "lookahead_tests_passed",
    "realistic_costs_and_fills",
    "chronological_holdout_untouched",
    "deterministic_replay",
    "replay_hash_match",
    "selection_bias_reported",
    "shadow_outcomes_deduplicated",
    "model_gate_fail_closed",
)

MODEL_TRUE_FIELDS = (
    "model_manifest_valid",
    "model_artifact_checksum_valid",
    "model_pointer_atomic",
)


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_manifest(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("strategy_hash", "config_hash", "data_hash"):
        value = payload.get(key)
        if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value) is None:
            errors.append(f"{key} must be sha256 followed by 64 hexadecimal characters")
    for key in TRUE_FIELDS:
        if payload.get(key) is not True:
            errors.append(f"{key} must be true")

    minimums = {
        "walk_forward_segments": 4,
        "net_expectancy_after_costs": 0.0,
        "profit_factor_after_costs": 1.0,
        "deflated_sharpe": 0.10,
        "shadow_days": 30,
        "independent_mature_outcomes": 100,
    }
    for key, minimum in minimums.items():
        value = _number(payload, key)
        strict = key in {"net_expectancy_after_costs", "profit_factor_after_costs"}
        failed = value is None or value < minimum or (strict and value == minimum)
        if failed:
            comparator = ">" if strict else ">="
            errors.append(f"{key} must be {comparator} {minimum:g}")
        elif key in {"walk_forward_segments", "shadow_days", "independent_mature_outcomes"} and not value.is_integer():
            errors.append(f"{key} must be an integer")

    pbo = _number(payload, "pbo")
    if pbo is None or pbo < 0 or pbo > 0.5:
        errors.append("pbo must be between 0 and 0.5")
    divergence = payload.get("unexplained_forward_divergence")
    if divergence is not False:
        errors.append("unexplained_forward_divergence must be false")
    incidents = _number(payload, "naked_position_incidents")
    if incidents != 0:
        errors.append("naked_position_incidents must be 0")

    if payload.get("uses_model") is True:
        for key in MODEL_TRUE_FIELDS:
            if payload.get(key) is not True:
                errors.append(f"{key} must be true when uses_model is true")
    elif payload.get("uses_model") is not False:
        errors.append("uses_model must be a boolean")
    return errors


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
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
        raw, errors = None, [f"cannot read manifest: {exc}"]
    else:
        errors = validate_manifest(raw) if isinstance(raw, dict) else ["manifest root must be an object"]
    report = {
        "status": "PASS_FOR_REVIEW" if not errors else "BLOCKED",
        "live_authorization": False,
        "errors": errors,
        "source": str(args.input),
    }
    _atomic_write(args.output_dir / "evidence-contract-report.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
