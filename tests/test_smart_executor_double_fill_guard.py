"""Pin the 2026-05-02 fix for smart-executor double-fill on Bybit.

Bug we're fixing
================
The smart executor's `execute_limit_with_fallback` flow:
  1. Place limit order
  2. Wait up to N seconds for fill
  3. On timeout: cancel order, then place market

Pre-fix, step 3 swallowed cancel errors silently and always placed a
market order. But on Bybit, when a limit order fills DURING the timeout
window, the cancel returns "order not exists or too late to cancel"
(retCode 110001). The pre-fix code interpreted this as "cancel succeeded"
and placed a market order on top — double-filling the position.

Live incident at 2026-05-02 08:03:02 UTC: limit BUY 302 DOGE filled,
then market BUY 302 DOGE filled = 604 DOGE long. SL placement failed
(too many stop orders), fail-closed sold 302 DOGE leaving 302 stranded.
Ghost-reconciler then re-imported the orphaned 302 as a manual position.

Fix
===
After ANY cancel attempt (success or failure), re-fetch the order's
true status. If status='closed' / filled, return without market — the
limit got us the position already.

These tests pin both behaviors:
  • Cancel succeeds, order was canceled → market fallback runs
  • Cancel fails because already filled → return original, NO market
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_executor():
    """Build a SmartExecutor with mocked _record_stat and orderbook."""
    from core.smart_executor import SmartExecutor
    se = SmartExecutor()
    se._record_stat = MagicMock()
    se._market_order = MagicMock(return_value={"status": "closed", "id": "MARKET_FALLBACK", "average": 100.0})
    se.get_entry_price = MagicMock(return_value=100.0)  # bypass orderbook fetch
    se.limit_timeout_sec = 0.1  # speed up test
    return se


def _make_exchange(limit_status_during_wait="open",
                   limit_status_after_cancel="closed",
                   cancel_raises=None):
    """Build a mock exchange. Default scenario: limit fills during cancel
    window, cancel fails because order already filled."""
    ex = MagicMock()
    ex.create_order = MagicMock(return_value={"id": "LIMIT_ORDER_ID", "status": "open"})

    # Wait loop with timeout=0.1 and sleep(1) iterates ~once before exit.
    # Provide: 1× during-wait response + 1× verification response + padding.
    during_wait = {"id": "LIMIT_ORDER_ID", "status": limit_status_during_wait,
                   "filled": 0.0, "average": 0.0}
    verification = {
        "id": "LIMIT_ORDER_ID",
        "status": limit_status_after_cancel,
        "filled": 100.0 if limit_status_after_cancel == "closed" else 0.0,
        "average": 100.0,
        "price": 100.0,
    }
    ex.exchange = MagicMock()
    ex.exchange.fetch_order = MagicMock(side_effect=[during_wait, verification] + [verification] * 10)

    if cancel_raises:
        ex.cancel_order = MagicMock(side_effect=Exception(cancel_raises))
    else:
        ex.cancel_order = MagicMock(return_value={"status": "canceled"})

    return ex


# ─── Bug-fix scenarios ─────────────────────────────────────────────────


def test_cancel_succeeds_market_fallback_runs():
    """Normal timeout path: cancel works, market fallback executes."""
    se = _make_executor()
    ex = _make_exchange(
        limit_status_during_wait="open",
        limit_status_after_cancel="canceled",
        cancel_raises=None,
    )
    result = se.execute_limit_with_fallback(
        ex, "DOGE/USDT:USDT", "buy", 100.0,
        market_type="futures",
    )
    # Cancel was called
    assert ex.cancel_order.called
    # Market fallback was used
    assert se._market_order.called
    assert result["id"] == "MARKET_FALLBACK"


def test_cancel_fails_because_already_filled_NO_double_fill():
    """The bug-fix scenario: limit filled during timeout window. Cancel
    raises 'order not exists or too late to cancel'. Verification fetches
    the order showing status='closed' / filled. Return THAT, do NOT market."""
    se = _make_executor()
    ex = _make_exchange(
        limit_status_during_wait="open",
        limit_status_after_cancel="closed",  # actually filled
        cancel_raises="bybit {retCode:110001,retMsg:order not exists or too late to cancel}",
    )
    result = se.execute_limit_with_fallback(
        ex, "DOGE/USDT:USDT", "buy", 100.0,
        market_type="futures",
    )
    # Cancel was attempted
    assert ex.cancel_order.called
    # Critical: market fallback was NOT called
    assert not se._market_order.called, "BUG: market_order called after limit already filled — would be a double-fill"
    # Returned the filled limit order
    assert result["status"] == "closed"
    assert result["id"] == "LIMIT_ORDER_ID"


def test_cancel_fails_AND_verify_fails_returns_uncertain():
    """If cancel fails AND verification fails too, refuse to market.
    This is a stuck-state requiring operator reconciliation."""
    se = _make_executor()
    ex = MagicMock()
    ex.create_order = MagicMock(return_value={"id": "LIMIT_ORDER_ID", "status": "open"})
    # During wait: order still open
    ex.exchange = MagicMock()
    # Wait loop call → "open"; then verification call → raises (network err)
    ex.exchange.fetch_order = MagicMock(side_effect=[
        {"id": "LIMIT_ORDER_ID", "status": "open", "filled": 0.0},
        Exception("network timeout"),  # verification call fails
    ])
    ex.cancel_order = MagicMock(side_effect=Exception("cancel network error"))

    result = se.execute_limit_with_fallback(
        ex, "DOGE/USDT:USDT", "buy", 100.0,
        market_type="futures",
    )
    # Market NOT called (defensive — could double-fill)
    assert not se._market_order.called, "must not market when cancel+verify both failed"
    assert result.get("_executor_warning") == "cancel+verify both failed"


def test_cancel_succeeds_but_partial_fill_existed():
    """Partial fill scenario: cancel succeeded but ~95% of order filled.
    Return the partial fill state, no market on top (operator can decide)."""
    se = _make_executor()
    ex = MagicMock()
    ex.create_order = MagicMock(return_value={"id": "LIMIT_ORDER_ID", "status": "open"})
    ex.cancel_order = MagicMock(return_value={"status": "canceled"})
    # During wait: open. Verification: partially filled at 96% (cancelled).
    ex.exchange = MagicMock()
    ex.exchange.fetch_order = MagicMock(side_effect=[
        {"id": "LIMIT_ORDER_ID", "status": "open", "filled": 0.0},
        {"id": "LIMIT_ORDER_ID", "status": "canceled",
         "filled": 96.0, "average": 100.0, "price": 100.0},  # 96% filled
    ])
    result = se.execute_limit_with_fallback(
        ex, "DOGE/USDT:USDT", "buy", 100.0,
        market_type="futures",
    )
    # Treated as effectively filled — no market fallback
    assert not se._market_order.called, "near-fully-filled limit must not trigger market fallback"
    assert result["filled"] == 96.0
