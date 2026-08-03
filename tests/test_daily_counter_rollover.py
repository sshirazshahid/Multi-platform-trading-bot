"""Regression: daily counters must roll over on a UTC-day crossing even when
NO entry is executed.

2026-06-13 defect: the daily-counter reset (daily_pnl=0, trades_today=0) lived
ONLY inside `can_trade()` and `note_trade_opened()`. Both are reached only via
`bot_engine._execute_open` (the entry-execution path). When entries are gated
upstream for >1 day (e.g. CLAUDE_PORTFOLIO_MODE=off routes to the algo-only
SCALP path which produced 0 ALLOW for 2 days), `can_trade()` is never called,
so `trades_today`/`daily_pnl` froze at their 2026-06-11 values across two UTC
day boundaries. Dashboards/notifiers that read `risk_state.json` then report a
2-day-stale "today" PnL.

Fix: extract the rollover into `roll_day_if_needed()` and call it once per
portfolio cycle (decoupled from entry execution). These tests pin the realized
equity carry, counter resets, restart persistence, fail-closed invalid anchors,
idempotence, and common RLock-protected use by the entry and close paths.
"""

from __future__ import annotations

import json
import threading
from datetime import date

import pytest

import core.risk_manager as rmod
from core.risk_manager import RiskManager

_D11 = date(2026, 6, 11)
_D13 = date(2026, 6, 13)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager()


def test_roll_day_if_needed_resets_stale_counters_without_entries(rm, monkeypatch):
    """The frozen-counter bug: stale daily counters must clear on a day crossing
    with ZERO entries executed (no can_trade / note_trade_opened call)."""
    # Simulate a long-idle process carrying yesterday's counters.
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_050.0
    rm._daily_pnl = -55.97
    rm._opens_today = 200

    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    rolled = rm.roll_day_if_needed()

    assert rolled is True
    assert rm._trading_day == _D13
    assert rm._daily_pnl == 0.0
    assert rm._opens_today == 0
    assert rm._start_balance == pytest.approx(944.03)
    assert rm._peak_balance == pytest.approx(944.03)


def test_roll_day_carries_winning_realized_equity(rm, monkeypatch):
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_025.0
    rm._daily_pnl = 25.0
    rm._opens_today = 4

    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    assert rm.roll_day_if_needed() is True

    assert rm._start_balance == pytest.approx(1_025.0)
    assert rm._peak_balance == pytest.approx(1_025.0)
    assert rm._daily_pnl == 0.0
    assert rm._opens_today == 0


def test_roll_day_if_needed_is_idempotent_within_day(rm, monkeypatch):
    """Calling every cycle must not clobber same-day counters after the first roll."""
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_000.0
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    rm.roll_day_if_needed()  # establishes today
    rm._daily_pnl = -3.0
    rm._opens_today = 5
    start = rm._start_balance
    peak = rm._peak_balance

    rolled = rm.roll_day_if_needed()  # same day → no-op

    assert rolled is False
    assert rm._daily_pnl == -3.0
    assert rm._opens_today == 5
    assert rm._start_balance == start
    assert rm._peak_balance == peak


def test_can_trade_still_rolls_over(rm, monkeypatch):
    """Behavior preserved: the entry-path rollover still fires via can_trade()."""
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_000.0
    rm._daily_pnl = -55.97
    rm._opens_today = 200

    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    rm.can_trade(open_position_count=0)

    assert rm._trading_day == _D13
    assert rm._opens_today == 0
    assert rm._daily_pnl == 0.0


def test_rollover_anchor_survives_same_day_restart(rm, monkeypatch):
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_020.0
    rm._daily_pnl = -25.0
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)

    assert rm.roll_day_if_needed() is True
    fresh = RiskManager()
    fresh.set_start_balance(975.0)

    assert fresh._start_balance == pytest.approx(975.0)
    assert fresh._peak_balance == pytest.approx(975.0)
    assert fresh._daily_pnl == 0.0
    assert fresh._daily_anchor_valid is True


def test_restart_spanning_midnight_uses_common_realized_carry(rm, monkeypatch):
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_020.0
    rm._daily_pnl = -25.0
    rm._save_state()
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)

    fresh = RiskManager()
    fresh.set_start_balance(700.0)  # Must not replace carried realized equity.

    assert fresh._start_balance == pytest.approx(975.0)
    assert fresh._peak_balance == pytest.approx(975.0)
    assert fresh._daily_pnl == 0.0


@pytest.mark.parametrize(
    ("start", "daily_pnl"),
    [(0.0, 1.0), (100.0, -100.0), (float("nan"), 0.0)],
)
def test_rollover_invalid_anchor_fails_closed_without_guessing(
        rm, monkeypatch, start, daily_pnl):
    rm._trading_day = _D11
    rm._start_balance = start
    rm._peak_balance = 100.0
    rm._daily_pnl = daily_pnl
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)

    assert rm.roll_day_if_needed() is True

    assert rm._start_balance == 0.0
    assert rm._peak_balance == 0.0
    assert rm._daily_pnl == 0.0
    assert rm._daily_anchor_valid is False
    assert rm.can_trade(open_position_count=0) is False
    assert rm.halt_reason == "daily_equity_anchor_invalid"
    saved = json.loads(rmod.RISK_STATE_PATH.read_text(encoding="utf-8"))
    assert saved["daily_anchor_valid"] is False

    fresh = RiskManager()
    fresh.set_start_balance(999.0)
    assert fresh._daily_anchor_valid is False
    assert fresh._start_balance == 0.0
    assert fresh.can_trade(open_position_count=0) is False


def test_record_trade_pnl_uses_common_rollover_before_booking_close(rm, monkeypatch):
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_000.0
    rm._daily_pnl = -25.0
    rm._opens_today = 3
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)

    rm.record_trade_pnl(+5.0, balance=123.0, is_win=True)

    assert rm._start_balance == pytest.approx(975.0)
    assert rm._peak_balance == pytest.approx(980.0)
    assert rm._daily_pnl == pytest.approx(5.0)
    assert rm._opens_today == 0


def test_roll_day_if_needed_acquires_rlock(rm, monkeypatch):
    rm._trading_day = _D11
    rm._start_balance = 1_000.0
    rm._peak_balance = 1_000.0
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    done = threading.Event()

    def worker():
        rm.roll_day_if_needed()
        done.set()

    rm._lock.acquire()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert not done.wait(timeout=0.2)
    finally:
        rm._lock.release()

    assert done.wait(timeout=3.0)
    thread.join(timeout=3.0)
