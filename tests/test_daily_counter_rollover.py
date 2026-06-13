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
portfolio cycle (decoupled from entry execution). These tests pin: (a) the
method resets stale counters with zero entries, (b) it is idempotent within a
day, (c) `can_trade()` still rolls over (behavior preserved).
"""

from __future__ import annotations

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
    rm._daily_pnl = -55.97
    rm._opens_today = 200

    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    rolled = rm.roll_day_if_needed()

    assert rolled is True
    assert rm._trading_day == _D13
    assert rm._daily_pnl == 0.0
    assert rm._opens_today == 0


def test_roll_day_if_needed_is_idempotent_within_day(rm, monkeypatch):
    """Calling every cycle must not clobber same-day counters after the first roll."""
    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    rm.roll_day_if_needed()  # establishes today
    rm._daily_pnl = -3.0
    rm._opens_today = 5

    rolled = rm.roll_day_if_needed()  # same day → no-op

    assert rolled is False
    assert rm._daily_pnl == -3.0
    assert rm._opens_today == 5


def test_can_trade_still_rolls_over(rm, monkeypatch):
    """Behavior preserved: the entry-path rollover still fires via can_trade()."""
    rm._trading_day = _D11
    rm._daily_pnl = -55.97
    rm._opens_today = 200

    monkeypatch.setattr(rmod, "_utc_today", lambda: _D13)
    rm.can_trade(open_position_count=0)

    assert rm._trading_day == _D13
    assert rm._opens_today == 0
    assert rm._daily_pnl == 0.0
