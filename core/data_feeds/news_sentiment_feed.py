"""
core/data_feeds/news_sentiment_feed.py — CoinDesk News Sentiment Feed

Fetches news from CoinDesk Data API with built-in sentiment classification.
Provides per-coin news sentiment scores and a news-veto gate.

Signal thesis:
  - Breaking negative news (hack, exploit, delist, SEC action) on a specific
    coin within the last 2h = VETO entry on that coin
  - Strong positive news cluster = mild confirmation (not entry trigger)
  - Absence of news = neutral (not bearish)

CoinDesk returns pre-classified SENTIMENT field ("POSITIVE"/"NEGATIVE"/"NEUTRAL")
which we use directly, plus our own keyword scan for crypto-specific events.

Cache TTL: 600s (10 min). News doesn't change faster than this for scoring.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

# High-impact negative keywords (veto-worthy)
_VETO_KEYWORDS = frozenset({
    "hack", "hacked", "exploit", "exploited", "vulnerability",
    "delist", "delisted", "delisting",
    "sec charges", "sec sues", "lawsuit",
    "rug pull", "rugpull", "scam",
    "bankrupt", "bankruptcy", "insolvent",
    "freeze", "frozen", "suspend", "suspended",
    "attack", "compromised", "breach",
})

# Positive keywords (confirmation bonus)
_POSITIVE_KEYWORDS = frozenset({
    "partnership", "launch", "upgrade", "adoption",
    "etf approved", "etf approval", "institutional",
    "record high", "all-time high", "ath",
    "integration", "expansion", "listing",
    "mainnet", "upgrade", "v2", "v3",
})


class NewsSentimentFeed:
    """Fetches and scores news sentiment per coin."""

    def __init__(self, *, cache_ttl: int = 600, veto_window_sec: int = 7200):
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._cache_ttl = cache_ttl
        self._veto_window = veto_window_sec  # 2 hours
        self._raw_articles: list[dict] = []
        self._raw_time: float = 0.0

    def fetch(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch news sentiment for a list of base coins.

        Returns:
            {coin: {
                "sentiment_score": float,    # [-1.0, 1.0]
                "article_count_2h": int,     # articles mentioning coin in 2h
                "has_veto_news": bool,        # True if negative-impact news
                "veto_reason": str|None,      # headline that triggered veto
                "positive_count": int,
                "negative_count": int,
                "stale": bool,
            }}
            Plus a special "__market__" key with overall market sentiment.
        """
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache

        # Fetch raw articles (only if stale)
        if now - self._raw_time > self._cache_ttl:
            self._raw_articles = self._fetch_articles()
            self._raw_time = now

        result: dict[str, dict] = {}

        # Coin-symbol aliases for matching
        coin_aliases = {}
        for coin in coins:
            aliases = {coin.upper(), coin.lower()}
            # Common full names
            _NAME_MAP = {
                "BTC": {"bitcoin"}, "ETH": {"ethereum", "ether"},
                "BNB": {"binance coin", "bnb"}, "SOL": {"solana"},
                "XRP": {"ripple", "xrp"}, "DOGE": {"dogecoin", "doge"},
                "ADA": {"cardano"}, "AVAX": {"avalanche"},
                "DOT": {"polkadot"}, "LINK": {"chainlink"},
                "ATOM": {"cosmos"}, "ARB": {"arbitrum"},
                "OP": {"optimism"}, "NEAR": {"near protocol"},
                "APT": {"aptos"}, "SUI": {"sui"},
                "FET": {"fetch.ai", "artificial superintelligence"},
                "INJ": {"injective"}, "SEI": {"sei"},
                "PEPE": {"pepe"}, "WIF": {"dogwifhat"},
            }
            aliases |= _NAME_MAP.get(coin.upper(), set())
            coin_aliases[coin] = aliases

        cutoff_ts = now - self._veto_window

        # Score each coin
        for coin in coins:
            aliases = coin_aliases.get(coin, {coin.upper()})
            relevant = []

            for article in self._raw_articles:
                # Check if article mentions this coin
                title = (article.get("title", "") or "").lower()
                body = (article.get("body", "") or "").lower()[:500]
                categories = {
                    c.get("NAME", "").upper()
                    for c in article.get("categories", [])
                }

                # Match by category tag (most reliable)
                cat_match = coin.upper() in categories
                # Match by text content
                text_match = any(
                    alias.lower() in title or alias.lower() in body
                    for alias in aliases
                )

                if cat_match or text_match:
                    ts = article.get("published_on", 0)
                    relevant.append({
                        "title": article.get("title", ""),
                        "sentiment": article.get("sentiment", "NEUTRAL"),
                        "published_on": ts,
                        "is_recent": ts > cutoff_ts,
                    })

            # Compute sentiment score
            pos = sum(1 for a in relevant if a["sentiment"] == "POSITIVE")
            neg = sum(1 for a in relevant if a["sentiment"] == "NEGATIVE")
            total = len(relevant)
            if total > 0:
                sentiment_score = (pos - neg) / total
            else:
                sentiment_score = 0.0

            # Check for veto-worthy news in recent window
            has_veto = False
            veto_reason = None
            recent = [a for a in relevant if a["is_recent"]]
            for a in recent:
                if a["sentiment"] == "NEGATIVE":
                    title_lower = a["title"].lower()
                    if any(kw in title_lower for kw in _VETO_KEYWORDS):
                        has_veto = True
                        veto_reason = a["title"][:100]
                        break

            result[coin] = {
                "sentiment_score": round(sentiment_score, 3),
                "article_count_2h": len(recent),
                "has_veto_news": has_veto,
                "veto_reason": veto_reason,
                "positive_count": pos,
                "negative_count": neg,
                "stale": False,
            }

        # Market-wide sentiment
        all_sentiments = [
            a.get("sentiment", "NEUTRAL") for a in self._raw_articles
        ]
        mkt_pos = sum(1 for s in all_sentiments if s == "POSITIVE")
        mkt_neg = sum(1 for s in all_sentiments if s == "NEGATIVE")
        mkt_total = len(all_sentiments) or 1
        result["__market__"] = {
            "sentiment_score": round((mkt_pos - mkt_neg) / mkt_total, 3),
            "article_count_2h": len(self._raw_articles),
            "has_veto_news": False,
            "veto_reason": None,
            "positive_count": mkt_pos,
            "negative_count": mkt_neg,
            "stale": False,
        }

        self._cache = result
        self._cache_time = now
        logger.debug(
            f"[NewsFeed] {len(self._raw_articles)} articles, "
            f"{len(result)-1} coins scored, "
            f"market_sentiment={result['__market__']['sentiment_score']:+.3f}")
        return result

    def _fetch_articles(self) -> list[dict]:
        """Fetch raw articles from CoinDesk."""
        articles = []

        # Primary: CoinDesk via MCP tool
        try:
            from core.data_feeds._coindesk_caller import call_coindesk_news
            raw = call_coindesk_news(limit=50)
            if raw and "Data" in raw:
                for item in raw["Data"]:
                    categories = [
                        c for c in item.get("CATEGORY_DATA", [])
                    ]
                    articles.append({
                        "title": item.get("TITLE", ""),
                        "body": item.get("BODY", ""),
                        "sentiment": item.get("SENTIMENT", "NEUTRAL").upper(),
                        "published_on": item.get("PUBLISHED_ON", 0),
                        "source": item.get("SOURCE_DATA", {}).get("NAME", ""),
                        "categories": categories,
                    })
        except Exception as e:
            logger.debug(f"[NewsFeed] CoinDesk fetch failed: {e}")

        # Fallback: CryptoCompare (already used by mcp_brain.fetch_news)
        if not articles:
            try:
                import os
                import urllib.request
                cc_key = os.getenv("CRYPTOCOMPARE_API_KEY", "").strip()
                url = ("https://min-api.cryptocompare.com/data/v2/news/"
                       "?lang=EN&sortOrder=latest")
                headers = {"User-Agent": "TradingBot/2.0"}
                if cc_key:
                    headers["authorization"] = f"Apikey {cc_key}"
                req = urllib.request.Request(url, headers=headers)
                import json
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                for item in self._cryptocompare_items(data):
                    title = item.get("title", "")
                    sentiment = self._classify_sentiment(title)
                    articles.append({
                        "title": title,
                        "body": item.get("body", "")[:500],
                        "sentiment": sentiment,
                        "published_on": item.get("published_on", 0),
                        "source": item.get("source_info", {}).get("name", ""),
                        "categories": [],
                    })
            except Exception as e:
                logger.debug(f"[NewsFeed] CryptoCompare fallback failed: {e}")

        return articles

    @staticmethod
    def _cryptocompare_items(data: dict) -> list[dict]:
        """Normalize CryptoCompare's occasionally wrapped news payload."""
        raw_items = data.get("Data", []) if isinstance(data, dict) else []
        if isinstance(raw_items, dict):
            raw_items = (
                raw_items.get("Data")
                or raw_items.get("items")
                or raw_items.get("news")
                or raw_items.get("results")
                or []
            )
        if not isinstance(raw_items, list):
            return []
        return [item for item in raw_items[:30] if isinstance(item, dict)]

    @staticmethod
    def _classify_sentiment(title: str) -> str:
        """Simple keyword-based sentiment classifier (fallback)."""
        tl = title.lower()
        pos_words = ("bull", "surge", "rally", "soar", "jump", "gain",
                     "high", "break", "pump", "ath", "record",
                     "buy", "strong", "boost", "grow", "adopt")
        neg_words = ("crash", "bear", "dump", "plunge", "fall", "drop",
                     "low", "fear", "hack", "exploit", "ban", "fraud",
                     "liquidat", "bankrupt", "sec ", "lawsuit", "sell",
                     "weak", "slump", "tank", "warn", "delist")
        pos = sum(1 for w in pos_words if w in tl)
        neg = sum(1 for w in neg_words if w in tl)
        if pos > neg:
            return "POSITIVE"
        elif neg > pos:
            return "NEGATIVE"
        return "NEUTRAL"

    @staticmethod
    def _neutral(*, stale: bool = False) -> dict:
        return {
            "sentiment_score": 0.0,
            "article_count_2h": 0,
            "has_veto_news": False,
            "veto_reason": None,
            "positive_count": 0,
            "negative_count": 0,
            "stale": stale,
        }
