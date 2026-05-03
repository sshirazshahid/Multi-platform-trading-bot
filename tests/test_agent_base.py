"""Task A.2 — BaseAgent + Proposal dataclass."""
from __future__ import annotations

import pytest


def test_proposal_dataclass_fields():
    from core.agents.base_agent import Proposal
    p = Proposal(
        agent_id="ScalpAgent", symbol="BTC/USDT:USDT", side="buy",
        entry=70000.0, sl=69000.0, tp=72000.0,
        confidence=0.7, reason="5m breakout",
    )
    assert p.agent_id == "ScalpAgent"
    assert p.side == "buy"
    assert 0 <= p.confidence <= 1
    assert p.proposal_id.startswith("p-")
    assert p.ts > 0


def test_baseagent_subclass_must_implement_propose():
    from core.agents.base_agent import BaseAgent

    class Incomplete(BaseAgent):
        pass

    with pytest.raises(TypeError):
        Incomplete("bad")


def test_baseagent_subclass_with_propose_works():
    from core.agents.base_agent import BaseAgent, Proposal

    class Demo(BaseAgent):
        def propose(self, ctx):
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="buy",
                entry=100, sl=98, tp=104, confidence=0.6, reason="demo",
            )

    a = Demo("DemoAgent")
    p = a.propose({"symbol": "BTC/USDT:USDT"})
    assert p.agent_id == "DemoAgent"


def test_baseagent_on_outcome_default_noop():
    from core.agents.base_agent import BaseAgent, Proposal

    class Demo(BaseAgent):
        def propose(self, ctx):
            return None

    a = Demo("X")
    a.on_outcome(Proposal(
        agent_id="X", symbol="BTC", side="buy",
        entry=1, sl=1, tp=1, confidence=1, reason="x"
    ), 0.0)
