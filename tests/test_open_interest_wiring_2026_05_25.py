"""Open Interest wiring (2026-05-25, no-edge-forensics bundle).

OI was the one microstructure data gap. fetch_open_interest pulls Binance
OI history, computes the 6h delta, and feeds it to the Claude prompt as
qualitative entry-timing context. DELIBERATELY not wired into the
deterministic algo score (that changes WR and needs validation).
"""
from __future__ import annotations

from unittest.mock import patch

import core.mcp_brain as mb


def test_fetch_open_interest_computes_rising_trend():
    """Rising OI series → oi_trend='rising', positive delta."""
    fake_hist = [
        {"sumOpenInterest": "1000", "timestamp": 1},
        {"sumOpenInterest": "1010", "timestamp": 2},
        {"sumOpenInterest": "1050", "timestamp": 3},
    ]
    with patch.object(mb, "_http_get", return_value=fake_hist):
        out = mb.fetch_open_interest(["BTC"])
    assert "BTC" in out
    assert out["BTC"]["oi_trend"] == "rising"
    assert out["BTC"]["oi_delta_pct"] == 0.05   # (1050-1000)/1000


def test_fetch_open_interest_falling_trend():
    fake_hist = [
        {"sumOpenInterest": "2000", "timestamp": 1},
        {"sumOpenInterest": "1900", "timestamp": 2},
    ]
    with patch.object(mb, "_http_get", return_value=fake_hist):
        out = mb.fetch_open_interest(["ETH"])
    assert out["ETH"]["oi_trend"] == "falling"
    assert out["ETH"]["oi_delta_pct"] == -0.05


def test_fetch_open_interest_flat_trend():
    fake_hist = [
        {"sumOpenInterest": "1000", "timestamp": 1},
        {"sumOpenInterest": "1005", "timestamp": 2},   # +0.5%, within flat band
    ]
    with patch.object(mb, "_http_get", return_value=fake_hist):
        out = mb.fetch_open_interest(["SOL"])
    assert out["SOL"]["oi_trend"] == "flat"


def test_fetch_open_interest_failsafe_on_bad_data():
    """Missing / malformed responses must not raise — return empty/skip."""
    for bad in (None, [], [{"sumOpenInterest": "0"}, {"sumOpenInterest": "0"}], "err"):
        with patch.object(mb, "_http_get", return_value=bad):
            out = mb.fetch_open_interest(["BTC"])
        assert isinstance(out, dict)   # never raises; coin skipped if unusable


def test_oi_is_prompt_only_not_scored():
    """Guard the design decision: OI must NOT appear in _score_coin's
    deterministic scoring (it's prompt/instrumentation only, to avoid
    silently changing WR). If a future edit scores OI, this test should be
    updated deliberately + the change validated against the WR floor."""
    import inspect
    src = inspect.getsource(mb.MCPBrain._score_coin)
    assert "oi_delta" not in src and "oi_trend" not in src, (
        "OI leaked into _score_coin. Per the 2026-05-25 design it is "
        "prompt-context only until forward data validates it as a scored "
        "signal (WR-floor rule). Update this test deliberately if scoring OI."
    )
