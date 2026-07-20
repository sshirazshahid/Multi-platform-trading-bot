"""Executable MCP scorer universe must be explicit and fail closed."""

from __future__ import annotations

from core.mcp_strategy_scorer import MCPStrategyScorer
from core.strategy_spec import StrategySpec, load_all_specs, save_spec


class _Brain:
    def __init__(self, actions):
        self._enabled = True
        self._last_entry_run = 0.0
        self._last_trade_actions = []
        self.actions = [dict(action) for action in actions]
        self.scored_coins = None
        self.logged = None

    def _fetch_all_data(self, coins):
        self.scored_coins = list(coins)
        return {"sources_ok": 3, "prices": {}, "funding": {}, "orderbook": {}}

    def _fetch_exchange_indicators(self, coins):
        return {coin: {} for coin in coins}

    def _algorithmic_portfolio(self, coins, *_args):
        self.scored_coins = list(coins)
        return [dict(action) for action in self.actions]

    def _log_decisions(self, payload, _kind):
        self.logged = payload

    def _save_state(self):
        return None


def _open(symbol, *, exchange="binance", market_type="futures"):
    return {
        "type": "OPEN",
        "symbol": symbol,
        "side": "buy",
        "exchange": exchange,
        "market_type": market_type,
        "confidence": 0.7,
    }


def _active_spec(*, symbols=None, venues=None, version="directional-research-v1"):
    return StrategySpec(
        id="MCP_DIRECTIONAL_PAPER",
        strategy_version=version,
        family="algorithmic_scorer_directional",
        market_type="futures",
        venues=list(venues or ["binance"]),
        symbols=list(symbols or ["BTC/USDT"]),
        promotion_status="active-paper",
    )


def _run(specs, actions=None):
    proposed = actions or [
        _open("BTC/USDT:USDT"),
        _open("ETH/USDT:USDT"),
        _open("SOL/USDT:USDT", exchange="bybit"),
    ]
    brain = _Brain(proposed)
    scorer = MCPStrategyScorer(brain=brain, specs=specs)
    result = scorer.analyze_portfolio(
        coins=["BTC", "ETH", "SOL"],
        open_positions=[],
        exchange_balances={},
        risk_envelope={"max_new_positions": 3},
        recent_trades=[],
    )
    return result, scorer, brain


def test_empty_specs_block_all_opens_but_keep_research_scan():
    result, scorer, brain = _run([])

    assert result == []
    assert brain.scored_coins == ["BTC", "ETH", "SOL"]
    assert len(scorer._last_research_actions) == 3


def test_no_active_paper_futures_spec_blocks_opens_but_scans():
    research = StrategySpec(
        id="RESEARCH_ONLY",
        strategy_version="research-v1",
        market_type="futures",
        venues=["binance"],
        symbols=["BTC/USDT", "ETH/USDT"],
        promotion_status="research",
    )

    result, scorer, brain = _run([research])

    assert result == []
    assert brain.scored_coins == ["BTC", "ETH", "SOL"]
    assert len(scorer._last_research_actions) == 3


def test_parse_error_blocks_execution_even_with_an_active_valid_spec(tmp_path):
    spec_dir = tmp_path / "specs"
    save_spec(_active_spec(symbols=["BTC/USDT"]), directory=spec_dir)
    (spec_dir / "BROKEN.json").write_text("{not-json", encoding="utf-8")
    loaded = load_all_specs(directory=spec_dir)

    result, scorer, brain = _run(loaded)

    assert len(loaded) == 1
    assert loaded.errors
    assert result == []
    assert scorer.spec_load_errors
    assert brain.scored_coins == ["BTC", "ETH", "SOL"]


def test_explicit_active_versioned_universe_allows_only_declared_routes():
    spec = _active_spec(symbols=["BTC/USDT", "SOL/USDT"], venues=["binance"])
    actions = [
        _open("BTC/USDT:USDT", exchange="binance"),
        _open("ETH/USDT:USDT", exchange="binance"),
        _open("SOL/USDT:USDT", exchange="bybit"),
        _open("BTC/USDT", exchange="binance", market_type="spot"),
    ]

    result, scorer, brain = _run([spec], actions=actions)

    assert [action["symbol"] for action in result] == ["BTC/USDT:USDT"]
    assert result[0]["source"] == "algo_det"
    assert result[0]["decision_id"]
    assert brain.scored_coins == ["BTC", "ETH", "SOL"]
    assert len(scorer._last_research_actions) == 4


def test_active_futures_spec_without_version_fails_closed():
    result, scorer, brain = _run([_active_spec(version="")])

    assert result == []
    assert brain.scored_coins == ["BTC", "ETH", "SOL"]
    assert len(scorer._last_research_actions) == 3


def test_blocked_open_warning_records_the_blocked_routes():
    """The gate must say WHICH routes it blocked (BZ score=80 confusion,
    2026-07-20): blocked base@venue pairs are recorded on the scorer."""
    spec = _active_spec(symbols=["BTC/USDT"], venues=["binance"])
    actions = [
        _open("BTC/USDT:USDT", exchange="binance"),
        _open("BZ/USDT:USDT", exchange="binance"),
        _open("CL/USDT:USDT", exchange="bybit"),
    ]

    result, scorer, _ = _run([spec], actions=actions)

    assert [action["symbol"] for action in result] == ["BTC/USDT:USDT"]
    assert scorer._last_blocked_open_routes == ["BZ@binance", "CL@bybit"]


def test_non_open_research_action_is_not_suppressed_by_entry_spec_gate():
    close = {"type": "CLOSE", "symbol": "BTC/USDT:USDT", "position_id": "p1"}

    result, _, _ = _run([], actions=[close])

    assert len(result) == 1
    assert result[0]["type"] == "CLOSE"
    assert result[0]["position_id"] == "p1"
    assert result[0]["source"] == "algo_det"
    assert result[0]["decision_id"]
