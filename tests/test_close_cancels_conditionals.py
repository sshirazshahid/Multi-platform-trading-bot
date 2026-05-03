"""Pin the 2026-05-02 fix: close_position cancels associated conditionals.

Bug we're fixing
================
Before this fix, `OrderManager.close_position` placed the close order on
the exchange and called `tracker.close()` — but never cancelled the
position's associated SL/TP conditional orders. Over time these
accumulated, eventually hitting Bybit's per-symbol stop-order limit
(~10 per symbol). New entries then failed SL placement with retCode
110009 → fail-closed → fee bleed.

Fix
===
After successful close, call `exchange.cancel_all_orders(symbol, mtype)`.
For Bybit specifically, the BybitClient overrides cancel_all_orders to
also handle the V5 conditional StopOrder ledger (which the base class
misses).

Test plan
=========
  1. close_position calls exchange.cancel_all_orders post-close
  2. Failure of cancel_all_orders does NOT break the close
  3. Paper trades skip the cancel (no real orders to cancel)
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_position(paper=False, symbol="DOGE/USDT:USDT"):
    from core.position_tracker import Position
    return Position(
        id="testpos-1", exchange="bybit", symbol=symbol,
        side="buy", market_type="futures", strategy="claude_portfolio",
        entry_price=0.10, size=100.0, stop_loss=0.095, take_profit=0.11,
        leverage=2, paper_trade=paper,
    )


def _make_order_manager():
    from core.order_manager import OrderManager
    om = OrderManager.__new__(OrderManager)
    # Bypass full __init__ — set only what close_position touches
    om.tracker = MagicMock()
    om.tracker.close = MagicMock(return_value={"id": "testpos-1", "pnl": 0.0})
    om.dry_run = False
    om.sim = MagicMock()
    om.notifier = MagicMock()
    om.compliance = MagicMock()
    om._close_fail_count = {}
    om._save_close_fail_count = MagicMock()
    om._oneway_mode = set()
    om._mid_from_ticker = lambda t: 0.0
    om.mcp_brain = None
    om._exchanges = {}
    return om


def _make_exchange(name="bybit"):
    ex = MagicMock()
    ex.name = name
    ex.fetch_ticker = MagicMock(return_value={"last": 0.10, "close": 0.10})
    ex.create_order = MagicMock(return_value={
        "id": "CLOSE_ORDER", "average": 0.10, "price": 0.10,
    })
    ex.cancel_all_orders = MagicMock(return_value=None)
    ex._is_oneway = False
    return ex


# ─── Tests ─────────────────────────────────────────────────────────────


def test_close_calls_cancel_all_orders_post_close():
    """The fix: after a successful close, cancel_all_orders MUST be called."""
    om = _make_order_manager()
    ex = _make_exchange("bybit")
    pos = _make_position(paper=False)

    om.close_position(ex, pos, "trailing_stop", price=0.10)

    # cancel_all_orders called for the symbol + market_type
    ex.cancel_all_orders.assert_called_once_with("DOGE/USDT:USDT", "futures")


def test_close_cancel_failure_does_NOT_break_close():
    """If cancel_all_orders raises, the position close itself must still
    complete. Cancel is best-effort (orphan cleanup script + daily run
    will catch what gets missed)."""
    om = _make_order_manager()
    ex = _make_exchange("bybit")
    ex.cancel_all_orders = MagicMock(side_effect=Exception("network blip"))
    pos = _make_position(paper=False)

    # Must not raise
    om.close_position(ex, pos, "trailing_stop", price=0.10)

    # Tracker close still happened
    assert om.tracker.close.called
    # Cancel was attempted (and raised, but we ignored it)
    assert ex.cancel_all_orders.called


def test_paper_trade_close_skips_cancel():
    """Paper positions don't have real exchange orders, so don't call cancel."""
    om = _make_order_manager()
    om.dry_run = True
    om.sim.paper_fill_price = MagicMock(return_value=0.10)
    ex = _make_exchange("bybit")
    pos = _make_position(paper=True)

    om.close_position(ex, pos, "trailing_stop", price=0.10)

    # cancel_all_orders should NOT be called for paper trades
    assert not ex.cancel_all_orders.called
