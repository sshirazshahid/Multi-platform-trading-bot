"""Tests for the drawdown-halt auto-resume cooldown.

The old auto-resume paths required live trades to satisfy (WR≥60% on last 5,
or drawdown≤7.5% with daily_pnl>0). But trading is blocked while halted, so
a bug-induced drawdown would deadlock until midnight. The new escape hatch:
after DRAWDOWN_HALT_COOLDOWN_MIN (4h) the halt clears with a peak_balance
reset. Real losing streaks are caught by Spec §12 independently.
"""
from __future__ import annotations

import time as _time
from datetime import date
from pathlib import Path

import pytest

from core.risk_manager import DRAWDOWN_HALT_COOLDOWN_MIN, RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _force_drawdown_halt(rm: RiskManager, peak: float, current: float,
                          minutes_ago: float = 0.0):
    """Simulate an existing drawdown halt stored in the risk-manager state."""
    rm._start_balance = current
    rm._peak_balance = peak
    rm._daily_pnl = 0.0
    rm._trading_day = date.today()
    rm._halted = True
    rm._halt_reason = f"drawdown {(peak-current)/peak*100:.1f}% (peak ${peak:.2f})"
    rm._halt_time = _time.time() - minutes_ago * 60


def test_drawdown_halt_blocked_before_cooldown():
    rm = RiskManager()
    _force_drawdown_halt(rm, peak=500.0, current=400.0, minutes_ago=30)
    assert rm._should_auto_resume() is False


def test_drawdown_halt_resumes_after_cooldown():
    rm = RiskManager()
    _force_drawdown_halt(rm, peak=500.0, current=400.0,
                         minutes_ago=DRAWDOWN_HALT_COOLDOWN_MIN + 1)
    assert rm._should_auto_resume() is True


def test_drawdown_resume_resets_peak_to_current():
    """Without peak reset, the next losing trade would instantly re-trip DD
    because the math (peak − effective) / peak stays unchanged."""
    rm = RiskManager()
    _force_drawdown_halt(rm, peak=500.0, current=400.0,
                         minutes_ago=DRAWDOWN_HALT_COOLDOWN_MIN + 1)
    rm._should_auto_resume()
    # Peak should now equal effective balance (start_balance + daily_pnl)
    assert rm._peak_balance == pytest.approx(400.0)


def test_drawdown_resume_preserves_daily_pnl():
    """daily-loss circuit-breaker is a separate constraint; don't mask it."""
    rm = RiskManager()
    _force_drawdown_halt(rm, peak=500.0, current=400.0,
                         minutes_ago=DRAWDOWN_HALT_COOLDOWN_MIN + 1)
    rm._daily_pnl = -5.0
    rm._should_auto_resume()
    assert rm._daily_pnl == -5.0


def test_cooldown_does_not_apply_to_daily_halt():
    """Daily-loss halt has its own midnight reset path; cooldown must not
    accidentally resume it."""
    rm = RiskManager()
    rm._halted = True
    rm._halt_reason = "daily loss (-20.00 USDT)"
    rm._halt_time = _time.time() - (DRAWDOWN_HALT_COOLDOWN_MIN + 10) * 60
    rm._trading_day = date.today()
    rm._start_balance = 400.0
    rm._peak_balance = 400.0
    rm._daily_pnl = -20.0
    # No win-rate data and still same day, so should stay halted
    assert rm._should_auto_resume() is False


def test_cooldown_does_not_preempt_wr_recovery_path():
    """Fast WR recovery should still win if it fires before the cooldown."""
    rm = RiskManager()
    _force_drawdown_halt(rm, peak=500.0, current=400.0,
                         minutes_ago=20)  # past HALT_COOLDOWN_MIN (15)
    rm._recent_results = [True, True, True, True, True]
    assert rm._should_auto_resume() is True


def test_resume_after_fresh_risk_manager_load():
    """State persisted with is_halted=True should auto-resume on the next
    _should_auto_resume poll if cooldown has elapsed. Also verifies that
    start_balance round-trips through disk — without that, the peak reset
    silently degrades into a no-op because (start or peak) falls through
    to peak, and the next loss instantly re-trips drawdown."""
    rm = RiskManager()
    _force_drawdown_halt(rm, peak=500.0, current=400.0,
                         minutes_ago=DRAWDOWN_HALT_COOLDOWN_MIN + 30)
    rm._save_state()
    rm2 = RiskManager()  # reload
    assert rm2._halted is True
    assert "drawdown" in rm2._halt_reason
    assert rm2._start_balance == pytest.approx(400.0)  # loaded from disk
    assert rm2._should_auto_resume() is True
    # Peak must reset to the actual effective balance, not to the old peak
    assert rm2._peak_balance == pytest.approx(400.0)
