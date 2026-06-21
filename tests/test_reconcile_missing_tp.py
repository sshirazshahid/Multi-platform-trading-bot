"""Audit 2026-06-21 B7 (PIECE 1): _reconcile_missing_tp — restore an orphaned
exchange-side TP in LIVE futures. No-op in PAPER/spot.

Critical property (verify-lens correction): fetch_open_orders NEVER raises — it
returns [] on error AND when not-ready (exchanges/base.py:378-387). So an EMPTY
list must be treated as INCONCLUSIVE (no-op), never as "TP missing", or a transient
API failure would churn a cancel-all+re-place every cycle. Re-assert ONLY when a
non-empty list has a loss-side SL conditional but no profit-side TP conditional.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position


def _om(dry_run=False):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = dry_run
    om._replace_exchange_sl = MagicMock()
    return om


def _pos(*, side="buy", entry=100.0, tp=110.0, market_type="futures"):
    p = Position(id="r1", exchange="Binance", symbol="ETH/USDT", side=side,
                 market_type=market_type, strategy="claude_portfolio",
                 entry_price=entry, size=1.0, stop_loss=92.0, take_profit=tp,
                 leverage=3, open_time=1000.0)
    p._exchange_tp = True
    return p


def _ex(open_orders):
    ex = MagicMock(); ex.name = "Binance"
    ex.fetch_open_orders.return_value = open_orders
    return ex


def _cond(trig):  # a reduce-only conditional at trigger price `trig`
    return {"triggerPrice": trig, "reduceOnly": True}


# ── no-op paths ──────────────────────────────────────────────────────────────
def test_paper_is_noop():
    om = _om(dry_run=True)
    ex = _ex([_cond(92.0)])
    assert om._reconcile_missing_tp(ex, _pos()) is True
    ex.fetch_open_orders.assert_not_called()
    om._replace_exchange_sl.assert_not_called()


def test_spot_is_noop():
    om = _om(dry_run=False)
    ex = _ex([_cond(92.0)])
    assert om._reconcile_missing_tp(ex, _pos(market_type="spot")) is True
    ex.fetch_open_orders.assert_not_called()


def test_exchange_tp_false_is_noop():
    om = _om(dry_run=False)
    pos = _pos(); pos._exchange_tp = False
    ex = _ex([_cond(92.0)])
    assert om._reconcile_missing_tp(ex, pos) is True
    ex.fetch_open_orders.assert_not_called()


def test_no_planned_tp_is_noop():
    om = _om(dry_run=False)
    ex = _ex([_cond(92.0)])
    assert om._reconcile_missing_tp(ex, _pos(tp=0.0)) is True
    ex.fetch_open_orders.assert_not_called()


# ── idempotence / inconclusive (must NOT churn orders) ───────────────────────
def test_tp_present_is_idempotent():
    om = _om(dry_run=False)
    ex = _ex([_cond(92.0), _cond(110.0)])  # buy: tp 110 > entry 100 -> present
    assert om._reconcile_missing_tp(ex, _pos()) is True
    om._replace_exchange_sl.assert_not_called()


def test_empty_list_inconclusive_no_replace():
    # The verify-lens correction: [] = error/not-ready, NOT "TP missing".
    om = _om(dry_run=False)
    ex = _ex([])
    assert om._reconcile_missing_tp(ex, _pos()) is True
    om._replace_exchange_sl.assert_not_called()


def test_no_sl_side_either_is_inconclusive():
    om = _om(dry_run=False)
    ex = _ex([{"reduceOnly": True}])  # no triggerPrice -> skipped -> neither side
    assert om._reconcile_missing_tp(ex, _pos()) is True
    om._replace_exchange_sl.assert_not_called()


# ── the orphan signature: SL present, TP absent -> downgrade to polled ───────
# (Reviewer HIGH fix: never cancel the WORKING SL to restore a TP. The reconciler
#  downgrades the orphaned TP to local polled enforcement instead.)
def test_orphan_downgrades_to_polled_buy():
    om = _om(dry_run=False)
    pos = _pos()
    ex = _ex([_cond(92.0)])  # SL (loss-side) present, no profit-side TP
    out = om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_not_called()  # SL never cancelled
    assert pos._exchange_tp is False             # downgraded -> polled path owns TP
    assert out is True


def test_orphan_downgrades_to_polled_sell():
    om = _om(dry_run=False)
    # sell: loss-side SL is ABOVE entry (108 > 100); profit-side TP would be < 100
    pos = _pos(side="sell", entry=100.0, tp=90.0)
    ex = _ex([_cond(108.0)])
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_not_called()
    assert pos._exchange_tp is False


def test_after_downgrade_next_call_is_noop():
    # After downgrade _exchange_tp is False, so a later call short-circuits at the
    # guard (even with the throttle cleared) — no repeated fetch / churn.
    om = _om(dry_run=False)
    pos = _pos()
    ex = _ex([_cond(92.0)])
    om._reconcile_missing_tp(ex, pos)            # downgrades
    assert pos._exchange_tp is False
    pos._tp_retry_ts = 0.0                        # isolate the _exchange_tp guard
    ex.fetch_open_orders.reset_mock()
    assert om._reconcile_missing_tp(ex, pos) is True
    ex.fetch_open_orders.assert_not_called()


# ── throttle ─────────────────────────────────────────────────────────────────
def test_throttle_skips_second_call():
    # Use the TP-present (idempotent) path so _exchange_tp stays True and the
    # SECOND call is stopped by the throttle (not the post-downgrade guard).
    om = _om(dry_run=False)
    pos = _pos()
    ex = _ex([_cond(92.0), _cond(110.0)])  # buy: tp 110 > entry 100 -> present
    om._reconcile_missing_tp(ex, pos)
    om._reconcile_missing_tp(ex, pos)  # within 60s -> throttle short-circuits
    assert ex.fetch_open_orders.call_count == 1
    assert pos._exchange_tp is True     # unchanged (TP was present, no downgrade)
