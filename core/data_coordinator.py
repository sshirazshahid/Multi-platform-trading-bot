"""
core/data_coordinator.py — Master Data Feed Orchestrator

Coordinates all external data feeds on independent schedules, provides a
unified `get_market_context(symbol)` method that mcp_brain.py calls during
scoring.

Design principles:
  1. Each feed runs on its own TTL — no feed blocks another
  2. Fail-open: if a feed is down, its data is marked stale and scoring
     uses neutral defaults (never blocks entry on missing data)
  3. Background refresh: feeds are refreshed in a ThreadPoolExecutor so
     they don't add latency to the scoring cycle
  4. Staleness tracking: if any feed is >2x its TTL, it's marked stale
  5. Single source of truth: mcp_brain reads ONLY from here, not from
     individual feed modules directly

Usage in mcp_brain.py:
    from core.data_coordinator import get_coordinator
    coordinator = get_coordinator()
    coordinator.set_coins(["BTC", "ETH", ...])
    ctx = coordinator.get_market_context("BTC")
    # ctx.funding.fr_zscore, ctx.oi.oi_price_divergence, etc.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Lazy imports to avoid circular dependency at module load time
_coordinator_instance: DataCoordinator | None = None
_coordinator_lock = threading.Lock()


@dataclass
class FeedSnapshot:
    """Snapshot of a single feed's data for one coin. All fields are dicts."""
    data: dict = field(default_factory=dict)
    stale: bool = True
    last_update: float = 0.0


@dataclass
class MarketContext:
    """Unified market context for a single coin, consumed by mcp_brain.

    Each field is a dict with the feed's normalized output.
    `stale` is True if ANY feed is stale (caller should know).
    """
    funding: dict = field(default_factory=dict)
    open_interest: dict = field(default_factory=dict)
    orderbook: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    smart_money: dict = field(default_factory=dict)
    any_stale: bool = True

    def get(self, feed_name: str, key: str, default: Any = None) -> Any:
        """Convenience accessor: ctx.get('funding', 'fr_zscore', 0.0)."""
        feed_data = getattr(self, feed_name, {})
        if isinstance(feed_data, dict):
            return feed_data.get(key, default)
        return default


class DataCoordinator:
    """Orchestrates all data feeds with independent refresh schedules."""

    def __init__(self):
        self._coins: list[str] = []
        self._price_changes: dict[str, float] = {}  # for OI divergence
        self._lock = threading.Lock()

        # Feed instances (lazy-loaded)
        self._funding_feed = None
        self._oi_feed = None
        self._orderbook_feed = None
        self._news_feed = None
        self._smart_money_feed = None

        # Feed data caches
        self._funding_data: dict = {}
        self._oi_data: dict = {}
        self._orderbook_data: dict = {}
        self._news_data: dict = {}
        self._smart_money_data: dict = {}

        # Timestamps
        self._funding_time: float = 0.0
        self._oi_time: float = 0.0
        self._orderbook_time: float = 0.0
        self._news_time: float = 0.0
        self._smart_money_time: float = 0.0

        # Config (can be overridden via set_config)
        self._config = {
            "funding_enabled": True,
            "oi_enabled": True,
            "orderbook_enabled": True,
            "news_enabled": True,
            "smart_money_enabled": True,
            "funding_ttl": 300,
            "oi_ttl": 180,
            "orderbook_ttl": 60,
            "news_ttl": 600,
            "smart_money_ttl": 900,
            "staleness_multiplier": 2.0,  # >2x TTL = stale
            "max_workers": 5,
            # Hard wall-clock deadline for a single refresh(). The bot runs on
            # a single-threaded scheduler; a slow CoinDesk feed must NEVER
            # stall the position monitor. Feeds not done by the deadline are
            # marked stale (False) and their work is cancelled/abandoned — we
            # never block on stragglers. (2026-05-30 audit fix.)
            "refresh_deadline_sec": 20.0,
        }

        self._initialized = False

    def set_config(self, overrides: dict) -> None:
        """Override default config values."""
        self._config.update(overrides)

    def set_coins(self, coins: list[str]) -> None:
        """Set the universe of coins to track."""
        self._coins = [c.split("/")[0].upper() for c in coins]

    def set_price_changes(self, changes: dict[str, float]) -> None:
        """Set 6h price change % per coin (for OI divergence computation)."""
        self._price_changes = changes

    def _ensure_feeds(self) -> None:
        """Lazy-initialize feed instances."""
        if self._initialized:
            return

        try:
            from core.data_feeds.funding_rate_feed import FundingRateFeed
            self._funding_feed = FundingRateFeed(
                cache_ttl=self._config["funding_ttl"])
        except Exception as e:
            logger.warning(f"[DataCoord] FundingRateFeed init failed: {e}")

        try:
            from core.data_feeds.open_interest_feed import OpenInterestFeed
            self._oi_feed = OpenInterestFeed(
                cache_ttl=self._config["oi_ttl"])
        except Exception as e:
            logger.warning(f"[DataCoord] OpenInterestFeed init failed: {e}")

        try:
            from core.data_feeds.orderbook_depth_feed import OrderBookDepthFeed
            self._orderbook_feed = OrderBookDepthFeed(
                cache_ttl=self._config["orderbook_ttl"])
        except Exception as e:
            logger.warning(f"[DataCoord] OrderBookDepthFeed init failed: {e}")

        try:
            from core.data_feeds.news_sentiment_feed import NewsSentimentFeed
            self._news_feed = NewsSentimentFeed(
                cache_ttl=self._config["news_ttl"])
        except Exception as e:
            logger.warning(f"[DataCoord] NewsSentimentFeed init failed: {e}")

        try:
            from core.data_feeds.smart_money_feed import SmartMoneyFeed
            self._smart_money_feed = SmartMoneyFeed(
                cache_ttl=self._config["smart_money_ttl"])
        except Exception as e:
            logger.warning(f"[DataCoord] SmartMoneyFeed init failed: {e}")

        self._initialized = True

    def refresh(self, *, force: bool = False) -> dict[str, bool]:
        """Refresh all feeds that are due.

        Returns: {feed_name: success_bool}
        """
        self._ensure_feeds()
        now = time.time()
        coins = self._coins[:15]  # cap per feed limits
        if not coins:
            return {}

        results: dict[str, bool] = {}

        # Determine which feeds need refresh
        feed_specs = [
            ("funding", self._funding_feed, self._funding_time,
             self._config["funding_ttl"], self._config["funding_enabled"]),
            ("oi", self._oi_feed, self._oi_time,
             self._config["oi_ttl"], self._config["oi_enabled"]),
            ("orderbook", self._orderbook_feed, self._orderbook_time,
             self._config["orderbook_ttl"], self._config["orderbook_enabled"]),
            ("news", self._news_feed, self._news_time,
             self._config["news_ttl"], self._config["news_enabled"]),
            ("smart_money", self._smart_money_feed, self._smart_money_time,
             self._config["smart_money_ttl"], self._config["smart_money_enabled"]),
        ]

        deadline = self._config.get("refresh_deadline_sec", 20.0)
        pool = ThreadPoolExecutor(max_workers=self._config["max_workers"])
        try:
            futures = {}
            for name, feed, last_time, ttl, enabled in feed_specs:
                if not enabled or feed is None:
                    results[name] = False
                    continue
                if not force and (now - last_time) < ttl:
                    results[name] = True  # still fresh
                    continue
                # Submit refresh
                if name == "oi":
                    fut = pool.submit(
                        feed.fetch, coins,
                        price_changes=self._price_changes)
                else:
                    fut = pool.submit(feed.fetch, coins)
                futures[fut] = name
                results[name] = False  # stale until this feed completes below

            for fut in as_completed(futures, timeout=deadline):
                name = futures[fut]
                try:
                    data = fut.result(timeout=1)
                    with self._lock:
                        if name == "funding":
                            self._funding_data = data or {}
                            self._funding_time = now
                        elif name == "oi":
                            self._oi_data = data or {}
                            self._oi_time = now
                        elif name == "orderbook":
                            self._orderbook_data = data or {}
                            self._orderbook_time = now
                        elif name == "news":
                            self._news_data = data or {}
                            self._news_time = now
                        elif name == "smart_money":
                            self._smart_money_data = data or {}
                            self._smart_money_time = now
                    results[name] = True
                except Exception as e:
                    logger.warning(f"[DataCoord] {name} refresh failed: {e}")
                    results[name] = False
        except FuturesTimeout:
            pending = [n for f, n in futures.items() if not f.done()]
            logger.warning(
                f"[DataCoord] refresh deadline ({deadline}s) hit; feeds still "
                f"pending kept stale: {pending} (not blocking the scheduler)")
        finally:
            # NEVER block the single-threaded scheduler on a slow feed. The old
            # `with ThreadPoolExecutor(...)` exit called shutdown(wait=True),
            # which awaited EVERY submitted fetch regardless of the deadline —
            # a slow CoinDesk call stalled the position monitor for minutes.
            # cancel_futures drops not-yet-started work; in-flight fetches are
            # abandoned (their results discarded) rather than awaited.
            pool.shutdown(wait=False, cancel_futures=True)

        return results

    def get_market_context(self, coin: str) -> MarketContext:
        """Get unified market context for a single coin.

        This is the main API consumed by mcp_brain._score_coin.
        Always returns a MarketContext — never raises.
        """
        cu = coin.split("/")[0].upper()
        now = time.time()
        stale_mult = self._config["staleness_multiplier"]

        with self._lock:
            funding = self._funding_data.get(cu, {})
            oi = self._oi_data.get(cu, {})
            ob = self._orderbook_data.get(cu, {})
            news = self._news_data.get(cu, {})
            smart = self._smart_money_data.get(cu, {})

            # Check staleness
            any_stale = False
            if self._config["funding_enabled"]:
                if (now - self._funding_time >
                        self._config["funding_ttl"] * stale_mult):
                    any_stale = True
                    funding = funding.copy()
                    funding["stale"] = True
            if self._config["oi_enabled"]:
                if (now - self._oi_time >
                        self._config["oi_ttl"] * stale_mult):
                    any_stale = True
                    oi = oi.copy()
                    oi["stale"] = True
            if self._config["orderbook_enabled"]:
                if (now - self._orderbook_time >
                        self._config["orderbook_ttl"] * stale_mult):
                    any_stale = True
                    ob = ob.copy()
                    ob["stale"] = True
            if self._config["news_enabled"]:
                if (now - self._news_time >
                        self._config["news_ttl"] * stale_mult):
                    any_stale = True
                    news = news.copy()
                    news["stale"] = True
            if self._config["smart_money_enabled"]:
                if (now - self._smart_money_time >
                        self._config["smart_money_ttl"] * stale_mult):
                    any_stale = True
                    smart = smart.copy()
                    smart["stale"] = True

        return MarketContext(
            funding=funding,
            open_interest=oi,
            orderbook=ob,
            news=news,
            smart_money=smart,
            any_stale=any_stale,
        )

    def get_all_contexts(self) -> dict[str, MarketContext]:
        """Get MarketContext for all tracked coins."""
        return {coin: self.get_market_context(coin) for coin in self._coins}

    def status(self) -> dict[str, Any]:
        """Return feed health status for monitoring/dashboard."""
        now = time.time()
        return {
            "coins_tracked": len(self._coins),
            "feeds": {
                "funding": {
                    "enabled": self._config["funding_enabled"],
                    "age_sec": round(now - self._funding_time, 1),
                    "ttl": self._config["funding_ttl"],
                    "n_coins": len(self._funding_data),
                    "stale": (now - self._funding_time >
                              self._config["funding_ttl"] *
                              self._config["staleness_multiplier"]),
                },
                "oi": {
                    "enabled": self._config["oi_enabled"],
                    "age_sec": round(now - self._oi_time, 1),
                    "ttl": self._config["oi_ttl"],
                    "n_coins": len(self._oi_data),
                    "stale": (now - self._oi_time >
                              self._config["oi_ttl"] *
                              self._config["staleness_multiplier"]),
                },
                "orderbook": {
                    "enabled": self._config["orderbook_enabled"],
                    "age_sec": round(now - self._orderbook_time, 1),
                    "ttl": self._config["orderbook_ttl"],
                    "n_coins": len(self._orderbook_data),
                    "stale": (now - self._orderbook_time >
                              self._config["orderbook_ttl"] *
                              self._config["staleness_multiplier"]),
                },
                "news": {
                    "enabled": self._config["news_enabled"],
                    "age_sec": round(now - self._news_time, 1),
                    "ttl": self._config["news_ttl"],
                    "n_coins": len(self._news_data),
                    "stale": (now - self._news_time >
                              self._config["news_ttl"] *
                              self._config["staleness_multiplier"]),
                },
                "smart_money": {
                    "enabled": self._config["smart_money_enabled"],
                    "age_sec": round(now - self._smart_money_time, 1),
                    "ttl": self._config["smart_money_ttl"],
                    "n_coins": len(self._smart_money_data),
                    "stale": (now - self._smart_money_time >
                              self._config["smart_money_ttl"] *
                              self._config["staleness_multiplier"]),
                },
            },
        }


def get_coordinator() -> DataCoordinator:
    """Get or create the singleton DataCoordinator instance."""
    global _coordinator_instance
    if _coordinator_instance is None:
        with _coordinator_lock:
            if _coordinator_instance is None:
                _coordinator_instance = DataCoordinator()
                # Apply config from config.py if available
                try:
                    from config import DATA_FEEDS
                    _coordinator_instance.set_config(DATA_FEEDS)
                except (ImportError, AttributeError):
                    pass
                logger.info("[DataCoord] Initialized data coordinator")
    return _coordinator_instance
