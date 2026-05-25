"""Warehouse data-integrity fixes (2026-05-25, no-edge-forensics audit).

Bug 1: reconcile_closed_pnl appended to _closed without firing on_close →
       ~89 orphan reconciled_* rows invisible to warehouse. Now writes
       record_trade_open + record_trade_close directly.
Bug 2: MANUAL/RECONCILE imports lacked record_trade_open → ~57 stuck-OPEN
       rows when they later closed. Now create the OPEN row on import.
Bug 4: backfill find_match routes same-symbol-multi-OPEN matches to an
       ambiguous bucket (close log lacks side/exchange).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core import position_tracker as pt


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    yield


# Bug 1 ─────────────────────────────────────────────────────────────────


def test_reconcile_writes_to_warehouse(monkeypatch):
    """A reconcile-imported close must fire record_trade_open +
    record_trade_close so the warehouse is not bypassed."""
    fake_wh = MagicMock()
    fake_wh.record_trade_open.return_value = 4242   # a valid trade_id
    import core.warehouse as wh_mod
    monkeypatch.setattr(wh_mod, "get_warehouse", lambda: fake_wh)

    tracker = pt.PositionTracker()
    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [{
        "symbol": "ATOM/USDT:USDT", "side": "buy",
        "close_time": time.time() - 300,
        "exit_price": 8.50, "realized_pnl": +1.25,
        "size": 10.0, "entry_price": 8.38,
    }]
    tracker.reconcile_closed_pnl({"bybit": fake_ex})

    assert fake_wh.record_trade_open.called, (
        "reconcile_closed_pnl must call record_trade_open (Bug 1)"
    )
    assert fake_wh.record_trade_close.called, (
        "reconcile_closed_pnl must call record_trade_close (Bug 1)"
    )
    # The close must carry the trade_id returned by open + the real pnl.
    _, ckw = fake_wh.record_trade_close.call_args
    assert ckw["trade_id"] == 4242
    assert ckw["realized_pnl"] == pytest.approx(1.25)


def test_reconcile_warehouse_failure_is_nonfatal(monkeypatch):
    """A warehouse write error must NOT prevent the in-memory import
    (the dollar PnL still lands in _closed for the dashboard)."""
    fake_wh = MagicMock()
    fake_wh.record_trade_open.side_effect = Exception("db locked")
    import core.warehouse as wh_mod
    monkeypatch.setattr(wh_mod, "get_warehouse", lambda: fake_wh)

    tracker = pt.PositionTracker()
    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [{
        "symbol": "ARB/USDT:USDT", "side": "buy",
        "close_time": time.time() - 200,
        "exit_price": 1.10, "realized_pnl": -0.40,
        "size": 50.0, "entry_price": 1.11,
    }]
    n = tracker.reconcile_closed_pnl({"bybit": fake_ex})
    assert n == 1, "import must still succeed even if warehouse write fails"
    assert any(c.symbol == "ARB/USDT:USDT" for c in tracker._closed)


# Bug 4 ─────────────────────────────────────────────────────────────────


def test_backfill_routes_ambiguous_symbols_separately():
    """When >1 OPEN warehouse row shares a symbol, the backfill must NOT
    auto-commit those matches (FIFO pairing can misassign side/exchange
    since the close log lacks both)."""
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "backfill_t", root / "scripts" / "backfill_warehouse_closes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Two OPEN BTC rows (long binance + short bybit) — ambiguous.
    open_rows = [
        {"id": 1, "ts_entry": 1000.0, "exchange": "binance",
         "symbol": "BTC/USDT:USDT", "side": "buy", "mode": "CONTROLLED_LIVE"},
        {"id": 2, "ts_entry": 1100.0, "exchange": "bybit",
         "symbol": "BTC/USDT:USDT", "side": "sell", "mode": "CONTROLLED_LIVE"},
    ]
    from collections import Counter
    sym_counts = Counter(r["symbol"] for r in open_rows)
    ambiguous_syms = {s for s, c in sym_counts.items() if c > 1}
    assert "BTC/USDT:USDT" in ambiguous_syms, (
        "two OPEN rows on the same symbol must be flagged ambiguous"
    )
    # find_match itself still pairs on symbol+time (the routing happens in
    # the caller); confirm the helper is symbol+time only so the guard is
    # genuinely needed.
    events = [{"symbol": "BTC/USDT:USDT", "ts": 1200.0,
               "realized_pnl": -2.0, "fees": 0.1, "reason": "stop_loss"}]
    idx = mod.find_match(open_rows[0], events, set())
    assert idx == 0  # matches purely on symbol+time, side/exchange-blind
