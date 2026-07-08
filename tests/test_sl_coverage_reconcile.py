"""C4 (tpbot retrofit): unconditional resting-SL coverage verification.

Gap (survey 2026-07-08, lever 8): the 10s monitor + reconcilers existed, but
SL re-placement was gated on the placement-time ``_sl_failed`` flag and the
TP reconciler explicitly no-op'd on the "TP present, SL absent" shape — a
venue-side SL cancel/expiry with healthy flags (``_exchange_sl=True``,
``_sl_failed=False``) was UNDETECTABLE by any loop: the one naked-position
shape no rail covered (polled direct triggers are suppressed while the
exchange claims both conditionals).

``_reconcile_missing_tp`` now verifies BOTH legs from its single throttled
open-orders fetch (via the C1 classifier, one source of truth):

  * SL owned + conclusively absent  -> re-place through _replace_exchange_sl
    (per-position lock serializes vs trailing advances; wrong-side pre-flight
    refuses doomed re-places; level is pos.stop_loss — the bot-authoritative
    stop that only ever tightens, so WIDEN is structurally impossible here)
  * TP owned + conclusively orphaned -> downgrade to polled (unchanged B7)
  * empty list -> INCONCLUSIVE no-op (base.py returns [] on error/not-ready)
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


def _pos(
    *,
    side="buy",
    entry=100.0,
    sl=92.0,
    tp=110.0,
    market_type="futures",
    sl_owned=True,
    tp_owned=False,
):
    p = Position(
        id="c4",
        exchange="Bybit",
        symbol="ETH/USDT",
        side=side,
        market_type=market_type,
        strategy="claude_portfolio",
        entry_price=entry,
        size=1.0,
        stop_loss=sl,
        take_profit=tp,
        leverage=3,
        open_time=1000.0,
    )
    p._exchange_sl = sl_owned
    p._exchange_tp = tp_owned
    return p


def _ex(open_orders):
    ex = MagicMock()
    ex.name = "Bybit"
    ex.fetch_open_orders.return_value = open_orders
    return ex


def _cond(trig):
    return {"triggerPrice": trig, "reduceOnly": True}


# ── the C4 shape: SL owned, venue lost it ────────────────────────────────────
def test_vanished_sl_detected_and_replaced_within_one_pass():
    om = _om()
    pos = _pos(sl_owned=True)
    # TP conditional rests (110 > entry 100), NO loss-side conditional: the
    # previously undetectable naked shape.
    ex = _ex([_cond(110.0)])
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_called_once_with(ex, pos)


def test_vanished_sl_with_no_conditionals_at_all_is_replaced():
    om = _om()
    pos = _pos(sl_owned=True)
    # Non-empty list (conclusive) but zero trigger orders resting.
    ex = _ex([{"id": "resting-limit", "type": "limit", "price": 101.0}])
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_called_once()


def test_sell_side_vanished_sl_detected():
    om = _om()
    pos = _pos(side="sell", sl=108.0, tp=90.0, sl_owned=True)
    # sell profit side is BELOW entry: TP@90 rests, loss side (above) empty.
    ex = _ex([_cond(90.0)])
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_called_once()


def test_reconcile_never_widens_pos_stop_loss():
    # WIDEN prohibition: the repair level is pos.stop_loss itself — the
    # reconciler must not mutate it in either direction.
    om = _om()
    pos = _pos(sl_owned=True, sl=92.0)
    ex = _ex([_cond(110.0)])
    om._reconcile_missing_tp(ex, pos)
    assert pos.stop_loss == 92.0


# ── no-action shapes ─────────────────────────────────────────────────────────
def test_sl_resting_no_replace():
    om = _om()
    pos = _pos(sl_owned=True)
    ex = _ex([_cond(92.0)])  # loss-side conditional rests -> covered
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_not_called()


def test_empty_list_inconclusive_no_replace():
    om = _om()
    pos = _pos(sl_owned=True)
    ex = _ex([])  # error/not-ready/none — never treat as "SL gone"
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_not_called()


def test_sl_not_owned_is_inert():
    om = _om()
    pos = _pos(sl_owned=False, tp_owned=False)
    ex = _ex([_cond(110.0)])
    om._reconcile_missing_tp(ex, pos)
    ex.fetch_open_orders.assert_not_called()
    om._replace_exchange_sl.assert_not_called()


def test_paper_is_noop():
    om = _om(dry_run=True)
    pos = _pos(sl_owned=True)
    ex = _ex([_cond(110.0)])
    assert om._reconcile_missing_tp(ex, pos) is True
    ex.fetch_open_orders.assert_not_called()


def test_spot_is_noop():
    om = _om()
    pos = _pos(market_type="spot", sl_owned=True)
    ex = _ex([_cond(110.0)])
    assert om._reconcile_missing_tp(ex, pos) is True
    ex.fetch_open_orders.assert_not_called()


def test_throttle_one_fetch_per_minute():
    om = _om()
    pos = _pos(sl_owned=True)
    ex = _ex([_cond(92.0)])  # covered -> idempotent path
    om._reconcile_missing_tp(ex, pos)
    om._reconcile_missing_tp(ex, pos)
    assert ex.fetch_open_orders.call_count == 1


# ── both branches from ONE fetch ─────────────────────────────────────────────
def test_tp_orphan_downgrade_still_works_when_sl_covered():
    om = _om()
    pos = _pos(sl_owned=True, tp_owned=True)
    ex = _ex([_cond(92.0)])  # SL rests, no profit-side conditional
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_not_called()  # SL is fine — never touched
    assert pos._exchange_tp is False  # TP downgraded to polled


def test_sl_repair_takes_priority_over_tp_downgrade():
    # Both legs missing: restoring the protective SL is the priority action
    # this pass — _replace_exchange_sl re-places BOTH legs anyway.
    om = _om()
    pos = _pos(sl_owned=True, tp_owned=True)
    ex = _ex([{"id": "x", "type": "limit", "price": 101.0}])  # neither rests
    om._reconcile_missing_tp(ex, pos)
    om._replace_exchange_sl.assert_called_once()
    # TP flag NOT blindly downgraded in the same pass — the re-place path
    # re-establishes both legs and maintains its own flags.
    assert pos._exchange_tp is True
