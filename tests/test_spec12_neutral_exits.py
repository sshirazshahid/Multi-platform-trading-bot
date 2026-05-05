"""Spec §12 neutrality tests.

Background (2026-04-27): five back-to-back ``STALE`` exits with PnL ≈ -$0.09
falsely tripped the 5-consec-global-losses halt and paused the
``claude_portfolio`` family for 12h. Infrastructure exits and scratches must
not extend or break a streak — only real strategy outcomes do.

These tests pin that contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Phase 33 flipped SPEC12 per-symbol/family pause flags to False.
    Re-enable for these tests — they verify the underlying neutrality
    LOGIC still works when the gates are on."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    monkeypatch.setattr("config.SPEC12_SYMBOL_PAUSE_ENABLED", True, raising=False)
    monkeypatch.setattr("config.SPEC12_FAMILY_PAUSE_ENABLED", True, raising=False)
    yield


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager()


def _stale(rm, symbol, family, pnl_pct=-0.1, pnl_usd=-0.09):
    rm.record_trade_result(symbol=symbol, family=family, is_win=False,
                           pnl_usd=pnl_usd, pnl_pct=pnl_pct, reason="STALE")


def _real_loss(rm, symbol, family, pnl_pct=-2.0, pnl_usd=-0.6):
    rm.record_trade_result(symbol=symbol, family=family, is_win=False,
                           pnl_usd=pnl_usd, pnl_pct=pnl_pct, reason="stop_loss")


def test_five_stale_exits_do_not_halt(rm):
    """Five STALE timeouts in a row must not trigger §12 — that was the
    2026-04-27 false halt. claude_portfolio was paused 12h on -$0.09 noise."""
    for sym in ("A", "B", "C", "D", "E"):
        _stale(rm, f"{sym}/USDT", "claude_portfolio")
    assert rm._halted is False
    assert not rm.is_family_paused("claude_portfolio")
    assert not Path("data/review_required.json").exists()


def test_five_real_losses_still_halt(rm):
    """Real strategy losses still trigger §12 — neutrality must not blunt
    the actual safety rail."""
    for sym in ("A", "B", "C", "D", "E"):
        _real_loss(rm, f"{sym}/USDT", "f1")
    assert rm._halted is True
    flag = Path("data/review_required.json")
    assert flag.exists()
    data = json.loads(flag.read_text(encoding="utf-8"))
    assert any("consecutive global losses" in e.get("reason", "") for e in data)


def test_neutral_reasons_do_not_break_loss_streak(rm):
    """A STALE between two real losses must not reset the symbol streak —
    otherwise infrastructure noise hides the underlying losing streak."""
    _real_loss(rm, "BTC/USDT", "f1")
    _stale(rm, "BTC/USDT", "f1")
    _real_loss(rm, "BTC/USDT", "f1")
    assert rm.is_symbol_paused("BTC/USDT"), (
        "two real losses with a STALE between should still pause the symbol")


def test_scratch_pnl_pct_is_neutral(rm):
    """|pnl_pct| < 0.5% (with reason=stop_loss) should be ignored too —
    these are essentially flat exits, not strategy losses."""
    for sym in ("A", "B", "C", "D", "E"):
        rm.record_trade_result(symbol=f"{sym}/USDT", family="f1",
                               is_win=False, pnl_usd=-0.05, pnl_pct=-0.1,
                               reason="stop_loss")
    assert rm._halted is False


def test_legacy_callers_without_pnl_pct_still_work(rm):
    """Pre-2026-04-27 call sites pass only is_win + pnl_usd. Those must
    continue to update streaks (back-compat)."""
    for sym in ("A", "B", "C", "D", "E"):
        rm.record_trade_result(symbol=f"{sym}/USDT", family="legacy",
                               is_win=False, pnl_usd=-1.0)
    assert rm._halted is True


def test_reconcile_reasons_are_neutral(rm):
    """``reconciled_*`` records come from the exchange-history sync path,
    which is not a strategy outcome — they must not extend streaks."""
    for sym, reason in (
        ("A/USDT", "reconciled_from_exchange"),
        ("B/USDT", "reconciled_no_context"),
        ("C/USDT", "ghost_force_close"),
        ("D/USDT", "sl_placement_failed"),
        ("E/USDT", "AGE_LIMIT"),
    ):
        rm.record_trade_result(symbol=sym, family="claude_portfolio",
                               is_win=False, pnl_usd=-1.0, pnl_pct=-3.0,
                               reason=reason)
    assert rm._halted is False
    assert not rm.is_family_paused("claude_portfolio")
