"""Task A.6 — RiskAgent veto."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


def _proposal(side="buy", sym="BTC/USDT:USDT"):
    from core.agents.base_agent import Proposal
    if side == "buy":
        return Proposal(
            agent_id="X", symbol=sym, side="buy",
            entry=100, sl=98, tp=104, confidence=0.6, reason="t",
        )
    return Proposal(
        agent_id="X", symbol=sym, side="sell",
        entry=100, sl=102, tp=96, confidence=0.6, reason="t",
    )


@pytest.fixture
def risk_state(tmp_path):
    p = tmp_path / "risk_state.json"
    p.write_text(json.dumps({"is_halted": False, "halt_reason": ""}), encoding="utf-8")
    return p


def test_risk_agent_approves_valid_long(risk_state):
    from core.agents.risk_agent import RiskAgent
    ra = RiskAgent(risk_state_path=risk_state)
    res = ra.review(_proposal("buy"), blacklist=set(), allowed_hours=set(range(24)))
    assert res.approved


def test_risk_agent_vetoes_when_halted(tmp_path):
    p = tmp_path / "risk_state.json"
    p.write_text(json.dumps({"is_halted": True, "halt_reason": "spec12_global_loss_streak"}),
                 encoding="utf-8")
    from core.agents.risk_agent import RiskAgent
    ra = RiskAgent(risk_state_path=p)
    res = ra.review(_proposal("buy"), blacklist=set(), allowed_hours=set(range(24)))
    assert not res.approved
    assert "halt_active" in res.reason


def test_risk_agent_vetoes_blacklisted_symbol(risk_state):
    from core.agents.risk_agent import RiskAgent
    ra = RiskAgent(risk_state_path=risk_state)
    res = ra.review(
        _proposal("buy", "ETH/USDT:USDT"),
        blacklist={"ETH/USDT:USDT"},
        allowed_hours=set(range(24)),
    )
    assert not res.approved
    assert "blacklist" in res.reason


def test_risk_agent_vetoes_blocked_hour(risk_state):
    from core.agents.risk_agent import RiskAgent
    ra = RiskAgent(risk_state_path=risk_state)
    # Build proposal at hour 17 UTC explicitly
    ts = int(datetime(2026, 5, 2, 17, 0, 0, tzinfo=timezone.utc).timestamp())
    p = _proposal("buy")
    p.ts = ts
    res = ra.review(p, blacklist=set(), allowed_hours={2, 3, 14, 15, 16})
    assert not res.approved
    assert "hour_blocked:17" in res.reason


def test_risk_agent_vetoes_invalid_long_levels(risk_state):
    """Long with SL above entry → invalid."""
    from core.agents.base_agent import Proposal
    from core.agents.risk_agent import RiskAgent
    ra = RiskAgent(risk_state_path=risk_state)
    bad = Proposal(
        agent_id="X", symbol="BTC/USDT:USDT", side="buy",
        entry=100, sl=102, tp=104, confidence=0.6, reason="t",
    )
    res = ra.review(bad, blacklist=set(), allowed_hours=set(range(24)))
    assert not res.approved
    assert "invalid_long_levels" in res.reason
