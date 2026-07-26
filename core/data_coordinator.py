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
        self._feed_times: dict[str, dict[str, float]] = {
            "funding": {},
            "oi": {},
            "orderbook": {},
            "news": {},
            "smart_money": {},
        }

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

    @staticmethod
    def _entry_is_fresh(entry: Any) -> bool:
        """Require a non-empty payload that explicitly declares itself fresh."""
        return (
            isinstance(entry, dict)
            and any(key != "stale" for key in entry)
            and entry.get("stale") is False
        )

    def _instrument_time(self, feed_name: str, coin: str) -> float:
        """Return an instrument timestamp without leaking another coin's age."""
        per_instrument = self._feed_times[feed_name]
        if coin in per_instrument:
            return per_instrument[coin]
        if per_instrument:
            return 0.0
        return float(getattr(self, f"_{feed_name}_time"))

    def _instrument_is_current(
        self,
        feed_name: str,
        coin: str,
        now: float,
        max_age: float,
    ) -> bool:
        data = getattr(self, f"_{feed_name}_data")
        entry = data.get(coin, {})
        updated_at = self._instrument_time(feed_name, coin)
        return (
            self._entry_is_fresh(entry)
            and updated_at > 0
            and now - updated_at < max_age
        )

    def _apply_refresh_payload(
        self,
        feed_name: str,
        requested_coins: list[str],
        payload: Any,
        updated_at: float,
    ) -> bool:
        """Merge a feed response and advance only fresh instrument clocks."""
        normalized = payload if isinstance(payload, dict) else {}
        fresh_count = 0

        with self._lock:
            cache = getattr(self, f"_{feed_name}_data")
            for key, entry in normalized.items():
                if isinstance(key, str) and isinstance(entry, dict):
                    cache[key] = entry

            for coin in requested_coins:
                entry = normalized.get(coin)
                if self._entry_is_fresh(entry):
                    self._feed_times[feed_name][coin] = updated_at
                    fresh_count += 1
                    continue

                # A completed request with no usable observation invalidates
                # this instrument without erasing its last known values.
                stale_entry = entry if isinstance(entry, dict) else cache.get(coin, {})
                stale_entry = stale_entry.copy()
                stale_entry["stale"] = True
                cache[coin] = stale_entry

            if fresh_count:
                setattr(self, f"_{feed_name}_time", updated_at)

        return bool(requested_coins) and fresh_count == len(requested_coins)

    def _fetch_with_deadline(
        self,
        feed_name: str,
        feed: Any,
        coins: list[str],
        deadline: float,
        cancel_event: threading.Event,
    ) -> Any:
        """Run a feed with a thread-local CoinDesk deadline/cancel context."""
        from core.data_feeds._coindesk_caller import coindesk_call_context

        with coindesk_call_context(
            deadline=deadline,
            cancel_event=cancel_event,
        ):
            if feed_name == "oi":
                return feed.fetch(coins, price_changes=self._price_changes)
            return feed.fetch(coins)

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
            ("funding", self._funding_feed,
             self._config["funding_ttl"], self._config["funding_enabled"]),
            ("oi", self._oi_feed,
             self._config["oi_ttl"], self._config["oi_enabled"]),
            ("orderbook", self._orderbook_feed,
             self._config["orderbook_ttl"], self._config["orderbook_enabled"]),
            ("news", self._news_feed,
             self._config["news_ttl"], self._config["news_enabled"]),
            ("smart_money", self._smart_money_feed,
             self._config["smart_money_ttl"], self._config["smart_money_enabled"]),
        ]

        deadline_seconds = max(
            0.0, float(self._config.get("refresh_deadline_sec", 20.0))
        )
        deadline_at = time.monotonic() + deadline_seconds
        cancel_event = threading.Event()
        pool = ThreadPoolExecutor(max_workers=self._config["max_workers"])
        try:
            futures = {}
            processed_futures = set()
            for name, feed, ttl, enabled in feed_specs:
                if not enabled or feed is None:
                    results[name] = False
                    continue

                due_coins = [
                    coin for coin in coins
                    if force or not self._instrument_is_current(
                        name, coin, now, ttl
                    )
                ]
                if not due_coins:
                    results[name] = True  # still fresh
                    continue

                fut = pool.submit(
                    self._fetch_with_deadline,
                    name,
                    feed,
                    due_coins,
                    deadline_at,
                    cancel_event,
                )
                futures[fut] = (name, due_coins)
                results[name] = False  # stale until this feed completes below

            if futures:
                wait_timeout = max(0.0, deadline_at - time.monotonic())
                for fut in as_completed(futures, timeout=wait_timeout):
                    processed_futures.add(fut)
                    name, requested_coins = futures[fut]
                    try:
                        data = fut.result()
                        results[name] = self._apply_refresh_payload(
                            name, requested_coins, data, time.time()
                        )
                    except Exception as e:
                        self._apply_refresh_payload(
                            name, requested_coins, {}, time.time()
                        )
                        logger.warning(f"[DataCoord] {name} refresh failed: {e}")
                        results[name] = False
        except FuturesTimeout:
            cancel_event.set()
            pending = []
            for future, (name, requested_coins) in futures.items():
                if future not in processed_futures:
                    pending.append(name)
                    self._apply_refresh_payload(
                        name, requested_coins, {}, time.time()
                    )
            logger.warning(
                f"[DataCoord] refresh deadline ({deadline_seconds}s) hit; feeds still "
                f"pending kept stale: {pending} (not blocking the scheduler)")
        finally:
            cancel_event.set()
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
            any_stale = False
            snapshots = {}
            for feed_name in (
                "funding", "oi", "orderbook", "news", "smart_money"
            ):
                data = getattr(self, f"_{feed_name}_data")
                entry = data.get(cu, {})
                enabled = self._config[f"{feed_name}_enabled"]
                if enabled and not self._instrument_is_current(
                    feed_name,
                    cu,
                    now,
                    self._config[f"{feed_name}_ttl"] * stale_mult,
                ):
                    entry = entry.copy()
                    entry["stale"] = True
                    any_stale = True
                snapshots[feed_name] = entry

        return MarketContext(
            funding=snapshots["funding"],
            open_interest=snapshots["oi"],
            orderbook=snapshots["orderbook"],
            news=snapshots["news"],
            smart_money=snapshots["smart_money"],
            any_stale=any_stale,
        )

    def get_all_contexts(self) -> dict[str, MarketContext]:
        """Get MarketContext for all tracked coins."""
        return {coin: self.get_market_context(coin) for coin in self._coins}

    def _feed_status(self, feed_name: str, now: float) -> dict[str, Any]:
        ttl = self._config[f"{feed_name}_ttl"]
        max_age = ttl * self._config["staleness_multiplier"]
        tracked = self._coins[:15]
        timestamps = [
            self._instrument_time(feed_name, coin) for coin in tracked
        ]
        oldest = min(timestamps) if timestamps else float(
            getattr(self, f"_{feed_name}_time")
        )
        stale = any(
            not self._instrument_is_current(feed_name, coin, now, max_age)
            for coin in tracked
        ) if tracked else oldest <= 0 or now - oldest >= max_age
        data = getattr(self, f"_{feed_name}_data")
        return {
            "enabled": self._config[f"{feed_name}_enabled"],
            "age_sec": round(now - oldest, 1),
            "ttl": ttl,
            "n_coins": len(data),
            "stale": stale,
        }

    def status(self) -> dict[str, Any]:
        """Return feed health status for monitoring/dashboard."""
        now = time.time()
        with self._lock:
            return {
                "coins_tracked": len(self._coins),
                "feeds": {
                    name: self._feed_status(name, now)
                    for name in (
                        "funding", "oi", "orderbook", "news", "smart_money"
                    )
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
