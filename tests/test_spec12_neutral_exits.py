"""Spec §12 neutrality tests.

Background (2026-04-27): five back-to-back ``STALE`` exits with PnL ≈ -$0.09
falsely tripped the 5-consec-global-losses halt and paused the
``claude_portfolio`` family for 12h. Infrastructure exits and scratches must
not extend or break a streak — only real strategy outcomes do.

These tests pin that contract.
"""
from __future__ import annotations

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


def test_five_stale_exits_do_not_extend_streak(rm):
    """Five STALE timeouts in a row must not extend the global streak — that
    was the 2026-04-27 false halt. claude_portfolio was paused 12h on -$0.09 noise."""
    for sym in ("A", "B", "C", "D", "E"):
        _stale(rm, f"{sym}/USDT", "claude_portfolio")
    # Neutral reasons must not accumulate in the global streak
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 0, (
        f"STALE exits should not extend global streak; got {rm._global_streak}")
    assert not rm.is_family_paused("claude_portfolio")
    assert not Path("data/review_required.json").exists()


def test_five_real_losses_extend_global_streak(rm):
    """Real strategy losses must extend the global streak — neutrality must
    not blunt streak tracking even with halts disabled."""
    for sym in ("A", "B", "C", "D", "E"):
        _real_loss(rm, f"{sym}/USDT", "f1")
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 5, (
        f"Real losses must extend global streak; got {rm._global_streak}")


def test_neutral_reasons_do_not_break_loss_streak(rm):
    """A STALE between two real losses must not reset the symbol streak —
    otherwise infrastructure noise hides the underlying losing streak."""
    _real_loss(rm, "BTC/USDT", "f1")
    _stale(rm, "BTC/USDT", "f1")
    _real_loss(rm, "BTC/USDT", "f1")
    # Symbol pauses are disabled, but streak tracking must still record
    # the two real losses without the STALE resetting the buffer.
    sym_streak = rm._symbol_streaks.get("BTC/USDT", [])
    real_losses = [r for r in sym_streak if r is False]
    assert len(real_losses) >= 2, (
        "two real losses with a STALE between should still track in the "
        f"symbol streak buffer; got {sym_streak}")


def test_scratch_pnl_pct_is_neutral(rm):
    """|pnl_pct| < 0.5% (with reason=stop_loss) should be ignored too —
    these are essentially flat exits, not strategy losses."""
    for sym in ("A", "B", "C", "D", "E"):
        rm.record_trade_result(symbol=f"{sym}/USDT", family="f1",
                               is_win=False, pnl_usd=-0.05, pnl_pct=-0.1,
                               reason="stop_loss")
    # Scratch trades must not extend global streak
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 0, (
        f"Scratch trades should not extend global streak; got {rm._global_streak}")


def test_legacy_callers_without_pnl_pct_still_work(rm):
    """Pre-2026-04-27 call sites pass only is_win + pnl_usd. Those must
    continue to update streaks (back-compat)."""
    for sym in ("A", "B", "C", "D", "E"):
        rm.record_trade_result(symbol=f"{sym}/USDT", family="legacy",
                               is_win=False, pnl_usd=-1.0)
    # Legacy callers must still extend the global streak
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 5, (
        f"Legacy callers must extend global streak; got {rm._global_streak}")


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
    # Reconcile reasons must not extend global streak
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 0, (
        f"Reconcile reasons should not extend global streak; got {rm._global_streak}")
    assert not rm.is_family_paused("claude_portfolio")
