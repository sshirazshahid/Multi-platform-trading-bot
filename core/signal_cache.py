"""
core/signal_cache.py — Background signal pre-computation + OHLCV cache.

Eliminates the two biggest latency bottlenecks:
  1. OHLCV double-fetch (strategy_selector AND strategy.run both fetch)
  2. 15-min scan interval (signals are always stale by the time they execute)

The SignalCache runs in a background thread, continuously pre-computing
technical signals for all symbols. The main scan loop reads fresh signals
from cache instead of blocking on OHLCV fetches.

Usage:
    cache = SignalCache(selector, exchanges, symbols)
    cache.start()
    ...
    signals = cache.get_signals()  # instant, no blocking
    ohlcv = cache.get_ohlcv("BTC/USDT:USDT", "1h", "futures")  # cached
"""

import time
import threading
from collections import defaultdict
from loguru import logger


# OHLCV cache TTL — 2 minutes (exchanges update candles every 1-3 min)
OHLCV_CACHE_TTL = 120

# Signal refresh interval — how often the background thread re-scans
SIGNAL_REFRESH_INTERVAL = 60  # 60 seconds


class OHLCVCache:
    """Thread-safe OHLCV data cache. Prevents redundant exchange API calls."""

    def __init__(self):
        self._data = {}   # (exchange_name, symbol, timeframe, market_type) -> (candles, timestamp)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, exchange, symbol: str, timeframe: str = "1h",
            limit: int = 100, market_type: str = "spot") -> list:
        """Get OHLCV data — from cache if fresh, else fetch from exchange."""
        key = (exchange.name, symbol, timeframe, market_type)

        with self._lock:
            cached = self._data.get(key)
            if cached and (time.time() - cached[1]) < OHLCV_CACHE_TTL:
                self._hits += 1
                return cached[0]

        # Cache miss — fetch from exchange (outside lock to avoid blocking)
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit,
                                       market_type=market_type)
            if raw:
                with self._lock:
                    self._data[key] = (raw, time.time())
                    self._misses += 1
                return raw
        except Exception as e:
            logger.debug("[OHLCVCache] Fetch {}/{}: {}".format(symbol, timeframe, e))

        # Return stale data if available, else empty
        with self._lock:
            if cached:
                return cached[0]
        return []

    def invalidate(self, symbol: str = None):
        """Clear cache for a symbol or all."""
        with self._lock:
            if symbol:
                self._data = {k: v for k, v in self._data.items() if k[1] != symbol}
            else:
                self._data.clear()

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total > 0 else 0,
            }


class SignalCache:
    """
    Background signal pre-computation engine.

    Runs strategy_selector.analyze_batch() in a daemon thread every 60s,
    caching results. The main scan loop reads pre-computed signals
    instantly instead of blocking on OHLCV fetches.
    """

    def __init__(self, selector, exchanges: dict, ohlcv_cache: OHLCVCache = None):
        """
        Args:
            selector: StrategySelector instance
            exchanges: Dict of {name: exchange_client}
            ohlcv_cache: Shared OHLCVCache (created if not provided)
        """
        self.selector = selector
        self.exchanges = exchanges
        self.ohlcv_cache = ohlcv_cache or OHLCVCache()

        self._signals = {}       # {symbol: [TradeOpportunity, ...]}
        self._signal_time = 0    # When signals were last refreshed
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._symbols = []       # Symbols to scan
        self._futures_map = {}   # spot -> futures mapping

        # Stats
        self._refresh_count = 0
        self._total_signals = 0
        self._last_duration = 0

    def start(self, symbols: list, futures_map: dict = None):
        """Start background signal pre-computation."""
        self._symbols = symbols
        self._futures_map = futures_map or {}
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._refresh_loop, daemon=True, name="SignalCache")
        self._thread.start()
        logger.info("[SignalCache] Started — {} symbols, refresh every {}s".format(
            len(symbols), SIGNAL_REFRESH_INTERVAL))

    def stop(self):
        """Stop background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("[SignalCache] Stopped")

    def update_symbols(self, symbols: list, futures_map: dict = None):
        """Update the symbol list (e.g., after portfolio rescan)."""
        self._symbols = symbols
        if futures_map:
            self._futures_map = futures_map

    def get_signals(self) -> dict:
        """Get pre-computed signals. Returns {symbol: [TradeOpportunity, ...]}."""
        with self._lock:
            return dict(self._signals)

    def get_signal_age(self) -> float:
        """Seconds since last signal refresh."""
        return time.time() - self._signal_time if self._signal_time else float("inf")

    @property
    def is_fresh(self) -> bool:
        """True if signals are less than 2 refresh intervals old."""
        return self.get_signal_age() < SIGNAL_REFRESH_INTERVAL * 2

    @property
    def stats(self) -> dict:
        return {
            "refresh_count": self._refresh_count,
            "total_signals": self._total_signals,
            "signal_age_s": round(self.get_signal_age(), 1),
            "last_duration_s": round(self._last_duration, 1),
            "symbols": len(self._symbols),
            "ohlcv": self.ohlcv_cache.stats,
        }

    # ── Background thread ────────────────────────────────────────────

    def _refresh_loop(self):
        """Background loop that continuously pre-computes signals."""
        # Initial delay — let exchanges connect first
        time.sleep(5)
        while self._running:
            try:
                self._refresh_once()
            except Exception as e:
                logger.debug("[SignalCache] Refresh error: {}".format(e))
            # Sleep in small increments so stop() is responsive
            for _ in range(SIGNAL_REFRESH_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    def _refresh_once(self):
        """Run one full scan cycle across all exchanges and symbols."""
        t0 = time.time()
        new_signals = {}

        for ex_name, exchange in self.exchanges.items():
            if not getattr(exchange, "_connected", False):
                continue
            try:
                results = self.selector.analyze_batch(
                    exchange, self._symbols,
                    futures_map=self._futures_map,
                    market_type="spot")
                for symbol, opps in results.items():
                    if symbol not in new_signals:
                        new_signals[symbol] = []
                    new_signals[symbol].extend(opps)
            except Exception as e:
                logger.debug("[SignalCache] {} scan error: {}".format(ex_name, e))

        # Atomic update
        with self._lock:
            self._signals = new_signals
            self._signal_time = time.time()

        duration = time.time() - t0
        self._last_duration = duration
        self._refresh_count += 1
        n_signals = sum(len(v) for v in new_signals.values())
        self._total_signals = n_signals

        if n_signals > 0 or self._refresh_count % 5 == 0:
            logger.info(
                "[SignalCache] Refresh #{}: {} signals across {} symbols "
                "in {:.1f}s | OHLCV cache: {}".format(
                    self._refresh_count, n_signals, len(new_signals),
                    duration, self.ohlcv_cache.stats))
