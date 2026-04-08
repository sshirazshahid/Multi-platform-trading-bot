"""
core/sentiment_agents.py — Parallel Sentiment Research Agents

Agent 1: Social Sentiment (CoinGecko trending, Fear & Greed)
Agent 2: News Sentiment (RSS feeds from crypto news sites)

Both agents run and combine scores to produce a unified sentiment
signal per symbol. Compares narrative against market odds.

NOTE: Twitter/Reddit scraping requires API keys. This module uses
free, no-auth sources. Upgrade to Twitter API v2 / Reddit PRAW
when API keys are available.
"""

import json
import time
import hashlib
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger

CACHE_PATH = Path("data/sentiment_cache.json")
CACHE_TTL  = 1800  # 30 minutes


class SentimentAgent:
    """Base class for sentiment agents."""

    def __init__(self, name: str):
        self.name = name
        self._scores: dict[str, float] = {}  # symbol → score (-1 to +1)

    def score(self, symbol: str) -> float:
        """Return sentiment score for symbol. -1=bearish, 0=neutral, +1=bullish."""
        base = symbol.split("/")[0].upper()
        return self._scores.get(base, 0.0)


class SocialSentimentAgent(SentimentAgent):
    """Agent 1: Social signals from CoinGecko trending + Fear & Greed."""

    def __init__(self):
        super().__init__("SocialSentiment")

    def analyze(self, news_scanner) -> dict[str, float]:
        """Pull social signals from existing news scanner."""
        self._scores = {}

        try:
            # Fear & Greed impacts all symbols
            fg = news_scanner.fear_greed_value()
            global_bias = 0.0
            if fg <= 20:
                global_bias = -0.3   # Extreme fear = bearish sentiment
            elif fg <= 35:
                global_bias = -0.15
            elif fg >= 75:
                global_bias = 0.3    # Extreme greed = bullish sentiment
            elif fg >= 55:
                global_bias = 0.15

            # Trending coins get a boost
            trending = news_scanner.trending_coins()
            for coin_data in trending:
                sym = coin_data if isinstance(coin_data, str) else coin_data.get("symbol", "")
                sym = sym.upper()
                if sym:
                    self._scores[sym] = global_bias + 0.2  # Trending = positive attention

            # Apply global bias to major coins
            for major in ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX"]:
                if major not in self._scores:
                    self._scores[major] = global_bias

            # Per-symbol sentiment from news scanner
            for sym in list(self._scores.keys()):
                sym_sent = news_scanner.symbol_sentiment(f"{sym}/USDT")
                if sym_sent != 0:
                    self._scores[sym] = round(
                        self._scores.get(sym, 0) * 0.6 + sym_sent * 0.4, 3)

        except Exception as e:
            logger.debug(f"[SocialSentiment] Error: {e}")

        return self._scores


class NewsSentimentAgent(SentimentAgent):
    """Agent 2: News sentiment from cached headlines."""

    # Bearish keywords
    _BEAR_WORDS = {
        "crash", "plunge", "dump", "sell-off", "selloff", "bear", "bearish",
        "decline", "drop", "fall", "hack", "exploit", "rug", "scam",
        "regulation", "ban", "fine", "lawsuit", "sec", "investigation",
        "liquidation", "bankruptcy", "collapse", "panic", "fear",
    }

    # Bullish keywords
    _BULL_WORDS = {
        "rally", "surge", "pump", "bull", "bullish", "breakout", "moon",
        "ath", "all-time high", "adoption", "partnership", "etf", "approval",
        "institutional", "whale", "accumulate", "upgrade", "launch",
        "mainnet", "staking", "airdrop", "record", "milestone",
    }

    def __init__(self):
        super().__init__("NewsSentiment")

    def analyze(self, news_cache: dict) -> dict[str, float]:
        """Analyze cached news headlines for sentiment."""
        self._scores = {}

        try:
            headlines = news_cache.get("news", [])
            if not headlines:
                return self._scores

            # Score each headline
            for article in headlines:
                title = (article.get("title", "") or "").lower()
                body  = (article.get("description", "") or "").lower()
                text  = title + " " + body

                # Count bull/bear words
                bull_count = sum(1 for w in self._BULL_WORDS if w in text)
                bear_count = sum(1 for w in self._BEAR_WORDS if w in text)

                if bull_count == 0 and bear_count == 0:
                    continue

                score = (bull_count - bear_count) / max(bull_count + bear_count, 1)

                # Identify which coins are mentioned
                for sym in ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA",
                           "DOGE", "AVAX", "XAU", "GOLD", "SILVER", "OIL"]:
                    sym_lower = sym.lower()
                    full_names = {
                        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                        "XRP": "ripple", "BNB": "binance", "ADA": "cardano",
                        "DOGE": "dogecoin", "AVAX": "avalanche",
                        "XAU": "gold", "GOLD": "gold",
                        "SILVER": "silver", "OIL": "oil",
                    }
                    name = full_names.get(sym, sym_lower)
                    if sym_lower in text or name in text:
                        key = "XAU" if sym in ("GOLD",) else sym
                        current = self._scores.get(key, 0)
                        self._scores[key] = round(
                            current * 0.7 + score * 0.3, 3)

        except Exception as e:
            logger.debug(f"[NewsSentiment] Error: {e}")

        return self._scores


class SentimentOrchestrator:
    """Runs both agents in parallel and combines scores."""

    def __init__(self):
        self.social = SocialSentimentAgent()
        self.news   = NewsSentimentAgent()
        self._combined: dict[str, dict] = {}
        self._last_run = 0.0

    def run(self, news_scanner, news_cache: dict = None) -> dict:
        """Run both agents and combine results."""
        if time.time() - self._last_run < 300:  # 5min cooldown
            return self._combined

        # Run agents in parallel
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_social = pool.submit(self.social.analyze, news_scanner)
            f_news   = pool.submit(
                self.news.analyze, news_cache or self._load_news_cache())

            social_scores = f_social.result()
            news_scores   = f_news.result()

        # Combine scores
        all_symbols = set(list(social_scores.keys()) + list(news_scores.keys()))
        self._combined = {}

        for sym in all_symbols:
            s1 = social_scores.get(sym, 0)
            s2 = news_scores.get(sym, 0)

            # Weighted average: social 40%, news 60%
            combined = round(s1 * 0.4 + s2 * 0.6, 3)

            # Agreement bonus: if both agents agree, boost signal
            if s1 > 0 and s2 > 0:
                combined = min(1.0, combined * 1.2)
            elif s1 < 0 and s2 < 0:
                combined = max(-1.0, combined * 1.2)

            self._combined[sym] = {
                "score": combined,
                "social": s1,
                "news": s2,
                "signal": "bullish" if combined > 0.15 else (
                    "bearish" if combined < -0.15 else "neutral"),
            }

        self._last_run = time.time()
        self._save()

        # Log
        signals = {s: d["signal"] for s, d in self._combined.items() if d["signal"] != "neutral"}
        if signals:
            logger.info(f"[Sentiment] Signals: {signals}")

        return self._combined

    def get_score(self, symbol: str) -> float:
        """Get combined sentiment score for a symbol."""
        base = symbol.split("/")[0].upper()
        entry = self._combined.get(base, {})
        return entry.get("score", 0.0)

    def get_signal(self, symbol: str) -> str:
        """Get signal string: bullish, bearish, neutral."""
        base = symbol.split("/")[0].upper()
        entry = self._combined.get(base, {})
        return entry.get("signal", "neutral")

    def confidence_adjustment(self, symbol: str, direction: str) -> float:
        """Return confidence multiplier based on sentiment alignment.
        >1.0 if sentiment aligns with trade direction.
        <1.0 if sentiment contradicts trade direction."""
        score = self.get_score(symbol)

        if direction == "buy" and score > 0.15:
            return 1.0 + min(0.15, score * 0.3)   # Up to +15% boost
        elif direction == "sell" and score < -0.15:
            return 1.0 + min(0.15, abs(score) * 0.3)
        elif direction == "buy" and score < -0.2:
            return max(0.75, 1.0 + score * 0.5)    # Up to -25% penalty
        elif direction == "sell" and score > 0.2:
            return max(0.75, 1.0 - score * 0.5)
        return 1.0

    def get_summary(self) -> dict:
        """Return summary for dashboard/email."""
        if not self._combined:
            return {"total": 0, "bullish": 0, "bearish": 0, "neutral": 0}

        signals = [d["signal"] for d in self._combined.values()]
        return {
            "total": len(signals),
            "bullish": signals.count("bullish"),
            "bearish": signals.count("bearish"),
            "neutral": signals.count("neutral"),
            "top_bullish": sorted(
                [(s, d["score"]) for s, d in self._combined.items() if d["score"] > 0],
                key=lambda x: x[1], reverse=True)[:3],
            "top_bearish": sorted(
                [(s, d["score"]) for s, d in self._combined.items() if d["score"] < 0],
                key=lambda x: x[1])[:3],
        }

    def _load_news_cache(self) -> dict:
        try:
            p = Path("data/news_cache.json")
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps({
                "combined": self._combined,
                "timestamp": datetime.now().isoformat(),
            }, indent=2), encoding="utf-8")
        except Exception:
            pass
