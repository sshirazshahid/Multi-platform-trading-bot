"""
core/news_scanner.py — Market News + Sentiment + Trending Coins (24/7 Enhanced)

Fetches real-time crypto market data from free public APIs (no API keys needed):

Sources (2026-07-10: CryptoCompare + CryptoPanic replaced — both dead keyless):
  CoinGecko          — trending coins, global market stats
  Alternative.me     — Fear & Greed Index
  CoinDesk RSS       — major crypto news via RSS feed
  Cointelegraph RSS  — crypto news via RSS feed
  Decrypt RSS        — crypto news via RSS feed
  X/Twitter          — curated trader/investor accounts (API bearer or RSSHub)

Output written to:
  data/news_cache.json         — full structured data
  data/news_latest.txt         — plain text for dashboard display
  data/sentiment_history.json  — per-coin sentiment over time

Sentiment scoring:
  - Each news headline is scored for positive / negative keywords
  - Per-symbol sentiment feeds into the strategy selector as a signal boost/cut
  - Fear & Greed index adjusts overall risk appetite

Impact classification:
  - HIGH: hack, exploit, ban, etf approve, sec lawsuit, halving, fed rate, crash, all-time high
  - MEDIUM: partnership, upgrade, listing, regulation
  - LOW: everything else

Breaking news detection:
  - HIGH impact items not seen in previous scan flagged as breaking
  - Logged at WARNING level for immediate attention

Adaptive scanning:
  - Default 30 min interval
  - Fast 10 min interval when Fear & Greed < 20 or > 80 (high volatility)

Runs on a schedule to avoid rate limits.
"""

import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

try:
    from config import NEWS as NEWS_CFG
except ImportError:
    NEWS_CFG = {}


NEWS_CACHE           = Path("data/news_cache.json")
NEWS_TEXT             = Path("data/news_latest.txt")
SENTIMENT_HISTORY    = Path("data/sentiment_history.json")

# Intervals (overridable from config)
CACHE_TTL            = NEWS_CFG.get("scan_interval_min", 30) * 60
FAST_CACHE_TTL       = NEWS_CFG.get("fast_scan_interval_min", 10) * 60
MAX_HEADLINES        = NEWS_CFG.get("max_headlines", 50)
SENTIMENT_HIST_DAYS  = NEWS_CFG.get("sentiment_history_days", 7)
BREAKING_NEWS_ALERT  = NEWS_CFG.get("breaking_news_alert", True)

# ── API Endpoints ──────────────────────────────────────────────────────

# Keyless RSS news sources (2026-07-10 repair):
#   - CryptoCompare min-api news now requires a key AND returns 0 items even
#     with one (product discontinued) — removed.
#   - CryptoPanic /api/free/v1 was removed upstream (404; v1 is 403) — removed.
#   - Replaced with Cointelegraph + Decrypt RSS alongside the existing
#     CoinDesk RSS (all probed working keyless 2026-07-10).
RSS_SOURCES = [
    ("coindesk",      "CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt",       "Decrypt",       "https://decrypt.co/feed"),
]

# CoinGecko trending coins (free, no key)
TRENDING_URL   = "https://api.coingecko.com/api/v3/search/trending"

# CoinGecko global market stats
GLOBAL_URL     = "https://api.coingecko.com/api/v3/global"

# Alternative.me Fear & Greed Index
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

# ── Sentiment keywords ────────────────────────────────────────────────

POSITIVE_WORDS = {
    "surge", "rally", "breakout", "bullish", "all-time high", "ath",
    "adoption", "partnership", "launch", "upgrade", "gain", "rise",
    "record", "growth", "institutional", "etf", "approve", "approval",
    "positive", "strong", "buy", "accumulate", "soar", "moon",
    "inflow", "inflows", "greenlight", "cleared", "wins", "won",
    "recovery", "rebounds", "rebound", "outperform", "milestone",
}
NEGATIVE_WORDS = {
    "crash", "drop", "plunge", "bearish", "hack", "exploit", "ban",
    "regulation", "lawsuit", "sec", "sell-off", "fear", "liquidation",
    "collapse", "decline", "lose", "loss", "warning", "risk", "fraud",
    "scam", "dump", "rug", "panic", "correction", "tumble", "slide",
    "outflow", "outflows", "probe", "charges", "indictment", "seize",
    "seizure", "delist", "delisting", "insolvent", "default", "halt",
}

# ── Impact classification keyword patterns ─────────────────────────────

HIGH_IMPACT_KEYWORDS = [
    "hack", "exploit", "ban", "etf approve", "etf approval",
    "sec lawsuit", "halving", "fed rate", "crash", "all-time high",
    "ath", "black swan", "de-peg", "depeg", "insolvency", "bankrupt",
    "emergency", "flash crash", "sec charges", "doj charges",
    "exchange halt", "withdrawal halt", "forced liquidation",
]
MEDIUM_IMPACT_KEYWORDS = [
    "partnership", "upgrade", "listing", "regulation", "fork",
    "airdrop", "mainnet", "testnet", "acquisition", "integration",
    "treasury", "spot etf", "options etf", "unlock", "token unlock",
]

# ── Rate limiting ──────────────────────────────────────────────────────
_RATE_LIMIT = {
    "coindesk":      {"last": 0.0, "min_gap": 120},
    "cointelegraph": {"last": 0.0, "min_gap": 120},
    "decrypt":       {"last": 0.0, "min_gap": 120},
    "twitter":       {"last": 0.0, "min_gap": 180},
    "coingecko":     {"last": 0.0, "min_gap": 60},
    "feargreed":     {"last": 0.0, "min_gap": 60},
}

# ── Per-source health (source -> ok/dead attempt counts, process lifetime) ──
_SOURCE_HEALTH: dict[str, dict[str, int]] = {}


def _health_mark(source: str, ok: bool):
    """Record the outcome of an actual fetch attempt (rate-limit skips don't count)."""
    h = _SOURCE_HEALTH.setdefault(source, {"ok": 0, "dead": 0})
    h["ok" if ok else "dead"] += 1


def _health_summary() -> str:
    """One-line source health for the heartbeat log, e.g. 'coindesk:5ok/0dead'."""
    return ", ".join(
        f"{s}:{h['ok']}ok/{h['dead']}dead"
        for s, h in sorted(_SOURCE_HEALTH.items())
    )


def _rate_ok(source: str) -> bool:
    """Return True if enough time has passed since last call to this source."""
    info = _RATE_LIMIT.get(source)
    if not info:
        return True
    return (time.time() - info["last"]) >= info["min_gap"]


def _rate_mark(source: str):
    """Record that we just called this source."""
    if source in _RATE_LIMIT:
        _RATE_LIMIT[source]["last"] = time.time()


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


def _fetch_xml(url: str, timeout: int = 8) -> str | None:
    """Fetch raw XML/RSS from a URL. Returns None on any error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TradingBot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        logger.debug(f"[News] RSS fetch error ({url[:50]}...): {e}")
        return None


def _parse_rss_items(xml_text: str, source_name: str) -> list:
    """Parse an RSS 2.0 feed into the scanner's article-dict format.

    Pure function (no network) so parsing is testable offline.
    Returns [] on any parse failure (fail-open).
    """
    try:
        root = ET.fromstring(xml_text)
        items = root.findall(".//item")
        result = []
        for item in items[:15]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            pub_date = (item.findtext("pubDate") or "").strip()
            link = (item.findtext("link") or "").strip()

            # Parse RFC 822 date to our format
            published = ""
            if pub_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    published = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    published = pub_date[:16]

            # Extract categories from RSS
            categories = ",".join(
                (cat.text or "") for cat in item.findall("category")
                if cat.text
            )

            impact = _classify_impact(title)
            result.append({
                "title":      title,
                "source":     source_name,
                "url":        link,
                "published":  published,
                "categories": categories,
                "sentiment":  _score_headline(title),
                "impact":     impact,
                "breaking":   False,
                "tags":       [c.strip() for c in categories.split(",")
                               if c.strip()],
            })
        return result
    except ET.ParseError as e:
        logger.debug(f"[News] {source_name} RSS parse error: {e}")
        return []
    except Exception:
        return []


def _score_headline(title: str) -> int:
    """Score a headline: +1 per positive word, -1 per negative. Returns -5..+5."""
    title_lower = title.lower()
    score = sum(1 for w in POSITIVE_WORDS if w in title_lower)
    score -= sum(1 for w in NEGATIVE_WORDS if w in title_lower)
    return max(-5, min(5, score))


def _classify_impact(title: str) -> str:
    """Classify headline impact as HIGH, MEDIUM, or LOW based on keyword patterns."""
    title_lower = title.lower()
    for kw in HIGH_IMPACT_KEYWORDS:
        if kw in title_lower:
            return "HIGH"
    for kw in MEDIUM_IMPACT_KEYWORDS:
        if kw in title_lower:
            return "MEDIUM"
    return "LOW"


def _extract_coin_symbols(title: str, categories: str = "", tags: list = None) -> set:
    """Extract probable coin symbols from headline text, categories, and tags."""
    # Well-known coin names → symbols
    COIN_NAMES = {
        "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
        "ripple": "XRP", "cardano": "ADA", "dogecoin": "DOGE",
        "avalanche": "AVAX", "polkadot": "DOT", "chainlink": "LINK",
        "litecoin": "LTC", "polygon": "MATIC", "uniswap": "UNI",
        "cosmos": "ATOM", "near protocol": "NEAR", "near": "NEAR",
        "arbitrum": "ARB", "optimism": "OP", "sui": "SUI",
        "aptos": "APT", "injective": "INJ", "celestia": "TIA",
        "binance coin": "BNB", "bnb": "BNB",
        "filecoin": "FIL", "render": "RENDER", "fetch.ai": "FET",
        "artificial superintelligence": "FET", "hedera": "HBAR",
        "stellar": "XLM", "tron": "TRX", "toncoin": "TON", "ton": "TON",
        "worldcoin": "WLD", "pepe": "PEPE", "bonk": "BONK",
        "jupiter": "JUP", "sei": "SEI", "stacks": "STX",
        "maker": "MKR", "aave": "AAVE", "hyperliquid": "HYPE",
    }
    symbols = set()
    title_lower = title.lower()

    # Check coin names in title
    for name, sym in COIN_NAMES.items():
        if name in title_lower:
            symbols.add(sym)

    # Check categories and tags
    cats = categories.upper() if categories else ""
    tag_list = [t.upper() for t in (tags or [])]
    candidates = set(cats.replace(",", " ").split() + tag_list)
    for coin in candidates:
        coin = coin.strip()
        if len(coin) >= 2 and coin.isalpha():
            symbols.add(coin)

    return symbols


class NewsScanner:

    def __init__(self):
        self._cache:          dict  = {}
        self._last_run:       float = 0.0
        self._prev_headlines: set   = set()   # for breaking news detection
        self._sentiment_hist: dict  = {}      # loaded from disk
        self._load_sentiment_history()
        self._hydrate_from_disk()

    def _hydrate_from_disk(self) -> None:
        """Load last successful scan so consumers work before the first network fetch.

        Seeds ``_prev_headlines`` so a restart does not re-flag every HIGH item
        as BREAKING. Fail-open on missing/invalid cache.
        """
        try:
            if not NEWS_CACHE.exists():
                return
            raw = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not raw.get("news"):
                return
            self._cache = raw
            fetched = raw.get("fetched_at")
            if fetched:
                try:
                    dt = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        self._last_run = dt.timestamp()
                    else:
                        self._last_run = dt.timestamp()
                except (TypeError, ValueError):
                    self._last_run = time.time() - CACHE_TTL
            else:
                self._last_run = time.time() - CACHE_TTL
            self._prev_headlines = {
                str(a.get("title") or "")
                for a in raw.get("news", [])
                if a.get("title")
            }
            age_min = max(0.0, (time.time() - self._last_run) / 60.0)
            logger.info(
                f"[News] Hydrated cache from disk "
                f"({len(raw.get('news', []))} headlines, age={age_min:.0f}m)"
            )
        except Exception as exc:
            logger.debug(f"[News] Cache hydrate skipped: {exc}")

    # ── Public API (preserved from original) ───────────────────────────

    def scan(self, force: bool = False) -> dict:
        """
        Fetch all news + market data. Uses cache if fresh.
        Returns structured data dict.

        Adaptive interval: uses fast TTL when market volatility is high
        (fear & greed < 20 or > 80).
        """
        ttl = self._get_adaptive_ttl()
        if not force and (time.time() - self._last_run) < ttl:
            return self._cache

        logger.info("[News] Fetching market news and trends...")

        result = {
            "fetched_at":   datetime.now().isoformat(),
            "fear_greed":   self._fetch_fear_greed(),
            "trending":     self._fetch_trending(),
            "global":       self._fetch_global(),
            "news":         [],
            "sentiment":    {},
        }

        # Fetch from all news sources and merge
        result["news"] = self._fetch_all_news()

        # Deduplicate by title similarity, keep newest, cap at MAX_HEADLINES
        result["news"] = self._deduplicate_news(result["news"])[:MAX_HEADLINES]

        # Detect breaking news (HIGH impact items not seen before)
        self._detect_breaking_news(result["news"])

        # Build per-symbol sentiment from news
        result["sentiment"] = self._build_sentiment(result["news"])

        # Update persistent sentiment history
        self._update_sentiment_history(result["sentiment"])

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
        Base symbol extracted: BTC/USDT -> BTC
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

    # ── New Public API ─────────────────────────────────────────────────

    def get_news_signals(self) -> list[dict]:
        """
        Return actionable trading signals based on news sentiment.

        Logic:
          - If a specific coin has 3+ recent negative headlines -> bearish signal
          - If a specific coin has 3+ recent positive headlines -> bullish signal

        Returns list of dicts:
          [{"coin": "BTC", "signal": "bearish"|"bullish",
            "strength": float, "headlines": [...]}]
        """
        news = self._cache.get("news", [])
        if not news:
            return []

        # Aggregate per-coin headline sentiments
        coin_headlines: dict[str, list[dict]] = {}
        for article in news:
            title = article.get("title", "")
            score = article.get("sentiment", 0)
            if score == 0:
                continue

            symbols = _extract_coin_symbols(
                title,
                article.get("categories", ""),
                article.get("tags", []),
            )
            for sym in symbols:
                coin_headlines.setdefault(sym, []).append({
                    "title": title,
                    "sentiment": score,
                    "impact": article.get("impact", "LOW"),
                })

        signals = []
        for coin, items in coin_headlines.items():
            positive = [i for i in items if i["sentiment"] > 0]
            negative = [i for i in items if i["sentiment"] < 0]

            if len(negative) >= 3:
                avg_score = sum(i["sentiment"] for i in negative) / len(negative)
                # Boost strength for HIGH impact items
                high_count = sum(1 for i in negative if i["impact"] == "HIGH")
                strength = min(1.0, abs(avg_score) / 5.0 + high_count * 0.15)
                signals.append({
                    "coin": coin,
                    "signal": "bearish",
                    "strength": round(strength, 3),
                    "headline_count": len(negative),
                    "headlines": [i["title"] for i in negative[:5]],
                })

            if len(positive) >= 3:
                avg_score = sum(i["sentiment"] for i in positive) / len(positive)
                high_count = sum(1 for i in positive if i["impact"] == "HIGH")
                strength = min(1.0, avg_score / 5.0 + high_count * 0.15)
                signals.append({
                    "coin": coin,
                    "signal": "bullish",
                    "strength": round(strength, 3),
                    "headline_count": len(positive),
                    "headlines": [i["title"] for i in positive[:5]],
                })

        # Sort by strength descending
        signals.sort(key=lambda s: s["strength"], reverse=True)
        return signals

    def get_market_context(self) -> dict:
        """
        Return a structured dict suitable for the MCP Brain's scoring engine.

        Keys:
          overall_sentiment   — float -1.0 to +1.0
          fear_greed_zone     — str: extreme_fear / fear / neutral / greed / extreme_greed
          breaking_news       — list of HIGH impact breaking items
          coin_sentiments     — dict coin -> sentiment score (-1 to +1)
          market_cap_trend    — str: up / down / flat
        """
        fg_val = self.fear_greed_value()
        sentiments = self._cache.get("sentiment", {})
        news = self._cache.get("news", [])
        glb = self._cache.get("global", {})

        # Overall sentiment: average of all coin sentiments, scaled to -1..+1
        if sentiments:
            raw_avg = sum(sentiments.values()) / len(sentiments)
            overall = max(-1.0, min(1.0, raw_avg / 5.0))
        else:
            overall = 0.0

        # Fear & greed zone
        if fg_val <= 20:
            fg_zone = "extreme_fear"
        elif fg_val <= 40:
            fg_zone = "fear"
        elif fg_val <= 60:
            fg_zone = "neutral"
        elif fg_val <= 80:
            fg_zone = "greed"
        else:
            fg_zone = "extreme_greed"

        # Breaking news: HIGH impact items flagged as breaking
        breaking = [
            {
                "title":     a.get("title", ""),
                "source":    a.get("source", ""),
                "impact":    a.get("impact", "LOW"),
                "sentiment": a.get("sentiment", 0),
            }
            for a in news
            if a.get("breaking") and a.get("impact") == "HIGH"
        ]

        # Coin sentiments scaled to -1..+1
        coin_sents = {}
        for coin, score in sentiments.items():
            coin_sents[coin] = round(max(-1.0, min(1.0, score / 5.0)), 3)

        # Market cap trend from 24h change
        mktcap_chg = glb.get("market_cap_change_24h", 0)
        if mktcap_chg > 1.0:
            mktcap_trend = "up"
        elif mktcap_chg < -1.0:
            mktcap_trend = "down"
        else:
            mktcap_trend = "flat"

        return {
            "overall_sentiment": round(overall, 3),
            "fear_greed_value":  fg_val,
            "fear_greed_zone":   fg_zone,
            "breaking_news":     breaking,
            "coin_sentiments":   coin_sents,
            "market_cap_trend":  mktcap_trend,
            "news_signal_count": len(self.get_news_signals()),
            "fetched_at":        self._cache.get("fetched_at", ""),
        }

    def get_sentiment_history(self, coin: str = None) -> dict:
        """
        Return sentiment history. If coin is specified, returns only that coin's
        history; otherwise returns the full dict.
        """
        if coin:
            return self._sentiment_hist.get(coin.upper(), {})
        return dict(self._sentiment_hist)

    # ── Adaptive scan interval ─────────────────────────────────────────

    def _get_adaptive_ttl(self) -> float:
        """
        Return the scan TTL in seconds.
        Uses fast interval when Fear & Greed is in extreme zones (< 20 or > 80).
        """
        fg = self._cache.get("fear_greed", {}).get("value", 50)
        if fg < 20 or fg > 80:
            logger.debug(
                f"[News] High volatility detected (F&G={fg}), "
                f"using fast scan interval ({FAST_CACHE_TTL}s)"
            )
            return FAST_CACHE_TTL
        return CACHE_TTL

    # ── News fetchers (all sources) ────────────────────────────────────

    def _fetch_all_news(self) -> list:
        """Fetch from all RSS news sources (+ optional X/Twitter) and merge."""
        all_articles = []
        for rate_key, source_name, url in RSS_SOURCES:
            all_articles.extend(
                self._fetch_news_rss(rate_key, source_name, url)
            )
        all_articles.extend(self._fetch_twitter_news())

        if not all_articles:
            cached = list(self._cache.get("news") or [])
            if cached:
                logger.warning(
                    "[News] All RSS sources returned 0 items — "
                    f"serving {len(cached)} cached headlines "
                    f"(health: {_health_summary() or 'n/a'})"
                )
                return cached
            logger.warning(
                "[News] All RSS sources returned 0 items and no cache "
                f"(health: {_health_summary() or 'n/a'})"
            )
            return []

        # Sort by published date descending (newest first)
        all_articles.sort(
            key=lambda a: a.get("published", ""),
            reverse=True,
        )
        return all_articles

    def _fetch_twitter_news(self) -> list:
        """Curated X/Twitter headlines (fail-open). See data_feeds/twitter_feed."""
        if not NEWS_CFG.get("twitter_enabled", True):
            return []
        if not _rate_ok("twitter"):
            logger.debug("[News] Twitter rate-limited, skipping")
            return [
                a for a in self._cache.get("news", [])
                if str(a.get("source") or "").startswith("X/@")
            ]
        _rate_mark("twitter")
        try:
            from core.data_feeds.twitter_feed import fetch_twitter_headlines

            items = fetch_twitter_headlines(
                {
                    "enabled": True,
                    "accounts": NEWS_CFG.get("twitter_accounts") or None,
                    "max_results": NEWS_CFG.get("twitter_max_results", 20),
                    "rss_fallback": NEWS_CFG.get("twitter_rss_fallback", True),
                    "rsshub_base": NEWS_CFG.get(
                        "twitter_rsshub_base", "https://rsshub.app"
                    ),
                }
            )
            _health_mark("twitter", ok=bool(items))
            return items or []
        except Exception as exc:
            _health_mark("twitter", ok=False)
            logger.warning(f"[News] Twitter feed failed (fail-open): {exc}")
            return []

    def _fetch_news_rss(self, rate_key: str, source_name: str, url: str) -> list:
        """Fetch and parse one RSS news source (keyless), with health tracking."""
        if not _rate_ok(rate_key):
            logger.debug(f"[News] {source_name} rate-limited, skipping")
            return self._get_cached_source_articles(source_name)
        _rate_mark(rate_key)

        xml_text = _fetch_xml(url)
        if not xml_text:
            _health_mark(rate_key, ok=False)
            return []

        items = _parse_rss_items(xml_text, source_name)
        _health_mark(rate_key, ok=bool(items))
        return items

    def _get_cached_source_articles(self, source: str) -> list:
        """Return articles from a specific source that are in the current cache."""
        return [
            a for a in self._cache.get("news", [])
            if a.get("source") == source
        ]

    def _deduplicate_news(self, articles: list) -> list:
        """Remove near-duplicate headlines (exact title match)."""
        seen = set()
        unique = []
        for a in articles:
            title_key = a.get("title", "").strip().lower()
            if title_key and title_key not in seen:
                seen.add(title_key)
                unique.append(a)
        return unique

    # ── Breaking news detection ────────────────────────────────────────

    def _detect_breaking_news(self, articles: list):
        """
        Flag HIGH impact news items not seen in the previous scan as breaking.
        Logs breaking news at WARNING level.
        """
        current_titles = set()
        for article in articles:
            title = article.get("title", "")
            current_titles.add(title)

            if article.get("impact") != "HIGH":
                continue

            # Check if this is a new headline
            if title not in self._prev_headlines:
                article["breaking"] = True
                logger.warning(
                    f"[News] BREAKING: {title} "
                    f"(impact={article['impact']}, "
                    f"sentiment={article['sentiment']:+d}, "
                    f"source={article.get('source', '?')})"
                )

        # Update previous headlines set for next scan
        self._prev_headlines = current_titles

    # ── Original fetchers (unchanged signatures) ───────────────────────

    def _fetch_fear_greed(self) -> dict:
        if not _rate_ok("feargreed"):
            return self._cache.get("fear_greed", {"value": 50, "label": "Neutral"})
        _rate_mark("feargreed")

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
        if not _rate_ok("coingecko"):
            return self._cache.get("trending", [])
        _rate_mark("coingecko")

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
        # Uses same coingecko rate bucket — check separately
        data = _fetch(GLOBAL_URL)
        if not data:
            return self._cache.get("global", {})
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
            return self._cache.get("global", {})

    # ── Original _fetch_news removed — replaced by _fetch_all_news ────
    # 2026-07-10: CryptoCompare (keyless 401 / keyed 0-items) and CryptoPanic
    # (free/v1 endpoint removed upstream) sources dropped; RSS_SOURCES via
    # _fetch_news_rss() is the news path now.

    def _build_sentiment(self, news: list) -> dict:
        """Aggregate sentiment score per coin from news tags and titles."""
        scores: dict[str, list[int]] = {}
        for article in news:
            score = article.get("sentiment", 0)
            if score == 0:
                continue
            # Extract coins from categories, tags, and title text
            symbols = _extract_coin_symbols(
                article.get("title", ""),
                article.get("categories", ""),
                article.get("tags", []),
            )
            for coin in symbols:
                scores.setdefault(coin, []).append(score)

        return {coin: round(sum(v) / len(v), 2)
                for coin, v in scores.items()}

    # ── Persistent sentiment history ───────────────────────────────────

    def _load_sentiment_history(self):
        """Load sentiment history from disk."""
        try:
            if SENTIMENT_HISTORY.exists():
                self._sentiment_hist = json.loads(
                    SENTIMENT_HISTORY.read_text(encoding="utf-8")
                )
            else:
                self._sentiment_hist = {}
        except Exception as e:
            logger.debug(f"[News] Sentiment history load error: {e}")
            self._sentiment_hist = {}

    def _update_sentiment_history(self, sentiments: dict):
        """
        Append current sentiment scores to history, keyed by date.
        Prunes entries older than SENTIMENT_HIST_DAYS.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        cutoff = (
            datetime.now() - timedelta(days=SENTIMENT_HIST_DAYS)
        ).strftime("%Y-%m-%d")

        for coin, score in sentiments.items():
            if coin not in self._sentiment_hist:
                self._sentiment_hist[coin] = {}
            self._sentiment_hist[coin][today] = round(score, 2)

            # Prune old entries
            self._sentiment_hist[coin] = {
                date: val
                for date, val in self._sentiment_hist[coin].items()
                if date >= cutoff
            }

        # Remove coins with no remaining data
        self._sentiment_hist = {
            coin: dates
            for coin, dates in self._sentiment_hist.items()
            if dates
        }

        self._save_sentiment_history()

    def _save_sentiment_history(self):
        """Write sentiment history to disk."""
        try:
            SENTIMENT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
            SENTIMENT_HISTORY.write_text(
                json.dumps(self._sentiment_hist, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"[News] Sentiment history save error: {e}")

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

            # Show breaking news first if any
            breaking = [a for a in news if a.get("breaking")]
            if breaking:
                lines += ["", "  *** BREAKING NEWS ***"]
                for a in breaking[:3]:
                    title = a.get("title", "")[:68]
                    src   = a.get("source", "")
                    lines.append(f"    !! [{a.get('impact','?')}] {title}")
                    lines.append(f"       {src}")
                    lines.append("")

            lines += ["", "  LATEST NEWS:"]
            for a in news[:8]:
                sentiment = a.get("sentiment", 0)
                impact    = a.get("impact", "LOW")
                icon = "▲" if sentiment > 0 else ("▼" if sentiment < 0 else "●")
                impact_tag = f"[{impact[0]}]" if impact != "LOW" else "   "
                title = a.get("title", "")[:64]
                src   = a.get("source", "")
                ts    = a.get("published", "")[:10]
                lines.append(f"    {icon} {impact_tag} [{ts}] {title}")
                lines.append(f"       {src}")
                lines.append("")

            # News signals summary
            signals = self.get_news_signals()
            if signals:
                lines += ["  NEWS SIGNALS:"]
                for sig in signals[:5]:
                    direction = "BULL" if sig["signal"] == "bullish" else "BEAR"
                    lines.append(
                        f"    {direction} {sig['coin']:<6} "
                        f"strength={sig['strength']:.2f}  "
                        f"({sig['headline_count']} headlines)"
                    )
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
        news = data.get("news", [])
        breaking_count = sum(1 for a in news if a.get("breaking"))
        high_count     = sum(1 for a in news if a.get("impact") == "HIGH")

        sources = set(a.get("source", "") for a in news)
        source_list = ", ".join(s for s in sorted(sources) if s)

        logger.info(
            f"[News] Fear&Greed={fg.get('value','?')} ({fg.get('label','?')}) | "
            f"Mkt cap chg={glb.get('market_cap_change_24h',0):+.2f}% | "
            f"BTC dom={glb.get('btc_dominance',0):.1f}% | "
            f"Trending: {', '.join(top)} | "
            f"{len(news)} headlines ({high_count} HIGH, {breaking_count} breaking) | "
            f"Sources: {source_list} | "
            f"SrcHealth: {_health_summary() or 'n/a'}"
        )
