"""Deterministic rule-fire tests for MetaFilter.

Each case constructs a FeatureVector, runs `MetaFilter.evaluate()`, and
asserts on (decision, reason-prefix). First-match-wins is verified by
including the chop case with a low spread_pctl.
"""
from __future__ import annotations

import pytest

from core.features import FeatureVector
from core.meta_filter import MetaFilter


@pytest.fixture
def mf() -> MetaFilter:
    return MetaFilter()


def _base(**over) -> FeatureVector:
    fv = FeatureVector(
        spread_pctl=0.10, vol_pctl=0.30, funding_bias=0.0001,
        ob_imbalance=0.0, trend_strength=0.25,
        regime_label="trend", hour_of_day=12, day_of_week=2,
        recent_loss_streak=0, exchange_id="binance", symbol_id="BTC/USDT",
    )
    for k, v in over.items():
        setattr(fv, k, v)
    return fv


def test_clean_setup_allows(mf):
    d = mf.evaluate(_base(), side="buy", confidence=0.8)
    assert d.decision == "ALLOW"
    assert d.size_multiplier == 1.0


def test_spread_skip(mf):
    d = mf.evaluate(_base(spread_pctl=0.99), side="buy")
    assert d.decision == "SKIP"
    assert "spread" in d.reason


def test_vol_skip(mf):
    d = mf.evaluate(_base(vol_pctl=0.99), side="buy")
    assert d.decision == "SKIP"
    assert "vol" in d.reason


def test_loss_streak_low_confidence_skip(mf):
    d = mf.evaluate(_base(recent_loss_streak=3), side="buy", confidence=0.60)
    assert d.decision == "SKIP"
    assert "loss_streak" in d.reason


def test_loss_streak_high_confidence_still_allows(mf):
    d = mf.evaluate(_base(recent_loss_streak=3), side="buy", confidence=0.90)
    assert d.decision == "ALLOW"


def test_chop_skip(mf):
    d = mf.evaluate(_base(regime_label="chop"), side="buy")
    assert d.decision == "SKIP"
    assert "chop" in d.reason


def test_squeeze_skip(mf):
    d = mf.evaluate(_base(regime_label="squeeze"), side="buy")
    assert d.decision == "SKIP"
    assert "squeeze" in d.reason


def test_ob_imbalance_against_buy_reviews(mf):
    d = mf.evaluate(_base(ob_imbalance=-0.80), side="buy")
    assert d.decision == "REVIEW"


def test_ob_imbalance_against_sell_reviews(mf):
    d = mf.evaluate(_base(ob_imbalance=0.80), side="sell")
    assert d.decision == "REVIEW"


def test_ob_imbalance_with_direction_allows(mf):
    d = mf.evaluate(_base(ob_imbalance=0.80), side="buy")
    assert d.decision == "ALLOW"


def test_missing_fields_default_neutral(mf):
    fv = FeatureVector(regime_label="trend", symbol_id="BTC/USDT")
    d = mf.evaluate(fv, side="buy")
    assert d.decision == "ALLOW"


def test_priority_spread_beats_chop(mf):
    """Rules fire in priority order — spread SKIP must win over chop SKIP."""
    d = mf.evaluate(_base(spread_pctl=0.99, regime_label="chop"), side="buy")
    assert d.decision == "SKIP"
    assert "spread" in d.reason  # not 'chop'


def test_high_score_high_vol_skips(mf):
    """Score 80+ + top-decile ATR is a known loss bucket — must SKIP."""
    d = mf.evaluate(_base(vol_pctl=0.90), side="buy", confidence=0.85,
                    mcp_score=88.0)
    assert d.decision == "SKIP"
    assert "mcp_score" in d.reason
    assert "vol_pctl" in d.reason


def test_high_score_normal_vol_still_allows(mf):
    """High score is fine on its own — only the high-vol pairing is a trap."""
    d = mf.evaluate(_base(vol_pctl=0.50), side="buy", confidence=0.85,
                    mcp_score=88.0)
    assert d.decision == "ALLOW"


def test_low_score_high_vol_still_allows(mf):
    """Vol percentile 0.85 alone is below the 0.95 SKIP threshold."""
    d = mf.evaluate(_base(vol_pctl=0.86), side="buy", confidence=0.70,
                    mcp_score=70.0)
    assert d.decision == "ALLOW"


def test_high_score_filter_optional_when_unset(mf):
    """When mcp_score is omitted (legacy callers), no new SKIP is introduced."""
    d = mf.evaluate(_base(vol_pctl=0.90), side="buy", confidence=0.85)
    assert d.decision == "ALLOW"
