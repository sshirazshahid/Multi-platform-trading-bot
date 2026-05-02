"""Golden cases for core.short_side_filter.

The filter is the gate against shorting alts into a BTC uptrend
(warehouse evidence: 126 shorts net -$54 vs 210 longs net -$4).
"""
from __future__ import annotations

from core.short_side_filter import evaluate, extract_btc_trends


def test_long_always_passes():
    d = evaluate(
        side="buy", symbol="ETH/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
    )
    assert d.block is False
    assert d.reason == "not_short"


def test_short_blocked_when_btc_aligned_up():
    d = evaluate(
        side="sell", symbol="APT/USDT:USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
    )
    assert d.block is True
    assert "btc_aligned_up" in d.reason


def test_short_allowed_when_btc_4h_down():
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=False, btc_1h_uptrend=True,
    )
    assert d.block is False


def test_short_allowed_when_btc_1h_down():
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=False,
    )
    assert d.block is False


def test_short_allowed_when_btc_trends_unavailable():
    """Data outage must not silently start blocking trades."""
    d = evaluate(
        side="sell", symbol="ETH/USDT",
        btc_4h_uptrend=None, btc_1h_uptrend=None,
    )
    assert d.block is False


def test_news_bearish_catalyst_overrides_block():
    d = evaluate(
        side="sell", symbol="XRP/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
        symbol_news_sentiment=-3,
    )
    assert d.block is False
    assert "news_bearish_catalyst" in d.reason


def test_news_neutral_does_not_override():
    d = evaluate(
        side="sell", symbol="XRP/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
        symbol_news_sentiment=0,
    )
    assert d.block is True


def test_extract_btc_trends_happy_path():
    ei = {"BTC": {"4h": {"ema20_above_50": True}, "1h": {"ema20_above_50": False}}}
    assert extract_btc_trends(ei) == (True, False)


def test_extract_btc_trends_missing_keys():
    assert extract_btc_trends(None) == (None, None)
    assert extract_btc_trends({}) == (None, None)
    assert extract_btc_trends({"BTC": {}}) == (None, None)
    assert extract_btc_trends({"BTC": {"4h": {}, "1h": {}}}) == (None, None)


def test_case_insensitive_side():
    d = evaluate(
        side="SELL", symbol="ETH/USDT",
        btc_4h_uptrend=True, btc_1h_uptrend=True,
    )
    assert d.block is True
