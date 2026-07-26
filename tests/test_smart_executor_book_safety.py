from unittest.mock import MagicMock

from core.smart_executor import SmartExecutor


def test_spread_check_is_market_type_pinned():
    exchange = MagicMock()
    exchange.fetch_order_book.return_value = {
        "bids": [[100, 2]],
        "asks": [[100.1, 2]],
    }

    result = SmartExecutor().check_spread(
        exchange, "BTC/USDT:USDT", market_type="futures"
    )

    assert result["ok"] is True
    exchange.fetch_order_book.assert_called_once_with(
        "BTC/USDT:USDT", limit=5, market_type="futures"
    )


def test_missing_or_crossed_books_fail_closed():
    exchange = MagicMock()
    executor = SmartExecutor()

    exchange.fetch_order_book.return_value = {"bids": [], "asks": []}
    assert executor.check_spread(exchange, "BTC/USDT", "spot") == {
        "ok": False,
        "reason": "order_book_unavailable",
        "spread_pct": 0,
        "bid": 0,
        "ask": 0,
        "mid": 0,
    }

    exchange.fetch_order_book.return_value = {
        "bids": [[101, 1]],
        "asks": [[100, 1]],
    }
    result = executor.check_spread(exchange, "BTC/USDT", "spot")
    assert result["ok"] is False
    assert result["reason"] == "order_book_invalid"


def test_book_fetch_exception_fails_closed():
    exchange = MagicMock()
    exchange.fetch_order_book.side_effect = TimeoutError("book timeout")

    result = SmartExecutor().check_spread(exchange, "BTC/USDT", "spot")

    assert result["ok"] is False
    assert result["reason"] == "order_book_fetch_failed"
