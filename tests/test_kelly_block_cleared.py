"""Kelly block-gate cleared (2026-04-29).

User directive: "clear kelly_block ultrathink". The kelly_sizer
.should_block_trade method was rejecting every MCP-approved
claude_portfolio trade because the historical kelly_stats showed
82W/104L (44% WR, avg_loss > avg_win) → kelly ≈ -56%, way below the
-0.30 catastrophic threshold for the MCP-approved path.

The stats are real (the bot did 186 trades) but heavily poisoned by
pre-fix bot bugs: SL placement failures, ghost-sync close paths,
BREAKEVEN fail-closed, L99 ETH disaster. The user has accepted full
capital deployment and doesn't want this gate firing.

These tests pin: should_block_trade returns (False, "") regardless of
strategy, stats, or MCP-approval.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def test_kelly_block_disabled_for_catastrophic_negative_edge():
    """Even with kelly = -56% (the actual claude_portfolio number),
    must NOT block."""
    Path("data/kelly_stats.json").write_text(json.dumps({
        "claude_portfolio": {
            "wins": 82, "losses": 104,
            "total_win": 19.60, "total_loss": 44.17,
        },
    }))
    from core.kelly_sizer import KellySizer
    ks = KellySizer()
    blocked, reason = ks.should_block_trade("claude_portfolio", mcp_approved=True)
    assert blocked is False, (
        "Kelly block must be cleared per user directive — even at "
        "kelly=-56% the trade must proceed.")
    assert reason == ""


def test_kelly_block_disabled_when_not_mcp_approved():
    """Same: non-MCP-approved path must also pass through."""
    Path("data/kelly_stats.json").write_text(json.dumps({
        "test_strat": {
            "wins": 5, "losses": 50,
            "total_win": 5.0, "total_loss": 100.0,
        },
    }))
    from core.kelly_sizer import KellySizer
    ks = KellySizer()
    blocked, reason = ks.should_block_trade("test_strat", mcp_approved=False)
    assert blocked is False
    assert reason == ""


def test_kelly_block_disabled_for_terrible_wr():
    """Even at WR=10% (catastrophic), no block."""
    Path("data/kelly_stats.json").write_text(json.dumps({
        "test_strat": {
            "wins": 2, "losses": 18,  # 10% WR
            "total_win": 1.0, "total_loss": 50.0,
        },
    }))
    from core.kelly_sizer import KellySizer
    ks = KellySizer()
    blocked, reason = ks.should_block_trade("test_strat", mcp_approved=False)
    assert blocked is False


def test_kelly_block_disabled_with_no_stats():
    """No kelly_stats.json: still no block (was already this way)."""
    from core.kelly_sizer import KellySizer
    ks = KellySizer()
    blocked, reason = ks.should_block_trade("anything", mcp_approved=True)
    assert blocked is False


def test_kelly_optimal_position_pct_still_works():
    """The block is gone but Kelly SIZING math is preserved.
    optimal_position_pct should still return a number."""
    Path("data/kelly_stats.json").write_text(json.dumps({
        "test_strat": {
            "wins": 50, "losses": 50,
            "total_win": 50.0, "total_loss": 25.0,  # +EV
        },
    }))
    from core.kelly_sizer import KellySizer
    ks = KellySizer()
    pct = ks.optimal_position_pct("test_strat", balance=400.0)
    # Sizing math intact — returns a number for +EV strategy
    assert isinstance(pct, (int, float))
    assert pct >= 0  # Non-negative
