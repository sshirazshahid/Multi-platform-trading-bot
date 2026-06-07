"""Claude-dependence control (owner 2026-06-07: "make the bot less dependent on ClaudeCode").

Both the Claude decision and the algorithmic scorer are NO_EDGE (this session: corr(mcp_score,
realized_pnl) ~ -0.008), but Claude costs Max-plan usage and ~17-27% of calls time out (120s wait).
CLAUDE_PORTFOLIO_MODE gates Claude at both decision dispatches (portfolio entry + position monitor);
the existing algorithmic path decides whenever Claude is skipped. This is a cost/robustness change,
NOT an edge change — and the algo entry gate (score>=66) MAY lower WR (mid-score buckets ran worse),
so it is reversible (CLAUDE_PORTFOLIO_MODE=primary) and must be measured in PAPER.
"""
from __future__ import annotations

from pathlib import Path

from core.mcp_brain import _claude_should_run


def test_off_never_runs_claude():
    assert all(_claude_should_run("off", c, 3) is False for c in range(6))


def test_primary_always_runs_claude():
    assert all(_claude_should_run("primary", c, 3) is True for c in range(6))


def test_unknown_mode_defaults_to_always():
    # Fail SAFE: an unrecognised mode keeps the original (Claude-primary) behaviour.
    assert _claude_should_run("garbage", 1, 3) is True
    assert _claude_should_run("", 1, 3) is True
    assert _claude_should_run(None, 1, 3) is True


def test_throttled_runs_every_nth_cycle():
    got = [_claude_should_run("throttled", c, 3) for c in range(7)]
    assert got == [True, False, False, True, False, False, True]  # cycles 0,3,6


def test_throttled_n1_is_every_cycle():
    assert all(_claude_should_run("throttled", c, 1) for c in range(4))


def test_throttled_guards_bad_n():
    # n<=0 must not divide-by-zero / lock Claude off; clamp to every cycle.
    assert _claude_should_run("throttled", 0, 0) is True


def test_case_insensitive():
    assert _claude_should_run("OFF", 0, 3) is False
    assert _claude_should_run("Throttled", 3, 3) is True


def test_both_dispatches_gated_and_config_exists():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    # portfolio (analyze_portfolio) + monitor (monitor_positions) both gate Claude.
    assert src.count("_claude_should_run(") >= 2, "both dispatches must use the gate"
    assert "CLAUDE_PORTFOLIO_MODE" in src
    import config
    assert hasattr(config, "CLAUDE_PORTFOLIO_MODE")
    assert hasattr(config, "CLAUDE_THROTTLE_N")
