from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import dashboard


def _shadow_config():
    return SimpleNamespace(
        ENTRY_POLICY="SHADOW_ONLY",
        OPERATING_MODE="PAPER",
        APPROVED_PAPER_STRATEGIES=frozenset(),
        APPROVED_LIVE_STRATEGIES=frozenset(),
        CONTROLLED_LIVE_ENABLED=False,
        LIVE_ENTRY_APPROVAL_PATH="data/live_entry_approval.json",
    )


def _heartbeat(now):
    return {
        "timestamp": now.isoformat(),
        "is_halted": False,
        "halted_exchanges": [],
    }


def test_dashboard_payload_exposes_separate_local_readiness_states():
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    payload = dashboard.build_health_status_payload(
        heartbeat=_heartbeat(now),
        risk_state={},
        now=now,
        config_module=_shadow_config(),
        approved_evidence=(),
    )

    assert payload["SYSTEM_HEALTHY"] is True
    assert payload["PAPER_READY"] is False
    assert payload["EDGE_UNPROVEN"] is True
    assert payload["LIVE_BLOCKED"] is True
    assert payload["operational_status"] == "healthy"
    assert payload["status_source"] == "local_files"
    assert payload["profitability_claimed"] is False


def test_dashboard_payload_marks_stale_or_halted_system_unhealthy():
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    stale = _heartbeat(now - timedelta(seconds=601))
    stale_payload = dashboard.build_health_status_payload(
        heartbeat=stale,
        risk_state={},
        now=now,
        config_module=_shadow_config(),
        approved_evidence=(),
    )
    assert stale_payload["SYSTEM_HEALTHY"] is False
    assert stale_payload["state_reasons"]["SYSTEM_HEALTHY"] == "heartbeat_stale"

    halted_payload = dashboard.build_health_status_payload(
        heartbeat=_heartbeat(now),
        risk_state={"is_halted": True},
        now=now,
        config_module=_shadow_config(),
        approved_evidence=(),
    )
    assert halted_payload["SYSTEM_HEALTHY"] is False
    assert halted_payload["state_reasons"]["SYSTEM_HEALTHY"] == ("risk_state_reports_halted")


def test_status_json_exits_before_exchange_fetcher_construction(monkeypatch, capsys):
    monkeypatch.setattr(
        dashboard,
        "parse_args",
        lambda: Namespace(status_json=True, refresh=60, width=None),
    )
    monkeypatch.setattr(
        dashboard,
        "build_health_status_payload",
        lambda: {
            "SYSTEM_HEALTHY": False,
            "PAPER_READY": False,
            "EDGE_UNPROVEN": True,
            "LIVE_BLOCKED": True,
        },
    )

    def fail_if_network_fetcher_is_constructed():
        raise AssertionError("status JSON must not construct LiveFetcher")

    monkeypatch.setattr(dashboard, "LiveFetcher", fail_if_network_fetcher_is_constructed)
    dashboard.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["SYSTEM_HEALTHY"] is False
    assert payload["EDGE_UNPROVEN"] is True
