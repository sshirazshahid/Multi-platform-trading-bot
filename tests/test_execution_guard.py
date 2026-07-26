from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.execution_guard import (
    fetch_and_validate_execution_book,
    validate_execution_book,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _book(**updates):
    book = {
        "timestamp": int(NOW.timestamp() * 1000),
        "nonce": 42,
        "bids": [[99, 2], [98, 3]],
        "asks": [[101, 1], [102, 3]],
    }
    book.update(updates)
    return book


def _validate(book, *, side="buy", quantity=2, limit=500):
    return validate_execution_book(
        book,
        venue="bybit",
        market_type="futures",
        canonical_symbol="BTC/USDT:USDT",
        exchange_symbol="BTC/USDT:USDT",
        side=side,
        requested_quantity=quantity,
        max_slippage_bps=limit,
        received_at=NOW,
    )


def test_buy_walk_accumulates_base_quantity_and_quote_cost():
    result = _validate(_book(), side="buy", quantity=2)

    assert result.allowed
    assert result.filled_quantity == Decimal("2")
    assert result.quote_cost == Decimal("203")
    assert result.vwap == Decimal("101.5")
    assert result.mid == Decimal("100")
    assert result.slippage_bps == Decimal("150")
    assert result.snapshot.instrument.venue.value == "bybit"
    assert result.snapshot.instrument.market.value == "perpetual"
    assert result.snapshot.sequence == 42


def test_sell_walk_uses_bids_and_computes_vwap():
    result = _validate(_book(), side="sell", quantity=3)

    assert result.allowed
    assert result.filled_quantity == Decimal("3")
    assert result.quote_cost == Decimal("296")
    assert result.vwap == Decimal("296") / Decimal("3")
    assert result.slippage_bps == pytest.approx(Decimal("133.3333333333333333333333333"))


def test_insufficient_depth_is_rejected_without_extrapolation():
    result = _validate(_book(asks=[[101, 0.25], [102, 0.5]]), quantity=1)

    assert not result.allowed
    assert result.reason == "insufficient_order_book_depth"
    assert result.filled_quantity == Decimal("0.75")
    assert result.vwap == 0


@pytest.mark.parametrize(
    ("book", "reason"),
    [
        (_book(bids=[], asks=[]), "order_book_invalid"),
        (_book(bids=[[102, 1]], asks=[[101, 1]]), "order_book_crossed"),
        (_book(bids=[[98, 1], [99, 1]]), "order_book_invalid"),
        (_book(timestamp=int((NOW - timedelta(seconds=6)).timestamp() * 1000)), "order_book_stale"),
        (_book(timestamp=int((NOW + timedelta(seconds=3)).timestamp() * 1000)), "order_book_clock_skew"),
    ],
)
def test_invalid_or_stale_books_fail_closed(book, reason):
    result = _validate(book)

    assert not result.allowed
    assert result.reason == reason


def test_slippage_limit_includes_crossing_half_the_spread():
    result = _validate(_book(), side="buy", quantity=2, limit=149.99)

    assert not result.allowed
    assert result.reason == "entry_slippage_exceeds_limit"
    assert result.snapshot is not None


def test_missing_exchange_timestamp_fails_closed():
    result = _validate(_book(timestamp=None), quantity=1)

    assert not result.allowed
    assert result.reason == "order_book_timestamp_missing"


def test_fetch_is_pinned_to_selected_venue_symbol_and_market():
    class FakeExchange:
        def __init__(self):
            self.calls = []

        def fetch_order_book(self, symbol, *, limit, market_type):
            self.calls.append((symbol, limit, market_type))
            return {
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                "bids": [[99, 5]],
                "asks": [[101, 5]],
            }

    exchange = FakeExchange()
    result = fetch_and_validate_execution_book(
        exchange,
        venue="bitget",
        market_type="futures",
        canonical_symbol="ETH/USDT:USDT",
        exchange_symbol="ETH/USDT:USDT",
        side="sell",
        requested_quantity=1,
        max_slippage_bps=200,
    )

    assert result.allowed
    assert exchange.calls == [("ETH/USDT:USDT", 50, "futures")]
    assert result.snapshot.instrument.venue.value == "bitget"


def test_fetch_error_is_terminal_denial():
    class BrokenExchange:
        def fetch_order_book(self, *args, **kwargs):
            raise TimeoutError("ambiguous network failure")

    result = fetch_and_validate_execution_book(
        BrokenExchange(),
        venue="binance",
        market_type="spot",
        canonical_symbol="BTC/USDT",
        exchange_symbol="BTC/USDT",
        side="buy",
        requested_quantity=1,
        max_slippage_bps=30,
    )

    assert not result.allowed
    assert result.reason == "order_book_fetch_failed"


def test_healthy_realtime_book_is_preferred_over_rest():
    class RestMustNotRun:
        def fetch_order_book(self, *args, **kwargs):
            raise AssertionError("REST must not run when realtime is healthy")

    class Realtime:
        def get_order_book_dict(self, venue, market, symbol):
            assert (venue, market, symbol) == (
                "bybit", "perpetual", "BTC/USDT:USDT"
            )
            return _book(
                timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
                source="bybit.ccxt.pro.order_book",
            )

    result = fetch_and_validate_execution_book(
        RestMustNotRun(),
        venue="bybit",
        market_type="futures",
        canonical_symbol="BTC/USDT:USDT",
        exchange_symbol="BTC/USDT:USDT",
        side="buy",
        requested_quantity=1,
        max_slippage_bps=200,
        realtime_provider=Realtime(),
    )

    assert result.allowed
    assert result.snapshot.source == "bybit.ccxt.pro.order_book"


def test_realtime_absence_can_fail_closed_without_rest_fallback():
    class RestMustNotRun:
        def fetch_order_book(self, *args, **kwargs):
            raise AssertionError("REST fallback is disabled")

    class Realtime:
        def get_order_book_dict(self, *args, **kwargs):
            return None

    result = fetch_and_validate_execution_book(
        RestMustNotRun(),
        venue="binance",
        market_type="futures",
        canonical_symbol="BTC/USDT:USDT",
        exchange_symbol="BTC/USDT:USDT",
        side="sell",
        requested_quantity=1,
        max_slippage_bps=200,
        realtime_provider=Realtime(),
        allow_rest_fallback=False,
    )

    assert not result.allowed
    assert result.reason == "realtime_order_book_unavailable"
