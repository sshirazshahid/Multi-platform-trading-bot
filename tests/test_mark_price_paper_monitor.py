from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.order_manager import OrderManager, _mark_from_ticker
from core.position_tracker import Position
from core.sim_execution import SimExecutionModel


def _position(*, side: str = "buy", mark_liquidation_test: bool = True) -> Position:
    return Position(
        id="paper-mark-1",
        exchange="Bybit",
        symbol="BTC/USDT:USDT",
        side=side,
        market_type="futures",
        strategy="test",
        entry_price=100.0,
        size=1.0,
        stop_loss=70.0 if mark_liquidation_test else 90.0,
        take_profit=130.0 if mark_liquidation_test else 110.0,
        leverage=5,
        paper_trade=True,
    )


def _manager(monkeypatch, position: Position):
    tracker = MagicMock()
    tracker.get_open.return_value = [position]
    risk = MagicMock()
    manager = OrderManager(tracker, risk, MagicMock())
    manager.dry_run = True
    manager.enforce_mark_price_triggers = True
    manager.accrue_paper_funding = MagicMock()
    manager.close_position = MagicMock()
    manager._update_trade_extremes = MagicMock()
    monkeypatch.setattr("core.order_manager._maker_first_cfg", lambda: {"enabled": False})
    return manager, risk


def test_mark_field_freshness_is_independent_of_last_trade():
    now = 1_800_000_000.0
    assert _mark_from_ticker(
        {"last": 1.0, "timestamp": now * 1000, "info": {"markPrice": "99.5"}},
        now=now,
    ) == pytest.approx(99.5)
    assert (
        _mark_from_ticker(
            {
                "last": 100.0,
                "timestamp": (now - 16.0) * 1000,
                "info": {"markPrice": "99.5"},
            },
            now=now,
        )
        == 0.0
    )
    assert _mark_from_ticker({"info": {"markPrice": "99.5"}}, now=now) == 0.0


def _mark_manager() -> OrderManager:
    manager = OrderManager(MagicMock(), MagicMock(), MagicMock())
    manager.enforce_mark_price_triggers = True
    return manager


def _payload(mark: float, age_s: float, *, ms: bool = False) -> dict:
    stamp = time.time() - age_s
    if ms:
        stamp *= 1000.0
    return {"mark": mark, "index": None, "ts": stamp, "mark_ts": stamp, "index_ts": stamp}


# ── _required_futures_mark producer/consumer contract (2026-07-18 AXS skew) ──
# The AXS incident was a producer/consumer pairing failure: fetch_mark_index's
# mark-OHLCV fallback serves the last CLOSED 1m candle stamped with its REAL
# open time (structural age 30-120s), while _required_futures_mark still
# enforced a 15s freshness bound — so the fallback mark was rejected on EVERY
# cycle in-process even though standalone probes of fetch_mark_index returned
# valid data (mark 0.9385, age ~34s). These tests pin both halves of the pair.


def test_required_futures_mark_accepts_structural_candle_age():
    """Ages of 30-90s are structural for the closed-1m-candle fallback and
    must be accepted — a regression to the 15s bound re-creates the AXS
    latch-and-flatten failure."""
    manager = _mark_manager()
    pos = _position()
    exchange = MagicMock()
    for age in (34.0, 60.0, 85.0):
        exchange.fetch_mark_index.return_value = _payload(0.9385, age)
        assert manager._required_futures_mark(exchange, pos, {}) == pytest.approx(0.9385), (
            f"age={age}"
        )


def test_required_futures_mark_rejects_stale_stamp():
    """The 90s staleness bound still holds — a stale fallback mark must not
    drive SL/TP/liquidation triggers."""
    manager = _mark_manager()
    exchange = MagicMock()
    exchange.fetch_mark_index.return_value = _payload(0.9385, 120.0)
    assert manager._required_futures_mark(exchange, _position(), {}) == 0.0


def test_required_futures_mark_normalizes_ms_stamp():
    """Unit-skew hardening: a producer regression to millisecond stamps must
    not zero the mark (age would compute hugely negative). Mirrors
    _mark_from_ticker's ms normalization."""
    manager = _mark_manager()
    exchange = MagicMock()
    exchange.fetch_mark_index.return_value = _payload(0.9385, 34.0, ms=True)
    assert manager._required_futures_mark(exchange, _position(), {}) == pytest.approx(0.9385)


def test_required_futures_mark_pairs_with_fetch_mark_index_producer():
    """End-to-end pairing pin: the REAL BaseExchange.fetch_mark_index payload
    (ticker without mark keys -> closed-candle fallback, ms candle open) must
    be accepted by the REAL consumer. This is the cross-module test that
    would have caught the 2026-07-18 skew."""
    from exchanges.base import BaseExchange

    candle_open_s = time.time() - 60.0

    class _Client:
        def fetch_ticker(self, symbol, params=None):
            return {"last": 0.94, "timestamp": None, "info": {}}

        def fetch_mark_ohlcv(self, symbol, timeframe, limit=None):
            return [[int(candle_open_s * 1000), 0.938, 0.939, 0.937, 0.9385, 12.0]]

    class _StubVenue(BaseExchange):
        def _init_exchange(self):
            self.exchange = _Client()
            self._connected = True

    venue = _StubVenue("", "")
    payload = venue.fetch_mark_index("AXS/USDT:USDT", "futures")
    assert payload["mark"] == pytest.approx(0.9385)
    assert payload["mark_ts"] == pytest.approx(candle_open_s, abs=1.0)

    manager = _mark_manager()
    exchange = MagicMock()
    exchange.fetch_mark_index.return_value = payload
    assert manager._required_futures_mark(exchange, _position(), {}) == pytest.approx(0.9385)


@pytest.mark.parametrize(
    ("side", "mark"),
    (("buy", 79.0), ("sell", 121.0)),
)
def test_paper_liquidation_uses_mark_not_last(monkeypatch, side, mark):
    pos = _position(side=side)
    manager, risk = _manager(monkeypatch, pos)
    exchange = MagicMock()
    exchange.name = "Bybit"
    # Last is deliberately benign/opposite; mark has crossed liquidation.
    exchange.fetch_ticker.return_value = {
        "last": 100.0,
        "timestamp": int(time.time() * 1000),
        "info": {"markPrice": str(mark)},
    }
    exchange.exchange.market.return_value = {"info": {"maintMarginPercent": "0.5"}}

    manager.check_sl_tp(exchange, "futures")

    args = manager.close_position.call_args.args
    assert args[2] == "liquidation"
    expected = 80.5 if side == "buy" else 119.5
    assert args[3] == pytest.approx(expected)
    risk.latch_incident.assert_not_called()


def test_missing_mark_latches_data_incident_and_flattens_paper(monkeypatch):
    pos = _position(mark_liquidation_test=False)
    manager, risk = _manager(monkeypatch, pos)
    exchange = MagicMock()
    exchange.name = "Binance"
    exchange.fetch_ticker.return_value = {"last": 99.0, "info": {}}
    exchange.fetch_mark_index.return_value = {
        "mark": None,
        "index": None,
        "ts": time.time(),
    }

    manager.check_sl_tp(exchange, "futures")

    assert manager.close_position.call_args.args[2:] == (
        "data_feed_failure",
        99.0,
    )
    assert risk.latch_incident.call_args.kwargs["category"] == "data"


def test_mark_candle_path_keeps_sl_first_and_target_trade_through():
    now = 1_800_000_000.0

    class Exchange:
        def fetch_mark_ohlcv(self, symbol, timeframe, limit=3):
            return [[(now - 60) * 1000, 100, 105, 95, 100, 1]]

        def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("trade-price candles must not be substituted")

    model = SimExecutionModel()
    model.wick_enable = True
    assert model.check_wick_trigger(
        Exchange(),
        "BTC/USDT:USDT",
        "futures",
        "buy",
        sl=95,
        tp=105,
        now=now,
        entry_ts=now - 120,
        price_basis="mark",
    ) == ("stop_loss", 95)


def test_production_target_requires_trade_through_and_fills_at_limit(monkeypatch):
    pos = _position(mark_liquidation_test=False)
    manager, _ = _manager(monkeypatch, pos)

    assert manager._target_traded_through(pos, 110.0) is False
    assert manager._target_traded_through(pos, 110.01) is True

    manager._close_take_profit(MagicMock(), pos, 110.01)
    assert manager.close_position.call_args.kwargs["order_type"] == "limit"
    assert manager.close_position.call_args.args[3] == 110.0
