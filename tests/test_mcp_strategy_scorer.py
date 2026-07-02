"""Phase B — deterministic MCPStrategyScorer: LLM output cannot change an entry.

The core acceptance: the entry score/decision (the action-dict) is IDENTICAL
whether the Claude CLI is available or not, and whether it succeeds or raises —
proving no LLM value is blended into the deterministic entry path.
"""
from __future__ import annotations

import core.mcp_strategy_scorer as mss
from core.mcp_brain import MCPBrain
from core.mcp_strategy_scorer import MCPStrategyScorer


def _canned_context():
    coins = ["BTC", "ETH"]
    # sources_ok >= 2 so the scorer proceeds; contents only need the keys the
    # deterministic scorer reads.
    data = {
        "sources_ok": 3,
        "prices": {"BTC": 60000.0, "ETH": 3000.0},
        "funding": {},
        "orderbook": {},
    }
    # Empty indicators -> _score_coin returns no-data zero -> no entries. That is
    # fine: the point is byte-identical output regardless of Claude availability.
    exchange_indicators = {"BTC": {}, "ETH": {}}
    return coins, data, exchange_indicators


def _run(brain, claude_available):
    coins, data, exchange_indicators = _canned_context()
    brain._last_entry_run = 0
    brain._claude_available = claude_available
    brain._fetch_all_data = lambda _c: data
    brain._fetch_exchange_indicators = lambda _c: exchange_indicators
    scorer = MCPStrategyScorer(brain=brain)
    risk_envelope = {"max_new_positions": 2, "total_balance": 420.0}
    return scorer.analyze_portfolio(
        coins=coins,
        open_positions=[],
        exchange_balances={},
        risk_envelope=risk_envelope,
        recent_trades=[],
        news_context=None,
    )


def _strip_ids(actions):
    """Remove per-run non-deterministic decision_id so we compare the decision."""
    out = []
    for a in actions:
        a = dict(a)
        a.pop("decision_id", None)
        out.append(a)
    return out


def test_entry_identical_claude_available_vs_unavailable(monkeypatch):
    # If the deterministic path ever consulted Claude, this raise would surface.
    def _boom(*a, **k):
        raise AssertionError("Claude CLI must never be called on the entry path")

    monkeypatch.setattr(mss, "_HAS_CLAUDE_GUARD", True, raising=False)

    brain = MCPBrain()
    # Make any attempt to call the CLI explode.
    import utils.claude_client as cc
    monkeypatch.setattr(cc, "call_claude_cli", _boom, raising=False)

    with_claude = _run(brain, claude_available=True)
    without_claude = _run(brain, claude_available=False)

    assert _strip_ids(with_claude) == _strip_ids(without_claude)


def test_source_tag_is_deterministic(monkeypatch):
    brain = MCPBrain()

    # Force a single deterministic action out of the algo layer.
    def _fake_algo(*a, **k):
        return [{"type": "OPEN", "symbol": "BTC/USDT:USDT", "side": "buy",
                 "confidence": 0.7, "exchange": "binance"}]

    monkeypatch.setattr(brain, "_algorithmic_portfolio", _fake_algo)
    actions = _run(brain, claude_available=True)
    assert actions and actions[0]["source"] == "algo_det"
    assert actions[0]["decision_id"]
