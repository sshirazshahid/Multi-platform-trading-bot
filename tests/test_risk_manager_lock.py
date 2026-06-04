"""Regression: RiskManager.record_trade_pnl must serialize its PnL accumulator.

Audit 2026-06-04 (HIGH, LIVE-active): `self._daily_pnl += pnl` in record_trade_pnl
was an unlocked read-modify-write. record_trade_pnl runs from the post-close hook,
which PositionTracker fires OUTSIDE its own lock, on whatever thread closed the
position — the 10s SL/TP daemon's ThreadPoolExecutor (fanned per exchange×{spot,
futures}), the 5-min cycle, the 90s monitor. Two concurrent closes interleave the
+= and one loss is dropped, so the day's cumulative loss is under-counted and the
LIVE daily-loss breaker (can_trade: `_daily_pnl <= -loss_budget`) trips late,
briefly permitting new real-money entries past the configured cap.

Fix: a threading.RLock guards record_trade_pnl's body (the day-rollover reset, the
+= , the _recent_results / _trade_history list ops consumed by Kelly/scaling, and
the peak update). Behavior-preserving in PAPER and LIVE — pure serialization.

(record_trade_result is intentionally NOT locked: all its action blocks —
per-symbol / per-family / global-streak halt / outlier flag — are gated `if False`
(disabled), so its streak mutations drive no live decision today. Lock it if a
streak pause is ever re-enabled.)

Deterministic test: hold the lock and prove the mutator blocks.
"""
from __future__ import annotations

import threading
from pathlib import Path


def _rm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(400.0)
    return rm


def test_record_trade_pnl_acquires_lock(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    done = threading.Event()

    def worker():
        rm.record_trade_pnl(-5.0, balance=400.0, is_win=False)
        done.set()

    rm._lock.acquire()
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        assert not done.wait(timeout=0.4), "record_trade_pnl did NOT wait on self._lock"
    finally:
        rm._lock.release()
    assert done.wait(timeout=3.0), "record_trade_pnl never completed after lock release"
    t.join(timeout=3.0)
    # and the accumulator actually moved
    assert abs(rm._daily_pnl - (-5.0)) < 1e-9


def test_serial_accumulation_still_correct(tmp_path, monkeypatch):
    """Sanity: serialized accumulation sums correctly (no behavior change)."""
    rm = _rm(tmp_path, monkeypatch)
    for _ in range(10):
        rm.record_trade_pnl(-1.0, balance=400.0, is_win=False)
    assert abs(rm._daily_pnl - (-10.0)) < 1e-9


def test_lock_source_pin():
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    assert "self._lock" in src and "threading.RLock()" in src
    i = src.index("def record_trade_pnl")
    body = src[i:i + 2000]
    assert "with self._lock:" in body, "record_trade_pnl must hold self._lock"
