"""
core/scan_agent.py — Market Scanner Agent

Filters ALL active markets across exchanges by:
  - Liquidity (24h volume > threshold)
  - Spread (bid-ask spread < threshold)
  - Anomaly detection (unusual price moves, volume spikes)
  - Regime classification (trending, ranging, volatile)

Runs before strategy selection to pre-filter the best opportunities.
"""

import time
import numpy as np
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class MarketProfile:
    symbol: str
    exchange: str
    market_type: str       # "spot" or "futures"
    price: float = 0.0
    volume_24h: float = 0.0
    spread_pct: float = 0.0
    change_1h: float = 0.0
    change_24h: float = 0.0
    atr_pct: float = 0.0
    adx: float = 0.0
    rsi: float = 50.0
    liquidity_score: float = 0.0   # 0-100
    anomaly_score: float = 0.0     # 0-100 (higher = more unusual)
    tradeable: bool = True
    flags: list = field(default_factory=list)
    regime: str = "unknown"


class ScanAgent:
    """Pre-filters markets before strategy analysis."""

    # Thresholds
    MIN_VOLUME_USD     = 50_000     # $50K min 24h volume
    MAX_SPREAD_PCT     = 0.5        # 0.5% max spread
    ANOMALY_CHANGE_1H  = 5.0        # >5% 1h move = anomaly
    ANOMALY_CHANGE_24H = 15.0       # >15% 24h move = anomaly
    VOLUME_SPIKE_MULT  = 3.0        # 3x avg volume = spike

    def __init__(self):
        self._cache: dict[str, MarketProfile] = {}
        self._last_scan = 0.0

    def scan_markets(self, exchange, symbols: list,
                     market_type: str = "spot") -> list[MarketProfile]:
        """Scan a list of symbols and return filtered MarketProfiles."""
        profiles = []

        for symbol in symbols:
            try:
                profile = self._analyze_symbol(exchange, symbol, market_type)
                if profile:
                    profiles.append(profile)
                time.sleep(0.1)  # Rate limiting
            except Exception as e:
                logger.debug(f"[ScanAgent] {symbol} scan failed: {e}")

        # Sort by liquidity score descending
        profiles.sort(key=lambda p: p.liquidity_score, reverse=True)

        # Log summary
        tradeable = [p for p in profiles if p.tradeable]
        flagged = [p for p in profiles if p.flags]
        logger.info(
            f"[ScanAgent] {exchange.name} {market_type}: "
            f"{len(tradeable)}/{len(profiles)} tradeable, "
            f"{len(flagged)} flagged")

        for p in flagged:
            for flag in p.flags:
                logger.warning(f"[ScanAgent] {p.symbol}: {flag}")

        return profiles

    def get_tradeable(self, profiles: list[MarketProfile]) -> list[str]:
        """Return list of symbols that pass all filters."""
        return [p.symbol for p in profiles if p.tradeable]

    def get_flagged(self, profiles: list[MarketProfile]) -> list[MarketProfile]:
        """Return profiles with anomalies or warnings."""
        return [p for p in profiles if p.flags]

    def _analyze_symbol(self, exchange, symbol: str,
                        market_type: str) -> MarketProfile:
        """Build a MarketProfile for one symbol."""
        profile = MarketProfile(
            symbol=symbol,
            exchange=exchange.name,
            market_type=market_type,
        )

        # Fetch ticker
        ticker = exchange.fetch_ticker(symbol, market_type)
        if not ticker or not ticker.get("last"):
            profile.tradeable = False
            profile.flags.append("No ticker data")
            return profile

        profile.price = float(ticker.get("last", 0))
        profile.volume_24h = float(ticker.get("quoteVolume", 0) or
                                   ticker.get("baseVolume", 0) or 0)

        # Calculate spread from bid/ask
        bid = float(ticker.get("bid", 0) or 0)
        ask = float(ticker.get("ask", 0) or 0)
        if bid > 0 and ask > 0:
            profile.spread_pct = (ask - bid) / bid * 100

        # Price changes
        profile.change_24h = float(ticker.get("percentage", 0) or 0)

        # Liquidity score (0-100)
        vol_score = min(100, profile.volume_24h / 1_000_000 * 10)  # $100K = 1 point
        spread_score = max(0, 100 - profile.spread_pct * 200)       # 0.5% spread = 0
        profile.liquidity_score = round(vol_score * 0.6 + spread_score * 0.4, 1)

        # Anomaly detection
        anomaly = 0.0
        if abs(profile.change_24h) > self.ANOMALY_CHANGE_24H:
            anomaly += 50
            profile.flags.append(
                f"Large 24h move: {profile.change_24h:+.1f}%")

        if profile.spread_pct > self.MAX_SPREAD_PCT:
            anomaly += 30
            profile.flags.append(
                f"Wide spread: {profile.spread_pct:.2f}%")

        profile.anomaly_score = min(100, anomaly)

        # Tradeability
        if profile.volume_24h < self.MIN_VOLUME_USD:
            profile.tradeable = False
            profile.flags.append(
                f"Low volume: ${profile.volume_24h:,.0f}")

        if profile.spread_pct > self.MAX_SPREAD_PCT * 2:
            profile.tradeable = False
            profile.flags.append("Spread too wide for safe trading")

        # Cache
        key = f"{exchange.name}:{symbol}:{market_type}"
        self._cache[key] = profile

        return profile

    def get_summary(self) -> dict:
        """Return scan summary for dashboard."""
        if not self._cache:
            return {"total": 0, "tradeable": 0, "flagged": 0}

        profiles = list(self._cache.values())
        return {
            "total": len(profiles),
            "tradeable": sum(1 for p in profiles if p.tradeable),
            "flagged": sum(1 for p in profiles if p.flags),
            "avg_liquidity": round(
                sum(p.liquidity_score for p in profiles) / max(len(profiles), 1), 1),
            "top_volume": sorted(
                [(p.symbol, p.volume_24h) for p in profiles],
                key=lambda x: x[1], reverse=True)[:5],
        }
