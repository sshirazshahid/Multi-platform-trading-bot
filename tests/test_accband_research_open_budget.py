"""AccBand research daily-open tuition cap (PAPER + MAX_FLOW_BAND only)."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config.gates import (
    _max_flow_research_knob_enabled,
    _profile_gated_accband_research_max_opens,
)
from core.engine.helpers import (
    accband_research_open_budget_allows,
    count_mcp_directional_opens_utc_day,
)


def test_budget_disabled_outside_max_flow_band():
    assert _profile_gated_accband_research_max_opens("12", "PAPER", "STANDARD") is None
    assert _profile_gated_accband_research_max_opens("12", "CONTROLLED_LIVE", "MAX_FLOW_BAND") is None


def test_budget_default_under_paper_max_flow_band():
    assert _profile_gated_accband_research_max_opens("12", "PAPER", "MAX_FLOW_BAND") == 12
    assert _profile_gated_accband_research_max_opens("", "PAPER", "MAX_FLOW_BAND") == 12


def test_max_flow_knob_gate():
    assert _max_flow_research_knob_enabled("PAPER", "MAX_FLOW_BAND") is True
    assert _max_flow_research_knob_enabled("PAPER", "AGGRESSIVE_RESEARCH") is False


def _pos(*, open_time, strategy="claude_portfolio", accuracy=False, market="futures"):
    return SimpleNamespace(
        open_time=open_time,
        strategy=strategy,
        _accuracy_band=accuracy,
        market_type=market,
    )


def test_count_and_cap_blocks_at_limit():
    now = time.time()
    opens = [_pos(open_time=now - i, accuracy=True) for i in range(12)]
    tracker = MagicMock()
    tracker.get_open.return_value = opens
    tracker._closed = []
    lock = MagicMock()
    lock.__enter__ = MagicMock(return_value=lock)
    lock.__exit__ = MagicMock(return_value=False)
    tracker._lock = lock

    assert count_mcp_directional_opens_utc_day(tracker, now_ts=now) == 12
    allowed, n = accband_research_open_budget_allows(tracker, 12, now_ts=now)
    assert n == 12
    assert allowed is False
    assert accband_research_open_budget_allows(tracker, None, now_ts=now)[0] is True


def test_count_ignores_f1_and_spot():
    now = time.time()
    tracker = MagicMock()
    tracker.get_open.return_value = [
        _pos(open_time=now, strategy="f1_carry"),
        _pos(open_time=now, strategy="claude_portfolio", market="spot"),
        _pos(open_time=now, accuracy=True),
    ]
    tracker._closed = []
    tracker._lock = MagicMock()
    tracker._lock.__enter__ = MagicMock(return_value=None)
    tracker._lock.__exit__ = MagicMock(return_value=False)
    assert count_mcp_directional_opens_utc_day(tracker, now_ts=now) == 1


def test_allows_when_under_cap():
    now = time.time()
    tracker = MagicMock()
    tracker.get_open.return_value = [_pos(open_time=now, accuracy=True) for _ in range(3)]
    tracker._closed = []
    tracker._lock = MagicMock()
    tracker._lock.__enter__ = MagicMock(return_value=None)
    tracker._lock.__exit__ = MagicMock(return_value=False)
    allowed, n = accband_research_open_budget_allows(tracker, 12, now_ts=now)
    assert allowed is True and n == 3
