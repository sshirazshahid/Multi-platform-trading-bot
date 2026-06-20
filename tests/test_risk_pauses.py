"""Spec §12 pause-rule tests for RiskManager.

Covers streak tracking, operational hazard pauses (spread hazard,
order-rejection rolling window, stale-data detection).

Note: symbol/family pauses via record_trade_result and the global halt
mechanism have been removed. Tests for those are replaced by streak-tracking
verification. Operational pauses (spread hazard, order rejection) remain active.
"""
from __future__ import annotations

import pytest

from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Run every test with a pristine state dir so previous runs don't bleed."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager()


def _loss(rm, symbol, family, n):
    for _ in range(n):
        rm.record_trade_result(symbol=symbol, family=family,
                               is_win=False, pnl_usd=-0.5)


def test_two_losses_track_symbol_streak(rm):
    """Two consecutive losses on a symbol are recorded in the streak buffer."""
    _loss(rm, "BTC/USDT", "systematic_v3_1", 2)
    sym_streak = rm._symbol_streaks.get("BTC/USDT", [])
    losses = [r for r in sym_streak if r is False]
    assert len(losses) == 2, (
        f"Expected 2 losses in symbol streak; got {sym_streak}")


def test_one_loss_does_not_pause(rm):
    _loss(rm, "BTC/USDT", "systematic_v3_1", 1)
    assert not rm.is_symbol_paused("BTC/USDT")


def test_three_losses_track_family_streak(rm):
    """Three consecutive losses on a family are recorded in the streak buffer."""
    _loss(rm, "BTC/USDT", "Supertrend", 1)
    _loss(rm, "ETH/USDT", "Supertrend", 1)
    _loss(rm, "SOL/USDT", "Supertrend", 1)
    fam_streak = rm._family_streaks.get("Supertrend", [])
    losses = [r for r in fam_streak if r is False]
    assert len(losses) == 3, (
        f"Expected 3 losses in family streak; got {fam_streak}")


def test_five_global_losses_track_streak(rm):
    """Five consecutive global losses are recorded in the global streak."""
    _loss(rm, "A/USDT", "f1", 1)
    _loss(rm, "B/USDT", "f2", 1)
    _loss(rm, "C/USDT", "f3", 1)
    _loss(rm, "D/USDT", "f4", 1)
    _loss(rm, "E/USDT", "f5", 1)
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 5, (
        f"Expected 5 losses in global streak; got {rm._global_streak}")


def test_five_global_losses_do_not_halt(rm):
    """Spec §12 global halt was removed 2026-05-27: 5 consecutive losses must
    NOT write data/review_required.json and must NOT switch OPERATING_MODE.
    Only the streak is tracked (for audit); per-trade SLs remain the loss rail.
    """
    from pathlib import Path
    for i in range(5):
        _loss(rm, f"S{i}/USDT", f"fam{i}", 1)
    assert not Path("data/review_required.json").exists(), (
        "Loss streak wrote a review flag — the Spec §12 halt should be removed")


def test_win_resets_streak(rm):
    _loss(rm, "BTC/USDT", "x", 1)
    rm.record_trade_result(symbol="BTC/USDT", family="x",
                           is_win=True, pnl_usd=+1.0)
    _loss(rm, "BTC/USDT", "x", 1)
    # A win between two losses should break the consecutive-loss run
    sym_streak = rm._symbol_streaks.get("BTC/USDT", [])
    # The streak should contain [False, True, False] — only one consecutive loss at the tail
    assert sym_streak[-1] is False
    assert any(r is True for r in sym_streak), (
        f"Win should appear in symbol streak; got {sym_streak}")


def test_order_rejection_pauses_after_threshold(rm):
    for _ in range(3):
        rm.note_order_rejection("BTC/USDT", "minQty")
    assert rm.is_symbol_paused("BTC/USDT")


def test_stale_data_returns_true_only_when_stale(rm):
    assert rm.note_stale_data("binance", 10.0) is False
    assert rm.note_stale_data("binance", 120.0) is True


def test_spread_hazard_only_pauses_after_persistence(monkeypatch, rm):
    """First observation starts the timer; we fast-forward 6 minutes and
    confirm the second observation trips the pause."""
    import core.risk_manager as rmod
    base = 1_000_000.0
    monkeypatch.setattr(rmod, "_time", _FakeTime(base))
    rm.note_spread_hazard("BTC/USDT", 0.99)
    assert not rm.is_symbol_paused("BTC/USDT")
    monkeypatch.setattr(rmod, "_time", _FakeTime(base + 360))  # +6 min
    rm.note_spread_hazard("BTC/USDT", 0.99)
    assert rm.is_symbol_paused("BTC/USDT")


def test_spread_hazard_resets_on_low_reading(monkeypatch, rm):
    import core.risk_manager as rmod
    base = 2_000_000.0
    monkeypatch.setattr(rmod, "_time", _FakeTime(base))
    rm.note_spread_hazard("BTC/USDT", 0.99)
    # A normal reading clears the tracker
    rm.note_spread_hazard("BTC/USDT", 0.30)
    monkeypatch.setattr(rmod, "_time", _FakeTime(base + 600))
    rm.note_spread_hazard("BTC/USDT", 0.99)  # timer starts over
    assert not rm.is_symbol_paused("BTC/USDT")


class _FakeTime:
    def __init__(self, now: float):
        self._now = now

    def time(self) -> float:
        return self._now
