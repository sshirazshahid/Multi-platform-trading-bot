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

    The dedup key is (exchange, symbol, side, close_time//600) (widened from
    the original per-minute bucket on 2026-04-27 — see
    test_reconcile_dedup_within_ten_minute_bucket below). A second pass over
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


def test_reconcile_dedup_within_ten_minute_bucket():
    """2026-04-27: LINK on bybit produced two ``reconciled_from_exchange``
    records for one physical close because the exchange reported close_time
    as 05:36:36 in one fetch and 05:48:02 in another. The pre-fix
    per-minute bucket let both pass; the 10-minute bucket plus side keys
    them to the same slot.
    """
    tracker = PositionTracker()
    base = {
        "symbol":       "LINK/USDT:USDT",
        "exchange":     "bybit",
        "side":         "buy",
        "realized_pnl": -0.66,
        "entry_price":  9.525,
        "exit_price":   9.341,
        "size":         3.4,
        "leverage":     3,
    }
    # Pin close_times within one 600s bucket. Bucket index is floor(ts/600);
    # 1_700_000_036 and 1_700_000_300 both land in bucket 2_833_333 (range
    # 1_699_999_800 .. 1_700_000_399). The 264s spread mirrors the LINK
    # 04-27 incident (05:36:36 vs 05:48:02 was 686s, fell in two adjacent
    # minute-buckets pre-fix; the new 10-min bucket would still catch most
    # near-bucket collisions and certainly catches same-bucket ones).
    first_record = dict(base, close_time=1_700_000_036.0)
    second_record = dict(base, close_time=1_700_000_300.0)
    ex = _make_exchange_with_records([first_record, second_record])
    imported = tracker.reconcile_closed_pnl(
        {"bybit": ex}, since_ts=1_700_000_000.0)
    assert imported == 1, (
        "two records for the same physical close in one 10-minute bucket "
        "must dedup to one")
    assert len(tracker._closed) == 1


def test_reconcile_skips_when_live_position_open():
    """2026-04-27: when a live tracked position exists for the same
    (exchange, symbol, side), the live close path will record the canonical
    close. Reconcile must not import a parallel record — that's how LINK
    got both `reconciled_from_exchange` and `ghost_reconciled` for the
    same fill.
    """
    tracker = PositionTracker()
    now = _time.time()
    live = Position(
        id="LIVE-bybit-LINK",
        exchange="bybit",
        symbol="LINK/USDT:USDT",
        side="buy",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=9.525,
        size=3.4,
        stop_loss=9.10,
        take_profit=10.0,
        leverage=3,
        open_time=now - 3600,
        paper_trade=False,
    )
    tracker._open[live.id] = live

    record = {
        "symbol":       "LINK/USDT:USDT",
        "exchange":     "bybit",
        "side":         "buy",
        "realized_pnl": -0.66,
        "entry_price":  9.525,
        "exit_price":   9.341,
        "size":         3.4,
        "leverage":     3,
        "close_time":   now - 30,
    }
    ex = _make_exchange_with_records([record])
    imported = tracker.reconcile_closed_pnl(
        {"bybit": ex}, since_ts=now - 120)
    assert imported == 0, (
        "reconcile must defer to the live close path when a tracked "
        "position with the same (exchange, symbol, side) is still open")
    assert len(tracker._closed) == 0


def test_reconcile_imports_when_no_matching_live_position():
    """Sanity check the live-position skip is identity-scoped: a live
    position on a different exchange/symbol/side must not block the import.
    """
    tracker = PositionTracker()
    now = _time.time()
    other_live = Position(
        id="LIVE-bybit-BTC",
        exchange="bybit",
        symbol="BTC/USDT:USDT",
        side="buy",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=50000.0,
        size=0.001,
        stop_loss=49000.0,
        take_profit=52000.0,
        leverage=3,
        open_time=now - 3600,
        paper_trade=False,
    )
    tracker._open[other_live.id] = other_live

    record = {
        "symbol":       "LINK/USDT:USDT",   # different symbol
        "exchange":     "bybit",
        "side":         "buy",
        "realized_pnl": -0.66,
        "entry_price":  9.525,
        "exit_price":   9.341,
        "size":         3.4,
        "leverage":     3,
        "close_time":   now - 30,
    }
    ex = _make_exchange_with_records([record])
    imported = tracker.reconcile_closed_pnl(
        {"bybit": ex}, since_ts=now - 120)
    assert imported == 1


# ── on_close hook wiring (safety-rail close path) ─────────────────────
#
# bot_engine wires tracker.on_close = order_mgr._finalize_close so EVERY
# close (normal, SL/TP, ghost-sync) routes through warehouse recording,
# risk updates, and notifications. Assert the hook fires on close — a
# regression here silently drops a closed trade's accounting.


def _make_open_position(symbol="BTC/USDT:USDT"):
    return Position(
        id=f"wire-{symbol}",
        exchange="bybit",
        symbol=symbol,
        side="buy",
        market_type="futures",
        strategy="test",
        entry_price=100.0,
        size=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        leverage=3,
    )


def test_close_fires_on_close_hook():
    tracker = PositionTracker()
    fired = {}
    tracker.on_close = lambda pos, exit_price, reason: fired.update(
        symbol=pos.symbol, exit_price=exit_price, reason=reason)
    pos = _make_open_position()
    tracker.add(pos)
    tracker.close(pos.id, exit_price=108.0, reason="take_profit")
    assert fired.get("symbol") == "BTC/USDT:USDT"
    assert fired.get("exit_price") == 108.0
    assert fired.get("reason") == "take_profit"


def test_close_hook_failure_does_not_lose_close():
    """A buggy hook must not prevent the position from being closed/recorded."""
    tracker = PositionTracker()

    def boom(pos, exit_price, reason):
        raise RuntimeError("hook bug")

    tracker.on_close = boom
    pos = _make_open_position("ETH/USDT:USDT")
    tracker.add(pos)
    closed = tracker.close(pos.id, exit_price=90.0, reason="stop_loss")
    assert closed is not None
    assert pos.id not in tracker._open
