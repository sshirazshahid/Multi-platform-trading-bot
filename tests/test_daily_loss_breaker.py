"""Soft daily-loss circuit breaker (2026-05-28).

The user removed all nine global halt/pause mechanisms (2026-05-19 directive).
This is the opt-in REPLACEMENT they requested: a SOFT breaker that pauses only
NEW entries once the day's realized loss exceeds a % of start-of-day balance,
then AUTO-RESETS at the day rollover. It does NOT switch OPERATING_MODE, does
NOT write review_required.json, does NOT kill the process, and does NOT touch
existing positions (their per-trade SLs still protect them).

It lives in RiskManager.can_trade() — the gate bot_engine consults before every
open (bot_engine.py:2375).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def rm() -> RiskManager:
    r = RiskManager()
    r.set_start_balance(400.0)
    r._trading_day = date.today()      # deterministic: no spurious rollover
    return r


def _enable(monkeypatch, enabled=True, pct=0.02):
    monkeypatch.setattr(
        "config.DAILY_LOSS_BREAKER",
        {"enabled": enabled, "max_loss_pct": pct},
        raising=False,
    )


def test_allows_trading_with_no_loss(rm, monkeypatch):
    _enable(monkeypatch)
    assert rm.can_trade(0) is True


def test_allows_trading_below_threshold(rm, monkeypatch):
    _enable(monkeypatch, pct=0.02)            # $8 budget on $400
    rm.record_trade_pnl(-5.0, balance=400.0, is_win=False)
    assert rm.can_trade(0) is True


def test_blocks_new_entries_after_threshold(rm, monkeypatch):
    _enable(monkeypatch, pct=0.02)            # $8 budget
    rm.record_trade_pnl(-9.0, balance=400.0, is_win=False)   # exceeds $8
    assert rm.can_trade(0) is False


def test_disabled_never_blocks(rm, monkeypatch):
    _enable(monkeypatch, enabled=False, pct=0.02)
    rm.record_trade_pnl(-100.0, balance=400.0, is_win=False)
    assert rm.can_trade(0) is True


def test_soft_breaker_writes_no_review_flag(rm, monkeypatch):
    """Breaker is SOFT — it must not create the global-halt review artifact."""
    _enable(monkeypatch, pct=0.02)
    rm.record_trade_pnl(-20.0, balance=400.0, is_win=False)
    assert rm.can_trade(0) is False
    assert not Path("data/review_required.json").exists()


def test_auto_resets_next_day(rm, monkeypatch):
    """Crossing into a new day clears the daily loss and re-enables entries."""
    _enable(monkeypatch, pct=0.02)
    rm.record_trade_pnl(-20.0, balance=400.0, is_win=False)
    assert rm.can_trade(0) is False
    rm._trading_day = date.today() - timedelta(days=1)   # simulate midnight cross
    assert rm.can_trade(0) is True
    assert rm._daily_pnl == 0.0


def test_noop_without_start_balance(monkeypatch):
    """If start balance is unknown (0), the % threshold is undefined — fail OPEN
    (do not block) rather than blocking on a bogus computation."""
    _enable(monkeypatch, pct=0.02)
    r = RiskManager()
    r._start_balance = 0.0
    r._trading_day = date.today()
    r.record_trade_pnl(-100.0, balance=0.0, is_win=False)
    assert r.can_trade(0) is True
