"""Spec §12 pause-rule tests for RiskManager.

Covers 2-symbol/3-family/5-global pauses, outlier flagging, spread hazard
persistence, order-rejection rolling window, and stale-data detection.
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path

import pytest

from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Run every test with a pristine state dir so previous runs don't bleed."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager()


def _loss(rm, symbol, family, n):
    for _ in range(n):
        rm.record_trade_result(symbol=symbol, family=family,
                               is_win=False, pnl_usd=-0.5)


def test_two_losses_pause_symbol(rm):
    _loss(rm, "BTC/USDT", "systematic_v3_1", 2)
    assert rm.is_symbol_paused("BTC/USDT")


def test_one_loss_does_not_pause(rm):
    _loss(rm, "BTC/USDT", "systematic_v3_1", 1)
    assert not rm.is_symbol_paused("BTC/USDT")


def test_three_losses_pause_family(rm):
    _loss(rm, "BTC/USDT", "Supertrend", 1)
    _loss(rm, "ETH/USDT", "Supertrend", 1)
    _loss(rm, "SOL/USDT", "Supertrend", 1)
    assert rm.is_family_paused("Supertrend")


def test_five_global_losses_write_review_flag(rm):
    _loss(rm, "A/USDT", "f1", 1)
    _loss(rm, "B/USDT", "f2", 1)
    _loss(rm, "C/USDT", "f3", 1)
    _loss(rm, "D/USDT", "f4", 1)
    _loss(rm, "E/USDT", "f5", 1)
    flag = Path("data/review_required.json")
    assert flag.exists()
    data = json.loads(flag.read_text(encoding="utf-8"))
    assert any("consecutive global losses" in e.get("reason", "") for e in data)


def test_win_resets_streak(rm):
    _loss(rm, "BTC/USDT", "x", 1)
    rm.record_trade_result(symbol="BTC/USDT", family="x",
                           is_win=True, pnl_usd=+1.0)
    _loss(rm, "BTC/USDT", "x", 1)
    assert not rm.is_symbol_paused("BTC/USDT"), (
        "two non-consecutive losses should not trip the pause")


def test_outlier_loss_writes_review_flag(rm):
    rm.record_trade_result(symbol="BTC/USDT", family="x",
                           is_win=False, pnl_usd=-99.0)
    flag = Path("data/review_required.json")
    assert flag.exists()
    data = json.loads(flag.read_text(encoding="utf-8"))
    assert any("outlier_loss" in e.get("reason", "") for e in data)


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
