"""Symbol/family streak buffer tracking verification (2026-05-27).

Original tests (2026-05-24) verified that streak buffers cleared when
pause triggers fired. With halt/pause mechanisms removed, these tests
verify that streak buffers still correctly accumulate losses and that
wins properly reset consecutive-loss runs.
"""
from __future__ import annotations

import json
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "risk_state.json").write_text(json.dumps({
        "halt_reason": "", "halt_time": 0.0,
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


def _record_win(rm, symbol="BTC/USDT:USDT", family="claude_portfolio"):
    rm.record_trade_result(
        symbol=symbol, family=family, is_win=True,
        pnl_usd=+1.00, pnl_pct=+2.0, reason="take_profit",
    )


def test_symbol_streak_accumulates_losses():
    """Two consecutive losses on the same symbol accumulate in the streak buffer."""
    from core.risk_manager import RiskManager
    rm = RiskManager()
    sym = "BTC/USDT:USDT"
    _record_loss(rm, sym)
    _record_loss(rm, sym)
    streak = rm._symbol_streaks.get(sym, [])
    losses = [r for r in streak if r is False]
    assert len(losses) == 2, (
        f"Expected 2 losses in symbol streak; got {streak}"
    )


def test_family_streak_accumulates_losses():
    """Three consecutive losses on a family accumulate in the streak buffer."""
    from core.risk_manager import RiskManager
    rm = RiskManager()
    fam = "claude_portfolio"
    for i in range(3):
        _record_loss(rm, symbol=f"X{i}/USDT:USDT", family=fam)
    streak = rm._family_streaks.get(fam, [])
    losses = [r for r in streak if r is False]
    assert len(losses) == 3, (
        f"Expected 3 losses in family streak; got {streak}"
    )


def test_win_breaks_consecutive_loss_run_in_symbol_streak():
    """A win between losses breaks the consecutive-loss run. After the
    win, a single subsequent loss should show only 1 trailing loss."""
    from core.risk_manager import RiskManager
    rm = RiskManager()
    sym = "BTC/USDT:USDT"
    _record_loss(rm, sym)
    _record_loss(rm, sym)
    _record_win(rm, sym)
    _record_loss(rm, sym)

    streak = rm._symbol_streaks.get(sym, [])
    # The tail should be [..., True, False] — only 1 consecutive loss
    assert streak[-1] is False
    assert streak[-2] is True, (
        f"Win should break consecutive-loss run; got {streak}"
    )
