"""Symbol/family streak buffers must clear when pause triggers (2026-05-24).

Bug: when SPEC_SYMBOL_LOSSES_TO_PAUSE (2) consecutive losses paused a
symbol, the streak buffer kept `[False, False]`. After the 6-hour
pause expired, a single subsequent loss appended a third False →
condition `not any(sym_hist[-2:])` fired again → instant re-pause.
The cooldown was meaningless on a losing tape. Same mechanism for
family streaks.

Conflicts with the UNBLOCK_ALL standing directive.
"""
from __future__ import annotations

import json
import time as _time
from datetime import date
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "risk_state.json").write_text(json.dumps({
        "is_halted": False, "halt_reason": "", "halt_time": 0.0,
        "daily_pnl": 0.0, "max_drawdown_pct": 0.0,
        "start_balance": 500.0, "peak_balance": 500.0,
        "trading_day": date.today().isoformat(),
        "trades_today": 0, "recent_results": [],
        "trade_history": [], "symbol_pauses": {}, "family_pauses": {},
        "global_streak": [], "timestamp": 0,
    }), encoding="utf-8")
    yield


def _record_loss(rm, symbol="BTC/USDT:USDT", family="claude_portfolio"):
    rm.record_trade_result(
        symbol=symbol, family=family, is_win=False,
        pnl_usd=-0.50, pnl_pct=-1.5, reason="stop_loss",
    )


def _enable_symbol_pause(monkeypatch):
    """Both flags must be True for the symbol-pause logic to fire.
    UNBLOCK_ALL leaves them False by default; tests force them on."""
    import config
    monkeypatch.setattr(config, "SPEC12_SYMBOL_PAUSE_ENABLED", True)
    monkeypatch.setitem(config.HALT_MECHANISMS, "symbol_pause", True)


def _enable_family_pause(monkeypatch):
    import config
    monkeypatch.setattr(config, "SPEC12_FAMILY_PAUSE_ENABLED", True)
    monkeypatch.setitem(config.HALT_MECHANISMS, "family_pause", True)


def test_symbol_streak_clears_on_pause_fire(monkeypatch):
    _enable_symbol_pause(monkeypatch)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    sym = "BTC/USDT:USDT"
    _record_loss(rm, sym)
    _record_loss(rm, sym)  # 2nd loss — triggers pause
    assert sym in rm._symbol_pauses
    assert rm._symbol_streaks.get(sym, []) == [], (
        f"streak must reset on pause; got {rm._symbol_streaks.get(sym)}"
    )


def test_family_streak_clears_on_pause_fire(monkeypatch):
    _enable_family_pause(monkeypatch)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    fam = "claude_portfolio"
    for i in range(3):  # 3 = SPEC_FAMILY_LOSSES_TO_PAUSE
        _record_loss(rm, symbol=f"X{i}/USDT:USDT", family=fam)
    assert fam in rm._family_pauses
    assert rm._family_streaks.get(fam, []) == [], (
        f"family streak must reset on pause; got {rm._family_streaks.get(fam)}"
    )


def test_symbol_no_immediate_repause_after_cooldown(monkeypatch):
    """The full UNBLOCK_ALL scenario: pause fires, time advances past the
    cooldown, a single loss should NOT immediately re-pause."""
    _enable_symbol_pause(monkeypatch)
    from core.risk_manager import RiskManager, SPEC_SYMBOL_LOSSES_TO_PAUSE
    rm = RiskManager()
    sym = "BTC/USDT:USDT"
    for _ in range(SPEC_SYMBOL_LOSSES_TO_PAUSE):
        _record_loss(rm, sym)
    assert sym in rm._symbol_pauses

    # Advance "time" past the pause window — simulate by clearing the
    # pause entry (`is_symbol_paused` returns False once expired).
    del rm._symbol_pauses[sym]

    # Single subsequent loss after cooldown must NOT re-pause.
    _record_loss(rm, sym)
    assert sym not in rm._symbol_pauses, (
        "single post-cooldown loss must not instantly re-trigger pause"
    )
