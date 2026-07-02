"""Phase 4 (Rev 5 carry-first): SIGNAL_SOURCE=none = directional-OFF knob.

'none' makes the directional entry seam emit ZERO actions (the carry runner is
the only opener) while the engine stays alive: deterministic SL/TP monitoring
still manages open positions, and the MCP advisory monitor is skipped exactly
like tsmom/machine. Default stays 'tsmom' — 'none' is opt-in.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import core.bot_engine as be

_ROOT = Path(__file__).resolve().parents[1]


def _set_signal_source(monkeypatch, value):
    monkeypatch.setattr(sys.modules["config"], "SIGNAL_SOURCE", value, raising=False)


def _fake_engine():
    brain = MagicMock()
    brain.is_enabled = True
    tracker = MagicMock()
    tracker.get_open.return_value = []
    return SimpleNamespace(mcp_brain=brain, tracker=tracker)


def test_config_accepts_none_env():
    env = dict(os.environ)
    env["SIGNAL_SOURCE"] = "none"
    out = subprocess.run(
        [sys.executable, "-c", "import config; print(config.SIGNAL_SOURCE)"],
        capture_output=True, text=True, cwd=str(_ROOT), env=env,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "none"


def test_config_default_stays_tsmom():
    # .env can override at runtime; the CODE default (getenv fallback) must stay tsmom.
    src = (_ROOT / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("SIGNAL_SOURCE", "tsmom")' in src


def test_none_signal_returns_empty_actions():
    fake = _fake_engine()
    sig = be.BotEngine._none_signal(fake)
    actions = sig.analyze_portfolio(
        coins=[{"symbol": "BTC/USDT"}],
        open_positions=[],
        exchange_balances={},
        risk_envelope={"total_balance": 100.0},
        recent_trades=[],
        news_context={},
    )
    assert actions == []


def test_none_branch_wired_at_seam():
    src = inspect.getsource(be.BotEngine._claude_portfolio_cycle)
    assert 'SIGNAL_SOURCE == "none"' in src
    assert "_none_signal" in src


def test_mcp_monitor_skipped_under_none(monkeypatch):
    _set_signal_source(monkeypatch, "none")
    fake = _fake_engine()
    be.BotEngine._run_mcp_position_monitor(fake)
    fake.tracker.get_open.assert_not_called()
    fake.mcp_brain.monitor_positions.assert_not_called()
