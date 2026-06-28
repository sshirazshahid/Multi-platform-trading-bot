"""Audit 2026-06-21 H8: free-text LLM close rationale must not become the
warehouse exit_reason GROUP BY key. _canonical_exit_reason collapses prose to
a clean machine label while passing through real labels unchanged.
"""
from __future__ import annotations

import pytest

from core.bot_engine import _canonical_exit_reason


@pytest.mark.parametrize("clean", [
    "stop_loss",
    "take_profit",
    "trailing_stop",
    "mcp_take_profit",
    "claude_portfolio_close",
    "scalp_stale_close",
    "hard_max_loss",
])
def test_clean_machine_labels_pass_through(clean):
    assert _canonical_exit_reason(clean) == clean


@pytest.mark.parametrize("prose", [
    "Lock +2%; OB imb -0.712 heavy sell pressure",
    "book +1.9% at target, 4h RSI18.6 bounce risk",
    "Bank +3.1%, past scalp target",
    "+2.2% above scalp TP; tape mean=-$0.83 bleeds",
])
def test_claude_prose_collapses_to_canonical(prose):
    assert _canonical_exit_reason(prose) == "claude_close"


def test_empty_or_none_is_canonical():
    assert _canonical_exit_reason("") == "claude_close"
    assert _canonical_exit_reason(None) == "claude_close"
    assert _canonical_exit_reason("   ") == "claude_close"


def test_long_spaceless_blob_is_canonical():
    # No spaces but absurdly long -> not a real label.
    assert _canonical_exit_reason("x" * 60) == "claude_close"


def test_idempotent_on_canonical():
    assert _canonical_exit_reason(_canonical_exit_reason("anything with spaces")) == "claude_close"


def test_source_specific_prose_fallbacks():
    assert _canonical_exit_reason("machine time stop: age 433m", source="machine") == "machine_close"
    assert _canonical_exit_reason("", source="machine") == "machine_close"
    assert _canonical_exit_reason("TSMOM exit: daily momentum flipped", source="tsmom") == "tsmom_close"
    assert _canonical_exit_reason("machine_time_stop", source="machine") == "machine_time_stop"
