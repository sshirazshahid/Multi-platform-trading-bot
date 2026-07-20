"""Focused fail-closed coverage for pair discovery and UniverseFilter."""

from __future__ import annotations

from typing import Optional

import pytest

from core.pair_discovery import (
    UniverseFilter,
    discover_all,
    discover_all_mode,
    discover_pairs,
)


def _market(
    base: str,
    market_type: str = "spot",
    *,
    symbol: Optional[str] = None,
    active: bool = True,
    status: str = "TRADING",
    expiry=None,
) -> dict:
    if symbol is None:
        suffix = ":USDT" if market_type == "swap" else ""
        symbol = f"{base}/USDT{suffix}"
    is_spot = market_type == "spot"
    is_swap = market_type == "swap"
    is_future = market_type == "future"
    return {
        "id": symbol.replace("/", "").replace(":", ""),
        "symbol": symbol,
        "base": base,
        "quote": "USDT",
        "settle": "USDT" if not is_spot else None,
        "type": market_type,
        "spot": is_spot,
        "swap": is_swap,
        "future": is_future,
        "contract": not is_spot,
        "contractSize": 1,
        "active": active,
        "expiry": expiry,
        "info": {"status": status, "quoteVolume": "50000000"},
    }


class _RawVenue:
    id = "testvenue"

    def __init__(self, current_markets: dict, *, initial_markets=None, fail=False):
        self._current_markets = current_markets
        self.markets = current_markets if initial_markets is None else initial_markets
        self.fail = fail
        self.reload_calls = []

    def load_markets(self, reload=False):
        self.reload_calls.append(reload)
        if self.fail:
            raise RuntimeError("metadata unavailable")
        self.markets = self._current_markets
        return self.markets


class _Exchange:
    name = "TestVenue"
    _connected = True
    _futures_blocked = False

    def __init__(self, markets: dict, *, book=None, fail_metadata=False, initial_markets=None):
        self.exchange = _RawVenue(
            markets,
            initial_markets=initial_markets,
            fail=fail_metadata,
        )
        self.book = book if book is not None else _valid_book()
        self.book_calls = []
        self.ohlcv_calls = []

    def fetch_order_book(self, symbol, limit=10, market_type="spot"):
        self.book_calls.append((symbol, limit, market_type))
        return self.book

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        self.ohlcv_calls.append((symbol, timeframe, limit, market_type))
        count = 19 if timeframe == "1h" else 11
        return _trending_candles(count)


def _valid_book():
    return {
        "bids": [[99.99 - index * 0.01, 100] for index in range(5)],
        "asks": [[100.01 + index * 0.01, 100] for index in range(5)],
    }


def _trending_candles(count: int):
    candles = []
    for index in range(count):
        close = 100.0 + index
        candles.append([index, close - 0.25, close + 1.0, close - 1.0, close, 1_000])
    return candles


def test_discovery_uses_refreshed_metadata_and_only_accepts_btc_spot_and_perp():
    btc_spot = _market("BTC")
    btc_perp = _market("BTC", "swap")
    markets = {
        btc_spot["symbol"]: btc_spot,
        btc_perp["symbol"]: btc_perp,
        "WTI/USDT:USDT": _market("WTI", "swap"),
        "AAPL/USDT": _market("AAPL"),
        "NEW/USDT:USDT": _market("NEW", "swap", status="PreLaunch"),
        "BTC3L/USDT": _market("BTC3L"),
        "OLD/USDT": _market("OLD", active=False, status="Delisted"),
        "ETH/USDT:USDT-260925": _market(
            "ETH",
            "future",
            symbol="ETH/USDT:USDT-260925",
            expiry=1_798_128_000_000,
        ),
        "USDC/USDT": _market("USDC"),
        "WBTC/USDT": _market("WBTC"),
        "DOGE/USDT": _market("DOGE"),
    }
    stale_aapl = _market("AAPL")
    exchange = _Exchange(markets, initial_markets={stale_aapl["symbol"]: stale_aapl})

    result = discover_pairs(exchange, exchange.name)

    assert result == {
        "spot": ["BTC/USDT"],
        "futures": ["BTC/USDT:USDT"],
    }
    assert exchange.exchange.reload_calls == [True]


@pytest.mark.parametrize("discover", [discover_all, discover_all_mode])
def test_discovery_failure_returns_empty_venue_without_static_fallback(discover):
    stale_btc = _market("BTC")
    exchange = _Exchange(
        {},
        initial_markets={stale_btc["symbol"]: stale_btc},
        fail_metadata=True,
    )

    assert discover({"testvenue": exchange}) == {"testvenue": {"spot": [], "futures": []}}
    assert exchange.exchange.reload_calls == [True]


def test_universe_filter_accepts_btc_spot_and_perp_with_one_book_each_and_canonical_cache():
    spot = _market("BTC")
    perp = _market("BTC", "swap")
    exchange = _Exchange({spot["symbol"]: spot, perp["symbol"]: perp})
    universe_filter = UniverseFilter()

    spot_result = universe_filter.check(exchange, "BTC/USDT", "spot")
    perp_result = universe_filter.check(exchange, "BTC/USDT", "futures")
    cached_perp_result = universe_filter.check(exchange, "BTC/USDT:USDT", "futures")

    assert spot_result["ok"] is True
    assert perp_result["ok"] is True
    assert cached_perp_result is perp_result
    assert exchange.book_calls == [
        ("BTC/USDT", 10, "spot"),
        ("BTC/USDT:USDT", 10, "futures"),
    ]
    assert set(universe_filter._cache) == {
        ("testvenue", "spot", "BTC/USDT"),
        ("testvenue", "futures", "BTC/USDT:USDT"),
    }


@pytest.mark.parametrize(
    ("market", "market_type", "expected_reason"),
    [
        (_market("WTI", "swap"), "futures", "disabled_asset:WTI"),
        (_market("AAPL"), "spot", "disabled_asset:AAPL"),
        (
            _market("NEW", "swap", status="Pre-Market"),
            "futures",
            "prelaunch_or_premarket",
        ),
        (_market("BTC3L"), "spot", "leveraged_token:BTC3L"),
        (_market("OLD", active=False, status="Delisted"), "spot", "delisted_market"),
        (
            _market(
                "ETH",
                "future",
                symbol="ETH/USDT:USDT-260925",
                expiry=1_798_128_000_000,
            ),
            "futures",
            "dated_future",
        ),
    ],
)
def test_universe_filter_rejects_ineligible_market_metadata(market, market_type, expected_reason):
    exchange = _Exchange({market["symbol"]: market})

    result = UniverseFilter().check(exchange, market["symbol"], market_type)

    assert result["ok"] is False
    assert result["reasons"] == [expected_reason]
    assert exchange.book_calls == []


def test_universe_filter_rejects_unknown_market_without_fetching_data():
    exchange = _Exchange({})

    result = UniverseFilter().check(exchange, "BTC/USDT", "spot")

    assert result["ok"] is False
    assert result["reasons"] == ["unknown_market"]
    assert exchange.book_calls == []
    assert exchange.ohlcv_calls == []


@pytest.mark.parametrize(
    ("book", "expected_reason"),
    [
        ({"bids": [], "asks": []}, "missing_order_book"),
        ({"bids": [[float("nan"), 1]], "asks": [[101, 1]]}, "invalid_order_book"),
        ({"bids": [[101, 10]], "asks": [[100, 10]]}, "crossed_order_book"),
    ],
)
def test_universe_filter_rejects_missing_invalid_and_crossed_book_once(book, expected_reason):
    spot = _market("BTC")
    exchange = _Exchange({spot["symbol"]: spot}, book=book)

    result = UniverseFilter().check(exchange, spot["symbol"], "spot")

    assert result["ok"] is False
    assert expected_reason in result["reasons"]
    assert result["spread_pct"] is None
    assert result["depth_usd"] is None
    assert len(exchange.book_calls) == 1


def test_universe_filter_rejects_missing_volatility_and_range_explicitly():
    spot = _market("BTC")
    exchange = _Exchange({spot["symbol"]: spot})
    exchange.fetch_ohlcv = lambda *args, **kwargs: []

    result = UniverseFilter().check(exchange, spot["symbol"], "spot")

    assert result["ok"] is False
    assert "missing_volatility_data" in result["reasons"]
    assert "missing_range_data" in result["reasons"]
    assert result["atr_pct"] is None
    assert result["range_of_change"] is None
    assert result["trend_efficiency"] is None
