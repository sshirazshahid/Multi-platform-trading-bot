"""Task A.12 — KillCriteriaMonitor."""
from __future__ import annotations

import json
import pytest


def test_kill_insufficient_decisions_no_trigger():
    from core.agents.kill_criteria import KillCriteriaMonitor
    m = KillCriteriaMonitor()
    v = m.evaluate(
        shadow_pnls=[0.1] * 10,
        live_fee_burn_per_trade=0.05,
        shadow_fee_burn_per_trade=0.05,
        halts_in_7d=0,
    )
    assert not v.triggered
    assert "insufficient" in v.reason


def test_kill_low_wr_triggers():
    from core.agents.kill_criteria import KillCriteriaMonitor
    m = KillCriteriaMonitor()
    pnls = [-0.1] * 80 + [0.1] * 20  # 20% WR over 100
    v = m.evaluate(pnls, 0.05, 0.05, halts_in_7d=0)
    assert v.triggered
    assert "wr_below_floor" in v.reason


def test_kill_fee_burn_triggers():
    from core.agents.kill_criteria import KillCriteriaMonitor
    m = KillCriteriaMonitor()
    pnls = [0.1] * 100  # 100% WR (so WR doesn't trigger)
    v = m.evaluate(pnls,
                   live_fee_burn_per_trade=0.05,
                   shadow_fee_burn_per_trade=0.20,  # 4x live
                   halts_in_7d=0)
    assert v.triggered
    assert "fee_burn_high" in v.reason


def test_kill_halts_triggers():
    from core.agents.kill_criteria import KillCriteriaMonitor
    m = KillCriteriaMonitor()
    pnls = [0.1] * 100
    v = m.evaluate(pnls, 0.05, 0.05, halts_in_7d=4)
    assert v.triggered
    assert "halts_7d" in v.reason


def test_kill_disable_writes_state(tmp_path):
    from core.agents.kill_criteria import KillCriteriaMonitor
    p = tmp_path / "kill.json"
    m = KillCriteriaMonitor(state_path=p)
    m.disable_shadow_runtime("test_reason")
    j = json.loads(p.read_text())
    assert j["killed"] is True
    assert j["reason"] == "test_reason"


def test_kill_healthy_no_trigger():
    from core.agents.kill_criteria import KillCriteriaMonitor
    m = KillCriteriaMonitor()
    pnls = [0.5, -0.2] * 50  # 50% WR
    v = m.evaluate(pnls, 0.05, 0.04, halts_in_7d=1)
    assert not v.triggered
    assert v.reason == "ok"
