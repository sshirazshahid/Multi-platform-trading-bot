from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validate_risk_contract.py"
SPEC = importlib.util.spec_from_file_location("validate_risk_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def valid_contract() -> dict:
    payload = {
        "profile": "CONTROLLED_LIVE",
        "max_effective_leverage": 1.0,
        "max_concurrent_positions": 1,
        "risk_per_trade_pct": 0.1,
        "gross_exposure_pct": 2.0,
    }
    payload.update({key: True for key in MODULE.BOOL_REQUIREMENTS})
    return payload


def test_strict_profile_passes() -> None:
    assert MODULE.validate_contract(valid_contract()) == []


def test_legacy_three_x_and_four_positions_are_blocked() -> None:
    payload = valid_contract()
    payload["max_effective_leverage"] = 3
    payload["max_concurrent_positions"] = 4
    errors = MODULE.validate_contract(payload)
    assert any("max_effective_leverage" in error for error in errors)
    assert any("max_concurrent_positions" in error for error in errors)


def test_missing_fail_closed_control_is_blocked() -> None:
    payload = valid_contract()
    payload.pop("protection_pending_persisted")
    assert "protection_pending_persisted must be true" in MODULE.validate_contract(payload)


def test_cli_writes_atomic_non_authorizing_report(tmp_path: Path) -> None:
    source = tmp_path / "risk.json"
    output = tmp_path / "reports"
    source.write_text(json.dumps(valid_contract()), encoding="utf-8")
    assert MODULE.main(["--input", str(source), "--output-dir", str(output)]) == 0
    report = json.loads((output / "risk-contract-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS_FOR_REVIEW"
    assert report["live_authorization"] is False
    assert not list(output.glob("*.tmp"))
