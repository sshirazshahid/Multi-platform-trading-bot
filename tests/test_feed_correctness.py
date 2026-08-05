import math
import time

import pytest


def test_orderbook_buy_slippage_uses_base_quantity_and_quote_cost():
    from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed

    feed = OrderBookDepthFeed(cache_ttl=0)
    asks = [["101", "1"], ["103", "2"]]

    slippage = feed._estimate_slippage(asks, 150.0, 100.0, "buy")

    # Target base = 1.5; quote cost = 1 * 101 + 0.5 * 103 = 152.5.
    expected_vwap = 152.5 / 1.5
    assert slippage == pytest.approx(
        (expected_vwap - 100.0) / 100.0 * 10_000
    )


def test_orderbook_sell_slippage_uses_base_quantity_and_quote_proceeds():
    from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed

    feed = OrderBookDepthFeed(cache_ttl=0)
    bids = [["99", "1"], ["97", "2"]]

    slippage = feed._estimate_slippage(bids, 150.0, 100.0, "sell")

    # Target base = 1.5; quote proceeds = 1 * 99 + 0.5 * 97 = 147.5.
    expected_vwap = 147.5 / 1.5
    assert slippage == pytest.approx(
        (100.0 - expected_vwap) / 100.0 * 10_000
    )


@pytest.mark.parametrize(
    ("direction", "levels"),
    [
        ("buy", [["101", "1.99"]]),
        ("sell", [["99", "1.99"]]),
    ],
)
def test_orderbook_rejects_insufficient_depth_on_both_sides(direction, levels):
    from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed

    feed = OrderBookDepthFeed(cache_ttl=0)

    assert math.isinf(
        feed._estimate_slippage(levels, 200.0, 100.0, direction)
    )


def test_orderbook_cache_is_scoped_per_instrument(monkeypatch):
    from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed

    feed = OrderBookDepthFeed(cache_ttl=60, target_notional_usd=100)
    calls = []

    def fetch_depth(coin):
        calls.append(coin)
        return {
            "bids": [["99", "10"]],
            "asks": [["101", "10"]],
        }

    monkeypatch.setattr(feed, "_fetch_binance_depth", fetch_depth)
    monkeypatch.setattr(feed, "_fetch_bybit_depth", lambda coin: None)

    assert set(feed.fetch(["BTC"])) == {"BTC"}
    assert set(feed.fetch(["ETH"])) == {"ETH"}
    assert calls == ["BTC", "ETH"]


class _StaticFeed:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def fetch(self, coins, **kwargs):
        self.calls.append(list(coins))
        return self.payload


def _funding_only_coordinator(coins, payload):
    from core.data_coordinator import DataCoordinator

    coordinator = DataCoordinator()
    coordinator.set_coins(coins)
    coordinator.set_config({
        "funding_enabled": True,
        "oi_enabled": False,
        "orderbook_enabled": False,
        "news_enabled": False,
        "smart_money_enabled": False,
        "refresh_deadline_sec": 1.0,
    })
    coordinator._initialized = True
    coordinator._funding_feed = _StaticFeed(payload)
    return coordinator


def test_empty_refresh_does_not_advance_feed_freshness():
    coordinator = _funding_only_coordinator(["BTC"], {})

    result = coordinator.refresh(force=True)

    assert result["funding"] is False
    assert coordinator._funding_time == 0.0
    assert coordinator.get_market_context("BTC").any_stale is True


def test_stale_neutral_refresh_does_not_advance_feed_freshness():
    fallback = {
        "BTC": {"fr_current": 0.0, "fr_side_signal": "neutral", "stale": True}
    }
    coordinator = _funding_only_coordinator(["BTC"], fallback)

    result = coordinator.refresh(force=True)

    assert result["funding"] is False
    assert coordinator._funding_time == 0.0
    assert coordinator.get_market_context("BTC").funding["stale"] is True


def test_refresh_freshness_is_scoped_per_instrument():
    payload = {
        "BTC": {"fr_current": 0.0001, "fr_side_signal": "neutral", "stale": False}
    }
    coordinator = _funding_only_coordinator(["BTC", "ETH"], payload)

    result = coordinator.refresh(force=True)

    assert result["funding"] is False
    assert coordinator.get_market_context("BTC").any_stale is False
    assert coordinator.get_market_context("ETH").any_stale is True


def test_next_refresh_retries_only_the_missing_instrument():
    payload = {
        "BTC": {"fr_current": 0.0001, "fr_side_signal": "neutral", "stale": False}
    }
    coordinator = _funding_only_coordinator(["BTC", "ETH"], payload)

    coordinator.refresh(force=True)
    coordinator.refresh()

    assert coordinator._funding_feed.calls == [["BTC", "ETH"], ["ETH"]]


def test_payload_stale_flag_wins_over_recent_legacy_timestamp():
    coordinator = _funding_only_coordinator(["BTC"], {})
    coordinator._funding_data = {"BTC": {"fr_current": 0.0, "stale": True}}
    coordinator._funding_time = time.time()

    context = coordinator.get_market_context("BTC")

    assert context.funding["stale"] is True
    assert context.any_stale is True


def test_smart_money_without_any_backing_source_is_stale(monkeypatch):
    from core.data_feeds.smart_money_feed import SmartMoneyFeed

    feed = SmartMoneyFeed(cache_ttl=0)
    monkeypatch.setattr(feed, "_fetch_smart_money", lambda: {})
    monkeypatch.setattr(feed, "_fetch_social_hype", lambda: {})

    result = feed.fetch(["BTC"])

    assert result["BTC"]["stale"] is True
