"""
core/data_feeds/funding_rate_feed.py — CoinDesk Funding Rate History Feed

Fetches funding rate tick + 24h OHLCV history from CoinDesk Data API.
Computes:
  - Current funding rate (fr_current)
  - 24h funding rate percentile vs trailing window (fr_pctl_24h)
  - Funding rate z-score vs 7-day mean/std (fr_zscore)
  - Extreme flag: |z| > 2.0 indicates crowded positioning

Signal thesis:
  Extreme negative FR = shorts overcrowded = long squeeze incoming (bullish)
  Extreme positive FR = longs overcrowded = short squeeze incoming (bearish)
  The scoring engine awards a BONUS when FR aligns with proposed side
  (e.g., extreme neg FR + long entry = bonus), and a VETO when FR is
  strongly contrary (e.g., extreme pos FR + long entry = crowded trade).

Cache TTL: 300s (5 min). Funding rates settle every 8h on Binance/Bybit
but the real-time predicted rate changes continuously.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from loguru import logger

# CoinDesk instrument naming: {BASE}-USDT-VANILLA-PERPETUAL
_COINDESK_INSTRUMENT_FMT = "{base}-USDT-VANILLA-PERPETUAL"
_COINDESK_MARKET = "binance"  # primary venue; Bybit as fallback


def _coin_to_instrument(coin: str) -> str:
    """Convert base coin (e.g. 'BTC') to CoinDesk instrument name."""
    return _COINDESK_INSTRUMENT_FMT.format(base=coin.upper())


class FundingRateFeed:
    """Fetches and normalizes funding rate data from CoinDesk + Binance."""

    def __init__(self, *, cache_ttl: int = 300):
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._cache_ttl = cache_ttl
        # 7-day trailing window for z-score computation (populated from OHLCV)
        self._history: dict[str, list[float]] = {}  # coin -> [rate, rate, ...]
        self._history_time: float = 0.0
        self._history_ttl = 3600  # refresh history hourly

    def fetch(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch funding rate context for a list of base coins.

        Returns:
            {coin: {
                "fr_current": float,        # raw funding rate (e.g. 0.0001 = 0.01%)
                "fr_zscore": float,          # z-score vs 7d trailing window
                "fr_extreme": str|None,      # "long_crowded"|"short_crowded"|None
                "fr_side_signal": str,       # "bullish"|"bearish"|"neutral"
                "fr_history_mean": float,    # 7d mean for context
                "stale": bool,              # True if data is older than 2x TTL
            }}
        """
        now = time.time()

        # Return cache if fresh
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache

        # Refresh 7-day history if stale (used for z-score)
        if now - self._history_time > self._history_ttl:
            self._refresh_history(coins[:15])  # warm history for the same coins we score below
            self._history_time = now

        result: dict[str, dict] = {}

        # Strategy: try CoinDesk tick API first (batch), fallback to Binance
        cd_data = self._fetch_coindesk_tick(coins[:15])

        for coin in coins[:15]:
            try:
                tick = cd_data.get(coin, {})
                fr_current = tick.get("fr_current")

                # Fallback to Binance if CoinDesk missed this coin
                if fr_current is None:
                    fr_current = self._fetch_binance_single(coin)

                if fr_current is None:
                    result[coin] = self._neutral(stale=True)
                    continue

                # Z-score against trailing history
                hist = self._history.get(coin, [])
                if len(hist) >= 5:
                    mean = statistics.mean(hist)
                    std = statistics.stdev(hist) if len(hist) >= 2 else 0.0001
                    zscore = (fr_current - mean) / max(std, 1e-8)
                else:
                    mean = fr_current
                    zscore = 0.0

                # Classify extremes
                extreme = None
                side_signal = "neutral"
                if zscore > 2.0:
                    extreme = "long_crowded"
                    side_signal = "bearish"  # longs overcrowded = bearish
                elif zscore < -2.0:
                    extreme = "short_crowded"
                    side_signal = "bullish"  # shorts overcrowded = bullish
                elif zscore > 1.0:
                    side_signal = "bearish"  # mild long crowding
                elif zscore < -1.0:
                    side_signal = "bullish"  # mild short crowding

                result[coin] = {
                    "fr_current": round(fr_current, 8),
                    "fr_zscore": round(zscore, 3),
                    "fr_extreme": extreme,
                    "fr_side_signal": side_signal,
                    "fr_history_mean": round(mean, 8),
                    "stale": False,
                }
            except Exception as e:
                logger.debug(f"[FundingFeed] {coin}: {e}")
                result[coin] = self._neutral(stale=True)

        self._cache = result
        self._cache_time = now
        logger.debug(f"[FundingFeed] fetched {len(result)} coins, "
                     f"{sum(1 for v in result.values() if not v['stale'])} fresh")
        return result

    # ── CoinDesk tick API ──────────────────────────────────────────────

    def _fetch_coindesk_tick(self, coins: list[str]) -> dict[str, dict]:
        """Batch fetch current FR from CoinDesk."""
        out: dict[str, dict] = {}
        try:
            instruments = ",".join(_coin_to_instrument(c) for c in coins)
            # Import dynamically to avoid hard dependency at module level
            from core.data_feeds._coindesk_caller import call_coindesk_fr_tick
            raw = call_coindesk_fr_tick(
                market=_COINDESK_MARKET, instruments=instruments)
            if not raw or "Data" not in raw:
                return out
            for inst_name, inst_data in raw.get("Data", {}).items():
                # Extract base coin from instrument name
                base = inst_data.get("INDEX_UNDERLYING", "").upper()
                if not base:
                    parts = inst_name.split("-")
                    base = parts[0] if parts else ""
                if base:
                    out[base] = {
                        "fr_current": float(inst_data.get("VALUE", 0) or 0),
                        "fr_24h_high": float(
                            inst_data.get("MOVING_24_HOUR_HIGH", 0) or 0),
                        "fr_24h_low": float(
                            inst_data.get("MOVING_24_HOUR_LOW", 0) or 0),
                    }
        except Exception as e:
            logger.debug(f"[FundingFeed] CoinDesk tick failed: {e}")
        return out

    # ── 7-day history for z-score ──────────────────────────────────────

    def _refresh_history(self, coins: list[str]) -> None:
        """Fetch 7-day FR OHLCV from CoinDesk for z-score computation."""
        for coin in coins:
            try:
                from core.data_feeds._coindesk_caller import (
                    call_coindesk_fr_ohlcv,
                )
                raw = call_coindesk_fr_ohlcv(
                    market=_COINDESK_MARKET,
                    instrument=_coin_to_instrument(coin),
                    frequency="hours",
                    aggregate=8,
                    limit=21,  # 7 days * 3 funding periods/day
                )
                if not raw or "Data" not in raw:
                    continue
                rates = []
                for bar in raw.get("Data", []):
                    close = bar.get("CLOSE")
                    if close is not None:
                        rates.append(float(close))
                if rates:
                    self._history[coin] = rates
            except Exception as e:
                logger.debug(f"[FundingFeed] history {coin}: {e}")

    # ── Binance fallback ───────────────────────────────────────────────

    def _fetch_binance_single(self, coin: str) -> float | None:
        """Fetch single coin FR from Binance as fallback."""
        try:
            import json
            import urllib.request
            url = "https://fapi.binance.com/fapi/v1/premiumIndex"
            req = urllib.request.Request(
                url, headers={"User-Agent": "TradingBot/2.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            for item in data:
                sym = item.get("symbol", "")
                if sym == f"{coin}USDT":
                    return float(item.get("lastFundingRate", 0))
        except Exception:
            pass
        return None

    @staticmethod
    def _neutral(*, stale: bool = False) -> dict:
        return {
            "fr_current": 0.0,
            "fr_zscore": 0.0,
            "fr_extreme": None,
            "fr_side_signal": "neutral",
            "fr_history_mean": 0.0,
            "stale": stale,
        }
