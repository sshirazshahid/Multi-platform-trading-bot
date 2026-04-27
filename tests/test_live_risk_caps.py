"""Live-friendly risk-cap tests (2026-04-27).

After 16h/9-loss bleed in CONTROLLED_LIVE the user opted to keep live
trading on but trim risk aggressively. These tests pin the new caps so
they don't silently regress.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager()


def test_can_trade_blocks_after_max_trades_per_day(rm, monkeypatch):
    """RISK['max_trades_per_day'] must hard-cap opens per UTC day."""
    import core.risk_manager as rmod
    monkeypatch.setitem(rmod.RISK, "max_trades_per_day", 3)

    assert rm.can_trade(open_position_count=0) is True
    rm.note_trade_opened()
    assert rm.can_trade(open_position_count=0) is True
    rm.note_trade_opened()
    assert rm.can_trade(open_position_count=0) is True
    rm.note_trade_opened()
    # 3 opens used → cap reached
    assert rm.can_trade(open_position_count=0) is False


def test_can_trade_zero_cap_disables_check(rm, monkeypatch):
    """A 0 / missing cap must not block — back-compat for callers that
    haven't opted in to the per-day enforcement."""
    import core.risk_manager as rmod
    monkeypatch.setitem(rmod.RISK, "max_trades_per_day", 0)
    for _ in range(50):
        rm.note_trade_opened()
    assert rm.can_trade(open_position_count=0) is True


def test_opens_today_persists_across_save_load(rm, monkeypatch):
    """A mid-day process restart must restore the day's open count so the
    cap survives the crash-restart loop."""
    import core.risk_manager as rmod
    monkeypatch.setitem(rmod.RISK, "max_trades_per_day", 5)

    rm.note_trade_opened()
    rm.note_trade_opened()
    rm.note_trade_opened()
    # Persist + reload as a fresh instance — same data dir, same trading_day.
    rm._save_state()
    fresh = RiskManager()
    assert fresh._opens_today == 3
    fresh.note_trade_opened()
    fresh.note_trade_opened()
    # 5 used → cap reached
    assert fresh.can_trade(open_position_count=0) is False


def test_opens_today_resets_on_new_utc_day(rm, monkeypatch):
    """Crossing UTC midnight inside can_trade() must reset the counter."""
    import datetime as _dt

    import core.risk_manager as rmod
    monkeypatch.setitem(rmod.RISK, "max_trades_per_day", 2)

    rm.note_trade_opened()
    rm.note_trade_opened()
    assert rm.can_trade(open_position_count=0) is False

    # Simulate the next UTC day arriving without restart.
    next_day = rm._trading_day + _dt.timedelta(days=1)

    class _StubDate:
        @staticmethod
        def today():
            return next_day

    monkeypatch.setattr(rmod, "date", _StubDate)
    assert rm.can_trade(open_position_count=0) is True
    assert rm._opens_today == 0


def test_shorts_disabled_kill_switch(monkeypatch):
    """config.SHORTS_DISABLED=True must block shorts immediately, even
    when the post-mortem-driven window is inactive."""
    import config

    from core.auto_mutator import AutoMutator
    am = AutoMutator()
    am._state["shorts_blocked_until"] = 0  # post-mortem rule inactive

    monkeypatch.setattr(config, "SHORTS_DISABLED", False)
    assert am.shorts_blocked() is False

    monkeypatch.setattr(config, "SHORTS_DISABLED", True)
    assert am.shorts_blocked() is True
