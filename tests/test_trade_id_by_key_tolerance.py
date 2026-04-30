"""Regression tests for `Warehouse.trade_id_by_key` — 2026-04-30 fix.

The original implementation did exact-float equality on `ts_entry`.
`order_manager._finalize_close` calls it with the Position's `open_time`
which is a *different* float source than what was originally written to
the warehouse via `record_trade_open`. Sub-second drift /
json-roundtrip truncation / microsecond precision differences silently
caused the lookup to miss → the warehouse close UPDATE was skipped →
the trade row stayed at status='OPEN' forever despite a real close
having fired.

Symptom found: 5 DOGE trades + 17 others stuck OPEN in the warehouse
even though the bot had logged + emailed their closes.

These tests pin:
  1. Exact match still works (no regression for callers whose ts_entry
     matches bit-for-bit).
  2. Sub-second drift up to ±2.0s falls back to a tolerance window.
  3. The lookup ALWAYS scopes to status='OPEN' — never silently
     overwrites a previously-closed trade.
  4. Closest match wins when multiple OPEN rows fall inside the window.
  5. Drift beyond ±2s returns None (we don't want to match an unrelated
     trade just because the symbol/side coincidentally match).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.warehouse import Warehouse


@pytest.fixture
def wh(tmp_path: Path) -> Warehouse:
    """Per-test Warehouse with isolated thread-local connection.

    Warehouse._tls is a CLASS-level threading.local() — every Warehouse
    instance constructed in the same thread shares the same `_tls.conn`,
    which means without cleanup the second test in a thread reuses the
    first test's database file (whichever one was opened first). Closing
    + clearing `_tls.conn` here forces a fresh connection bound to the
    new tmp_path.
    """
    if hasattr(Warehouse._tls, "conn") and Warehouse._tls.conn is not None:
        try:
            Warehouse._tls.conn.close()
        except Exception:
            pass
        Warehouse._tls.conn = None
    return Warehouse(tmp_path / "wh.sqlite")


def _open(wh: Warehouse, *, ts_entry: float, exchange="binance",
          symbol="DOGE/USDT:USDT", side="buy") -> int:
    """Insert an OPEN trade row, returning its id."""
    return wh.record_trade_open(
        candidate_id=None,
        ts_entry=ts_entry,
        exchange=exchange,
        symbol=symbol,
        market_type="futures",
        side=side,
        strategy_family="claude_portfolio",
        entry_px=0.10,
        size=1000.0,
        leverage=2,
    )


# ─── Exact-match regression ───────────────────────────────────────────


def test_exact_match_returns_id(wh: Warehouse):
    tid = _open(wh, ts_entry=1777507918.5)
    found = wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777507918.5, side="buy",
    )
    assert found == tid


# ─── Tolerance window — the actual fix ────────────────────────────────


@pytest.mark.parametrize("drift_sec", [0.001, 0.5, 1.0, 1.999])
def test_finds_within_tolerance_window(wh: Warehouse, drift_sec: float):
    """Caller passes ts_entry that drifted from warehouse by < tolerance."""
    tid = _open(wh, ts_entry=1777507918.5)
    found = wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777507918.5 + drift_sec, side="buy",
    )
    assert found == tid


def test_drift_beyond_tolerance_returns_none(wh: Warehouse):
    """A 5-second drift is too far — must NOT match a coincidentally-near
    trade, that would mis-close the wrong row."""
    _open(wh, ts_entry=1777507918.5)
    found = wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777507918.5 + 5.0, side="buy",
    )
    assert found is None


def test_explicit_zero_tolerance_is_exact_only(wh: Warehouse):
    """tolerance_sec=0 disables the window — caller can opt out of the
    fuzzy match if they really need exact float equality."""
    tid = _open(wh, ts_entry=1777507918.5)
    assert wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777507918.5, side="buy", tolerance_sec=0,
    ) == tid
    assert wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777507918.5 + 0.001, side="buy", tolerance_sec=0,
    ) is None


# ─── OPEN-only filter — never overwrite CLOSED trades ─────────────────


def test_does_not_match_already_closed_trade(wh: Warehouse):
    """If a trade is already CLOSED, trade_id_by_key must NOT return its id
    — otherwise `_finalize_close` would silently UPDATE a finalized row
    with new (and possibly wrong) data."""
    tid = _open(wh, ts_entry=1777507918.5)
    wh.record_trade_close(
        trade_id=tid,
        ts_exit=1777510163.0,
        exit_px=0.1071,
        realized_pnl=5.4238,
        exit_reason="mcp_take_profit",
    )
    # After close, lookup must miss.
    assert wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777507918.5, side="buy",
    ) is None


def test_finds_open_when_closed_and_open_share_window(wh: Warehouse):
    """Two trades for the same symbol+side: an old CLOSED one and a fresh
    OPEN one inside the tolerance window. Must return the OPEN one."""
    closed_tid = _open(wh, ts_entry=1777500000.0)
    wh.record_trade_close(
        trade_id=closed_tid, ts_exit=1777501000.0,
        exit_px=0.10, realized_pnl=0.0, exit_reason="test",
    )
    open_tid = _open(wh, ts_entry=1777500000.5)  # 0.5s later, OPEN
    found = wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777500000.5, side="buy",
    )
    assert found == open_tid
    assert found != closed_tid


# ─── Closest match wins when multiple OPEN rows fit the window ────────


def test_picks_closest_open_match(wh: Warehouse):
    """Two OPEN trades inside the ±2s window — return the one whose
    ts_entry is numerically closest to the lookup value."""
    tid_far = _open(wh, ts_entry=1777500000.0 - 1.5)   # 1.5s before target
    tid_near = _open(wh, ts_entry=1777500000.0 + 0.2)  # 0.2s after target
    found = wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777500000.0, side="buy",
    )
    assert found == tid_near
    assert found != tid_far


# ─── Symbol / side / exchange must still match exactly ────────────────


def test_different_symbol_returns_none(wh: Warehouse):
    _open(wh, ts_entry=1777500000.0, symbol="DOGE/USDT:USDT")
    assert wh.trade_id_by_key(
        exchange="binance", symbol="ETH/USDT:USDT",
        ts_entry=1777500000.0, side="buy",
    ) is None


def test_different_side_returns_none(wh: Warehouse):
    _open(wh, ts_entry=1777500000.0, side="buy")
    assert wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=1777500000.0, side="sell",
    ) is None


def test_different_exchange_returns_none(wh: Warehouse):
    _open(wh, ts_entry=1777500000.0, exchange="binance")
    assert wh.trade_id_by_key(
        exchange="bybit", symbol="DOGE/USDT:USDT",
        ts_entry=1777500000.0, side="buy",
    ) is None


# ─── Real-world bug scenario from 2026-04-30 ──────────────────────────


def test_doge_5_42_scenario(wh: Warehouse):
    """Exact reproduction of the DOGE +$5.42 close that went silently
    missing on 2026-04-30:

      - record_trade_open writes ts_entry=1777507918.5  (warehouse row)
      - Position.open_time is 1777507918.500001        (sub-µs drift)
      - close path calls trade_id_by_key with the latter
      - Pre-fix: returns None → close skipped
      - Post-fix: tolerance window catches it
    """
    warehouse_ts = 1777507918.5
    position_open_time = 1777507918.500001  # microsecond drift

    tid = _open(wh, ts_entry=warehouse_ts)
    found = wh.trade_id_by_key(
        exchange="binance", symbol="DOGE/USDT:USDT",
        ts_entry=position_open_time, side="buy",
    )
    assert found == tid, (
        "Sub-microsecond ts_entry drift must NOT cause a missed match. "
        "Without the tolerance window, this is the bug that left 5 DOGE "
        "trades stuck OPEN in the warehouse on 2026-04-30."
    )
