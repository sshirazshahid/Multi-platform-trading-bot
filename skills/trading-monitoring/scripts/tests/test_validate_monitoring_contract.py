from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validate_monitoring_contract.py"
SPEC = importlib.util.spec_from_file_location("validate_monitoring_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def valid_contract() -> dict:
    payload = {key: True for key in MODULE.TRUE_FIELDS}
    payload.update(
        heartbeat_interval_seconds=60,
        stale_heartbeat_seconds=900,
        supervisor_poll_seconds=5,
        failure_strikes_before_restart=3,
    )
    return payload


def test_complete_monitoring_contract_passes() -> None:
    assert MODULE.validate_contract(valid_contract()) == []


def test_same_process_watchdog_and_non_atomic_heartbeat_are_blocked() -> None:
    payload = valid_contract()
    payload["external_supervisor"] = False
    payload["atomic_heartbeat"] = False
    errors = MODULE.validate_contract(payload)
    assert "external_supervisor must be true" in errors
    assert "atomic_heartbeat must be true" in errors


def test_alert_cooldown_requires_confirmed_delivery() -> None:
    payload = valid_contract()
    payload["cooldown_after_confirmed_delivery"] = False
    payload["alert_delivery_acknowledged"] = False
    errors = MODULE.validate_contract(payload)
    assert any("cooldown_after_confirmed_delivery" in error for error in errors)
    assert any("alert_delivery_acknowledged" in error for error in errors)


def test_restart_detection_must_fit_stale_budget() -> None:
    payload = valid_contract()
    payload["stale_heartbeat_seconds"] = 90
    payload["supervisor_poll_seconds"] = 60
    payload["failure_strikes_before_restart"] = 3
    assert any("stale heartbeat budget" in error for error in MODULE.validate_contract(payload))


def test_cli_writes_atomic_non_authorizing_report(tmp_path: Path) -> None:
    source = tmp_path / "monitoring.json"
    output = tmp_path / "reports"
    source.write_text(json.dumps(valid_contract()), encoding="utf-8")
    assert MODULE.main(["--input", str(source), "--output-dir", str(output)]) == 0
    report = json.loads((output / "monitoring-contract-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS_FOR_REVIEW"
    assert report["live_authorization"] is False
    assert not list(output.glob("*.tmp"))
