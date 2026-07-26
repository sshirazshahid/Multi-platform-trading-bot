from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.entry_policy import EntryPolicy, LiveApproval
from core.readiness import (
    evaluate_readiness,
    load_approved_evidence,
    runtime_readiness,
)

STATE_NAMES = {
    "SYSTEM_HEALTHY",
    "PAPER_READY",
    "EDGE_UNPROVEN",
    "LIVE_BLOCKED",
}


def _states(payload):
    return {name: payload[name] for name in STATE_NAMES}


def test_default_shadow_only_reports_edge_unproven_and_live_blocked():
    payload = evaluate_readiness(
        system_healthy=True,
        operating_mode="PAPER",
        entry_policy=EntryPolicy.SHADOW_ONLY,
    ).to_dict()

    assert _states(payload) == {
        "SYSTEM_HEALTHY": True,
        "PAPER_READY": False,
        "EDGE_UNPROVEN": True,
        "LIVE_BLOCKED": True,
    }
    assert payload["entry_policy"] == "SHADOW_ONLY"
    assert payload["strategy_allowlist"] == []
    assert payload["profitability_claimed"] is False
    assert payload["health_scope"] == "operational_only"


def test_unhealthy_system_does_not_rewrite_other_readiness_dimensions():
    base = dict(
        operating_mode="PAPER",
        entry_policy=EntryPolicy.APPROVED_PAPER,
        approved_paper={"F1"},
        approved_evidence={"F1"},
    )
    healthy = evaluate_readiness(system_healthy=True, **base).to_dict()
    unhealthy = evaluate_readiness(
        system_healthy=False,
        system_health_reason="scheduler_stale",
        **base,
    ).to_dict()

    assert healthy["SYSTEM_HEALTHY"] is True
    assert unhealthy["SYSTEM_HEALTHY"] is False
    assert unhealthy["state_reasons"]["SYSTEM_HEALTHY"] == "scheduler_stale"
    for state in ("PAPER_READY", "EDGE_UNPROVEN", "LIVE_BLOCKED"):
        assert unhealthy[state] is healthy[state]


def test_paper_ready_is_not_edge_or_profitability_proof():
    payload = evaluate_readiness(
        system_healthy=True,
        operating_mode="PAPER",
        entry_policy=EntryPolicy.APPROVED_PAPER,
        approved_paper={"F1"},
        approved_evidence=(),
    ).to_dict()

    assert payload["SYSTEM_HEALTHY"] is True
    assert payload["PAPER_READY"] is True
    assert payload["EDGE_UNPROVEN"] is True
    assert payload["LIVE_BLOCKED"] is True
    assert payload["profitability_claimed"] is False


def test_approved_edge_evidence_does_not_change_shadow_or_live_authority():
    payload = evaluate_readiness(
        system_healthy=True,
        operating_mode="PAPER",
        entry_policy=EntryPolicy.SHADOW_ONLY,
        approved_evidence={"F1"},
    ).to_dict()

    assert payload["EDGE_UNPROVEN"] is False
    assert payload["PAPER_READY"] is False
    assert payload["LIVE_BLOCKED"] is True


def test_live_unblocks_only_for_expiring_versioned_full_sha_human_approval():
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    sha = "a" * 40
    approval = LiveApproval(
        strategy_id="F1",
        strategy_version="f1-v3",
        git_sha=sha,
        expires_at=now + timedelta(hours=1),
        approved_by="operator",
    )
    base = dict(
        system_healthy=True,
        operating_mode="CONTROLLED_LIVE",
        entry_policy=EntryPolicy.CONTROLLED_LIVE,
        approved_live={"F1"},
        live_latch_enabled=True,
        current_git_sha=sha,
        now=now,
    )

    valid = evaluate_readiness(live_approval=approval, **base).to_dict()
    assert valid["LIVE_BLOCKED"] is False
    assert valid["state_reasons"]["LIVE_BLOCKED"] == "controlled_live_approved"

    short_sha = LiveApproval(
        strategy_id="F1",
        strategy_version="f1-v3",
        git_sha="abc123",
        expires_at=now + timedelta(hours=1),
        approved_by="operator",
    )
    blocked = evaluate_readiness(live_approval=short_sha, **base).to_dict()
    assert blocked["LIVE_BLOCKED"] is True
    assert blocked["state_reasons"]["LIVE_BLOCKED"] == "live_approval_git_sha_not_full"

    expired = LiveApproval(
        strategy_id="F1",
        strategy_version="f1-v3",
        git_sha=sha,
        expires_at=now - timedelta(seconds=1),
        approved_by="operator",
    )
    blocked = evaluate_readiness(live_approval=expired, **base).to_dict()
    assert blocked["LIVE_BLOCKED"] is True
    assert blocked["state_reasons"]["LIVE_BLOCKED"] == "live_approval_expired"


def test_evidence_registry_requires_explicit_accepted_status(tmp_path):
    registry = tmp_path / "active_strategies.json"
    registry.write_text(
        json.dumps(
            {
                "strategies": {
                    "F1": {
                        "promotion_status": "PROMOTED",
                        "oos_metrics": {
                            "accept": True,
                            "validation_schema": "paper-edge-v1",
                            "paper_edge": {"pass": True},
                        },
                    },
                    "F2": {
                        "promotion_status": "PROMOTED",
                        "oos_metrics": {"accept": False},
                    },
                    "F3": {
                        "promotion_status": "NO_EDGE",
                        "oos_metrics": {"accept": True},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_approved_evidence(registry) == frozenset({"F1"})
    registry.write_text("not json", encoding="utf-8")
    assert load_approved_evidence(registry) == frozenset()


def test_runtime_config_and_allowlist_fail_closed():
    malformed_config = SimpleNamespace(
        ENTRY_POLICY="APPROVED_PAPER",
        OPERATING_MODE="PAPER",
        APPROVED_PAPER_STRATEGIES={"F1": True},
        APPROVED_LIVE_STRATEGIES=(),
        CONTROLLED_LIVE_ENABLED=False,
        LIVE_ENTRY_APPROVAL_PATH="data/live_entry_approval.json",
    )
    payload = runtime_readiness(
        system_healthy=True,
        config_module=malformed_config,
        approved_evidence=(),
    ).to_dict()

    assert payload["config_valid"] is False
    assert payload["PAPER_READY"] is False
    assert payload["EDGE_UNPROVEN"] is True
    assert payload["LIVE_BLOCKED"] is True
