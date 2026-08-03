"""Live-friendly risk-cap tests (2026-04-27).

After 16h/9-loss bleed in CONTROLLED_LIVE the user opted to keep live
trading on but trim risk aggressively. These tests pin the new caps so
they don't silently regress.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


def test_same_day_restart_keeps_start_balance_no_double_count(rm):
    """Audit 2026-06-03: on a same-day restart, set_start_balance() must KEEP the
    disk-restored start-of-day balance, NOT overwrite it with current equity (which
    already includes today's realized PnL). Overwriting double-counts the day's loss
    in effective_balance (_start_balance + _daily_pnl) and deflates the daily-loss-
    breaker budget (loss_budget = max_loss_pct * _start_balance)."""
    rm.set_start_balance(400.0)        # start of day: start=peak=400
    rm._daily_pnl = -20.0              # lose $20 today (realized)
    rm._save_state()                   # persists start=400, daily_pnl=-20, peak=400
    fresh = RiskManager()              # same-day restart restores start=400, daily_pnl=-20, peak=400
    assert fresh._start_balance == 400.0 and fresh._daily_pnl == -20.0
    # bot startup passes CURRENT equity (400-20=380); it must NOT clobber start_balance
    fresh.set_start_balance(380.0)
    assert fresh._start_balance == 400.0, "start_balance overwritten -> today's PnL double-counted"
    assert abs((fresh._start_balance + fresh._daily_pnl) - 380.0) < 1e-6  # effective = 380, not 360


def test_same_day_restart_with_zero_start_balance_fails_closed():
    """A polluted ledger must not be reconstructed from a wallet snapshot."""
    Path("data/risk_state.json").write_text(json.dumps({
        "daily_pnl": -1.0,
        "start_balance": 0.0,
        "peak_balance": 99.0,
        "trading_day": datetime.now(timezone.utc).date().isoformat(),
        "trades_today": 0,
    }), encoding="utf-8")

    fresh = RiskManager()
    fresh.set_start_balance(15_595.18)

    assert fresh._start_balance == 0.0
    assert fresh._peak_balance == 0.0
    assert fresh._daily_pnl == pytest.approx(-1.0)
    assert fresh._daily_anchor_valid is False
    assert fresh.can_trade(open_position_count=0) is False
    assert fresh.halt_reason == "daily_equity_anchor_invalid"
    persisted = json.loads(Path("data/risk_state.json").read_text(encoding="utf-8"))
    assert persisted["start_balance"] == 0.0
    assert persisted["daily_anchor_valid"] is False


def test_opens_today_resets_on_new_utc_day(rm, monkeypatch):
    """Crossing UTC midnight inside can_trade() must reset the counter."""
    import datetime as _dt

    import core.risk_manager as rmod
    monkeypatch.setitem(rmod.RISK, "max_trades_per_day", 2)
    rm.set_start_balance(1_000.0)

    rm.note_trade_opened()
    rm.note_trade_opened()
    assert rm.can_trade(open_position_count=0) is False

    # Simulate the next UTC day arriving without restart. can_trade() now reads the UTC
    # calendar date via _utc_today() (audit 2026-06-03 fix: code had drifted to local
    # date()), so patch that single source of truth.
    next_day = rm._trading_day + _dt.timedelta(days=1)
    monkeypatch.setattr(rmod, "_utc_today", lambda: next_day)
    assert rm.can_trade(open_position_count=0) is True
    assert rm._opens_today == 0


def test_shorts_unblock_all_overrides_post_mortem_window(monkeypatch):
    """2026-04-28 (UNBLOCK_ALL/A): shorts_blocked() now always returns
    False per user directive. The post-mortem-driven window AND the
    config.SHORTS_DISABLED flag are short-circuited at the method top.

    Restoring the kill-switch semantics requires removing the early
    `return False` in `core.auto_mutator.AutoMutator.shorts_blocked` —
    after which a re-enabled assertion (preserved below as commented
    expected behaviour) becomes the right test:

        monkeypatch.setattr(config, "SHORTS_DISABLED", True)
        assert am.shorts_blocked() is True

    Until then, this test pins the UNBLOCK_ALL contract.
    """
    import config
    from core.auto_mutator import AutoMutator
    am = AutoMutator()
    am._state["shorts_blocked_until"] = 0  # post-mortem rule inactive

    monkeypatch.setattr(config, "SHORTS_DISABLED", False)
    assert am.shorts_blocked() is False

    monkeypatch.setattr(config, "SHORTS_DISABLED", True)
    assert am.shorts_blocked() is False, (
        "UNBLOCK_ALL/A overrides the SHORTS_DISABLED kill-switch — "
        "see core.auto_mutator.AutoMutator.shorts_blocked docstring."
    )

    # Even with an active post-mortem window, UNBLOCK_ALL must dominate.
    am._state["shorts_blocked_until"] = 9999999999
    monkeypatch.setattr(config, "SHORTS_DISABLED", False)
    assert am.shorts_blocked() is False
