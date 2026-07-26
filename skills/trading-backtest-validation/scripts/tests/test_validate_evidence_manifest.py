from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validate_evidence_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_evidence_manifest", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def valid_manifest() -> dict:
    payload = {
        "strategy_hash": "sha256:" + "a" * 64,
        "config_hash": "sha256:" + "b" * 64,
        "data_hash": "sha256:" + "c" * 64,
        "walk_forward_segments": 4,
        "net_expectancy_after_costs": 0.01,
        "profit_factor_after_costs": 1.01,
        "deflated_sharpe": 0.10,
        "pbo": 0.5,
        "shadow_days": 30,
        "independent_mature_outcomes": 100,
        "unexplained_forward_divergence": False,
        "naked_position_incidents": 0,
        "uses_model": True,
    }
    payload.update({key: True for key in MODULE.TRUE_FIELDS})
    payload.update({key: True for key in MODULE.MODEL_TRUE_FIELDS})
    return payload


def test_complete_evidence_passes() -> None:
    assert MODULE.validate_manifest(valid_manifest()) == []


def test_high_win_rate_cannot_replace_mature_shadow_evidence() -> None:
    payload = valid_manifest()
    payload.update({"raw_win_rate": 0.99, "shadow_days": 7, "independent_mature_outcomes": 20})
    errors = MODULE.validate_manifest(payload)
    assert any("shadow_days" in error for error in errors)
    assert any("independent_mature_outcomes" in error for error in errors)


def test_model_integrity_and_replay_fail_closed() -> None:
    payload = valid_manifest()
    payload["replay_hash_match"] = False
    payload["model_artifact_checksum_valid"] = False
    errors = MODULE.validate_manifest(payload)
    assert "replay_hash_match must be true" in errors
    assert "model_artifact_checksum_valid must be true when uses_model is true" in errors


def test_mutable_or_placeholder_hash_is_blocked() -> None:
    payload = valid_manifest()
    payload["strategy_hash"] = "sha256:latest"
    assert any("strategy_hash" in error for error in MODULE.validate_manifest(payload))


def test_cli_writes_atomic_non_authorizing_report(tmp_path: Path) -> None:
    source = tmp_path / "evidence.json"
    output = tmp_path / "reports"
    source.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    assert MODULE.main(["--input", str(source), "--output-dir", str(output)]) == 0
    report = json.loads((output / "evidence-contract-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS_FOR_REVIEW"
    assert report["live_authorization"] is False
    assert not list(output.glob("*.tmp"))
