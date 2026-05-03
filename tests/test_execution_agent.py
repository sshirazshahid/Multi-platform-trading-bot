"""Task A.7 — ExecutionAgent log-only simulator."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_exec_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_exec_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _proposal(side="buy"):
    from core.agents.base_agent import Proposal
    return Proposal(
        agent_id="ScalpAgent", symbol="BTC/USDT:USDT", side=side,
        entry=70000.0,
        sl=69650.0 if side == "buy" else 70350.0,
        tp=70525.0 if side == "buy" else 69475.0,
        confidence=0.6, reason="test",
    )


def test_execution_agent_writes_shadow_row_no_orders(wh):
    """Critical: must NEVER call OrderManager.open_position."""
    from core.agents.execution_agent import ExecutionAgent
    om = MagicMock()
    ea = ExecutionAgent(warehouse=wh)
    row = ea.execute_shadow(
        _proposal("buy"),
        projected_notional_current=8.0,
        projected_notional_alt=200.0,
    )
    assert row["agent_id"] == "ScalpAgent"
    assert row["projected_notional_alt"] == 200.0
    assert om.open_position.call_count == 0
    # Round-trip from DB
    rows = wh.query("SELECT * FROM shadow_decisions WHERE agent_id=?", ("ScalpAgent",))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT:USDT"


def test_execution_agent_projects_higher_pnl_at_higher_notional(wh):
    from core.agents.execution_agent import ExecutionAgent
    ea = ExecutionAgent(warehouse=wh)
    row = ea.execute_shadow(
        _proposal("buy"),
        projected_notional_current=8.0,
        projected_notional_alt=200.0,
    )
    # Both projected at TP, so projected_pnl (alt $200) should dwarf sim_pnl (cur $8)
    assert abs(row["projected_pnl"]) > abs(row["sim_pnl"])


def test_execution_agent_logs_veto(wh):
    from core.agents.execution_agent import ExecutionAgent
    ea = ExecutionAgent(warehouse=wh)
    ea.log_veto(_proposal("buy"), vetoed_by="RiskAgent", reason="halt_active")
    rows = wh.query(
        "SELECT * FROM shadow_decisions WHERE decision='VETO' AND vetoed_by=?",
        ("RiskAgent",),
    )
    assert len(rows) == 1
    assert rows[0]["veto_reason"] == "halt_active"


def test_execution_agent_short_pnl_sign(wh):
    """Short closing at TP (below entry) should yield positive PnL."""
    from core.agents.execution_agent import ExecutionAgent
    ea = ExecutionAgent(warehouse=wh)
    row = ea.execute_shadow(
        _proposal("sell"),
        projected_notional_current=8.0,
        projected_notional_alt=200.0,
    )
    assert row["projected_pnl"] > 0
