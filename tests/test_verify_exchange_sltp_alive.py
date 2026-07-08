"""C1 (tpbot retrofit): real verify_exchange_sl_alive / verify_exchange_tp_alive.

Gap (survey 2026-07-08): position_tracker._patch0_instrument_ghost calls
``order_manager.verify_exchange_sl_alive`` / ``verify_exchange_tp_alive``
(position_tracker.py:369-372) but those methods were NEVER implemented on
OrderManager — the AttributeError was silently swallowed by the helper's
blanket except (position_tracker.py:385-389), so on every profitable ghost
the reroute gate logged would-reroute data computed from a crash, i.e.
nothing. The existing tests in test_ghost_reroute_instrument.py pass only
because they MagicMock both methods.

These tests exercise the REAL methods with a fake exchange:

Semantics (fail-closed, verification not assumption):
  * True ONLY when a resting conditional on the correct side of entry is
    VERIFIED on the venue (same classification as _reconcile_missing_tp:
    trigger above entry = profit side for buys, below = loss side; mirrored
    for sells).
  * fetch_open_orders returns [] on error AND when not-ready
    (exchanges/base.py) — an empty list is INCONCLUSIVE and must return
    False (never credit unverified protection), without churning orders.
  * PAPER (dry_run) and spot return False without touching the venue —
    no exchange-side conditionals exist there at all.

Also regression-proofs the phantom-method class of bug:
  * OrderManager must actually HAVE both methods (hasattr, no mocks).
  * _patch0_instrument_ghost must log at ERROR (not the generic WARNING)
    when the verifier attribute is missing, so a future regression is loud.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from core import position_tracker as pt
from core.order_manager import OrderManager
from core.position_tracker import Position


@pytest.fixture
def caplog(caplog):
    """Bridge loguru -> stdlib so pytest's caplog captures loguru lines."""
    from loguru import logger as loguru_logger

    py_logger = logging.getLogger("tests.loguru_bridge")
    py_logger.setLevel(logging.DEBUG)
    py_logger.propagate = True

    def _sink(message):
        record = message.record
        py_level = getattr(logging, record["level"].name, logging.INFO)
        py_logger.log(py_level, record["message"])

    handler_id = loguru_logger.add(_sink, level=0, format="{message}")
    caplog.set_level(logging.DEBUG, logger="tests.loguru_bridge")
    yield caplog
    try:
        loguru_logger.remove(handler_id)
    except ValueError:
        pass


def _om(dry_run=False):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = dry_run
    return om


def _pos(*, side="buy", entry=100.0, market_type="futures"):
    return Position(
        id="v1",
        exchange="Binance",
        symbol="ETH/USDT",
        side=side,
        market_type=market_type,
        strategy="claude_portfolio",
        entry_price=entry,
        size=1.0,
        stop_loss=92.0,
        take_profit=110.0,
        leverage=3,
        open_time=1000.0,
    )


def _ex(open_orders, name="Binance"):
    ex = MagicMock()
    ex.name = name
    ex.fetch_open_orders.return_value = open_orders
    return ex


def _cond(trig):
    return {"triggerPrice": trig, "reduceOnly": True}


# ── methods must exist for real (the phantom-method regression guard) ────────
def test_order_manager_actually_has_both_verifiers():
    om = _om()
    assert callable(getattr(om, "verify_exchange_sl_alive", None)), (
        "verify_exchange_sl_alive missing from OrderManager — "
        "position_tracker.py:369 calls it on every profitable ghost"
    )
    assert callable(getattr(om, "verify_exchange_tp_alive", None)), (
        "verify_exchange_tp_alive missing from OrderManager — "
        "position_tracker.py:371 calls it on every profitable ghost"
    )


# ── classification: buy side ─────────────────────────────────────────────────
def test_sl_alive_when_loss_side_conditional_rests_buy():
    om = _om()
    ex = _ex([_cond(92.0)])  # below entry 100 on a buy -> loss side (SL)
    assert om.verify_exchange_sl_alive(ex, _pos()) is True
    assert om.verify_exchange_tp_alive(ex, _pos()) is False


def test_tp_alive_when_profit_side_conditional_rests_buy():
    om = _om()
    ex = _ex([_cond(110.0)])  # above entry 100 on a buy -> profit side (TP)
    assert om.verify_exchange_tp_alive(ex, _pos()) is True
    assert om.verify_exchange_sl_alive(ex, _pos()) is False


def test_both_alive_when_both_rest():
    om = _om()
    ex = _ex([_cond(92.0), _cond(110.0)])
    assert om.verify_exchange_sl_alive(ex, _pos()) is True
    assert om.verify_exchange_tp_alive(ex, _pos()) is True


# ── classification: sell side (mirrored) ─────────────────────────────────────
def test_sell_side_classification_mirrors():
    om = _om()
    pos = _pos(side="sell")
    # sell: loss side is ABOVE entry, profit side BELOW.
    ex = _ex([_cond(108.0)])
    assert om.verify_exchange_sl_alive(ex, pos) is True
    assert om.verify_exchange_tp_alive(ex, pos) is False
    ex2 = _ex([_cond(90.0)])
    assert om.verify_exchange_tp_alive(ex2, pos) is True
    assert om.verify_exchange_sl_alive(ex2, pos) is False


# ── stopPrice / nested info fallbacks (venue shape variance) ─────────────────
def test_stop_price_and_nested_info_shapes_recognized():
    om = _om()
    ex = _ex([{"stopPrice": 92.0}])
    assert om.verify_exchange_sl_alive(ex, _pos()) is True
    ex2 = _ex([{"info": {"triggerPrice": "110.0"}}])
    assert om.verify_exchange_tp_alive(ex2, _pos()) is True


# ── fail-closed shapes ────────────────────────────────────────────────────────
def test_empty_open_orders_is_inconclusive_false():
    # [] = error OR not-ready OR none (base.py returns [] on error): never
    # credit unverified protection.
    om = _om()
    ex = _ex([])
    assert om.verify_exchange_sl_alive(ex, _pos()) is False
    assert om.verify_exchange_tp_alive(ex, _pos()) is False


def test_paper_mode_false_without_venue_call():
    om = _om(dry_run=True)
    ex = _ex([_cond(92.0)])
    assert om.verify_exchange_sl_alive(ex, _pos()) is False
    assert om.verify_exchange_tp_alive(ex, _pos()) is False
    ex.fetch_open_orders.assert_not_called()


def test_spot_false_without_venue_call():
    om = _om()
    ex = _ex([_cond(92.0)])
    pos = _pos(market_type="spot")
    assert om.verify_exchange_sl_alive(ex, pos) is False
    assert om.verify_exchange_tp_alive(ex, pos) is False
    ex.fetch_open_orders.assert_not_called()


def test_invalid_entry_price_false():
    om = _om()
    ex = _ex([_cond(92.0)])
    assert om.verify_exchange_sl_alive(ex, _pos(entry=0.0)) is False
    assert om.verify_exchange_tp_alive(ex, _pos(entry=0.0)) is False


def test_conditionals_without_trigger_ignored():
    om = _om()
    ex = _ex([{"reduceOnly": True}, {"triggerPrice": "not-a-number"}])
    assert om.verify_exchange_sl_alive(ex, _pos()) is False
    assert om.verify_exchange_tp_alive(ex, _pos()) is False


def test_fetch_exception_is_fail_closed_not_raised():
    om = _om()
    ex = MagicMock()
    ex.name = "Binance"
    ex.fetch_open_orders.side_effect = RuntimeError("venue down")
    # base.py normally swallows to [], but the verifier must survive a raise
    # from any nonconforming client without propagating into the close path.
    assert om.verify_exchange_sl_alive(ex, _pos()) is False
    assert om.verify_exchange_tp_alive(ex, _pos()) is False


# ── end-to-end through _patch0_instrument_ghost with REAL verifiers ──────────
def test_patch0_uses_real_verifiers_end_to_end(caplog):
    """Profitable ghost + real OrderManager: would_reroute reflects the venue."""
    import config

    config.GHOST_REROUTE_INSTRUMENT = True

    om = _om()
    pos = _pos()  # buy @100
    ex = _ex([_cond(92.0), _cond(110.0)])  # both resting -> reroute candidate

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(MagicMock(), pos, om, ex, "ghost_sync", current_mark=103.0)

    msgs = [r.getMessage() for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(msgs) == 1
    assert "would_reroute=True" in msgs[0]
    assert "sl_alive=True" in msgs[0]
    assert "tp_alive=True" in msgs[0]


def test_patch0_missing_verifier_logs_error_not_warning(caplog):
    """A future phantom-method regression must be LOUD (ERROR), not a
    swallowed WARNING that corrupts gate telemetry silently for weeks."""
    import config

    config.GHOST_REROUTE_INSTRUMENT = True

    om = MagicMock(spec=[])  # attribute access raises AttributeError
    pos = _pos()
    ex = _ex([_cond(92.0)])

    with caplog.at_level(logging.ERROR):
        # Must not raise (never break the ghost-close path), but must be loud.
        pt._patch0_instrument_ghost(MagicMock(), pos, om, ex, "ghost_sync", current_mark=103.0)

    errors = [
        r
        for r in caplog.records
        if "GHOST_REROUTE_INSTRUMENT" in r.getMessage() and r.levelname == "ERROR"
    ]
    assert len(errors) == 1
    assert "AttributeError" in errors[0].getMessage()
