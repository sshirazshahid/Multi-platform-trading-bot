"""Regression tests for halt persistence (Bug 1) and trade-recording None-pnl_pct
handling (Bug 2B) in core/risk_manager.py.

Bug 1 — Spec §12 halt got silently wiped across restarts:
  - 1A: `_load_state` must honour `data/review_required.json` on BOTH same-day
        AND new-day branches, overriding `risk_state.json`.
  - 1B: `_should_auto_resume` must delete the flag BEFORE clearing `_halted`;
        if the unlink fails, stay halted (return False).
  - 1C: every `self._halted = True` site must pair with `self._halt_time = now()`
        so auto-resume cannot see infinite elapsed.
  - 1D: startup with the flag present logs a WARNING with elapsed minutes and
        cooldown remaining.

Bug 2B — `record_trade_pnl(pnl_pct=None, pnl=-0.50, is_win=False)`:
  - `_trade_history[-1][1]` must be exactly `None` (not 0.0).
  - The dollar `pnl` still moves `_daily_pnl`.
  - Spec §12 streak (`_global_streak`) is fed by the separately-called
    `record_trade_result(is_win=...)`. It does NOT depend on pnl_pct, so a
    None pct on the dollar-PnL path leaves `record_trade_result`'s
    streak-append behaviour intact — assert that here as a regression guard.
"""
from __future__ import annotations

import json
import time as _time
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.risk_manager import (
    _REVIEW_FLAG_PATH,
    SPEC12_AUTO_RESUME_COOLDOWN_MIN,
    RiskManager,
)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _write_review_flag(ts: float | None = None,
                        reason: str = "5 consecutive global losses",
                        action: str = "force_observation_mode") -> float:
    if ts is None:
        ts = _time.time()
    payload = [{"ts": ts, "reason": reason, "action": action}]
    _REVIEW_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REVIEW_FLAG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    return ts


def _write_risk_state(*, is_halted: bool, halt_reason: str,
                       halt_time: float, trading_day: date) -> None:
    state = {
        "is_halted": is_halted,
        "halt_reason": halt_reason,
        "halt_time": halt_time,
        "daily_pnl": 0.0,
        "max_drawdown_pct": 0.0,
        "start_balance": 400.0,
        "peak_balance": 400.0,
        "trading_day": trading_day.isoformat(),
        "trades_today": 0,
        "recent_results": [],
        "trade_history": [],
        "symbol_pauses": {},
        "family_pauses": {},
        "global_streak": [],
        "timestamp": _time.time(),
    }
    Path("data/risk_state.json").write_text(json.dumps(state), encoding="utf-8")


# ─────────────────────── Bug 1A — same-day branch ────────────────────────

def test_load_state_same_day_honours_review_flag():
    """risk_state.json says is_halted=False, but a review flag is on disk.
    The flag wins — the bot must come up halted."""
    flag_ts = _time.time() - 60  # 1 min ago
    _write_review_flag(ts=flag_ts)
    _write_risk_state(is_halted=False, halt_reason="",
                      halt_time=0.0, trading_day=date.today())

    rm = RiskManager()

    assert rm._halted is True
    assert rm._halt_time == pytest.approx(flag_ts, abs=1.0)
    assert "spec12" in rm._halt_reason


# ─────────────────────── Bug 1A — new-day branch ─────────────────────────

def test_load_state_new_day_honours_review_flag():
    """The new-day branch used to hard-clear `_halted`. With the flag on disk,
    that wipe is wrong — Spec §12 must persist across the midnight boundary."""
    flag_ts = _time.time() - 60
    _write_review_flag(ts=flag_ts)
    yesterday = date.today() - timedelta(days=1)
    _write_risk_state(is_halted=True, halt_reason="spec12:5_consec_global_losses",
                      halt_time=flag_ts, trading_day=yesterday)

    rm = RiskManager()

    assert rm._halted is True
    assert rm._halt_time == pytest.approx(flag_ts, abs=1.0)
    assert "spec12" in rm._halt_reason


# ─────────────────────── Bug 1B — unlink-fails-stays-halted ──────────────

def test_auto_resume_blocked_when_flag_unlink_fails(monkeypatch):
    """If we cannot delete review_required.json, we MUST stay halted.
    Previously a silent except swallowed the error, leaving _halted cleared
    in memory while the flag persisted on disk."""
    flag_ts = _time.time() - (SPEC12_AUTO_RESUME_COOLDOWN_MIN + 1) * 60
    _write_review_flag(ts=flag_ts)

    rm = RiskManager()
    assert rm._halted is True, "fixture: flag should have re-halted on load"

    # Make Path.unlink raise; capture that the function returns False AND
    # leaves _halted unchanged.
    def boom(self, *a, **kw):
        raise PermissionError("simulated AV file lock")

    monkeypatch.setattr(Path, "unlink", boom)
    result = rm._should_auto_resume()

    assert result is False, "unlink failure must NOT auto-resume"
    assert rm._halted is True, "_halted must remain True if unlink failed"
    # Flag is still on disk (since unlink raised) — the next poll has the same
    # opportunity to retry. No silent wipe.
    assert _REVIEW_FLAG_PATH.exists()


def test_auto_resume_succeeds_when_unlink_succeeds():
    """Sanity: when unlink works, the cooldown elapses, the flag is deleted,
    and we resume cleanly. Pairs with the unlink-fails test as a regression
    pair."""
    flag_ts = _time.time() - (SPEC12_AUTO_RESUME_COOLDOWN_MIN + 1) * 60
    _write_review_flag(ts=flag_ts)

    rm = RiskManager()
    assert rm._halted is True

    assert rm._should_auto_resume() is True
    assert not _REVIEW_FLAG_PATH.exists()


# ─────────────────────── Bug 1C — halt_time always paired ────────────────

def test_record_trade_result_pairs_halted_with_halt_time():
    """5 consecutive global losses must set BOTH `_halted=True` AND
    `_halt_time = now()`. A 0.0 halt_time would let `_should_auto_resume`
    see (now - 0)/60 = huge elapsed and wipe the halt on first poll."""
    rm = RiskManager()
    before = _time.time()
    for i in range(5):
        rm.record_trade_result(symbol=f"S{i}/USDT", family="f", is_win=False,
                                pnl_usd=-0.5)
    after = _time.time()

    assert rm._halted is True
    assert before <= rm._halt_time <= after, (
        f"_halt_time must be set to current time at the halt site, "
        f"got {rm._halt_time}")


# ─────────────────────── Bug 1D — startup WARNING log ────────────────────

def test_load_state_with_flag_logs_warning_with_cooldown_remaining():
    """Operator must be able to see the bot is in Spec §12 state by reading
    the startup log. The line must include elapsed minutes and remaining
    cooldown."""
    elapsed_min = 30.0
    flag_ts = _time.time() - elapsed_min * 60
    _write_review_flag(ts=flag_ts)

    captured = []

    from loguru import logger
    sink_id = logger.add(lambda m: captured.append(m), level="WARNING")
    try:
        RiskManager()
    finally:
        logger.remove(sink_id)

    joined = "".join(captured)
    assert "review_required.json" in joined
    assert "cooldown remaining" in joined
    # Remaining = COOLDOWN - elapsed; check the number is in the message.
    remaining = SPEC12_AUTO_RESUME_COOLDOWN_MIN - elapsed_min
    assert f"{remaining:.1f}" in joined or f"{remaining:.0f}" in joined


# ─────────────────────── Bug 2B — None pnl_pct preservation ──────────────

def test_record_trade_pnl_with_none_pnl_pct_stores_none_in_history():
    """pnl_pct=None (reconciled from exchange ledger without entry/size) must
    NOT be stored as 0.0 in trade_history. Storing 0.0 poisons Kelly avg-win/
    avg-loss math; storing None lets `_kelly_fraction` correctly skip."""
    rm = RiskManager()
    rm._start_balance = 100.0

    rm.record_trade_pnl(pnl=-0.50, balance=100.0, is_win=False, pnl_pct=None)

    assert rm._trade_history, "trade_history must receive an entry"
    last_is_win, last_pct = rm._trade_history[-1]
    assert last_is_win is False
    assert last_pct is None, f"expected None, got {last_pct!r}"


def test_record_trade_pnl_with_none_pnl_pct_keeps_dollar_pnl():
    """Daily PnL still moves by the dollar amount even when pnl_pct is None
    — exchange-realized USDT is ground truth."""
    rm = RiskManager()
    rm._start_balance = 100.0

    rm.record_trade_pnl(pnl=-0.50, balance=100.0, is_win=False, pnl_pct=None)

    assert rm._daily_pnl == pytest.approx(-0.50)


def test_record_trade_result_independent_of_pnl_pct():
    """The Spec §12 streak path takes is_win directly. Even with the dollar
    path receiving pnl_pct=None, the streak append must still register."""
    rm = RiskManager()
    rm._start_balance = 100.0

    # Simulate the order_manager pair: dollar PnL with None pct + Spec §12 result
    rm.record_trade_pnl(pnl=-0.50, balance=100.0, is_win=False, pnl_pct=None)
    rm.record_trade_result(symbol="BTC/USDT", family="f", is_win=False,
                            pnl_usd=-0.50)

    assert rm._global_streak[-1] is False
