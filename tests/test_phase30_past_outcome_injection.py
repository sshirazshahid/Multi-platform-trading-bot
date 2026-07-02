"""Phase 30 — TradingAgents-style past-outcome memory injection (2026-05-05).

Trade audit on 267 closed trades found MCP score INVERSELY correlated
with WR:

  score 65-70: 67% WR  (3 trades)
  score 75-80: 29% WR  (14 trades)
  score 80-85: 29% WR  (7 trades)
  score 90+:   29% WR  (7 trades)

The LLM (Claude CLI) generating the score sees current indicators but
never sees the empirical TAPE on those same symbols. TradingAgents
solves this with a "decision log" memory layer that injects past
similar-setup outcomes into each new analysis.

Phase 30 implements the lightweight version: for every candidate coin
in the portfolio prompt, append a one-line summary of the last 30d
realized PnL on that symbol, including last 5 outcomes (W/L sequence)
and a tag if the symbol is a clear winner or bleeder.

The LLM must now confront:
  "BTC: n=7 WR=14% mean=-$0.45 | last 5: L L L W L  ← BLEEDS"
before proposing a trade on BTC. Pure data, no opinion.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

# ─── Helper exists on MCPBrain ────────────────────────────────────────


def test_recent_tape_summary_method_exists():
    from core.mcp_brain import MCPBrain
    assert hasattr(MCPBrain, "_recent_tape_summary")


def test_recent_tape_summary_returns_list():
    """When warehouse has no data, helper returns empty list (not None)."""
    from core.mcp_brain import MCPBrain
    brain = MCPBrain.__new__(MCPBrain)  # skip __init__
    out = brain._recent_tape_summary([])
    assert isinstance(out, list)


def test_summary_skips_low_sample_coins():
    """Coins with fewer than 3 trades are skipped — no false signal."""
    from core.mcp_brain import MCPBrain
    brain = MCPBrain.__new__(MCPBrain)
    fake_wh = MagicMock()
    # Two rows total — under threshold
    fake_wh.query = MagicMock(return_value=[(0.5, 1700000000), (-0.3, 1700001000)])
    import core.warehouse as wh_mod
    orig = getattr(wh_mod, "get_warehouse", None)
    wh_mod.get_warehouse = MagicMock(return_value=fake_wh)
    try:
        out = brain._recent_tape_summary(["BTC"])
        assert out == []
    finally:
        if orig:
            wh_mod.get_warehouse = orig


def test_summary_emits_line_when_n_at_least_3():
    from core.mcp_brain import MCPBrain
    brain = MCPBrain.__new__(MCPBrain)
    fake_wh = MagicMock()
    rows = [(0.5, 1700000000), (-0.3, 1700001000), (-0.1, 1700002000),
            (-0.2, 1700003000), (0.4, 1700004000)]
    fake_wh.query = MagicMock(return_value=rows)
    import core.warehouse as wh_mod
    orig = wh_mod.get_warehouse
    wh_mod.get_warehouse = MagicMock(return_value=fake_wh)
    try:
        out = brain._recent_tape_summary(["BTC"])
        assert len(out) == 1
        line = out[0]
        # Must contain the coin, n, WR, mean, last5 fields
        assert "BTC" in line
        assert "n=5" in line
        assert "WR=" in line
        assert "mean=" in line
        assert "last 5" in line
    finally:
        wh_mod.get_warehouse = orig


def test_summary_tags_bleeder_with_marker():
    """A coin with strongly negative mean PnL gets a visible BLEEDS tag."""
    from core.mcp_brain import MCPBrain
    brain = MCPBrain.__new__(MCPBrain)
    fake_wh = MagicMock()
    # 5 losers — clear bleed
    rows = [(-1.0, t) for t in range(1700000000, 1700005000, 1000)]
    fake_wh.query = MagicMock(return_value=rows)
    import core.warehouse as wh_mod
    orig = wh_mod.get_warehouse
    wh_mod.get_warehouse = MagicMock(return_value=fake_wh)
    try:
        out = brain._recent_tape_summary(["BAD"])
        assert any("BLEEDS" in line for line in out)
    finally:
        wh_mod.get_warehouse = orig


def test_summary_tags_winner_with_marker():
    """A coin with strong positive mean PnL gets a 'winner' tag."""
    from core.mcp_brain import MCPBrain
    brain = MCPBrain.__new__(MCPBrain)
    fake_wh = MagicMock()
    rows = [(0.50, t) for t in range(1700000000, 1700005000, 1000)]
    fake_wh.query = MagicMock(return_value=rows)
    import core.warehouse as wh_mod
    orig = wh_mod.get_warehouse
    wh_mod.get_warehouse = MagicMock(return_value=fake_wh)
    try:
        out = brain._recent_tape_summary(["GOOD"])
        assert any("winner" in line for line in out)
    finally:
        wh_mod.get_warehouse = orig


# ─── Source-level: tape section is in the portfolio prompt ────────────


def test_portfolio_prompt_appends_recent_tape_section():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    # The injection runs inside _ask_claude_portfolio after sections.append("COINS:...")
    coins_idx = src.index('sections.append("COINS:"')
    after_coins = src[coins_idx:coins_idx + 2000]
    assert "_recent_tape_summary" in after_coins
    assert "RECENT TAPE" in after_coins


def test_phase30_marker_present():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert "Phase 30" in src
