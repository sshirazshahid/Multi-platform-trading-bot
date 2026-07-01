"""
Tests for core/prediction_agent.py, core/prediction_prompt.py, and
core/prediction_tracker.py.

Tests are structured to validate:
  1. PredictionResult validation (strict schema enforcement)
  2. Cache behavior (TTL, eviction, invalidation)
  3. Prompt construction (all sections, token efficiency)
  4. Integration modes (VETO, ADVISOR, FULL)
  5. Auto-disable logic
  6. Cost estimation
  7. Tracker accuracy computation
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.prediction_agent import (
    PredictionAgent,
    PredictionResult,
    _estimate_cost,
    _PredictionCache,
    _validate_prediction,
)
from core.prediction_prompt import build_prediction_prompt
from core.prediction_tracker import PredictionTracker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_prediction_dict():
    """A valid prediction response from Claude."""
    return {
        "prediction": "CONFIRM",
        "confidence": 0.82,
        "reasoning": "Strong 4h uptrend with increasing volume and bullish OB imbalance.",
        "predicted_direction": "long",
        "predicted_move_pct": 2.1,
        "predicted_hold_minutes": 90,
        "risk_flags": ["high_funding_rate"],
        "recommended_adjustments": {
            "sl_pct": 1.8,
            "tp_pct": 4.0,
            "size_multiplier": 1.1,
        },
    }


@pytest.fixture
def sample_action():
    """A sample MCP Brain OPEN action."""
    return {
        "type": "OPEN",
        "symbol": "ATOM/USDT",
        "exchange": "bybit",
        "market_type": "futures",
        "side": "buy",
        "leverage": 2,
        "size_pct": 5.0,
        "sl_pct": 1.5,
        "tp_pct": 3.0,
        "confidence": 0.75,
        "mcp_score": 72,
        "reason": "ALGO score=72 layers=6/7 p_ens=0.580 all_required_passed",
        "candidate_id": 1234,
    }


@pytest.fixture
def sample_context():
    """A minimal context dict for prompt building."""
    return {
        "symbol": "ATOM/USDT",
        "side": "buy",
        "mcp_score": 72,
        "mcp_reason": "ALGO score=72 layers=6/7",
        "confidence": 0.75,
        "sl_pct": 1.5,
        "tp_pct": 3.0,
        "leverage": 2,
        "mcp_breakdown": None,
        "candles_1h": [
            {"o": 10.1, "h": 10.3, "l": 10.0, "c": 10.2, "v": 1000}
            for _ in range(20)
        ],
        "candles_4h": [
            {"o": 10.0, "h": 10.5, "l": 9.8, "c": 10.3, "v": 5000}
            for _ in range(20)
        ],
        "funding": {"rate": 0.0001, "predicted_rate": 0.00012},
        "open_interest": {"oi_value": 50000000, "oi_change_pct_1h": 2.5},
        "orderbook": {
            "bid_depth_10": 100000,
            "ask_depth_10": 80000,
            "imbalance": 0.11,
            "spread_pct": 0.02,
        },
        "news": ["ATOM governance proposal approved", "Cosmos ecosystem TVL up 15%"],
        "symbol_history": {
            "n": 12,
            "win_rate": 0.58,
            "mean": 0.45,
            "sum": 5.4,
            "avg_r_multiple": 1.8,
            "avg_hold_min": 75,
        },
        "portfolio_state": {
            "total_open_positions": 2,
            "correlated_positions": [],
            "same_symbol_open": False,
        },
    }


# ---------------------------------------------------------------------------
# Tests: _validate_prediction
# ---------------------------------------------------------------------------

class TestValidatePrediction:
    def test_valid_confirm(self, valid_prediction_dict):
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert result.prediction == "CONFIRM"
        assert result.confidence == 0.82
        assert result.predicted_direction == "long"
        assert len(result.risk_flags) == 1

    def test_valid_veto(self, valid_prediction_dict):
        valid_prediction_dict["prediction"] = "VETO"
        valid_prediction_dict["predicted_direction"] = "short"
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert result.prediction == "VETO"

    def test_valid_weak_confirm(self, valid_prediction_dict):
        valid_prediction_dict["prediction"] = "WEAK_CONFIRM"
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert result.prediction == "WEAK_CONFIRM"

    def test_invalid_prediction_enum(self, valid_prediction_dict):
        valid_prediction_dict["prediction"] = "MAYBE"
        result = _validate_prediction(valid_prediction_dict)
        assert result is None

    def test_invalid_direction_enum(self, valid_prediction_dict):
        valid_prediction_dict["predicted_direction"] = "sideways"
        result = _validate_prediction(valid_prediction_dict)
        assert result is None

    def test_confidence_out_of_range(self, valid_prediction_dict):
        valid_prediction_dict["confidence"] = 1.5
        result = _validate_prediction(valid_prediction_dict)
        assert result is None

    def test_confidence_negative(self, valid_prediction_dict):
        valid_prediction_dict["confidence"] = -0.1
        result = _validate_prediction(valid_prediction_dict)
        assert result is None

    def test_missing_required_key(self, valid_prediction_dict):
        del valid_prediction_dict["reasoning"]
        result = _validate_prediction(valid_prediction_dict)
        assert result is None

    def test_non_dict_input(self):
        assert _validate_prediction("not a dict") is None
        assert _validate_prediction(42) is None
        assert _validate_prediction(None) is None

    def test_size_multiplier_clamped(self, valid_prediction_dict):
        valid_prediction_dict["recommended_adjustments"]["size_multiplier"] = 5.0
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert result.recommended_adjustments["size_multiplier"] == 1.5

    def test_size_multiplier_clamped_low(self, valid_prediction_dict):
        valid_prediction_dict["recommended_adjustments"]["size_multiplier"] = 0.1
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert result.recommended_adjustments["size_multiplier"] == 0.3

    def test_unknown_adjustment_keys_stripped(self, valid_prediction_dict):
        valid_prediction_dict["recommended_adjustments"]["leverage"] = 10
        valid_prediction_dict["recommended_adjustments"]["override_stop"] = True
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert "leverage" not in result.recommended_adjustments
        assert "override_stop" not in result.recommended_adjustments

    def test_risk_flags_capped_at_16(self, valid_prediction_dict):
        valid_prediction_dict["risk_flags"] = [f"flag_{i}" for i in range(30)]
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert len(result.risk_flags) == 16

    def test_reasoning_truncated(self, valid_prediction_dict):
        valid_prediction_dict["reasoning"] = "x" * 5000
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert len(result.reasoning) == 2000

    def test_numeric_coercion(self, valid_prediction_dict):
        valid_prediction_dict["predicted_move_pct"] = "2.5"
        valid_prediction_dict["predicted_hold_minutes"] = "60"
        result = _validate_prediction(valid_prediction_dict)
        assert result is not None
        assert result.predicted_move_pct == 2.5
        assert result.predicted_hold_minutes == 60


# ---------------------------------------------------------------------------
# Tests: _PredictionCache
# ---------------------------------------------------------------------------

class TestPredictionCache:
    def test_put_and_get(self, valid_prediction_dict):
        cache = _PredictionCache(ttl_sec=60)
        result = _validate_prediction(valid_prediction_dict)
        cache.put("ATOM:buy:abc123", result)
        cached = cache.get("ATOM:buy:abc123")
        assert cached is not None
        assert cached.prediction == "CONFIRM"

    def test_miss(self):
        cache = _PredictionCache(ttl_sec=60)
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self, valid_prediction_dict):
        cache = _PredictionCache(ttl_sec=1)
        result = _validate_prediction(valid_prediction_dict)
        cache.put("ATOM:buy:abc123", result)

        # Force expiry
        cache._store["ATOM:buy:abc123"] = (time.time() - 2, result)
        assert cache.get("ATOM:buy:abc123") is None

    def test_invalidate_all(self, valid_prediction_dict):
        cache = _PredictionCache(ttl_sec=60)
        result = _validate_prediction(valid_prediction_dict)
        cache.put("ATOM:buy:abc", result)
        cache.put("BTC:sell:def", result)
        cache.invalidate()
        assert cache.get("ATOM:buy:abc") is None
        assert cache.get("BTC:sell:def") is None

    def test_invalidate_by_symbol(self, valid_prediction_dict):
        cache = _PredictionCache(ttl_sec=60)
        result = _validate_prediction(valid_prediction_dict)
        cache.put("ATOM/USDT:buy:abc", result)
        cache.put("BTC/USDT:sell:def", result)
        cache.invalidate("ATOM/USDT")
        assert cache.get("ATOM/USDT:buy:abc") is None
        assert cache.get("BTC/USDT:sell:def") is not None


# ---------------------------------------------------------------------------
# Tests: Prompt building
# ---------------------------------------------------------------------------

class TestPromptBuilding:
    def test_produces_two_strings(self, sample_context):
        system, user = build_prediction_prompt(sample_context)
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert len(system) > 100
        assert len(user) > 100

    def test_system_prompt_has_schema(self, sample_context):
        system, _ = build_prediction_prompt(sample_context)
        assert "CONFIRM" in system
        assert "VETO" in system
        assert "WEAK_CONFIRM" in system
        assert "risk_flags" in system

    def test_user_prompt_contains_all_sections(self, sample_context):
        _, user = build_prediction_prompt(sample_context)
        assert "TRADE CANDIDATE" in user
        assert "PRICE ACTION" in user
        assert "FUNDING" in user
        assert "ORDER BOOK" in user
        assert "RECENT NEWS" in user
        assert "HISTORICAL PERFORMANCE" in user
        assert "PORTFOLIO STATE" in user
        assert "PRE-MORTEM" in user
        assert "YOUR TASK" in user

    def test_user_prompt_contains_symbol(self, sample_context):
        _, user = build_prediction_prompt(sample_context)
        assert "ATOM/USDT" in user
        assert "BUY" in user

    def test_missing_data_handled_gracefully(self):
        """Prompt should still build even with minimal data."""
        minimal = {
            "symbol": "BTC/USDT",
            "side": "buy",
            "mcp_score": 70,
        }
        system, user = build_prediction_prompt(minimal)
        assert "BTC/USDT" in user
        assert "No data available" in user or "No relevant headlines" in user

    def test_token_budget(self, sample_context):
        """User prompt should stay under ~4000 tokens (~16000 chars)."""
        _, user = build_prediction_prompt(sample_context)
        # Rough estimate: 1 token ~= 4 chars
        tokens_est = len(user) / 4
        assert tokens_est < 4000, f"Prompt too large: ~{tokens_est:.0f} tokens"

    def test_rr_ratio_computed(self, sample_context):
        _, user = build_prediction_prompt(sample_context)
        assert "2.0:1" in user  # 3.0 / 1.5 = 2.0

    def test_news_headlines_included(self, sample_context):
        _, user = build_prediction_prompt(sample_context)
        assert "ATOM governance" in user

    def test_historical_warnings(self):
        """Low WR should trigger warning in prompt."""
        ctx = {
            "symbol": "XRP/USDT",
            "side": "sell",
            "mcp_score": 68,
            "symbol_history": {
                "n": 15,
                "win_rate": 0.30,
                "mean": -0.50,
                "sum": -7.5,
            },
        }
        _, user = build_prediction_prompt(ctx)
        assert "WARNING" in user
        assert "below 40%" in user


# ---------------------------------------------------------------------------
# Tests: PredictionAgent
# ---------------------------------------------------------------------------

class TestPredictionAgent:
    def test_default_mode_advisor(self):
        agent = PredictionAgent(mode="ADVISOR")
        assert agent.mode == "ADVISOR"
        assert agent.is_active

    def test_should_veto_in_veto_mode(self):
        agent = PredictionAgent(mode="VETO")
        result = PredictionResult(
            prediction="VETO", confidence=0.9,
            reasoning="bad trade", predicted_direction="short",
            predicted_move_pct=-2.0, predicted_hold_minutes=30,
        )
        assert agent.should_veto(result) is True

    def test_should_not_veto_confirm(self):
        agent = PredictionAgent(mode="VETO")
        result = PredictionResult(
            prediction="CONFIRM", confidence=0.85,
            reasoning="good trade", predicted_direction="long",
            predicted_move_pct=2.0, predicted_hold_minutes=60,
        )
        assert agent.should_veto(result) is False

    def test_advisor_mode_never_vetoes(self):
        agent = PredictionAgent(mode="ADVISOR")
        result = PredictionResult(
            prediction="VETO", confidence=0.95,
            reasoning="terrible trade", predicted_direction="short",
            predicted_move_pct=-5.0, predicted_hold_minutes=10,
        )
        assert agent.should_veto(result) is False

    def test_get_size_multiplier(self):
        agent = PredictionAgent()
        result = PredictionResult(
            prediction="WEAK_CONFIRM", confidence=0.6,
            reasoning="marginal", predicted_direction="long",
            predicted_move_pct=1.0, predicted_hold_minutes=45,
            recommended_adjustments={"size_multiplier": 0.7},
        )
        assert agent.get_size_multiplier(result) == 0.7

    def test_get_size_multiplier_clamped(self):
        agent = PredictionAgent()
        result = PredictionResult(
            prediction="CONFIRM", confidence=0.9,
            reasoning="great", predicted_direction="long",
            predicted_move_pct=3.0, predicted_hold_minutes=60,
            recommended_adjustments={"size_multiplier": 5.0},
        )
        assert agent.get_size_multiplier(result) == 1.5

    def test_get_size_multiplier_default(self):
        agent = PredictionAgent()
        result = PredictionResult(
            prediction="CONFIRM", confidence=0.85,
            reasoning="ok", predicted_direction="long",
            predicted_move_pct=2.0, predicted_hold_minutes=60,
        )
        assert agent.get_size_multiplier(result) == 1.0

    def test_disabled_agent_returns_fallback(self):
        agent = PredictionAgent(mode="VETO")
        agent._disabled = True
        agent._disabled_reason = "test"
        result = agent.predict({"symbol": "BTC/USDT", "side": "buy"})
        assert result.prediction == "WEAK_CONFIRM"
        assert "agent_fallback" in result.risk_flags

    def test_force_enable(self):
        agent = PredictionAgent()
        agent._disabled = True
        agent._disabled_reason = "test"
        agent.force_enable()
        assert agent.is_active
        assert agent._disabled_reason == ""

    @patch("utils.claude_client.call_claude_cli")
    def test_predict_with_mock_claude(self, mock_cli, sample_action):
        """Test the full predict() flow with mocked Claude CLI."""
        # Claude CLI returns a JSON envelope; _extract_json_object parses it.
        # Mock the CLI to return a raw JSON string that _extract_json_object
        # can parse directly.
        mock_cli.return_value = json.dumps({
            "prediction": "CONFIRM",
            "confidence": 0.8,
            "reasoning": "Looks good",
            "predicted_direction": "long",
            "predicted_move_pct": 2.0,
            "predicted_hold_minutes": 60,
            "risk_flags": [],
            "recommended_adjustments": {"size_multiplier": 1.0},
        })

        agent = PredictionAgent(mode="VETO", model="haiku")
        result = agent.predict(sample_action)

        assert result.prediction == "CONFIRM"
        assert result.confidence == 0.8
        assert result.model_used == "haiku"
        assert not result.cache_hit


# ---------------------------------------------------------------------------
# Tests: Cost estimation
# ---------------------------------------------------------------------------

class TestCostEstimation:
    def test_haiku_cost(self):
        cost = _estimate_cost("haiku", 2000, 500)
        # 2000 * 0.25/1M + 500 * 1.25/1M = 0.0005 + 0.000625 = 0.001125
        assert 0.001 < cost < 0.002

    def test_sonnet_cost(self):
        cost = _estimate_cost("sonnet", 2000, 500)
        # 2000 * 3.0/1M + 500 * 15.0/1M = 0.006 + 0.0075 = 0.0135
        assert 0.01 < cost < 0.02

    def test_unknown_model_defaults_to_haiku(self):
        cost = _estimate_cost("unknown_model", 2000, 500)
        haiku_cost = _estimate_cost("haiku", 2000, 500)
        assert cost == haiku_cost


# ---------------------------------------------------------------------------
# Tests: PredictionTracker
# ---------------------------------------------------------------------------

class TestPredictionTracker:
    def test_load_empty(self, tmp_path):
        tracker = PredictionTracker(log_path=tmp_path / "nonexistent.jsonl")
        preds = tracker.load_predictions()
        assert preds == []

    def test_load_predictions(self, tmp_path):
        log = tmp_path / "test_log.jsonl"
        entries = [
            {"ts": time.time(), "symbol": "ATOM/USDT", "side": "buy",
             "prediction": "CONFIRM", "confidence": 0.8},
            {"ts": time.time() - 40 * 86400, "symbol": "BTC/USDT", "side": "sell",
             "prediction": "VETO", "confidence": 0.9},  # outside 30d window
        ]
        with log.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        tracker = PredictionTracker(log_path=log)
        preds = tracker.load_predictions(since_days=30)
        assert len(preds) == 1
        assert preds[0]["symbol"] == "ATOM/USDT"

    def test_accuracy_stats_no_data(self, tmp_path):
        tracker = PredictionTracker(log_path=tmp_path / "empty.jsonl")
        stats = tracker.accuracy_stats()
        assert stats is None

    def test_calibration_computation(self):
        """Test that _compute_calibration produces valid bucket structure."""
        tracker = PredictionTracker()
        resolved = [
            {"prediction": "CONFIRM", "confidence": 0.85,
             "_outcome": {"won": True, "realized_pnl": 1.0}},
            {"prediction": "CONFIRM", "confidence": 0.85,
             "_outcome": {"won": True, "realized_pnl": 0.5}},
            {"prediction": "CONFIRM", "confidence": 0.85,
             "_outcome": {"won": False, "realized_pnl": -0.8}},
            {"prediction": "WEAK_CONFIRM", "confidence": 0.55,
             "_outcome": {"won": True, "realized_pnl": 0.3}},
        ]
        cal = tracker._compute_calibration(resolved)
        assert isinstance(cal, list)
        assert len(cal) > 0

        # Find the 0.8-0.9 bucket (should have 3 entries)
        bucket_80 = [b for b in cal if b["confidence_range"] == "0.8-0.9"]
        assert len(bucket_80) == 1
        assert bucket_80[0]["n"] == 3
        assert abs(bucket_80[0]["actual_win_rate"] - 0.6667) < 0.01

    def test_summary_report(self, tmp_path):
        """Summary report should be a non-empty string."""
        tracker = PredictionTracker(log_path=tmp_path / "empty.jsonl")
        report = tracker.summary_report()
        assert "No predictions" in report


# ---------------------------------------------------------------------------
# Tests: PredictionResult serialization
# ---------------------------------------------------------------------------

class TestPredictionResultSerialization:
    def test_as_dict(self, valid_prediction_dict):
        result = _validate_prediction(valid_prediction_dict)
        d = result.as_dict()
        assert isinstance(d, dict)
        assert d["prediction"] == "CONFIRM"
        assert d["confidence"] == 0.82
        assert isinstance(d["risk_flags"], list)

    def test_json_roundtrip(self, valid_prediction_dict):
        result = _validate_prediction(valid_prediction_dict)
        json_str = json.dumps(result.as_dict())
        parsed = json.loads(json_str)
        assert parsed["prediction"] == "CONFIRM"
