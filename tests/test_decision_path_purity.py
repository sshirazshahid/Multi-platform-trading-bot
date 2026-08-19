"""Decision-path purity: no LLM/sentiment on the trade path (De-Emotion Phase 1)."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

DECISION_PATH_MODULES = (
    "core/mcp_brain.py",
    "core/bot_engine.py",
    "core/mcp_strategy_scorer.py",
    "core/machine_signal.py",
    "core/tsmom_signal.py",
    "core/order_manager.py",
    "core/direct_executor.py",
)

FORBIDDEN_IMPORT_ROOTS = (
    "utils.claude_client",
    "anthropic",
    "core.claude_advisor",
    "core.news_scanner",
    "core.data_feeds.news_sentiment_feed",
    "core.data_feeds.twitter_feed",
    "core.regime_short_bias",  # prereg-61 log-only; never on entry path
    "core.whale_events",  # whale harvest log-only; never on entry path
)

FORBIDDEN_SUBSTRINGS = (
    "alternative.me",
    "cryptocompare.com",
    "fearandgreed",
    "/fng/",
)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_decision_path_has_no_forbidden_imports():
    for rel in DECISION_PATH_MODULES:
        path = ROOT / rel
        assert path.is_file(), f"missing {rel}"
        imports = _module_imports(path)
        for bad in FORBIDDEN_IMPORT_ROOTS:
            offenders = [i for i in imports if i == bad or i.startswith(bad + ".")]
            assert not offenders, f"{rel} imports forbidden {offenders}"


def test_decision_path_has_no_sentiment_host_strings():
    for rel in DECISION_PATH_MODULES:
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            assert needle not in text, f"{rel} still contains {needle!r}"


def test_sources_ok_abort_uses_remaining_feeds_only():
    """After news/fng removal, blackout still fails closed; two healthy sources pass."""
    from core.mcp_brain import MCPBrain

    brain = MCPBrain.__new__(MCPBrain)
    brain._data_coordinator = None
    brain._enabled = True
    brain._last_entry_run = 0.0
    brain._accuracy = MagicMock()
    brain._save_state = MagicMock()
    brain._log_decisions = MagicMock()

    healthy = {
        "crypto": {"BTC": {"price": 1.0}},
        "gecko": {"BTC": {}},
        "funding": {},
        "orderbook": {},
        "oi": {},
        "news": [],
        "fng": {},
        "technicals": {},
        "prices": {"BTC": 1.0},
        "sources_ok": 2,
    }
    blackout = {**healthy, "crypto": {}, "gecko": {}, "sources_ok": 0}

    with patch.object(MCPBrain, "_fetch_all_data", return_value=blackout):
        with patch.object(MCPBrain, "_fetch_exchange_indicators", return_value={}):
            out = MCPBrain.analyze_portfolio(
                brain,
                coins=["BTC"],
                open_positions=[],
                exchange_balances={},
                risk_envelope={"max_new_positions": 1, "total_balance": 1000},
                recent_trades=[],
            )
    assert out == []

    brain._last_entry_run = 0.0
    with patch.object(MCPBrain, "_fetch_all_data", return_value=healthy):
        with patch.object(MCPBrain, "_fetch_exchange_indicators", return_value={"BTC": {}}):
            with patch.object(
                MCPBrain, "_algorithmic_portfolio",
                return_value=[{"type": "OPEN", "symbol": "BTC/USDT", "side": "buy", "confidence": 0.7}],
            ) as algo:
                out = MCPBrain.analyze_portfolio(
                    brain,
                    coins=["BTC"],
                    open_positions=[],
                    exchange_balances={},
                    risk_envelope={"max_new_positions": 1, "total_balance": 1000},
                    recent_trades=[],
                )
    assert out and out[0]["type"] == "OPEN" and out[0].get("source") == "algo"
    algo.assert_called_once()


def test_score_and_monitor_run_with_network_stubs_raising():
    """Algo path must not touch network when data is already in-memory."""
    from core.mcp_brain import MCPBrain

    def _boom(*_a, **_k):
        raise AssertionError("network must not be called on pure algo path")

    brain = MCPBrain()
    brain._last_entry_run = 0.0
    brain._last_position_run = 0.0

    ei = {
        "4h": {
            "adx": 25,
            "rsi": 50,
            "ema20_above_50": True,
            "ema_gap_pct": 0.5,
            "bb_width": 2.0,
            "atr_pct": 1.5,
            "ema20_slope": 0.1,
            "macd_hist": 0.5,
            "macd_hist_prev": 0.3,
            "swing_hh_hl": True,
            "vol_ratio": 1.5,
        },
        "1h": {
            "adx": 22,
            "rsi": 48,
            "ema20_above_50": True,
            "atr_pct": 1.2,
            "price_vs_vwap": 0.1,
            "vol_ratio": 1.0,
            "macd_hist": 0.0,
            "macd_hist_prev": -0.1,
            "ema20_slope": 0.05,
        },
        "15m": {
            "ema_cross_up": True,
            "ema20_above_50": True,
        },
    }
    data = {
        "crypto": {"BTC": {"price": 100.0}},
        "gecko": {},
        "funding": {"BTC": {"rate": 0.0001}},
        "orderbook": {"BTC": {"bid_depth": 1000, "ask_depth": 1000}},
        "oi": {},
        "technicals": {},
        "prices": {"BTC": 100.0},
        "sources_ok": 3,
    }

    with patch("urllib.request.urlopen", side_effect=_boom):
        result = brain._score_coin("BTC", data, ei)
        assert isinstance(result, dict)
        assert "score" in result

        actions = brain._algorithmic_portfolio(
            coins=["BTC"],
            data=data,
            exchange_indicators={"BTC": ei},
            open_positions=[],
            exchange_balances={"binance": 1000},
            risk_envelope={"max_new_positions": 2, "total_balance": 1000},
        )
        assert isinstance(actions, list)

        advice = brain._algorithmic_position_monitor(
            positions=[],
            data=data,
            exchange_indicators={"BTC": ei},
        )
        assert isinstance(advice, dict)
