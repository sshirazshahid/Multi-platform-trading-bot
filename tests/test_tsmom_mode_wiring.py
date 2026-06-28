"""Phase 2c (2026-06-15): under SIGNAL_SOURCE=tsmom the bot makes NO Claude/MCP
decisions — the MCP position monitor is skipped entirely (the deterministic SL/TP
loop and tsmom's own momentum-flip exits still run). This pins that gate so the
"restarted but still seeing Claude/MCP" regression can't come back.

Why a fake engine: BotEngine.__init__ wires real exchanges/threads. We only need
the early-return logic, so we call the unbound method against a minimal namespace
and assert whether it reached the tracker / MCP brain.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import core.bot_engine as be


def _set_signal_source(monkeypatch, value):
    """Patch the CANONICAL config module in sys.modules — the same object the
    method's ``from config import SIGNAL_SOURCE`` resolves at call time. Patching a
    locally-imported ``config`` is unreliable: the conftest's skill-test sys.path
    isolation can leave the test bound to a different module instance."""
    monkeypatch.setattr(sys.modules["config"], "SIGNAL_SOURCE", value, raising=False)


def _fake_engine():
    brain = MagicMock()
    brain.is_enabled = True
    tracker = MagicMock()
    tracker.get_open.return_value = []     # mcp path: empty -> clean early return after the gate
    return SimpleNamespace(mcp_brain=brain, tracker=tracker)


def test_mcp_monitor_skipped_under_tsmom(monkeypatch):
    _set_signal_source(monkeypatch, "tsmom")
    fake = _fake_engine()
    be.BotEngine._run_mcp_position_monitor(fake)
    # Early-returned BEFORE touching the tracker or calling Claude/MCP.
    fake.tracker.get_open.assert_not_called()
    fake.mcp_brain.monitor_positions.assert_not_called()


def test_mcp_monitor_skipped_under_machine(monkeypatch):
    _set_signal_source(monkeypatch, "machine")
    fake = _fake_engine()
    be.BotEngine._run_mcp_position_monitor(fake)
    fake.tracker.get_open.assert_not_called()
    fake.mcp_brain.monitor_positions.assert_not_called()


def test_mcp_monitor_runs_under_mcp(monkeypatch):
    _set_signal_source(monkeypatch, "mcp")
    fake = _fake_engine()
    be.BotEngine._run_mcp_position_monitor(fake)
    # Under mcp the gate is a no-op: the monitor proceeds and reads the tracker.
    fake.tracker.get_open.assert_called()
