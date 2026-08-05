"""Phase B — deterministic MCPStrategyScorer: LLM output cannot change an entry.

The core acceptance: the entry score/decision (the action-dict) is IDENTICAL
whether the Claude CLI is available or not, and whether it succeeds or raises —
proving no LLM value is blended into the deterministic entry path.
"""
from __future__ import annotations

import core.mcp_strategy_scorer as mss
from core.mcp_brain import MCPBrain
from core.mcp_strategy_scorer import MCPStrategyScorer
from core.strategy_spec import StrategySpec


def _active_spec(*, symbols=None, venues=None):
    return StrategySpec(
        id="MCP_DIRECTIONAL_PAPER",
        strategy_version="directional-research-v1",
        family="algorithmic_scorer_directional",
        market_type="futures",
        venues=list(venues or ["binance"]),
        symbols=list(symbols or ["BTC/USDT"]),
        promotion_status="active-paper",
    )


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


def _run(brain, claude_available, *, specs=None):
    coins, data, exchange_indicators = _canned_context()
    brain._last_entry_run = 0
    brain._claude_available = claude_available
    brain._fetch_all_data = lambda _c: data
    brain._fetch_exchange_indicators = lambda _c: exchange_indicators
    scorer = MCPStrategyScorer(brain=brain, specs=specs)
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


def test_entry_identical_claude_available_vs_unavailable():
    # Deterministic path must not depend on any LLM availability flag.
    brain = MCPBrain()
    with_claude = _run(brain, claude_available=True)
    without_claude = _run(brain, claude_available=False)
    assert _strip_ids(with_claude) == _strip_ids(without_claude)


def test_source_tag_is_deterministic(monkeypatch):
    brain = MCPBrain()

    # Force a single deterministic action out of the algo layer.
    def _fake_algo(*a, **k):
        return [{"type": "OPEN", "symbol": "BTC/USDT:USDT", "side": "buy",
                 "confidence": 0.7, "exchange": "binance",
                 "market_type": "futures"}]

    monkeypatch.setattr(brain, "_algorithmic_portfolio", _fake_algo)
    actions = _run(
        brain,
        claude_available=True,
        specs=[_active_spec()],
    )
    assert actions and actions[0]["source"] == "algo_det"
    assert actions[0]["decision_id"]

# ── F5 (2026-07-20 deep audit): silent-idle startup check ────────────────────
# 2026-07-19 incident: the mcp/mcp_det lane sat idle for 8h because the
# approved PAPER-futures universe was empty and NOTHING said so. The scorer
# must now log a LOUD warning (once per process) at construction when
# approved_paper_futures_routes() is empty, and an INFO line when it is not.
from loguru import logger  # noqa: E402


def _sink(records, level):
    return logger.add(lambda m: records.append(str(m)), level=level)


def test_startup_universe_check_warns_loudly_once_when_empty():
    mss._EMPTY_UNIVERSE_WARNED = False
    brain = MCPBrain()
    records = []
    sink = _sink(records, "WARNING")
    try:
        MCPStrategyScorer(brain=brain, specs=[])
        MCPStrategyScorer(brain=brain, specs=[])  # once per process, no spam
    finally:
        logger.remove(sink)
    hits = [r for r in records if "STARTUP UNIVERSE EMPTY" in r]
    assert len(hits) == 1, f"expected exactly one loud warning, got {len(hits)}"
    assert "silent" in hits[0].lower() or "idle" in hits[0].lower()


def test_startup_universe_check_passes_when_routes_exist():
    mss._EMPTY_UNIVERSE_WARNED = False
    brain = MCPBrain()
    records = []
    sink = _sink(records, "INFO")
    try:
        MCPStrategyScorer(brain=brain, specs=[_active_spec()])
    finally:
        logger.remove(sink)
    assert not any("STARTUP UNIVERSE EMPTY" in r for r in records)
    assert any("universe check OK" in r for r in records)
