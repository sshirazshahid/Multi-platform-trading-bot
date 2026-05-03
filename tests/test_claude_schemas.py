"""Schema validator tests — valid responses pass, invalid ones are rejected
*without* mutating the response or writing to the warehouse.
"""
from __future__ import annotations

import json

import pytest

from core import claude_schemas as cs


@pytest.fixture(autouse=True)
def _redirect_failure_log(tmp_path, monkeypatch):
    """Point the failure log at a tmp file so production data stays clean."""
    log = tmp_path / "failures.jsonl"
    monkeypatch.setattr(cs, "FAILURE_LOG", log)
    yield log


def test_valid_historical_annotation():
    out = cs.validate("historical_annotation", {
        "decision": "GOOD",
        "confidence": 4,
        "reason_labels": ["trend_follow", "breakout"],
        "commentary": "Clean multi-tf alignment.",
    })
    assert out is not None
    assert out["decision"] == "GOOD"


def test_valid_pre_trade_review():
    out = cs.validate("pre_trade_review", {
        "decision": "ALLOW",
        "confidence": 3,
        "red_flags": [],
        "commentary": "Looks fine.",
    })
    assert out is not None


def test_valid_weekly_diagnostic():
    out = cs.validate("weekly_diagnostic", {
        "top_loss_drivers": ["tight_stops"],
        "top_win_drivers": ["breakout_retest"],
        "symbols_to_disable": [],
        "setup_families_to_review": ["Supertrend"],
        "exchange_health_notes": [],
        "summary": "Supertrend family has negative expectancy; recommend pause.",
    })
    assert out is not None


def test_valid_drift_detection():
    out = cs.validate("drift_detection", {
        "drift_level": "LOW",
        "mismatched_features": [],
        "commentary": "Feature distributions within 1-sigma.",
        "recommended_action": "NONE",
    })
    assert out is not None


# ── Rejection cases ───────────────────────────────────────────────────

def test_unknown_task_rejected(_redirect_failure_log):
    assert cs.validate("nonexistent_task", {"foo": 1}) is None


def test_unknown_key_rejected(_redirect_failure_log):
    """Critical: must reject attempts to add fields like 'override_stop'."""
    assert cs.validate("pre_trade_review", {
        "decision": "ALLOW",
        "confidence": 3,
        "red_flags": [],
        "commentary": "",
        "override_stop": 0.01,  # attempt to sneak in a risk bypass
    }) is None


def test_missing_required_rejected(_redirect_failure_log):
    assert cs.validate("pre_trade_review", {
        "decision": "ALLOW",
        "confidence": 3,
        # 'red_flags' missing
        "commentary": "",
    }) is None


def test_bad_enum_rejected(_redirect_failure_log):
    assert cs.validate("pre_trade_review", {
        "decision": "MAYBE",
        "confidence": 3,
        "red_flags": [],
        "commentary": "",
    }) is None


def test_confidence_out_of_range_rejected(_redirect_failure_log):
    assert cs.validate("pre_trade_review", {
        "decision": "ALLOW",
        "confidence": 7,
        "red_flags": [],
        "commentary": "",
    }) is None


def test_wrong_type_rejected(_redirect_failure_log):
    assert cs.validate("pre_trade_review", {
        "decision": "ALLOW",
        "confidence": "3",  # str, not int
        "red_flags": [],
        "commentary": "",
    }) is None


def test_non_dict_response_rejected(_redirect_failure_log):
    assert cs.validate("pre_trade_review", ["not", "a", "dict"]) is None
    assert cs.validate("pre_trade_review", None) is None


def test_list_item_wrong_type_rejected(_redirect_failure_log):
    assert cs.validate("pre_trade_review", {
        "decision": "ALLOW",
        "confidence": 3,
        "red_flags": ["ok", 42],  # int in a list_str
        "commentary": "",
    }) is None


def test_failures_are_logged(_redirect_failure_log):
    cs.validate("pre_trade_review", {"decision": "MAYBE"})
    assert _redirect_failure_log.exists()
    lines = _redirect_failure_log.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "failure should be written"
    entry = json.loads(lines[-1])
    assert entry["task"] == "pre_trade_review"
    assert "error" in entry
