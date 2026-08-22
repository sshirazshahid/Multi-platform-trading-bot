from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "validate_deployment_contract.py"
SPEC = importlib.util.spec_from_file_location("validate_deployment_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def valid_contract() -> dict:
    payload = {key: True for key in MODULE.TRUE_FIELDS}
    payload.update(
        operating_mode="PAPER",
        controlled_live_enabled=False,
        task_restart_count=999,
        task_restart_interval_seconds=60,
        heartbeat_max_age_seconds=900,
        heartbeat_poll_seconds=5,
        failure_strikes_before_restart=3,
        startup_grace_seconds=600,
        worker_stop_grace_seconds=15,
        max_restart_backoff_seconds=300,
    )
    return payload


def test_canonical_paper_deployment_passes() -> None:
    assert MODULE.validate_contract(valid_contract()) == []


def test_scheduled_controlled_live_is_blocked() -> None:
    payload = valid_contract()
    payload["operating_mode"] = "CONTROLLED_LIVE"
    payload["controlled_live_enabled"] = True
    errors = MODULE.validate_contract(payload)
    assert any("operating_mode" in error for error in errors)
    assert any("controlled_live_enabled" in error for error in errors)


def test_same_process_or_pid_wide_supervision_is_blocked() -> None:
    payload = valid_contract()
    payload["external_heartbeat_monitoring"] = False
    payload["owned_child_only_termination"] = False
    errors = MODULE.validate_contract(payload)
    assert any("external_heartbeat_monitoring" in error for error in errors)
    assert any("owned_child_only_termination" in error for error in errors)


def test_fractional_task_restart_count_is_blocked() -> None:
    payload = valid_contract()
    payload["task_restart_count"] = 3.5
    assert "task_restart_count must be an integer" in MODULE.validate_contract(payload)


def test_cli_writes_atomic_non_authorizing_report(tmp_path: Path) -> None:
    source = tmp_path / "deployment.json"
    output = tmp_path / "reports"
    source.write_text(json.dumps(valid_contract()), encoding="utf-8")
    assert MODULE.main(["--input", str(source), "--output-dir", str(output)]) == 0
    report = json.loads((output / "deployment-contract-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS_FOR_PAPER_24X7_REVIEW"
    assert report["live_authorization"] is False
    assert not list(output.glob("*.tmp"))
