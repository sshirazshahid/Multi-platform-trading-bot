"""454-trade hardening (2026-05-27) — short-side score/confidence gates.

Evidence from 454 real trades:
  179 shorts: -$87 total PnL
  275 longs:  -$21 total PnL
  Shorts are 2.5x worse than longs on similar trade counts.

New gates added to evaluate():
  mcp_score  >= 75   (default; vs 65 open-threshold for longs)
  confidence >= 0.70 (default; vs 0.60 floor for longs)

The news_bearish_catalyst escape hatch overrides BOTH gates.
Missing score/confidence default to 0 → fail-closed (block).
"""
from __future__ import annotations

from core.short_side_filter import ShortFilterDecision, evaluate


# ── Original BTC-trend tests (must still pass) ────────────────────────────

def test_long_always_passes_with_high_score():
    """Longs are never gated by the short filter regardless of score."""
    d = evaluate(
        side="buy", symbol="ETH/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
        mcp_score=40.0, confidence=0.30,
    )
    assert d.block is False
    assert d.reason == "not_short"


def test_short_btc_aligned_still_blocked():
    """BTC-aligned block still fires when score passes both gates."""
    d = evaluate(
        side="sell", symbol="APT/USDT:USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
        mcp_score=80.0, confidence=0.75,
    )
    assert d.block is True
    assert "btc_aligned_up" in d.reason


# ── Score gate tests ──────────────────────────────────────────────────────

def test_short_blocked_when_score_below_threshold():
    """mcp_score < 75 blocks short regardless of BTC trend."""
    d = evaluate(
        side="sell", symbol="BTC/USDT:USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,  # BTC not aligned
        mcp_score=70.0, confidence=0.75,
    )
    assert d.block is True
    assert "short_score_too_low" in d.reason


def test_short_blocked_when_score_missing():
    """Missing mcp_score (None) is treated as 0 — fails score gate."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=None, confidence=0.75,
    )
    assert d.block is True
    assert "short_score_too_low" in d.reason


def test_short_blocked_when_score_zero():
    """mcp_score=0.0 is below threshold — blocked."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=0.0, confidence=0.75,
    )
    assert d.block is True
    assert "short_score_too_low" in d.reason


def test_short_allowed_when_score_exactly_at_threshold():
    """mcp_score == min_mcp_score (75) passes the score gate."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=75.0, confidence=0.75,
    )
    # Score passes; BTC not aligned so no BTC block either
    assert d.block is False


def test_short_allowed_when_score_above_threshold():
    """mcp_score > 75 passes the score gate."""
    d = evaluate(
        side="sell", symbol="SOL/USDT:USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=82.0, confidence=0.75,
    )
    assert d.block is False


# ── Confidence gate tests ─────────────────────────────────────────────────

def test_short_blocked_when_confidence_below_threshold():
    """confidence < 0.70 blocks short regardless of BTC trend."""
    d = evaluate(
        side="sell", symbol="BTC/USDT:USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=80.0, confidence=0.65,
    )
    assert d.block is True
    assert "short_conf_too_low" in d.reason


def test_short_blocked_when_confidence_missing():
    """Missing confidence (None) is treated as 0 — fails confidence gate."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=80.0, confidence=None,
    )
    assert d.block is True
    assert "short_conf_too_low" in d.reason


def test_short_allowed_when_confidence_exactly_at_threshold():
    """confidence == 0.70 passes the confidence gate."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=80.0, confidence=0.70,
    )
    assert d.block is False


# ── News catalyst overrides both gates ────────────────────────────────────

def test_news_catalyst_bypasses_score_gate():
    """Bearish catalyst overrides the score gate — short is allowed."""
    d = evaluate(
        side="sell", symbol="XRP/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
        symbol_news_sentiment=-5,
        mcp_score=50.0, confidence=0.40,  # both below threshold
    )
    assert d.block is False
    assert "news_bearish_catalyst" in d.reason


def test_news_catalyst_bypasses_confidence_gate():
    """Bearish catalyst overrides the confidence gate — short is allowed."""
    d = evaluate(
        side="sell", symbol="BNB/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
        symbol_news_sentiment=-1,
        mcp_score=80.0, confidence=0.30,  # confidence below threshold
    )
    assert d.block is False
    assert "news_bearish_catalyst" in d.reason


# ── Custom threshold override ─────────────────────────────────────────────

def test_custom_min_mcp_score_respected():
    """Callers can pass non-default min_mcp_score."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=70.0, confidence=0.75,
        min_mcp_score=65.0,  # lower threshold → should pass
    )
    assert d.block is False


def test_custom_min_confidence_respected():
    """Callers can pass non-default min_confidence."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=False,
        mcp_score=80.0, confidence=0.65,
        min_confidence=0.60,  # lower threshold → should pass
    )
    assert d.block is False


# ── Config values match expected defaults ─────────────────────────────────

def test_config_short_filter_thresholds():
    """Config must expose the expected default thresholds."""
    from config import SHORT_SIDE_FILTER
    assert SHORT_SIDE_FILTER.get("min_mcp_score") == 75.0, (
        f"Expected 75.0, got {SHORT_SIDE_FILTER.get('min_mcp_score')}"
    )
    assert SHORT_SIDE_FILTER.get("min_confidence") == 0.70, (
        f"Expected 0.70, got {SHORT_SIDE_FILTER.get('min_confidence')}"
    )
