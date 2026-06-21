"""Audit 2026-06-21 B7-P2: per-position close/SL-mutation lock.

Proven design (deadlock-proof workflow): one RLock per position id, default-OFF
(PER_POSITION_LOCK_ENABLED), 5s timeout-acquire with proceed-on-timeout, in-lock
tracker.is_position_open idempotency, dict eviction in _finalize_close.

RLock is MANDATORY: _replace_exchange_sl -> _place_exchange_sl_tp fail-closed
re-enters close_position on the SAME thread/id. A plain Lock would self-deadlock
the SL monitor. These tests pin that (the re-entrancy test would HANG on a Lock).

The wrapper logic is isolated by mocking _close_position_impl / _replace_exchange_sl_impl,
so the lock semantics are tested without the heavy close machinery.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position, PositionTracker


def _om(enabled=True):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = True
    om._per_pos_lock_enabled = enabled
    om.tracker.is_position_open.return_value = True
    return om


def _pos(pid="p1"):
    return Position(id=pid, exchange="Binance", symbol="ETH/USDT", side="buy",
                    market_type="futures", strategy="claude_portfolio",
                    entry_price=100.0, size=1.0, stop_loss=92.0, take_profit=110.0,
                    leverage=3, open_time=1000.0)


# ── _position_lock registry ──────────────────────────────────────────────────
def test_same_object_per_id_different_per_id():
    om = _om()
    a, b, c = om._position_lock("x"), om._position_lock("x"), om._position_lock("y")
    assert a is b
    assert a is not c


def test_concurrent_setdefault_yields_one_object():
    om = _om()
    objs = []
    barrier = threading.Barrier(16)

    def grab():
        barrier.wait()
        objs.append(om._position_lock("z"))
    ts = [threading.Thread(target=grab) for _ in range(16)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len({id(o) for o in objs}) == 1  # exactly one lock object for id z


# ── flag OFF = no lock, impl called directly ─────────────────────────────────
def test_flag_off_bypasses_lock():
    om = _om(enabled=False)
    om._close_position_impl = MagicMock(return_value="CLOSED")
    assert om.close_position(MagicMock(), _pos(), "x") == "CLOSED"
    assert om._pos_locks == {}            # no lock ever created
    om._close_position_impl.assert_called_once()


# ── idempotency: race loser no-ops via is_position_open ──────────────────────
def test_race_loser_noops_when_already_closed():
    om = _om()
    om.tracker.is_position_open.return_value = False  # position already gone
    om._close_position_impl = MagicMock(return_value="CLOSED")
    assert om.close_position(MagicMock(), _pos(), "x") is None
    om._close_position_impl.assert_not_called()


# ── concurrency: two threads race close on one id -> exactly one real close ──
def test_concurrency_one_real_close():
    om = _om()
    pos = _pos()
    open_ids = {pos.id}
    om.tracker.is_position_open.side_effect = lambda pid: pid in open_ids
    calls = []

    def _impl(ex, p, *a, **k):
        time.sleep(0.02)            # widen the race window
        open_ids.discard(p.id)      # this thread "closes" it
        calls.append(p.id)
        return "CLOSED"
    om._close_position_impl = MagicMock(side_effect=_impl)

    results = []

    def _close():
        results.append(om.close_position(MagicMock(), pos, "x"))
    ts = [threading.Thread(target=_close) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1               # serialized: exactly one real close
    assert results.count(None) == 1      # the loser no-op'd


# ── RE-ENTRANCY: _replace -> (fail-closed) close on same id must NOT deadlock ─
def test_replace_then_close_reentrancy_no_deadlock():
    om = _om()
    pos = _pos()
    om._close_position_impl = MagicMock(return_value="CLOSED")

    def _replace_impl(ex, p):            # simulate fail-closed re-entry
        om.close_position(ex, p, "sl_placement_failed")
        return None
    om._replace_exchange_sl_impl = MagicMock(side_effect=_replace_impl)

    done = threading.Event()

    def _run():
        om._replace_exchange_sl(MagicMock(), pos)  # acquires RLock, re-enters close
        done.set()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # With RLock this completes immediately; with a plain Lock it would HANG.
    assert done.wait(timeout=5.0), "self-deadlock: per-id lock must be RLock"
    om._close_position_impl.assert_called_once()


# ── timeout backstop: a held lock must NOT freeze the caller ─────────────────
def test_timeout_backstop_proceeds_unserialized():
    om = _om()
    om._pos_lock_timeout = 0.1
    pos = _pos()
    om._close_position_impl = MagicMock(return_value="CLOSED")
    lk = om._position_lock(pos.id)
    held = threading.Event()
    release = threading.Event()

    def _hold():
        lk.acquire()
        held.set()
        release.wait(timeout=2.0)
        lk.release()
    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    held.wait(timeout=2.0)               # ensure the OTHER thread holds it
    t0 = time.time()
    out = om.close_position(MagicMock(), pos, "x")   # times out at 0.1s, proceeds
    elapsed = time.time() - t0
    release.set()
    holder.join(timeout=2.0)
    assert out == "CLOSED"               # proceeded despite not acquiring
    om._close_position_impl.assert_called_once()
    assert elapsed < 1.5                 # did not block for the full hold


# ── dict eviction in _finalize_close (runs first; safe even if rest throws) ──
def test_finalize_close_evicts_lock():
    om = _om()
    pos = _pos()
    om._position_lock(pos.id)
    assert pos.id in om._pos_locks
    try:
        om._finalize_close(pos, pos.entry_price, "take_profit")
    except Exception:
        pass                              # eviction is the first statement
    assert pos.id not in om._pos_locks


# ── tracker.is_position_open ──────────────────────────────────────────────────
def test_tracker_is_position_open():
    tr = PositionTracker.__new__(PositionTracker)  # bypass heavy __init__
    tr._lock = threading.RLock()
    tr._open = {"a": object()}
    assert tr.is_position_open("a") is True
    assert tr.is_position_open("b") is False
