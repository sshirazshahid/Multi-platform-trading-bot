from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.risk_manager import (
    RiskManager,
    _utc_today,
    aggregate_open_risk_breached,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()


def _daily_breaker(monkeypatch, pct=0.02):
    monkeypatch.setattr(
        "config.DAILY_LOSS_BREAKER",
        {"enabled": True, "max_loss_pct": pct},
        raising=False,
    )


def test_daily_loss_breaker_is_reported_and_resets_at_utc_rollover(monkeypatch):
    _daily_breaker(monkeypatch)
    risk = RiskManager()
    risk.set_start_balance(1_000.0)
    risk.record_trade_pnl(-25.0, balance=1_000.0, is_win=False)

    assert risk.is_halted is True
    assert "daily_loss" in risk.halt_reason
    assert risk.can_trade(0) is False

    risk._trading_day = _utc_today() - timedelta(days=1)
    assert risk.is_halted is False
    assert risk.halt_reason == ""


def test_execution_incident_persists_and_requires_operator_clear_note():
    risk = RiskManager()
    assert risk.latch_incident("unknown order outcome", category="execution") is True
    assert risk.is_halted is True
    assert "execution" in risk.halt_reason
    assert risk.can_trade(0) is False

    restarted = RiskManager()
    assert restarted.is_halted is True
    assert restarted.clear_incident("") is False
    assert restarted.is_halted is True
    assert restarted.clear_incident("exchange reconciled; no unmanaged position") is True
    assert restarted.is_halted is False
    assert Path("data/risk_incident_history.jsonl").exists()


def test_kill_switch_is_visible_in_unified_report_without_touching_exits():
    import core.kill_switch as kill_switch

    kill_switch.KILL_SWITCH_PATH.write_text("containment", encoding="utf-8")
    risk = RiskManager()
    assert risk.is_halted is True
    assert risk.halt_reason == "kill_switch_active"


def test_aggregate_open_risk_includes_stop_distance_and_stressed_exit_cost():
    position = SimpleNamespace(
        size=1.0,
        entry_price=100.0,
        stop_loss=98.0,
    )
    # Existing risk: 100 * (2% stop + 20bps stress) = 2.2.
    # Candidate: 200 * (2% + 20bps) = 4.4. Total 6.6 < 0.75% of 1000 (7.5).
    assert aggregate_open_risk_breached(
        [position], 200.0, 0.02, 1_000.0, 0.0075, 0.002
    ) is False
    assert aggregate_open_risk_breached(
        [position], 300.0, 0.02, 1_000.0, 0.0075, 0.002
    ) is True


def test_aggregate_open_risk_fails_closed_on_unknown_stop_or_equity():
    unknown_stop = SimpleNamespace(size=1.0, entry_price=100.0, stop_loss=0.0)
    assert aggregate_open_risk_breached(
        [unknown_stop], 0.0, 0.02, 1_000.0, 0.0075, 0.002
    ) is True
    assert aggregate_open_risk_breached(
        [], 100.0, 0.02, 0.0, 0.0075, 0.002
    ) is True


def test_aggregate_risk_gate_is_wired_before_order_placement():
    source = (Path(__file__).resolve().parents[1] / "core" / "bot_engine.py").read_text(
        encoding="utf-8"
    )
    execute = source[source.index("def _execute_open"):source.index("def _execute_close")]
    assert "aggregate_open_risk_breached" in execute
    assert execute.index("aggregate_open_risk_breached") < execute.index("open_position(")
