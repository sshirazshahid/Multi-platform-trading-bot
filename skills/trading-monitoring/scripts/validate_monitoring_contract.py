#!/usr/bin/env python3
"""Validate monitoring, heartbeat, and alert-delivery safety properties."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


TRUE_FIELDS = (
    "atomic_heartbeat",
    "heartbeat_write_failures_visible",
    "heartbeat_schema_versioned",
    "external_supervisor",
    "health_reads_cached_state_only",
    "append_only_decision_events",
    "decision_provenance_complete",
    "operating_modes_segregated",
    "persistent_alert_outbox",
    "alert_delivery_acknowledged",
    "alert_timeout_bounded",
    "cooldown_after_confirmed_delivery",
    "critical_repeat_until_operator_ack",
    "stale_or_unknown_pauses_entries",
    "secrets_redacted",
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
    errors = [f"{key} must be true" for key in TRUE_FIELDS if payload.get(key) is not True]
    heartbeat = _number(payload, "heartbeat_interval_seconds")
    stale = _number(payload, "stale_heartbeat_seconds")
    poll = _number(payload, "supervisor_poll_seconds")
    strikes = _number(payload, "failure_strikes_before_restart")
    if heartbeat is None or not 1 <= heartbeat <= 60:
        errors.append("heartbeat_interval_seconds must be between 1 and 60")
    if stale is None or stale <= 0 or stale > 900:
        errors.append("stale_heartbeat_seconds must be between 0 and 900")
    elif heartbeat is not None and stale < heartbeat * 2:
        errors.append("stale_heartbeat_seconds must allow at least two heartbeat intervals")
    if poll is None or not 1 <= poll <= 60:
        errors.append("supervisor_poll_seconds must be between 1 and 60")
    if strikes is None or not strikes.is_integer() or not 2 <= strikes <= 5:
        errors.append("failure_strikes_before_restart must be an integer between 2 and 5")
    elif poll is not None and stale is not None and poll * strikes > stale:
        errors.append("supervisor_poll_seconds times failure strikes must not exceed stale heartbeat budget")
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
        "status": "PASS_FOR_REVIEW" if not errors else "BLOCKED",
        "live_authorization": False,
        "errors": errors,
        "source": str(args.input),
    }
    _atomic_write(args.output_dir / "monitoring-contract-report.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
