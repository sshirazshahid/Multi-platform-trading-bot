"""C5 (tpbot retrofit): conditional-order visibility + fill->protection latency.

Two grounded findings (survey + ccxt 4.5.54 source, 2026-07-08):

1. VISIBILITY: plain ``fetch_open_orders`` misses SL/TP conditionals on ALL
   THREE venues — Binance keeps algo orders in a separate book (documented
   in base.cancel_all_orders' 2026-04-20 fix), Bybit v5 needs
   orderFilter=StopOrder (the 2026-05-02 orphan incident), Bitget plan
   orders need the trigger/stop param. The CANCEL path got per-venue fixes;
   the FETCH path used by every reconciler (B7 TP-orphan, C1 verifiers,
   C4 SL-coverage) never did — they have been fail-safe no-ops in live.
   ``BaseExchange.fetch_open_conditionals`` (+ venue overrides) closes this;
   ``_classify_resting_conditionals`` merges both books.

2. LATENCY: ``_place_exchange_sl_tp`` did a fetch_ticker HTTP round-trip for
   the crossed-SL pre-flight even though open_position had JUST verified the
   fill price. ``mark_hint`` (the verified fill) replaces that round-trip on
   the entry path; the crossed-SL guard still runs, against the hint. The
   Apr-20 warehouse-ordering invariant (record_trade_open BEFORE placement)
   is untouched.

NOTE (deviation from the plan text, reported to the owner): attach-at-entry
(Bybit tpslMode Full / Bitget presets) is deferred — position-resident stops
are invisible to fetch_open_orders and immune to the cancel-all used by
every trailing/breakeven re-place, so a trail advance would leave a stale
WIDER stop armed alongside the new one (the exact double-authority bug class
this retrofit exists to kill), and none of it is exercisable before
CONTROLLED_LIVE.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position
from exchanges.base import BaseExchange


class _StubExchange(BaseExchange):
    name = "TestEx"

    def _init_exchange(self):  # type: ignore[override]
        return None


def _base_stub():
    b = _StubExchange.__new__(_StubExchange)
    b.exchange = MagicMock()
    b._ready = lambda: True
    return b


def _om(dry_run=False):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = dry_run
    return om


def _pos(*, side="buy", entry=100.0):
    return Position(
        id="c5",
        exchange="Bybit",
        symbol="ETH/USDT",
        side=side,
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=entry,
        size=1.0,
        stop_loss=92.0,
        take_profit=110.0,
        leverage=3,
        open_time=1000.0,
    )


# ── BaseExchange.fetch_open_conditionals ─────────────────────────────────────
def test_base_fetch_open_conditionals_uses_stop_param():
    b = _base_stub()
    b.exchange.fetch_open_orders.return_value = [{"id": "algo1"}]
    out = b.fetch_open_conditionals("ETH/USDT", "futures")
    assert out == [{"id": "algo1"}]
    _args, kwargs = b.exchange.fetch_open_orders.call_args
    params = kwargs.get("params") or {}
    # The proven Binance algo-book idiom from cancel_all_orders (2026-04-20/05-20)
    assert params.get("stop") is True
    assert params.get("acknowledged") is True


def test_base_fetch_open_conditionals_error_returns_none():
    # Review fix 2026-07-09: error must be INCONCLUSIVE (None), never "empty" —
    # treating errors as empty made C4 cancel working stops during outages.
    b = _base_stub()
    b.exchange.fetch_open_orders.side_effect = RuntimeError("down")
    assert b.fetch_open_conditionals("ETH/USDT", "futures") is None


def test_base_fetch_open_conditionals_not_ready_returns_none():
    b = _base_stub()
    b._ready = lambda: False
    assert b.fetch_open_conditionals("ETH/USDT", "futures") is None
    b.exchange.fetch_open_orders.assert_not_called()


# ── Bybit override: the StopOrder ledger (2026-05-02 incident idiom) ─────────
def test_bybit_fetch_open_conditionals_queries_stop_order_filter():
    from exchanges.bybit_client import BybitClient

    b = BybitClient.__new__(BybitClient)
    b.exchange = MagicMock()
    b._ok = lambda: True
    b.exchange.privateGetV5OrderRealtime.return_value = {
        "result": {
            "list": [
                {"orderId": "s1", "triggerPrice": "92.0", "symbol": "ETHUSDT"},
                {"orderId": "s2", "triggerPrice": "110.0", "symbol": "ETHUSDT"},
            ]
        }
    }
    out = b.fetch_open_conditionals("ETH/USDT:USDT", "futures")
    req = b.exchange.privateGetV5OrderRealtime.call_args[0][0]
    assert req["orderFilter"] == "StopOrder"
    assert req["category"] == "linear"
    assert req["symbol"] == "ETHUSDT"
    # Normalized to the classifier's shape: triggerPrice at the top level.
    assert [o["triggerPrice"] for o in out] == ["92.0", "110.0"]
    assert [o["id"] for o in out] == ["s1", "s2"]


def test_bybit_fetch_open_conditionals_spot_and_error_are_empty():
    from exchanges.bybit_client import BybitClient

    b = BybitClient.__new__(BybitClient)
    b.exchange = MagicMock()
    b._ok = lambda: True
    assert b.fetch_open_conditionals("ETH/USDT", "spot") == []
    b.exchange.privateGetV5OrderRealtime.side_effect = RuntimeError("x")
    assert b.fetch_open_conditionals("ETH/USDT:USDT", "futures") is None


# ── Bitget override: plan orders via the ccxt trigger param ─────────────────
def test_bitget_fetch_open_conditionals_uses_stop_true():
    from exchanges.bitget_client import BitgetClient

    b = BitgetClient.__new__(BitgetClient)
    import threading

    b.exchange = MagicMock()
    b._ok = lambda: True
    b._defaultType_lock = threading.RLock()
    b.switch_to_futures = MagicMock()
    b.switch_to_spot = MagicMock()
    b.exchange.fetch_open_orders.return_value = [{"id": "plan1", "triggerPrice": 92.0}]
    out = b.fetch_open_conditionals("ETH/USDT:USDT", "futures")
    assert out == [{"id": "plan1", "triggerPrice": 92.0}]
    _args, kwargs = b.exchange.fetch_open_orders.call_args
    assert (kwargs.get("params") or {}).get("stop") is True


# ── classifier merges both books ─────────────────────────────────────────────
def test_classifier_merges_regular_and_conditional_books():
    om = _om()
    pos = _pos()
    ex = MagicMock()
    ex.name = "Binance"
    ex.fetch_open_orders.return_value = [
        {"id": "r1", "type": "limit", "price": 101.0}
    ]  # no trigger
    ex.fetch_open_conditionals.return_value = [
        {"id": "a1", "stopPrice": 92.0},  # loss side
        {"id": "a2", "stopPrice": 110.0},  # profit side
    ]
    sl, tp, conclusive = om._classify_resting_conditionals(ex, pos)
    assert conclusive is True
    assert sl is True and tp is True


def test_classifier_survives_fake_without_conditionals_method():
    # Backward-compat: MagicMock fakes return a MagicMock (not a list) from
    # fetch_open_conditionals — must be ignored, never iterated/crashed.
    om = _om()
    pos = _pos()
    ex = MagicMock()
    ex.name = "Bybit"
    ex.fetch_open_orders.return_value = [{"triggerPrice": 92.0}]
    # ex.fetch_open_conditionals returns a bare MagicMock by default
    sl, tp, conclusive = om._classify_resting_conditionals(ex, pos)
    assert conclusive is True
    assert sl is True and tp is False


def test_classifier_conditionals_only_is_conclusive():
    # Regular book empty, conditional book has the SL: on Bybit/Bitget this
    # is the NORMAL live shape — must be conclusive, not the [] no-op.
    om = _om()
    pos = _pos()
    ex = MagicMock()
    ex.name = "Bybit"
    ex.fetch_open_orders.return_value = []
    ex.fetch_open_conditionals.return_value = [{"triggerPrice": 92.0}]
    sl, tp, conclusive = om._classify_resting_conditionals(ex, pos)
    assert conclusive is True
    assert sl is True


def test_classifier_both_books_empty_stays_inconclusive():
    om = _om()
    pos = _pos()
    ex = MagicMock()
    ex.name = "Bybit"
    ex.fetch_open_orders.return_value = []
    ex.fetch_open_conditionals.return_value = []
    _sl, _tp, conclusive = om._classify_resting_conditionals(ex, pos)
    assert conclusive is False


# ── mark_hint: one fewer HTTP round-trip fill->protection ────────────────────
def _sltp_exchange():
    ex = MagicMock()
    ex.name = "Binance"
    ex.round_price.side_effect = lambda s, p, market_type=None: p
    ex.round_quantity.side_effect = lambda s, q, market_type=None: q
    ex.create_order.return_value = {"id": "ok1"}
    return ex


def test_mark_hint_skips_ticker_fetch():
    om = _om()
    om.dry_run = False
    pos = _pos()
    ex = _sltp_exchange()
    om._place_exchange_sl_tp(
        ex, pos, 92.0, 110.0, "buy", "ETH/USDT", 1.0, "futures", mark_hint=100.0
    )
    ex.fetch_ticker.assert_not_called()
    assert ex.create_order.call_count == 2  # SL + TP placed


def test_mark_hint_crossed_sl_still_guarded():
    om = _om()
    om.dry_run = False
    om.close_position = MagicMock()
    pos = _pos()
    ex = _sltp_exchange()
    # hint says mark 91.0 — buy SL 92.0 is already crossed -> close, no orders
    om._place_exchange_sl_tp(
        ex, pos, 92.0, 110.0, "buy", "ETH/USDT", 1.0, "futures", mark_hint=91.0
    )
    om.close_position.assert_called_once()
    ex.create_order.assert_not_called()
    ex.fetch_ticker.assert_not_called()


def test_no_hint_falls_back_to_ticker_fetch():
    om = _om()
    om.dry_run = False
    pos = _pos()
    ex = _sltp_exchange()
    ex.fetch_ticker.return_value = {"last": 100.0, "close": 100.0}
    om._place_exchange_sl_tp(ex, pos, 92.0, 110.0, "buy", "ETH/USDT", 1.0, "futures")
    ex.fetch_ticker.assert_called_once()
    assert ex.create_order.call_count == 2


def test_classifier_conditional_book_error_none_is_inconclusive():
    # Review fix 2026-07-09: a failed conditional-book fetch (None) with a
    # regular order resting must be INCONCLUSIVE — never "SL missing".
    om = _om()
    pos = _pos()
    ex = MagicMock()
    ex.name = "Bybit"
    ex.fetch_open_orders.return_value = [{"id": "manual-limit", "price": 101.0}]
    ex.fetch_open_conditionals.return_value = None  # venue error contract
    _sl, _tp, conclusive = om._classify_resting_conditionals(ex, pos)
    assert conclusive is False
