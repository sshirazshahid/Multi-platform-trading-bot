"""reconcile_closed_pnl dedup + Binance side handling (2026-05-24).

T2 — Ghost-closed positions seeded with the LEDGER ct (not now()) so
     reconcile_closed_pnl deduplicates correctly. Previously, the
     ghost loop set pos.close_time=time.time() while the ledger ct
     was hours earlier, so existing_keys (bucketed by close_time //
     600) and the candidate dedup key (bucketed by ct // 600) lived
     in different buckets → same fill imported twice.

T3 — Binance income ledger returns side=None. The prior code defaulted
     every None-side record to "buy", poisoning per-side WR for every
     Binance short close. Now: check both buy/sell variants against
     live + existing keys; if neither matches, skip the import (no
     poisoning, dollar PnL for genuinely-orphan closes is lost).
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


# T2 ───────────────────────────────────────────────────────────────────


def test_ghost_close_seeds_ledger_ct_dedup_key():
    """After a ghost reconcile via ledger, the dedup-key set contains
    the ledger ct bucket — NOT the close_time=now() bucket."""
    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-T2-001",
        exchange="bybit",
        symbol="LINK/USDT:USDT",
        side="buy",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=14.27,
        size=1.05,
        stop_loss=13.50,
        take_profit=15.20,
        paper_trade=False,
    )
    # Old fill ~2 hours ago (different 600s bucket than now()).
    ledger_ct = time.time() - 7200
    p.open_time = ledger_ct - 3600
    tracker._open[p.id] = p

    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [{
        "symbol": "LINK/USDT:USDT", "side": "buy",
        "close_time": ledger_ct,
        "exit_price": 13.64,  # outside SL/TP band — keeps ghost_reconciled
        "realized_pnl": -0.66,
    }]
    fake_ex.fetch_ticker.return_value = {"last": 13.64, "info": {"markPrice": "13.64"}}
    fake_ex.fetch_positions.return_value = []

    tracker.sync_with_exchanges({"bybit": fake_ex})

    # Position reconciled via ghost path -> set was populated.
    closed = [c for c in tracker._closed if c.id == "TEST-T2-001"]
    assert len(closed) == 1
    assert closed[0].close_reason == "ghost_reconciled"
    # And reconcile_closed_pnl (called inside sync) cleared the set
    # after merging into its existing_keys. So no double-import:
    # the same record shouldn't appear as a second `reconciled_from_exchange`.
    reconciled_dups = [c for c in tracker._closed
                       if c.symbol == "LINK/USDT:USDT"
                       and c.close_reason == "reconciled_from_exchange"]
    assert reconciled_dups == [], (
        "ghost_reconciled fill must not be re-imported via reconcile_closed_pnl"
    )


def test_ghost_close_set_is_cleared_after_use():
    """The set is consumed (cleared) after reconcile_closed_pnl uses it,
    so it doesn't grow unbounded across sync cycles."""
    tracker = pt.PositionTracker()
    tracker._ghost_just_closed_dedup_keys.add(("bybit", "BTC/USDT:USDT", "buy", 12345))

    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = []  # no records
    tracker.reconcile_closed_pnl({"bybit": fake_ex})

    assert tracker._ghost_just_closed_dedup_keys == set(), (
        "set must be cleared after reconcile_closed_pnl reads it"
    )


# T3 ───────────────────────────────────────────────────────────────────


def test_binance_no_side_record_matches_existing_buy():
    """Binance side=None record must dedup against an existing tracked
    BUY position (no longer fabricated to "buy" — checked via the
    buy/sell variant scan)."""
    tracker = pt.PositionTracker()
    # Pre-seed _closed with a buy-side BTC close.
    p = pt.Position(
        id="PRE-BUY", exchange="binance", symbol="BTC/USDT:USDT",
        side="buy", market_type="futures", strategy="claude_portfolio",
        entry_price=70000.0, size=0.001,
        stop_loss=69000.0, take_profit=71000.0, paper_trade=False,
    )
    p.close(69500.0, "stop_loss")
    p.close_time = time.time() - 1000  # within the 10-min bucket
    tracker._closed.append(p)

    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [{
        "symbol": "BTC/USDT:USDT",
        "side": None,  # Binance income ledger
        "close_time": p.close_time + 30,  # same 10-min bucket
        "exit_price": 0.0,
        "realized_pnl": -0.50,
        "size": 0.001, "entry_price": 70000.0,
    }]
    before = len(tracker._closed)
    tracker.reconcile_closed_pnl({"binance": fake_ex})
    # The None-side record must match the existing buy entry and skip.
    assert len(tracker._closed) == before, (
        "Binance None-side record matched against existing buy should "
        "not produce a second reconciled_from_exchange row"
    )


def test_binance_no_side_record_skipped_when_no_match():
    """Binance side=None record with no matching tracked / closed
    position must be SKIPPED (not poisoned with side='buy')."""
    tracker = pt.PositionTracker()
    # No pre-existing positions. _open and _closed empty.
    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [{
        "symbol": "BTC/USDT:USDT",
        "side": None,
        "close_time": time.time() - 100,
        "exit_price": 70500.0,
        "realized_pnl": -0.50,
        "size": 0.001, "entry_price": 70000.0,
    }]
    tracker.reconcile_closed_pnl({"binance": fake_ex})
    # Must NOT create a Position with side="buy" — that's the bug we're killing.
    poisoned = [c for c in tracker._closed
                if c.symbol == "BTC/USDT:USDT" and c.side == "buy"
                and c.strategy == "reconciled_exchange"]
    assert poisoned == [], (
        "Unmatched None-side Binance record must be skipped, not "
        "imported with a fabricated side='buy'."
    )


def test_existing_explicit_side_path_still_works():
    """Bybit/Bitget records carry a real side — the new code path must
    not break them. Pre-seed nothing, send a buy record, expect it
    to import."""
    tracker = pt.PositionTracker()
    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [{
        "symbol": "ETH/USDT:USDT",
        "side": "sell",          # explicit side from Bybit ledger
        "close_time": time.time() - 100,
        "exit_price": 3200.0,
        "realized_pnl": +1.50,
        "size": 0.01, "entry_price": 3215.0,
    }]
    tracker.reconcile_closed_pnl({"bybit": fake_ex})
    imported = [c for c in tracker._closed
                if c.symbol == "ETH/USDT:USDT" and c.side == "sell"]
    assert len(imported) == 1, (
        "Explicit-side reconcile records must continue to import normally"
    )
