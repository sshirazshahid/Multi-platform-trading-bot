#!/usr/bin/env python3
"""Validate the fail-closed Windows 24x7 deployment manifest."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


TRUE_FIELDS = (
    "controlled_live_refused_by_launcher",
    "canonical_supervisor_action",
    "repository_working_directory",
    "at_startup_trigger",
    "start_when_available",
    "execution_time_limit_disabled",
    "multiple_instances_ignore_new",
    "limited_run_level",
    "secrets_absent_from_command_line",
    "singleton_lock",
    "supervisor_owns_worker",
    "external_heartbeat_monitoring",
    "atomic_heartbeat",
    "heartbeat_write_failures_visible",
    "bounded_restart_backoff",
    "owned_child_only_termination",
    "persistent_alert_outbox",
    "alert_delivery_acknowledged",
    "graceful_shutdown_then_kill",
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


def validate_contract(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(payload.get("operating_mode", "")).upper() not in {"PAPER", "OBSERVATION"}:
        errors.append("operating_mode must be PAPER or OBSERVATION")
    if payload.get("controlled_live_enabled") is not False:
        errors.append("controlled_live_enabled must be false")
    errors.extend(f"{key} must be true" for key in TRUE_FIELDS if payload.get(key) is not True)

    ranges = {
        "task_restart_count": (3, 10000),
        "task_restart_interval_seconds": (10, 600),
        "heartbeat_max_age_seconds": (30, 900),
        "heartbeat_poll_seconds": (1, 60),
        "failure_strikes_before_restart": (2, 5),
        "startup_grace_seconds": (30, 900),
        "worker_stop_grace_seconds": (5, 60),
        "max_restart_backoff_seconds": (30, 600),
    }
    for key, (minimum, maximum) in ranges.items():
        value = _number(payload, key)
        if value is None or value < minimum or value > maximum:
            errors.append(f"{key} must be between {minimum} and {maximum}")
        elif key in {"task_restart_count", "failure_strikes_before_restart"} \
                and not value.is_integer():
            errors.append(f"{key} must be an integer")
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
        errors = validate_contract(raw) if isinstance(raw, dict) else ["manifest root must be an object"]
    report = {
        "status": "PASS_FOR_PAPER_24X7_REVIEW" if not errors else "BLOCKED",
        "live_authorization": False,
        "errors": errors,
        "source": str(args.input),
    }
    _atomic_write(args.output_dir / "deployment-contract-report.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
