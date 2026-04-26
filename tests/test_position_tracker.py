"""Tests for PositionTracker.reconcile_closed_pnl phantom-PnL fix (Bug 2A).

When Binance's income ledger arrives without entry/exit/size context, the
tracker used to synthesize entry=1.0, size=1.0 and compute a fabricated
pnl_pct of -13% to -19%. Those bogus percentages bypassed the 1.5-3.5%
ATR SL clamp and poisoned risk_state, kelly_stats, and knowledge_model.

Per binary-baking-melody Phase 2A: when entry, exit_p, AND size are all
non-positive, trust only the dollar realized PnL from Binance, set
pnl_pct=None, and tag close_reason="reconciled_no_context".
"""
from __future__ import annotations

import time as _time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.position_tracker import Position, PositionTracker


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Each test gets its own data/ dir so saves don't touch real state."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _make_exchange_with_records(records):
    """Build a stub exchange exposing fetch_closed_pnl()."""
    ex = MagicMock()
    ex.fetch_closed_pnl = MagicMock(return_value=records)
    return ex


def test_reconcile_no_context_sets_pnl_pct_none():
    """Binance income with entry=0, exit=0, size=0 → pnl_pct must be None.

    Phantom synthesis is forbidden: pos.pnl carries the dollar amount,
    pos.pnl_pct stays None so downstream consumers know the percent is
    unknown rather than a fabricated -13% to -19% loss.
    """
    tracker = PositionTracker()
    now = _time.time()
    record = {
        "symbol":       "ALGO/USDT:USDT",
        "exchange":     "binance",
        "side":         "buy",
        "realized_pnl": -1.50,
        "entry_price":  0.0,    # missing
        "exit_price":   0.0,    # missing
        "size":         0.0,    # missing
        "leverage":     3,
        "close_time":   now - 30,
    }
    ex = _make_exchange_with_records([record])
    imported = tracker.reconcile_closed_pnl({"binance": ex},
                                             since_ts=now - 120)
    assert imported == 1
    closed = tracker._closed
    assert len(closed) == 1
    pos = closed[0]
    assert pos.pnl == pytest.approx(-1.50)
    assert pos.pnl_pct is None
    assert pos.close_reason == "reconciled_no_context"
    # Phantom synthesis forbidden: entry=1.0/size=1.0 must not be written.
    # These values previously poisoned positions.json and the warehouse.
    assert pos.entry_price != 1.0, (
        "entry_price=1.0 is the phantom-synthesis marker — must not be persisted")
    assert pos.size != 1.0, (
        "size=1.0 is the phantom-synthesis marker — must not be persisted")


def test_reconcile_with_full_context_computes_pct_normally():
    """Existing behavior unchanged when entry/exit/size are all valid."""
    tracker = PositionTracker()
    now = _time.time()
    record = {
        "symbol":       "BTC/USDT:USDT",
        "exchange":     "binance",
        "side":         "buy",
        "realized_pnl": -1.00,
        "entry_price":  100.0,
        "exit_price":   98.0,
        "size":         0.5,
        "leverage":     1,
        "close_time":   now - 30,
    }
    ex = _make_exchange_with_records([record])
    imported = tracker.reconcile_closed_pnl({"binance": ex},
                                             since_ts=now - 120)
    assert imported == 1
    pos = tracker._closed[0]
    assert pos.pnl == pytest.approx(-1.00)
    # notional = 100 * 0.5 = 50, margin = 50/1 = 50, pct = -1/50*100 = -2.0
    assert pos.pnl_pct == pytest.approx(-2.0)
    assert pos.close_reason == "reconciled_from_exchange"


def test_reconcile_no_context_is_idempotent():
    """Re-running reconcile on the same record must not double-count.

    The dedup key is (exchange, symbol, close_time//60). A second pass over
    the same Binance income row should import zero new trades, leaving
    _closed length, pnl, and pnl_pct=None untouched.
    """
    tracker = PositionTracker()
    now = _time.time()
    record = {
        "symbol":       "ALGO/USDT:USDT",
        "exchange":     "binance",
        "side":         "buy",
        "realized_pnl": -1.50,
        "entry_price":  0.0,
        "exit_price":   0.0,
        "size":         0.0,
        "leverage":     3,
        "close_time":   now - 30,
    }
    ex = _make_exchange_with_records([record])
    first = tracker.reconcile_closed_pnl({"binance": ex}, since_ts=now - 120)
    second = tracker.reconcile_closed_pnl({"binance": ex}, since_ts=now - 120)
    assert first == 1
    assert second == 0
    assert len(tracker._closed) == 1
    pos = tracker._closed[0]
    assert pos.pnl == pytest.approx(-1.50)
    assert pos.pnl_pct is None
    assert pos.close_reason == "reconciled_no_context"
