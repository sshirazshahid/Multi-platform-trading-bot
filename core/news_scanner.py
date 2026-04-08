"""
core/news_scanner.py — Market News + Sentiment + Trending Coins

Fetches real-time crypto market data from free public APIs (no API keys needed):

Sources:
  CoinGecko  — trending coins, global market stats, fear & greed index
  CryptoCompare — latest crypto news headlines (free, no key)
  Alternative.me — Fear & Greed Index

Output written to:
  data/news_cache.json   — full structured data
  data/news_latest.txt   — plain text for dashboard display

Sentiment scoring:
  - Each news headline is scored for positive / negative keywords
  - Per-symbol sentiment feeds into the strategy selector as a signal boost/cut
  - Fear & Greed index adjusts overall risk appetite

Runs on a schedule (every 30 min by default) to avoid rate limits.
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib  import Path
from loguru   import logger


NEWS_CACHE    = Path("data/news_cache.json")
NEWS_TEXT     = Path("data/news_latest.txt")

# How long cached news stays fresh (seconds)
CACHE_TTL     = 1800    # 30 minutes

# CryptoCompare free news endpoint (no API key required)
NEWS_URL      = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&limit=20"

# CoinGecko trending coins (free, no key)
TRENDING_URL  = "https://api.coingecko.com/api/v3/search/trending"

# CoinGecko global market stats
GLOBAL_URL    = "https://api.coingecko.com/api/v3/global"

# Alternative.me Fear & Greed Index
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

# Positive / Negative keyword lists for headline sentiment scoring
POSITIVE_WORDS = {
    "surge", "rally", "breakout", "bullish", "all-time high", "ath",
    "adoption", "partnership", "launch", "upgrade", "gain", "rise",
    "record", "growth", "institutional", "etf", "approve", "approval",
    "positive", "strong", "buy", "accumulate", "soar", "moon",
}
NEGATIVE_WORDS = {
    "crash", "drop", "plunge", "bearish", "hack", "exploit", "ban",
    "regulation", "lawsuit", "sec", "sell-off", "fear", "liquidation",
    "collapse", "decline", "lose", "loss", "warning", "risk", "fraud",
    "scam", "dump", "rug", "panic", "correction", "tumble", "slide",
}


def _fetch(url: str, timeout: int = 8) -> dict | None:
    """Fetch JSON from a URL. Returns None on any error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TradingBot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"[News] Fetch error ({url[:50]}...): {e}")
        return None


def _score_headline(title: str) -> int:
    """Score a headline: +1 per positive word, -1 per negative. Returns -5..+5."""
    title_lower = title.lower()
    score = sum(1 for w in POSITIVE_WORDS if w in title_lower)
    score -= sum(1 for w in NEGATIVE_WORDS if w in title_lower)
    return max(-5, min(5, score))


class NewsScanner:

    def __init__(self):
        self._cache:    dict  = {}
        self._last_run: float = 0.0

    # ── Public API ────────────────────────────────────────────────────

    def scan(self, force: bool = False) -> dict:
        """
        Fetch all news + market data. Uses cache if fresh.
        Returns structured data dict.
        """
        if not force and (time.time() - self._last_run) < CACHE_TTL:
            return self._cache

        logger.info("[News] Fetching market news and trends...")

        result = {
            "fetched_at":   datetime.now().isoformat(),
            "fear_greed":   self._fetch_fear_greed(),
            "trending":     self._fetch_trending(),
            "global":       self._fetch_global(),
            "news":         self._fetch_news(),
            "sentiment":    {},
        }

        # Build per-symbol sentiment from news
        result["sentiment"] = self._build_sentiment(result["news"])

        self._cache    = result
        self._last_run = time.time()
        self._save(result)
        self._write_text(result)
        self._log_summary(result)
        return result

    def symbol_sentiment(self, symbol: str) -> int:
        """
        Return net sentiment score for a symbol from recent news.
        Positive = bullish, Negative = bearish, 0 = neutral.
        Base symbol extracted: BTC/USDT → BTC
        """
        base = symbol.split("/")[0].split(":")[0].upper()
        return self._cache.get("sentiment", {}).get(base, 0)

    def fear_greed_value(self) -> int:
        """Return current Fear & Greed Index value (0=Extreme Fear, 100=Extreme Greed)."""
        return self._cache.get("fear_greed", {}).get("value", 50)

    def fear_greed_label(self) -> str:
        return self._cache.get("fear_greed", {}).get("label", "Neutral")

    def trending_coins(self) -> list[str]:
        """Return list of trending coin symbols (e.g. ['BTC', 'ETH', 'SOL'])."""
        return [c.get("symbol", "").upper()
                for c in self._cache.get("trending", [])]

    def is_bearish_environment(self) -> bool:
        """Return True if overall sentiment suggests caution."""
        fg = self.fear_greed_value()
        return fg < 25   # Extreme Fear

    def is_bullish_environment(self) -> bool:
        """Return True if sentiment supports aggressive entries."""
        fg = self.fear_greed_value()
        return fg > 70   # Greed / Extreme Greed

    def latest_headlines(self, limit: int = 5) -> list[str]:
        """Return the N most recent news headlines."""
        return [
            n.get("title", "")
            for n in self._cache.get("news", [])[:limit]
        ]

    # ── Fetchers ──────────────────────────────────────────────────────

    def _fetch_fear_greed(self) -> dict:
        data = _fetch(FEAR_GREED_URL)
        if not data:
            return {"value": 50, "label": "Neutral"}
        try:
            entry = data["data"][0]
            return {
                "value":     int(entry.get("value", 50)),
                "label":     entry.get("value_classification", "Neutral"),
                "timestamp": entry.get("timestamp", ""),
            }
        except Exception:
            return {"value": 50, "label": "Neutral"}

    def _fetch_trending(self) -> list:
        data = _fetch(TRENDING_URL)
        if not data:
            return []
        try:
            coins = data.get("coins", [])
            return [
                {
                    "name":        c["item"].get("name", ""),
                    "symbol":      c["item"].get("symbol", "").upper(),
                    "rank":        c["item"].get("market_cap_rank", 999),
                    "price_btc":   c["item"].get("price_btc", 0),
                }
                for c in coins[:10]
            ]
        except Exception:
            return []

    def _fetch_global(self) -> dict:
        data = _fetch(GLOBAL_URL)
        if not data:
            return {}
        try:
            d = data.get("data", {})
            mktcap_pct = d.get("market_cap_percentage", {})
            return {
                "total_market_cap_usd":   d.get("total_market_cap", {}).get("usd", 0),
                "total_volume_24h_usd":   d.get("total_volume", {}).get("usd", 0),
                "btc_dominance":          round(mktcap_pct.get("btc", 0), 1),
                "eth_dominance":          round(mktcap_pct.get("eth", 0), 1),
                "market_cap_change_24h":  round(
                    d.get("market_cap_change_percentage_24h_usd", 0), 2
                ),
                "active_cryptocurrencies": d.get("active_cryptocurrencies", 0),
            }
        except Exception:
            return {}

    def _fetch_news(self) -> list:
        data = _fetch(NEWS_URL)
        if not data:
            return []
        try:
            articles = data.get("Data", [])
            result   = []
            for a in articles[:20]:
                title = a.get("title", "")
                result.append({
                    "title":      title,
                    "source":     a.get("source_info", {}).get("name", "Unknown"),
                    "url":        a.get("url", ""),
                    "published":  datetime.fromtimestamp(
                        a.get("published_on", 0)
                    ).strftime("%Y-%m-%d %H:%M") if a.get("published_on") else "",
                    "categories": a.get("categories", ""),
                    "sentiment":  _score_headline(title),
                    "tags":       [t.strip() for t in a.get("tags", "").split("|")
                                   if t.strip()],
                })
            return result
        except Exception:
            return []

    def _build_sentiment(self, news: list) -> dict:
        """Aggregate sentiment score per coin from news tags and titles."""
        scores: dict[str, list[int]] = {}
        for article in news:
            score = article.get("sentiment", 0)
            if score == 0:
                continue
            # Extract coins from categories and tags
            cats = article.get("categories", "").upper()
            tags = [t.upper() for t in article.get("tags", [])]
            candidates = set(cats.replace(",", " ").split() + tags)
            for coin in candidates:
                coin = coin.strip()
                if len(coin) >= 2 and coin.isalpha():
                    scores.setdefault(coin, []).append(score)

        return {coin: round(sum(v) / len(v), 2)
                for coin, v in scores.items()}

    # ── Persistence + display ─────────────────────────────────────────

    def _save(self, data: dict):
        try:
            NEWS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            NEWS_CACHE.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[News] Save error: {e}")

    def _write_text(self, data: dict):
        """Write a plain-text summary for the dashboard."""
        try:
            fg    = data.get("fear_greed", {})
            glb   = data.get("global", {})
            trend = data.get("trending", [])
            news  = data.get("news", [])

            lines = [
                "=" * 60,
                f"  MARKET SNAPSHOT  —  {data.get('fetched_at','')[:16]}",
                "=" * 60,
                "",
                f"  Fear & Greed Index  :  {fg.get('value', '?')} / 100  —  {fg.get('label', '?')}",
                f"  Market Cap 24h      :  {glb.get('market_cap_change_24h', 0):+.2f}%",
                f"  BTC Dominance       :  {glb.get('btc_dominance', 0):.1f}%",
                f"  ETH Dominance       :  {glb.get('eth_dominance', 0):.1f}%",
                "",
                "  TRENDING COINS:",
            ]
            for i, c in enumerate(trend[:5], 1):
                lines.append(
                    f"    {i}. {c.get('symbol','?'):<8}  {c.get('name','?')}"
                    f"  (Rank #{c.get('rank','?')})"
                )

            lines += ["", "  LATEST NEWS:"]
            for a in news[:8]:
                sentiment = a.get("sentiment", 0)
                icon = "▲" if sentiment > 0 else ("▼" if sentiment < 0 else "●")
                title = a.get("title", "")[:68]
                src   = a.get("source", "")
                ts    = a.get("published", "")[:10]
                lines.append(f"    {icon} [{ts}] {title}")
                lines.append(f"       {src}")
                lines.append("")

            NEWS_TEXT.parent.mkdir(parents=True, exist_ok=True)
            NEWS_TEXT.write_text(
                "\n".join(lines), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[News] Text write error: {e}")

    def _log_summary(self, data: dict):
        fg  = data.get("fear_greed", {})
        glb = data.get("global", {})
        top = [c.get("symbol", "") for c in data.get("trending", [])[:3]]
        logger.info(
            f"[News] Fear&Greed={fg.get('value','?')} ({fg.get('label','?')}) | "
            f"Mkt cap chg={glb.get('market_cap_change_24h',0):+.2f}% | "
            f"BTC dom={glb.get('btc_dominance',0):.1f}% | "
            f"Trending: {', '.join(top)} | "
            f"{len(data.get('news',[]))} headlines fetched"
        )
