"""Audit 2026-06-21 A4: _execute_close discretionary-CLOSE guard.

The portfolio-cycle Claude CLOSE -> _execute_close was ungated (only OBSERVATION-
gated) — the un-suppressed twin of the disabled 30s monitor CLOSE. Two guards added:
  (1) ALWAYS-ON deterministic ownership guard (tsmom/machine own their exits), and
  (2) flag-gated (PORTFOLIO_DISCRETIONARY_CLOSE_GUARD_ENABLED, default-OFF)
      suppression of a Claude-sourced CLOSE of a NON-disaster position, with a
      catastrophic-loss (net <= -8%) escape hatch.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import config
from core.bot_engine import BotEngine


def _eng():
    eng = object.__new__(BotEngine)              # bypass heavy __init__
    eng.tracker = MagicMock()
    eng.order_mgr = MagicMock()
    ex = MagicMock()
    ex.fetch_ticker.return_value = {"last": 100.0}
    eng.active_exchanges = {"binance": ex}
    return eng


def _target(strategy="claude_portfolio"):
    return types.SimpleNamespace(
        id="tgt-12345678", symbol="ETH/USDT", side="buy",
        exchange="Binance", market_type="futures", strategy=strategy)


def _run(monkeypatch, eng, target, *, flag, source="claude", net_pct=5.0):
    monkeypatch.setattr(config, "OPERATING_MODE", "PAPER", raising=False)
    monkeypatch.setattr(config, "PORTFOLIO_DISCRETIONARY_CLOSE_GUARD_ENABLED",
                        flag, raising=False)
    eng.tracker.get_open.return_value = [target]
    eng.order_mgr._net_pnl_at_price = MagicMock(return_value=(net_pct, net_pct, 100.0))
    return eng._execute_close(
        {"type": "CLOSE", "position_id": target.id, "reason": "weak", "source": source})


# ── flag OFF: default behavior unchanged (executes) ──────────────────────────
def test_flag_off_executes_profitable_claude_close(monkeypatch):
    eng = _eng()
    out = _run(monkeypatch, eng, _target(), flag=False, source="claude", net_pct=5.0)
    assert out is True
    eng.order_mgr.close_position.assert_called_once()


# ── flag ON: suppress discretionary close of a non-disaster position ─────────
def test_flag_on_suppresses_profitable_claude_close(monkeypatch):
    eng = _eng()
    out = _run(monkeypatch, eng, _target(), flag=True, source="claude", net_pct=5.0)
    assert out is False
    eng.order_mgr.close_position.assert_not_called()


def test_flag_on_suppresses_small_loss_claude_close(monkeypatch):
    eng = _eng()  # -3% is not a disaster -> SL/TP own it
    out = _run(monkeypatch, eng, _target(), flag=True, source="claude", net_pct=-3.0)
    assert out is False
    eng.order_mgr.close_position.assert_not_called()


# ── flag ON: catastrophic loss still passes (escape hatch) ───────────────────
def test_flag_on_allows_disaster_claude_close(monkeypatch):
    eng = _eng()  # past the -8% disaster floor
    out = _run(monkeypatch, eng, _target(), flag=True, source="claude", net_pct=-9.0)
    assert out is True
    eng.order_mgr.close_position.assert_called_once()


# ── flag ON: algo-sourced CLOSE is exempt (only fires on a real loss-cut) ────
def test_flag_on_algo_source_exempt(monkeypatch):
    eng = _eng()
    out = _run(monkeypatch, eng, _target(), flag=True, source="algo", net_pct=5.0)
    assert out is True
    eng.order_mgr.close_position.assert_called_once()


# ── deterministic ownership guard is ALWAYS-ON for external closes ───────────
def test_tsmom_external_close_suppressed(monkeypatch):
    eng = _eng()
    out = _run(monkeypatch, eng, _target(strategy="tsmom"),
               flag=False, source="claude", net_pct=5.0)
    assert out is False
    eng.order_mgr.close_position.assert_not_called()


def test_tsmom_own_close_allowed(monkeypatch):
    eng = _eng()
    target = _target(strategy="tsmom")
    out = _run(monkeypatch, eng, target,
               flag=False, source="tsmom", net_pct=5.0)
    assert out is True
    eng.order_mgr.close_position.assert_called_once()


def test_machine_external_close_suppressed(monkeypatch):
    eng = _eng()
    out = _run(monkeypatch, eng, _target(strategy="machine"),
               flag=False, source="claude", net_pct=5.0)
    assert out is False
    eng.order_mgr.close_position.assert_not_called()


def test_machine_own_close_allowed(monkeypatch):
    eng = _eng()
    target = _target(strategy="machine")
    out = _run(monkeypatch, eng, target,
               flag=False, source="machine", net_pct=5.0)
    assert out is True
    eng.order_mgr.close_position.assert_called_once()
