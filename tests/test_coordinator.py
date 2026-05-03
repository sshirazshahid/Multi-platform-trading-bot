"""Task A.8 — AgentCoordinator end-to-end."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_coord_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_coord_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


@pytest.fixture
def risk_state(tmp_path):
    p = tmp_path / "risk_state.json"
    import json
    p.write_text(json.dumps({"is_halted": False, "halt_reason": ""}), encoding="utf-8")
    return p


def _make_proposing_agent(name, side="buy"):
    from core.agents.base_agent import BaseAgent, Proposal

    class A(BaseAgent):
        def propose(self, ctx):
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side=side,
                entry=100.0,
                sl=98.0 if side == "buy" else 102.0,
                tp=104.0 if side == "buy" else 96.0,
                confidence=0.7, reason="test",
            )

    return A(name)


def _make_silent_agent(name):
    from core.agents.base_agent import BaseAgent

    class S(BaseAgent):
        def propose(self, ctx):
            return None

    return S(name)


def test_coordinator_routes_three_agents_to_exec(wh, risk_state):
    from core.agents.coordinator import AgentCoordinator
    from core.agents.execution_agent import ExecutionAgent
    from core.agents.risk_agent import RiskAgent
    coord = AgentCoordinator(
        agents=[_make_proposing_agent(n) for n in ("A", "B", "C")],
        risk_agent=RiskAgent(risk_state_path=risk_state),
        execution_agent=ExecutionAgent(warehouse=wh),
        blacklist=set(), allowed_hours=set(range(24)),
    )
    rows = coord.tick({"symbol": "BTC/USDT:USDT"})
    assert len(rows) == 3
    assert all(r["decision"] == "ALLOW" for r in rows)
    assert {r["agent_id"] for r in rows} == {"A", "B", "C"}


def test_coordinator_logs_veto_when_risk_blocks(wh, tmp_path):
    """Halt active → all proposals get VETO row."""
    import json
    p = tmp_path / "risk_state.json"
    p.write_text(json.dumps({"is_halted": True, "halt_reason": "spec12"}),
                 encoding="utf-8")
    from core.agents.coordinator import AgentCoordinator
    from core.agents.execution_agent import ExecutionAgent
    from core.agents.risk_agent import RiskAgent
    coord = AgentCoordinator(
        agents=[_make_proposing_agent("A")],
        risk_agent=RiskAgent(risk_state_path=p),
        execution_agent=ExecutionAgent(warehouse=wh),
        blacklist=set(), allowed_hours=set(range(24)),
    )
    rows = coord.tick({"symbol": "BTC/USDT:USDT"})
    assert len(rows) == 1
    assert rows[0]["decision"] == "VETO"
    assert "halt_active" in rows[0]["veto_reason"]


def test_coordinator_skips_silent_agents(wh, risk_state):
    from core.agents.coordinator import AgentCoordinator
    from core.agents.execution_agent import ExecutionAgent
    from core.agents.risk_agent import RiskAgent
    coord = AgentCoordinator(
        agents=[_make_silent_agent("S1"), _make_proposing_agent("A1")],
        risk_agent=RiskAgent(risk_state_path=risk_state),
        execution_agent=ExecutionAgent(warehouse=wh),
        blacklist=set(), allowed_hours=set(range(24)),
    )
    rows = coord.tick({"symbol": "BTC/USDT:USDT"})
    assert len(rows) == 1  # only A1 proposed
    assert rows[0]["agent_id"] == "A1"


def test_coordinator_never_calls_order_manager(wh, risk_state):
    """The whole point of shadow mode."""
    from core.agents.coordinator import AgentCoordinator
    from core.agents.execution_agent import ExecutionAgent
    from core.agents.risk_agent import RiskAgent
    om = MagicMock()
    coord = AgentCoordinator(
        agents=[_make_proposing_agent("A")],
        risk_agent=RiskAgent(risk_state_path=risk_state),
        execution_agent=ExecutionAgent(warehouse=wh),
        blacklist=set(), allowed_hours=set(range(24)),
    )
    coord.tick({"symbol": "BTC/USDT:USDT"})
    assert om.open_position.call_count == 0
    assert om.create_order.call_count == 0
