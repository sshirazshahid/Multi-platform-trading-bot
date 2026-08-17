"""The bot must not liquidate the owner's OWN positions by default.

An "external" position (``source == "exchange"``) is one the position monitor
discovered on the venue but never opened — i.e. the owner's manual futures
position or manual spot holding. The monitor can act on those.

Phase 39 (2026-05-09) already suppressed EXT ``CLOSE`` after it accumulated
-$20.09 over 44 trades at 30% WR. EXT ``TAKE_PROFIT`` was left live, so in
CONTROLLED_LIVE the monitor would market-close a manual futures position at
>=1% PnL / >=70% confidence, or market-SELL the owner's spot coins at >=80%
confidence, on a signal the repo documents as non-predictive (mcp_score
corr ~= -0.008).

Three properties make this invisible until it costs real money:
  * DRY_RUN no-ops it, so PAPER accrues zero evidence either way;
  * it acts on assets outside the bot's allocated capital;
  * nothing in the CONTROLLED_LIVE config gate reaches it — the live gate
    validates configuration values, and this is behaviour.

So it becomes opt-in and defaults OFF. Enabling it is an owner decision about
their own coins, not a default the bot inherits by omission.

Sibling coverage: tests/test_close_external_position_dry_gate.py pins that
_close_external_position self-gates on DRY_RUN once CALLED. This file pins
whether the monitor decides to call it at all.

Run: venv/Scripts/python.exe -m pytest tests/test_external_position_actions.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.bot_engine import BotEngine  # noqa: E402
from tests.bot_engine_source import bot_engine_source_for_grep  # noqa: E402


def _eng():
    """Bare engine — the decision reads config, not engine state."""
    return BotEngine.__new__(BotEngine)


# ── the flag ────────────────────────────────────────────────────────────────

def test_flag_exists_and_defaults_off():
    """Acting on someone else's position is opt-in, never inherited."""
    assert hasattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED")
    if os.getenv("EXTERNAL_POSITION_ACTIONS_ENABLED") is None:
        assert config.EXTERNAL_POSITION_ACTIONS_ENABLED is False


# ── the suppression decision ────────────────────────────────────────────────

def test_take_profit_suppressed_by_default(monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED", False)
    assert _eng()._external_action_suppressed("TAKE_PROFIT"), (
        "EXT TAKE_PROFIT must be suppressed while the flag is off"
    )


def test_close_suppressed_regardless_of_flag(monkeypatch):
    """Phase 39's CLOSE suppression is evidence-based and not flag-defeatable."""
    for flag in (False, True):
        monkeypatch.setattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED", flag)
        assert _eng()._external_action_suppressed("CLOSE"), (
            f"EXT CLOSE must stay suppressed with flag={flag}"
        )


def test_take_profit_allowed_when_owner_opts_in(monkeypatch):
    """The owner can still turn it on deliberately — this is not a ban."""
    monkeypatch.setattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED", True)
    assert _eng()._external_action_suppressed("TAKE_PROFIT") == ""


def test_unrelated_actions_are_not_suppressed(monkeypatch):
    """TIGHTEN/BREAKEVEN are handled on a separate branch — don't capture them."""
    monkeypatch.setattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED", False)
    for action in ("TIGHTEN", "BREAKEVEN", "HOLD"):
        assert _eng()._external_action_suppressed(action) == ""


def test_flag_is_read_at_call_time(monkeypatch):
    """Must not bind the flag at import — a stale capture defeats the gate."""
    eng = _eng()
    monkeypatch.setattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED", True)
    assert eng._external_action_suppressed("TAKE_PROFIT") == ""
    monkeypatch.setattr(config, "EXTERNAL_POSITION_ACTIONS_ENABLED", False)
    assert eng._external_action_suppressed("TAKE_PROFIT") != ""


# ── wire-in pin (repo convention: source scan for the call site) ─────────────

def test_suppression_is_consulted_on_the_external_branch():
    """The external branch must consult the suppression BEFORE the dispatch
    that reaches _close_external_position."""
    src = bot_engine_source_for_grep()
    i = src.index("EXTERNAL POSITIONS")
    j = src.index("_close_external_position(ex_name", i)
    assert "_external_action_suppressed" in src[i:j], (
        "the external-position branch must consult _external_action_suppressed "
        "before dispatching a real close"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
