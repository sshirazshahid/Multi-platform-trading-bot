"""
Tests for core/data_feeds/ and core/data_coordinator.py.

Verifies:
  1. Each feed returns correct normalized dict structure
  2. DataCoordinator produces MarketContext with all fields
  3. Feed staleness detection works
  4. Fail-open: missing/errored feeds produce neutral defaults
  5. B11/B12/B13 scoring integration in _score_coin
  6. Veto gates V1/V2/V3 block entries correctly
"""

import time
from unittest.mock import MagicMock

# ── Feed module structure tests ────────────────────────────────────────

class TestFundingRateFeed:
    def test_neutral_structure(self):
        from core.data_feeds.funding_rate_feed import FundingRateFeed
        feed = FundingRateFeed(cache_ttl=0)
        neutral = feed._neutral(stale=True)
        assert neutral["fr_current"] == 0.0
        assert neutral["fr_zscore"] == 0.0
        assert neutral["fr_extreme"] is None
        assert neutral["fr_side_signal"] == "neutral"
        assert neutral["stale"] is True

    def test_fetch_returns_dict_per_coin(self):
        from core.data_feeds.funding_rate_feed import FundingRateFeed
        feed = FundingRateFeed(cache_ttl=0)
        # Mock out external calls
        feed._fetch_coindesk_tick = lambda coins: {}
        feed._fetch_binance_single = lambda coin: 0.0001
        result = feed.fetch(["BTC", "ETH"])
        assert "BTC" in result
        assert "ETH" in result
        # Both should have the standard keys
        for coin in ["BTC", "ETH"]:
            d = result[coin]
            assert "fr_current" in d
            assert "fr_zscore" in d
            assert "fr_side_signal" in d

    def test_extreme_negative_is_bullish(self):
        from core.data_feeds.funding_rate_feed import FundingRateFeed
        feed = FundingRateFeed(cache_ttl=0)
        # Pre-populate history with normal positive rates
        feed._history["BTC"] = [0.0001] * 20
        feed._history_time = time.time()  # prevent refresh
        # Return very negative current rate
        feed._fetch_coindesk_tick = lambda coins: {
            "BTC": {"fr_current": -0.005}
        }
        result = feed.fetch(["BTC"])
        assert result["BTC"]["fr_side_signal"] == "bullish"
        assert result["BTC"]["fr_extreme"] == "short_crowded"


class TestOpenInterestFeed:
    def test_neutral_structure(self):
        from core.data_feeds.open_interest_feed import OpenInterestFeed
        feed = OpenInterestFeed(cache_ttl=0)
        neutral = feed._neutral(stale=True)
        assert neutral["oi_trend"] == "flat"
        assert neutral["oi_price_divergence"] == "unknown"
        assert neutral["stale"] is True

    def test_classify_continuation(self):
        from core.data_feeds.open_interest_feed import OpenInterestFeed
        div, conv = OpenInterestFeed._classify_divergence(0.05, 0.02)
        assert div == "continuation"
        assert conv > 0

    def test_classify_exhaustion(self):
        from core.data_feeds.open_interest_feed import OpenInterestFeed
        div, conv = OpenInterestFeed._classify_divergence(-0.03, 0.02)
        assert div == "exhaustion"

    def test_classify_capitulation(self):
        from core.data_feeds.open_interest_feed import OpenInterestFeed
        div, conv = OpenInterestFeed._classify_divergence(-0.03, -0.02)
        assert div == "capitulation"

    def test_classify_building(self):
        from core.data_feeds.open_interest_feed import OpenInterestFeed
        div, conv = OpenInterestFeed._classify_divergence(0.05, -0.02)
        assert div == "building"


class TestOrderBookDepthFeed:
    def test_neutral_structure(self):
        from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed
        feed = OrderBookDepthFeed(cache_ttl=0)
        neutral = feed._neutral(stale=True)
        assert neutral["imbalance"] == 0.0
        assert neutral["spread_bps"] == 0.0
        assert neutral["stale"] is True

    def test_slippage_estimation(self):
        from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed
        feed = OrderBookDepthFeed(cache_ttl=0, target_notional_usd=200)
        # 3-level ask book with progressively worse prices
        asks = [
            ["100.0", "0.5"],   # $50 at 100.0
            ["100.5", "0.5"],   # $50.25 at 100.5
            ["101.0", "1.0"],   # $101 at 101.0
        ]
        mid = 99.9  # mid below best ask (realistic: mid = (bid+ask)/2)
        slip = feed._estimate_slippage(asks, 200, mid, "buy")
        # Walking the book: 0.5 @ 100, 0.5 @ 100.5, 1.0 @ 101 for $201.5
        # We need $200 notional which is 200/99.9 = 2.002 qty
        # Avg fill > mid, so slippage should be positive
        assert slip >= 0
        # Thin book should show meaningful slippage but not extreme
        assert slip < 200  # sanity: less than 2%


class TestNewsSentimentFeed:
    def test_neutral_structure(self):
        from core.data_feeds.news_sentiment_feed import NewsSentimentFeed
        feed = NewsSentimentFeed(cache_ttl=0)
        neutral = feed._neutral(stale=True)
        assert neutral["sentiment_score"] == 0.0
        assert neutral["has_veto_news"] is False

    def test_classify_sentiment(self):
        from core.data_feeds.news_sentiment_feed import NewsSentimentFeed
        assert NewsSentimentFeed._classify_sentiment(
            "Bitcoin surges to new record high") == "POSITIVE"
        assert NewsSentimentFeed._classify_sentiment(
            "Major exchange hacked, funds stolen") == "NEGATIVE"
        assert NewsSentimentFeed._classify_sentiment(
            "Bitcoin trading volume steady") == "NEUTRAL"

    def test_cryptocompare_wrapped_data_normalized(self):
        from core.data_feeds.news_sentiment_feed import NewsSentimentFeed
        payload = {"Data": {"Data": [{"title": "A"}, {"title": "B"}]}}
        assert NewsSentimentFeed._cryptocompare_items(payload) == [
            {"title": "A"}, {"title": "B"}
        ]


class TestSmartMoneyFeed:
    def test_neutral_structure(self):
        from core.data_feeds.smart_money_feed import SmartMoneyFeed
        feed = SmartMoneyFeed(cache_ttl=0)
        neutral = feed._neutral(stale=True)
        assert neutral["smart_money_inflow"] is False
        assert neutral["crowd_signal"] == "neutral"


# ── DataCoordinator tests ─────────────────────────────────────────────

class TestDataCoordinator:
    def test_singleton(self):
        from core.data_coordinator import get_coordinator
        c1 = get_coordinator()
        c2 = get_coordinator()
        assert c1 is c2

    def test_market_context_structure(self):
        from core.data_coordinator import DataCoordinator, MarketContext
        coord = DataCoordinator()
        ctx = coord.get_market_context("BTC")
        assert isinstance(ctx, MarketContext)
        assert isinstance(ctx.funding, dict)
        assert isinstance(ctx.open_interest, dict)
        assert isinstance(ctx.orderbook, dict)
        assert isinstance(ctx.news, dict)
        assert isinstance(ctx.smart_money, dict)
        # All stale since no data loaded
        assert ctx.any_stale is True

    def test_get_accessor(self):
        from core.data_coordinator import MarketContext
        ctx = MarketContext(
            funding={"fr_zscore": 1.5, "stale": False},
            open_interest={},
            orderbook={},
            news={},
            smart_money={},
            any_stale=False,
        )
        assert ctx.get("funding", "fr_zscore", 0) == 1.5
        assert ctx.get("funding", "missing_key", -1) == -1
        assert ctx.get("nonexistent_feed", "key", 42) == 42

    def test_config_override(self):
        from core.data_coordinator import DataCoordinator
        coord = DataCoordinator()
        coord.set_config({"funding_enabled": False})
        assert coord._config["funding_enabled"] is False

    def test_status_report(self):
        from core.data_coordinator import DataCoordinator
        coord = DataCoordinator()
        coord.set_coins(["BTC", "ETH"])
        status = coord.status()
        assert status["coins_tracked"] == 2
        assert "funding" in status["feeds"]
        assert "oi" in status["feeds"]
        assert "orderbook" in status["feeds"]
        assert "news" in status["feeds"]
        assert "smart_money" in status["feeds"]

    def test_disabled_feed_not_refreshed(self):
        from core.data_coordinator import DataCoordinator
        coord = DataCoordinator()
        coord.set_config({
            "funding_enabled": False,
            "oi_enabled": False,
            "orderbook_enabled": False,
            "news_enabled": False,
            "smart_money_enabled": False,
        })
        coord.set_coins(["BTC"])
        results = coord.refresh()
        # All feeds should report False (disabled)
        for name in ("funding", "oi", "orderbook", "news", "smart_money"):
            assert results.get(name) is False

    def test_refresh_does_not_block_on_hung_feed(self):
        """refresh() must NOT stall the (single-threaded) scheduler when a
        feed's fetch() hangs: it returns within the deadline and marks the
        hung feed stale (False). Regression for the 2026-05-30 bug where the
        `with ThreadPoolExecutor(...)` exit called shutdown(wait=True) and
        blocked on the slowest CoinDesk feed regardless of the timeout."""
        import threading

        from core.data_coordinator import DataCoordinator

        coord = DataCoordinator()
        coord.set_coins(["BTC"])
        coord._ensure_feeds()  # build real feeds + mark initialized
        # Short deadline keeps the test fast + deterministic (behavior test,
        # not a tight wall-clock assertion).
        coord.set_config({
            "refresh_deadline_sec": 0.5,
            "funding_enabled": True, "oi_enabled": False,
            "orderbook_enabled": False, "news_enabled": False,
            "smart_money_enabled": False,
        })

        never_set = threading.Event()

        class _HangingFeed:
            def fetch(self, coins, **kwargs):
                never_set.wait()  # blocks forever — event is never set
                return {}

        coord._initialized = True
        coord._funding_feed = _HangingFeed()
        coord._funding_time = 0  # force stale so it is submitted

        done = threading.Event()
        box = {}

        def _run():
            box["result"] = coord.refresh(force=True)
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        # Fixed code returns ~0.5s; the generous 8s join is a behavior check,
        # not a timing assertion. OLD code blocked here forever.
        finished = done.wait(timeout=8)
        never_set.set()  # release the hung worker so it can exit cleanly

        assert finished, (
            "refresh() blocked on a hung feed — shutdown(wait=True) regression")
        assert box["result"].get("funding") is False, (
            "a feed that never completed must be reported stale (False)")


# ── Config section tests ──────────────────────────────────────────────

class TestDataFeedsConfig:
    def test_data_feeds_dict_exists(self):
        from config import DATA_FEEDS
        assert isinstance(DATA_FEEDS, dict)

    def test_all_feed_keys_present(self):
        from config import DATA_FEEDS
        required_keys = [
            "funding_enabled", "oi_enabled", "orderbook_enabled",
            "news_enabled", "smart_money_enabled",
            "funding_ttl", "oi_ttl", "orderbook_ttl",
            "news_ttl", "smart_money_ttl",
            "b11_funding_enabled", "b12_oi_enabled", "b13_smart_money_enabled",
            "v1_oi_exhaustion_veto", "v2_news_veto", "v3_slippage_veto",
            "v4_fomo_size_reduction",
        ]
        for key in required_keys:
            assert key in DATA_FEEDS, f"Missing config key: {key}"

    def test_ttl_values_sane(self):
        from config import DATA_FEEDS
        # TTLs should be positive and reasonable
        assert 30 <= DATA_FEEDS["orderbook_ttl"] <= 300
        assert 60 <= DATA_FEEDS["funding_ttl"] <= 900
        assert 60 <= DATA_FEEDS["oi_ttl"] <= 600
        assert 300 <= DATA_FEEDS["news_ttl"] <= 1800
        assert 300 <= DATA_FEEDS["smart_money_ttl"] <= 3600


# ── MCP Brain integration tests ──────────────────────────────────────

class TestMCPBrainIntegration:
    """Test that _score_coin correctly uses data feed signals."""

    def _make_brain_with_mock_coordinator(self, ctx_overrides=None):
        """Create an MCPBrain with a mocked DataCoordinator."""
        from core.data_coordinator import MarketContext

        # Build a default MarketContext with overrides
        defaults = {
            "funding": {"fr_zscore": 0.0, "fr_side_signal": "neutral", "stale": False},
            "open_interest": {"oi_price_divergence": "unknown", "oi_conviction": 0.0, "stale": False},
            "orderbook": {"imbalance": 0.0, "imbalance_momentum": 0.0,
                         "depth_ratio_log": 0.0, "slippage_buy_bps": 5.0,
                         "slippage_sell_bps": 5.0, "stale": False},
            "news": {"has_veto_news": False, "sentiment_score": 0.0, "stale": False},
            "smart_money": {"smart_money_inflow": False, "crowd_signal": "neutral", "stale": False},
            "any_stale": False,
        }
        if ctx_overrides:
            for feed, vals in ctx_overrides.items():
                if feed in defaults and isinstance(defaults[feed], dict):
                    defaults[feed].update(vals)
                else:
                    defaults[feed] = vals

        mock_ctx = MarketContext(**defaults)
        mock_coord = MagicMock()
        mock_coord.get_market_context.return_value = mock_ctx
        return mock_coord

    def _make_minimal_ei(self, *, side="buy"):
        """Create minimal exchange indicators that pass all 4 required conditions."""
        ema20 = 100.0
        ema50 = 99.5 if side == "buy" else 100.5
        return {
            "4h": {
                "adx": 30, "ema20": ema20, "ema50": ema50,
                "ema20_above_50": side == "buy",
                "ema20_slope": 0.001 if side == "buy" else -0.001,
                "bb_width": 3.0,
                "smc_structure": 0, "smc_detail": {},
            },
            "1h": {
                "adx": 25, "rsi": 55 if side == "buy" else 42,
                "ema20_above_50": side == "buy",
                "macd_hist": 0.001, "macd_hist_rising": True,
                "vol_ratio": 1.5, "atr_pct": 2.0,
                "hh": True, "hl": True, "ll": False, "lh": False,
                "price": 100.0, "price_vs_vwap": 0.5,
                "smc_structure": 0, "smc_entry": 0,
                "smc_confirmation": 0, "smc_detail": {
                    "ms": {}, "sweep": {}, "pa": {},
                    "fvg": {}, "sd": {}, "ote": {}, "fib": {},
                },
            },
            "15m": {
                "ema20_above_50": side == "buy",
            },
        }

    def test_b11_funding_bonus_awarded(self, monkeypatch):
        """B11 should award points when FR z-score aligns with side (when ENABLED).

        B11 is disabled by default (2026-05-30: screened NO_EDGE / anti-predictive,
        Sharpe -1.29), so this unit test enables the flag to verify the MECHANISM
        independent of the production default."""
        import config
        monkeypatch.setitem(config.DATA_FEEDS, "b11_funding_enabled", True)
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "funding": {"fr_zscore": -2.5, "fr_side_signal": "bullish", "stale": False},
        })
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        assert result["score"] > 0, "should have a score"
        assert "fr_z=" in result["reason"], f"B11 not in reason: {result['reason']}"

    def test_b12_oi_continuation_bonus(self, monkeypatch):
        """B12 should award points for OI continuation signal (when ENABLED).

        B12 is disabled by default (2026-05-30: OI-divergence screened NO_EDGE),
        so enable the flag to verify the MECHANISM independent of the default."""
        import config
        monkeypatch.setitem(config.DATA_FEEDS, "b12_oi_enabled", True)
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "open_interest": {
                "oi_price_divergence": "continuation",
                "oi_conviction": 0.6,
                "stale": False,
            },
        })
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        assert "oi_continuation" in result["reason"], \
            f"B12 not in reason: {result['reason']}"

    def test_v1_oi_exhaustion_veto(self, monkeypatch):
        """V1 should block long entry when OI shows exhaustion (when ENABLED).

        V1 is disabled by default (2026-05-30: OI-divergence screened NO_EDGE),
        so enable the flag to verify the MECHANISM independent of the default."""
        import config
        monkeypatch.setitem(config.DATA_FEEDS, "v1_oi_exhaustion_veto", True)
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "open_interest": {
                "oi_price_divergence": "exhaustion",
                "oi_conviction": 0.7,
                "stale": False,
            },
        })
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        assert result["score"] == 0, "V1 veto should zero the score"
        assert "veto" in result["reason"].lower()

    def test_v2_news_veto(self):
        """V2 should block entry when negative news detected."""
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "news": {
                "has_veto_news": True,
                "veto_reason": "BTC exchange hacked",
                "stale": False,
            },
        })
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        assert result["score"] == 0, "V2 veto should zero the score"
        assert "veto" in result["reason"].lower()

    def test_v3_slippage_veto(self):
        """V3 should block entry when slippage exceeds threshold."""
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "orderbook": {
                "slippage_buy_bps": 50.0,  # 50 bps > 30 bps threshold
                "slippage_sell_bps": 50.0,
                "imbalance": 0.0,
                "imbalance_momentum": 0.0,
                "depth_ratio_log": 0.0,
                "stale": False,
            },
        })
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        assert result["score"] == 0, "V3 veto should zero the score"
        assert "slippage" in result["reason"].lower()

    def test_v4_fomo_reduces_confidence(self):
        """V4 FOMO signal should reduce confidence by multiplier."""
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        # First get baseline confidence without FOMO
        brain._data_coordinator = self._make_brain_with_mock_coordinator()
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        baseline = brain._score_coin("BTC", data, ei)
        baseline_conf = baseline["confidence"]

        # Now with FOMO signal
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "smart_money": {
                "smart_money_inflow": False,
                "crowd_signal": "fomo",
                "stale": False,
            },
        })
        fomo_result = brain._score_coin("BTC", data, ei)
        # Confidence should be reduced
        assert fomo_result["confidence"] < baseline_conf, \
            f"FOMO conf {fomo_result['confidence']} should be < baseline {baseline_conf}"

    def test_stale_feeds_produce_neutral(self):
        """Stale feeds should not award bonuses or fire vetos."""
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = self._make_brain_with_mock_coordinator({
            "funding": {"fr_zscore": -3.0, "fr_side_signal": "bullish", "stale": True},
            "open_interest": {"oi_price_divergence": "exhaustion", "oi_conviction": 0.9, "stale": True},
            "news": {"has_veto_news": True, "veto_reason": "hack", "stale": True},
        })
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        # Should NOT be vetoed (stale data = ignored)
        assert result["score"] > 0, \
            f"Stale feeds should not veto; got score={result['score']}, reason={result['reason']}"
        # Should NOT get funding bonus (stale)
        assert "fr_z=" not in result["reason"]

    def test_no_coordinator_still_scores(self):
        """When DataCoordinator is None, scoring works with base rules only."""
        from core.mcp_brain import MCPBrain
        brain = MCPBrain()
        brain._data_coordinator = None  # explicitly disabled
        data = {"funding": {}, "orderbook": {}}
        ei = self._make_minimal_ei(side="buy")
        result = brain._score_coin("BTC", data, ei)
        # Should produce a valid score using only legacy B1-B10
        assert result["score"] >= 50, "Base 50 for passing all 4 required"
