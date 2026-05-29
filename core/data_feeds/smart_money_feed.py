"""
core/data_feeds/smart_money_feed.py — Binance Smart Money + Social Hype Feed

Fetches two Binance Web3 intelligence endpoints:
  1. Smart Money Inflow: which tokens institutional/whale addresses are accumulating
  2. Social Hype: social buzz intensity + sentiment per token

Signal thesis:
  Smart Money Inflow:
    - Token in top-20 smart money inflow = institutional accumulation signal
    - Aligns with proposed side (buy when smart money buying) = confirmation
    - Used as a BONUS condition, not a VETO

  Social Hype (CONTRARIAN):
    - Extreme hype + Positive sentiment = retail FOMO = crowded long trade
    - Extreme hype + Negative sentiment = retail panic = potential bounce
    - Used as a risk-reduction signal: reduce size when hype is extreme

Cache TTL: 900s (15 min). These are on-chain/social aggregates, not tick data.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from loguru import logger

_SMART_MONEY_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/"
    "tracker/wallet/token/inflow/rank/query/ai"
)
_SOCIAL_HYPE_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/"
    "buw/wallet/market/token/pulse/social/hype/rank/leaderboard/ai"
)
_TRENDING_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/"
    "buw/wallet/market/token/pulse/unified/rank/list/ai"
)

# Map common token symbols on BSC/ETH to their trading base symbols
_TOKEN_ALIAS = {
    "WBTC": "BTC", "WETH": "ETH", "WBNB": "BNB",
    "stETH": "ETH", "STETH": "ETH",
    "BTCB": "BTC",
}


class SmartMoneyFeed:
    """Fetches smart money flow and social hype data from Binance Web3."""

    def __init__(self, *, cache_ttl: int = 900):
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._cache_ttl = cache_ttl

    def fetch(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch smart money + social signals for a list of base coins.

        Returns:
            {coin: {
                "smart_money_rank": int|None,     # rank in inflow list (1=highest)
                "smart_money_inflow": bool,        # True if in top-20
                "social_hype_rank": int|None,
                "social_sentiment": str,           # "Positive"|"Negative"|"Neutral"
                "social_hype_extreme": bool,       # True if rank <= 5
                "crowd_signal": str,               # "fomo"|"panic"|"mild_bullish"|
                                                  # "mild_bearish"|"neutral"
                "stale": bool,
            }}
        """
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache

        # Fetch both feeds
        sm_data = self._fetch_smart_money()
        social_data = self._fetch_social_hype()

        coin_set = {c.upper() for c in coins}
        result: dict[str, dict] = {}

        for coin in coins:
            cu = coin.upper()
            sm = sm_data.get(cu, {})
            soc = social_data.get(cu, {})

            sm_rank = sm.get("rank")
            sm_inflow = sm_rank is not None and sm_rank <= 20

            soc_rank = soc.get("rank")
            soc_sentiment = soc.get("sentiment", "Neutral")
            soc_extreme = soc_rank is not None and soc_rank <= 5

            # Classify crowd signal
            crowd = "neutral"
            if soc_extreme:
                if soc_sentiment == "Positive":
                    crowd = "fomo"  # retail FOMO = contrarian bearish
                elif soc_sentiment == "Negative":
                    crowd = "panic"  # retail panic = contrarian bullish
            elif soc_rank is not None and soc_rank <= 15:
                if soc_sentiment == "Positive":
                    crowd = "mild_bullish"
                elif soc_sentiment == "Negative":
                    crowd = "mild_bearish"

            result[coin] = {
                "smart_money_rank": sm_rank,
                "smart_money_inflow": sm_inflow,
                "social_hype_rank": soc_rank,
                "social_sentiment": soc_sentiment,
                "social_hype_extreme": soc_extreme,
                "crowd_signal": crowd,
                "stale": False,
            }

        self._cache = result
        self._cache_time = now
        n_sm = sum(1 for v in result.values() if v["smart_money_inflow"])
        logger.debug(
            f"[SmartMoneyFeed] {len(result)} coins, "
            f"{n_sm} with smart money inflow")
        return result

    def _fetch_smart_money(self) -> dict[str, dict]:
        """Fetch smart money inflow rankings from Binance Web3."""
        out: dict[str, dict] = {}
        try:
            payload = json.dumps({
                "chainId": "56",   # BSC
                "period": "24h",
                "tagType": 2,      # smart money tag
            }).encode()
            req = urllib.request.Request(
                _SMART_MONEY_URL,
                data=payload,
                headers={
                    "User-Agent": "TradingBot/2.0",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            items = data.get("data", [])
            if not isinstance(items, list):
                items = data.get("data", {}).get("list", [])
                if not isinstance(items, list):
                    return out

            for rank, item in enumerate(items[:30], 1):
                symbol = (item.get("symbol", "") or
                          item.get("tokenSymbol", "") or "").upper()
                symbol = _TOKEN_ALIAS.get(symbol, symbol)
                if symbol:
                    out[symbol] = {"rank": rank}
        except Exception as e:
            logger.debug(f"[SmartMoneyFeed] inflow fetch failed: {e}")
        return out

    def _fetch_social_hype(self) -> dict[str, dict]:
        """Fetch social hype rankings from Binance Web3."""
        out: dict[str, dict] = {}
        try:
            # The social hype endpoint may be GET or POST depending on version
            req = urllib.request.Request(
                _SOCIAL_HYPE_URL,
                headers={
                    "User-Agent": "TradingBot/2.0",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            items = data.get("data", [])
            if not isinstance(items, list):
                items = data.get("data", {}).get("list", [])
                if not isinstance(items, list):
                    return out

            for rank, item in enumerate(items[:30], 1):
                symbol = (item.get("symbol", "") or
                          item.get("tokenSymbol", "") or "").upper()
                symbol = _TOKEN_ALIAS.get(symbol, symbol)
                sentiment = item.get("sentiment", "Neutral")
                if isinstance(sentiment, str):
                    sentiment = sentiment.capitalize()
                if symbol:
                    out[symbol] = {
                        "rank": rank,
                        "sentiment": sentiment,
                    }
        except Exception as e:
            logger.debug(f"[SmartMoneyFeed] social hype fetch failed: {e}")
        return out

    @staticmethod
    def _neutral(*, stale: bool = False) -> dict:
        return {
            "smart_money_rank": None,
            "smart_money_inflow": False,
            "social_hype_rank": None,
            "social_sentiment": "Neutral",
            "social_hype_extreme": False,
            "crowd_signal": "neutral",
            "stale": stale,
        }
