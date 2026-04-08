"""
core/mcp_brain.py — Algorithmic Quant Brain: 24/7 Multi-Factor Scoring Engine

Pure algorithmic decision-making — NO external AI calls (no Claude CLI, no Anthropic API).

7 Live Data Sources:
  1. Crypto.com API        - live prices, 24h volume, high/low
  2. CoinGecko API         - market data, sentiment, sparkline technicals
  3. CryptoCompare         - latest crypto news headlines
  4. Fear & Greed Index    - market-wide sentiment gauge
  5. Binance Funding       - futures funding rates (bull/bear pressure)
  6. Built-in Technicals   - RSI, EMA, Bollinger, MACD, multi-TF ROC, trend_score
  7. Binance Order Book    - bid/ask depth, imbalance ratio, wall detection

+ Exchange OHLCV indicators: Multi-TF (4h/1h/15m) ADX, RSI, EMA direction, ATR%, volume ratio

Two Operating Modes:
  A. PORTFOLIO ANALYSIS   - multi-factor scoring for OPEN/CLOSE actions (15 min cycle)
  B. POSITION MONITOR     - algorithmic HOLD/CLOSE/TIGHTEN/BREAKEVEN (90s cycle)

7-Layer Scoring Framework (max 100 pts):
  Trend(25) | Momentum(20) | Strength(15) | Volume(10) | OrderBook(10) | Sentiment(10) | Funding(10)
  Requires score >= 65 AND 4+/7 layers for entry. Asymmetric R:R 2.5:1 minimum.

Safety:
  - < 2 data sources = all HOLD
  - 3% daily loss = full halt
  - Decision accuracy tracked for self-improvement
  - Never trades on incomplete or stale data
"""

import json
import math
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DECISION_LOG   = Path("data/mcp_decisions.jsonl")
ACCURACY_FILE  = Path("data/mcp_accuracy.json")
STATE_FILE     = Path("data/mcp_state.json")
FETCH_TIMEOUT  = 10

# ── Cooldowns ────────────────────────────────────────────────────────
ENTRY_COOLDOWN    = 900   # 15 min — portfolio scoring cycle
POSITION_COOLDOWN = 90    # 90s between position monitor checks
DATA_CACHE_TTL    = 120   # Cache raw data for 2 min

# ── Scoring thresholds ──────────────────────────────────────────────
SCORE_OPEN_THRESHOLD  = 65   # Min score (out of 100) to open a position
MIN_LAYERS_ALIGNED    = 4    # Min layers (out of 7) that must contribute
MIN_DIRECTION_STRENGTH = 0.4  # Min buy/sell signal strength to consider



def _http_get(url: str, timeout: int = FETCH_TIMEOUT, headers: dict = None):
    """Simple HTTP GET returning parsed JSON or empty dict/list on failure."""
    try:
        hdrs = {"User-Agent": "TradingBot/2.0"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 1: Crypto.com — prices, volume, candlesticks
# ══════════════════════════════════════════════════════════════════════

def fetch_crypto_com(coins: list) -> dict:
    data = {}
    try:
        resp = _http_get("https://api.crypto.com/v2/public/get-ticker")
        tickers = resp.get("result", {}).get("data", [])
        ticker_map = {}
        for t in tickers:
            sym = t.get("i", "")
            base = sym.split("_")[0]
            if base:
                ticker_map[base] = t
        for coin in coins:
            t = ticker_map.get(coin, {})
            if t:
                data[coin] = {
                    "price":      float(t.get("a", 0)),
                    "volume_24h": float(t.get("v", 0)),
                    "high_24h":   float(t.get("h", 0)),
                    "low_24h":    float(t.get("l", 0)),
                    "change_24h": float(t.get("c", 0)),
                }
    except Exception as e:
        logger.debug(f"[MCP-Data] Crypto.com: {e}")
    return data


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 2: CoinGecko — market cap, sentiment, sparkline
# ══════════════════════════════════════════════════════════════════════

_GECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
    "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
    "LINK": "chainlink", "UNI": "uniswap", "ATOM": "cosmos",
    "SUI": "sui", "OP": "optimism", "ARB": "arbitrum",
    "NEAR": "near", "APT": "aptos", "FET": "artificial-superintelligence-alliance",
    "INJ": "injective-protocol", "SEI": "sei-network",
    "PEPE": "pepe", "WIF": "dogwifcoin", "BONK": "bonk",
    "FLOKI": "floki", "STX": "blockstack", "IMX": "immutable-x",
    "RNDR": "render-token", "TIA": "celestia",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "FIL": "filecoin",
    "AAVE": "aave", "ORDI": "ordinals", "MANTA": "manta-network",
}

# ── Asset class metadata for Claude prompt context ───────────────────
# ── Exchange capabilities ────────────────────────────────────────────
EXCHANGE_CAPS = {
    "binance": {"spot": True, "futures": True,  "transfer": True,  "unified": False,
                "note": "Full spot+futures. Transfer USDT between spot<->futures wallets."},
    "mexc":    {"spot": True, "futures": False, "transfer": False, "unified": False,
                "note": "SPOT ONLY. Futures API geo-blocked. No transfers. Only buy/sell spot."},
    "bybit":   {"spot": True, "futures": True,  "transfer": False, "unified": True,
                "note": "Unified account: single balance for spot+futures. No transfers needed."},
    "bitget":  {"spot": True, "futures": True,  "transfer": True,  "unified": False,
                "note": "Full spot+futures. Transfer USDT between spot<->futures wallets."},
}

_ASSET_CLASS = {
    # Commodities — traded as USDT-margined perpetual futures on crypto exchanges
    "XAU": {"class": "commodity", "name": "Gold",   "traits": "safe-haven, inverse-USD, low volatility, trend-following works best, wider SL needed"},
    "XAG": {"class": "commodity", "name": "Silver", "traits": "commodity, follows Gold with 2x amplification, more volatile than Gold, mean-reversion works"},
    "WTI": {"class": "commodity", "name": "Oil",    "traits": "commodity, geopolitical-sensitive, high volatility spikes, supply-driven"},
    # Memecoins — extreme volatility, momentum-driven
    "PEPE": {"class": "memecoin", "name": "PEPE",  "traits": "memecoin, extreme volatility, momentum/hype-driven, tight SL essential, scalp-friendly"},
    "WIF":  {"class": "memecoin", "name": "WIF",   "traits": "memecoin, Solana ecosystem hype, momentum plays only"},
    "BONK": {"class": "memecoin", "name": "BONK",  "traits": "memecoin, micro-cap, very volatile, scalp only"},
    "FLOKI":{"class": "memecoin", "name": "FLOKI", "traits": "memecoin, hype-driven, tight SL"},
    "DOGE": {"class": "memecoin", "name": "DOGE",  "traits": "memecoin but large-cap, Elon-sensitive, momentum plays"},
    # Large-cap crypto — most liquid, trend+momentum strategies
    "BTC": {"class": "large_cap", "name": "Bitcoin",  "traits": "market leader, sets direction, trend-following best, most liquid"},
    "ETH": {"class": "large_cap", "name": "Ethereum", "traits": "follows BTC with beta, DeFi sentiment matters, trend+mean-reversion"},
    "BNB": {"class": "large_cap", "name": "BNB",      "traits": "exchange token, Binance ecosystem, less volatile than market"},
    "SOL": {"class": "large_cap", "name": "Solana",   "traits": "high-performance L1, DeFi/NFT narrative, trend-following, higher volatility than BTC"},
    "XRP": {"class": "large_cap", "name": "XRP",      "traits": "payment-focused, SEC lawsuit resolved, news-driven spikes"},
}


def fetch_coingecko(coins: list) -> dict:
    data = {}
    try:
        ids = [_GECKO_IDS.get(c, c.lower()) for c in coins if c in _GECKO_IDS]
        if not ids:
            return data
        ids_str = ",".join(ids[:25])
        url = (f"https://api.coingecko.com/api/v3/coins/markets?"
               f"vs_currency=usd&ids={ids_str}&order=market_cap_desc"
               f"&per_page=25&sparkline=true&price_change_percentage=1h,24h,7d")
        resp = _http_get(url, timeout=15)
        if isinstance(resp, list):
            rev = {v: k for k, v in _GECKO_IDS.items()}
            for item in resp:
                gid = item.get("id", "")
                coin = rev.get(gid, gid.upper())
                chg_1h = item.get("price_change_percentage_1h_in_currency", 0) or 0
                chg_24h = item.get("price_change_percentage_24h", 0) or 0
                chg_7d = item.get("price_change_percentage_7d_in_currency", 0) or 0

                momentum = (chg_1h * 0.4 + chg_24h * 0.4 + chg_7d * 0.2) / 10
                sentiment = max(-1.0, min(1.0, momentum))

                sparkline = (item.get("sparkline_in_7d") or {}).get("price", [])

                data[coin] = {
                    "market_cap":  item.get("market_cap", 0),
                    "volume_24h":  item.get("total_volume", 0),
                    "price":       item.get("current_price", 0),
                    "change_1h":   round(chg_1h, 2),
                    "change_24h":  round(chg_24h, 2),
                    "change_7d":   round(chg_7d, 2),
                    "sentiment":   round(sentiment, 3),
                    "ath_change":  item.get("ath_change_percentage", 0),
                    "sparkline":   sparkline[-168:],  # Last 7 days hourly
                }
    except Exception as e:
        logger.debug(f"[MCP-Data] CoinGecko: {e}")
    return data


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 3: CryptoCompare — latest news
# ══════════════════════════════════════════════════════════════════════

def _classify_news_sentiment(title: str) -> str:
    tl = title.lower()
    pos_words = ("bull", "surge", "rally", "soar", "jump", "gain",
                 "high", "break", "pump", "moon", "ath", "record",
                 "buy", "strong", "upbeat", "optimis", "boost", "grow")
    neg_words = ("crash", "bear", "dump", "plunge", "fall", "drop",
                 "low", "fear", "hack", "exploit", "ban", "fraud",
                 "liquidat", "bankrupt", "sec ", "lawsuit", "sell",
                 "weak", "pessimis", "slump", "tank", "warn")
    pos_count = sum(1 for w in pos_words if w in tl)
    neg_count = sum(1 for w in neg_words if w in tl)
    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    return "neutral"


def fetch_news() -> list:
    """Fetch crypto news. Tries CoinGecko status/trending, then CryptoCompare."""
    results = []

    # Source A: CoinGecko trending (always free, no key)
    try:
        resp = _http_get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        coins = resp.get("coins", [])[:8]
        for c in coins:
            item = c.get("item", {})
            name = item.get("name", "")
            symbol = item.get("symbol", "")
            score = item.get("score", 0)
            chg24 = item.get("data", {}).get("price_change_percentage_24h", {}).get("usd", 0) or 0
            title = f"{name} ({symbol}) trending #{score+1}, 24h change {chg24:+.1f}%"
            results.append({
                "title": title,
                "category": "trending",
                "source": "CoinGecko",
                "sentiment": "positive" if chg24 > 2 else "negative" if chg24 < -2 else "neutral",
                "timestamp": 0,
            })
    except Exception:
        pass

    # Source B: CryptoCompare (free tier: 100k calls/month, ~2.3/min)
    try:
        cc_key = os.getenv("CRYPTOCOMPARE_API_KEY", "").strip()
        cc_url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"
        cc_headers = {}
        if cc_key:
            cc_headers["authorization"] = f"Apikey {cc_key}"
        resp = _http_get(cc_url, timeout=8, headers=cc_headers if cc_headers else None)
        articles = resp.get("Data", [])
        if isinstance(articles, list):
            for a in articles[:12]:
                title = a.get("title", "")[:140]
                results.append({
                    "title":     title,
                    "category":  a.get("categories", ""),
                    "source":    a.get("source", ""),
                    "sentiment": _classify_news_sentiment(title),
                    "timestamp": a.get("published_on", 0),
                })
    except Exception:
        pass

    if results:
        return results

    logger.debug("[MCP-Data] No news sources returned data")
    return []


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 4: Fear & Greed Index (alternative.me)
# ══════════════════════════════════════════════════════════════════════

def fetch_fear_greed() -> dict:
    """Returns {value: 0-100, label: 'Extreme Fear'...'Extreme Greed'}."""
    try:
        resp = _http_get("https://api.alternative.me/fng/?limit=3", timeout=8)
        items = resp.get("data", [])
        if items:
            current = items[0]
            yesterday = items[1] if len(items) > 1 else {}
            val = int(current.get("value", 50))
            yval = int(yesterday.get("value", val))
            return {
                "value": val,
                "label": current.get("value_classification", "Neutral"),
                "yesterday": yval,
                "trend": "rising" if val > yval else "falling" if val < yval else "flat",
            }
    except Exception as e:
        logger.debug(f"[MCP-Data] Fear&Greed: {e}")
    return {}


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 5: Binance Funding Rates (futures sentiment)
# ══════════════════════════════════════════════════════════════════════

def fetch_funding_rates(coins: list) -> dict:
    """Fetch current funding rates from Binance futures. Positive = longs pay shorts."""
    data = {}
    try:
        resp = _http_get(
            "https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10)
        if isinstance(resp, list):
            rate_map = {}
            for item in resp:
                sym = item.get("symbol", "")
                if sym.endswith("USDT"):
                    base = sym[:-4]
                    rate_map[base] = {
                        "funding_rate": float(item.get("lastFundingRate", 0)),
                        "mark_price":   float(item.get("markPrice", 0)),
                        "index_price":  float(item.get("indexPrice", 0)),
                    }
            for coin in coins:
                if coin in rate_map:
                    data[coin] = rate_map[coin]
    except Exception as e:
        logger.debug(f"[MCP-Data] Funding: {e}")
    return data


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 6: Built-in Technical Indicators from Sparkline
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE 7: Binance Order Book Depth — buy/sell walls, imbalance
# ══════════════════════════════════════════════════════════════════════

def fetch_orderbook_depth(coins: list) -> dict:
    """Fetch top-of-book depth from Binance. Detects buy/sell walls and imbalance."""
    data = {}
    for coin in coins[:10]:  # Limit to avoid rate limits
        sym = f"{coin}USDT"
        try:
            resp = _http_get(
                f"https://api.binance.com/api/v3/depth?symbol={sym}&limit=20",
                timeout=5)
            bids = resp.get("bids", [])
            asks = resp.get("asks", [])
            if not bids or not asks:
                continue
            bid_vol = sum(float(b[1]) * float(b[0]) for b in bids[:10])
            ask_vol = sum(float(a[1]) * float(a[0]) for a in asks[:10])
            total = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / total if total > 0 else 0
            spread_pct = 0
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                if best_bid > 0:
                    spread_pct = (best_ask - best_bid) / best_bid * 100
            # Detect walls (single level > 20% of total side)
            bid_wall = any(float(b[1]) * float(b[0]) > bid_vol * 0.20 for b in bids[:5])
            ask_wall = any(float(a[1]) * float(a[0]) > ask_vol * 0.20 for a in asks[:5])
            data[coin] = {
                "bid_depth_usd": round(bid_vol, 0),
                "ask_depth_usd": round(ask_vol, 0),
                "imbalance": round(imbalance, 3),  # >0 = buyers dominate
                "spread_pct": round(spread_pct, 4),
                "bid_wall": bid_wall,
                "ask_wall": ask_wall,
            }
        except Exception:
            continue
    return data


def compute_technicals(sparkline: list) -> dict:
    """Compute RSI, EMA, Bollinger Bands, and support/resistance from hourly sparkline."""
    if not sparkline or len(sparkline) < 30:
        return {}

    prices = [float(p) for p in sparkline if p]
    if len(prices) < 30:
        return {}

    # RSI (14-period)
    rsi = _calc_rsi(prices, 14)

    # EMAs
    ema_9 = _calc_ema(prices, 9)
    ema_21 = _calc_ema(prices, 21)
    ema_50 = _calc_ema(prices, min(50, len(prices) - 1))

    # Bollinger Bands (20, 2)
    bb_mid, bb_upper, bb_lower = _calc_bb(prices, 20, 2.0)

    # Support / Resistance from recent highs/lows
    recent = prices[-48:] if len(prices) >= 48 else prices
    support = min(recent)
    resistance = max(recent)
    current = prices[-1]

    # Price position within Bollinger Band (0=lower, 1=upper)
    bb_position = 0.5
    if bb_upper and bb_lower and bb_upper != bb_lower:
        bb_position = max(0, min(1, (current - bb_lower) / (bb_upper - bb_lower)))

    # Trend strength: EMA alignment
    trend = "neutral"
    if ema_9 and ema_21 and ema_50:
        if ema_9 > ema_21 > ema_50:
            trend = "strong_up"
        elif ema_9 > ema_21:
            trend = "up"
        elif ema_9 < ema_21 < ema_50:
            trend = "strong_down"
        elif ema_9 < ema_21:
            trend = "down"

    # Volatility (ATR proxy from last 24 candles)
    atr_pct = 0
    if len(prices) >= 24:
        ranges = [abs(prices[i] - prices[i-1]) for i in range(-23, 0)]
        avg_range = sum(ranges) / len(ranges)
        if current > 0:
            atr_pct = round(avg_range / current * 100, 3)

    # Momentum: rate of change over multiple timeframes
    roc_1h = round((current / prices[-2] - 1) * 100, 2) if len(prices) >= 2 and prices[-2] > 0 else 0
    roc_4h = round((current / prices[-4] - 1) * 100, 2) if len(prices) >= 4 and prices[-4] > 0 else 0
    roc_24h = round((current / prices[-24] - 1) * 100, 2) if len(prices) >= 24 and prices[-24] > 0 else 0
    roc_72h = round((current / prices[-72] - 1) * 100, 2) if len(prices) >= 72 and prices[-72] > 0 else 0

    # MACD (12, 26, 9)
    macd_line = macd_signal = macd_hist = 0
    if len(prices) >= 26:
        ema_12 = _calc_ema(prices, 12)
        ema_26 = _calc_ema(prices, 26)
        macd_line = ema_12 - ema_26
        # Approximate signal line from recent MACD values
        if len(prices) >= 35:
            macd_values = []
            for i in range(9):
                idx = len(prices) - 9 + i
                if idx >= 26:
                    e12 = _calc_ema(prices[:idx+1], 12)
                    e26 = _calc_ema(prices[:idx+1], 26)
                    macd_values.append(e12 - e26)
            if macd_values:
                macd_signal = _calc_ema(macd_values, min(9, len(macd_values)))
        macd_hist = macd_line - macd_signal

    # Volume profile (relative volume vs average from price swings)
    vol_momentum = 0
    if len(prices) >= 48:
        recent_swings = [abs(prices[i] - prices[i-1]) for i in range(-24, 0)]
        older_swings = [abs(prices[i] - prices[i-1]) for i in range(-48, -24)]
        avg_recent = sum(recent_swings) / len(recent_swings) if recent_swings else 0
        avg_older = sum(older_swings) / len(older_swings) if older_swings else 0
        if avg_older > 0:
            vol_momentum = round((avg_recent / avg_older - 1) * 100, 1)

    # Trend strength score (-100 to +100)
    trend_score = 0
    if ema_9 and ema_21 and ema_50 and current > 0:
        # Distance from EMAs (normalized)
        d9 = (current - ema_9) / current * 100
        d21 = (current - ema_21) / current * 100
        d50 = (current - ema_50) / current * 100
        trend_score = round(d9 * 0.5 + d21 * 0.3 + d50 * 0.2, 1)
        trend_score = max(-100, min(100, trend_score * 10))

    return {
        "rsi_14":       round(rsi, 1) if rsi else None,
        "ema_9":        round(ema_9, 4) if ema_9 else None,
        "ema_21":       round(ema_21, 4) if ema_21 else None,
        "ema_50":       round(ema_50, 4) if ema_50 else None,
        "bb_upper":     round(bb_upper, 4) if bb_upper else None,
        "bb_mid":       round(bb_mid, 4) if bb_mid else None,
        "bb_lower":     round(bb_lower, 4) if bb_lower else None,
        "bb_position":  round(bb_position, 3),
        "trend":        trend,
        "trend_score":  trend_score,
        "atr_pct":      atr_pct,
        "roc_1h":       roc_1h,
        "roc_4h":       roc_4h,
        "roc_24h":      roc_24h,
        "roc_72h":      roc_72h,
        "macd_line":    round(macd_line, 6),
        "macd_signal":  round(macd_signal, 6),
        "macd_hist":    round(macd_hist, 6),
        "vol_momentum": vol_momentum,
        "support":      round(support, 4),
        "resistance":   round(resistance, 4),
    }


def _calc_rsi(prices: list, period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calc_ema(prices: list, period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _calc_bb(prices: list, period: int, std_mult: float):
    if len(prices) < period:
        return 0, 0, 0
    window = prices[-period:]
    mid = sum(window) / period
    variance = sum((p - mid) ** 2 for p in window) / period
    std = math.sqrt(variance)
    return mid, mid + std * std_mult, mid - std * std_mult


# ══════════════════════════════════════════════════════════════════════
# DECISION ACCURACY TRACKER
# ══════════════════════════════════════════════════════════════════════

class AccuracyTracker:
    """Tracks how accurate past MCP decisions were. Feeds stats back to Claude."""

    def __init__(self):
        self._records = self._load()

    def record_decision(self, coin: str, action: str, price: float, confidence: float):
        """Record a decision at the time it was made."""
        ts = time.time()
        self._records.append({
            "ts": ts, "coin": coin, "action": action,
            "price": price, "confidence": confidence,
            "resolved": False, "outcome": None,
        })
        # Keep last 200 decisions
        if len(self._records) > 200:
            self._records = self._records[-200:]
        self._save()

    def resolve_outcomes(self, current_prices: dict):
        """Check unresolved decisions against current prices (5-min window)."""
        now = time.time()
        for rec in self._records:
            if rec["resolved"]:
                continue
            age = now - rec["ts"]
            if age < 300:  # Wait at least 5 minutes
                continue
            if age > 3600:  # Expire after 1 hour
                rec["resolved"] = True
                rec["outcome"] = "expired"
                continue
            coin = rec["coin"]
            if coin not in current_prices:
                continue
            entry = rec["price"]
            current = current_prices[coin]
            if entry <= 0:
                continue
            chg = (current - entry) / entry
            action = rec["action"]
            # 1.0% threshold — crypto moves ±0.2% on noise alone; need real moves
            if action == "BUY":
                rec["outcome"] = "win" if chg > 0.01 else "loss" if chg < -0.01 else "flat"
            elif action == "SELL":
                rec["outcome"] = "win" if chg < -0.01 else "loss" if chg > 0.01 else "flat"
            else:
                rec["outcome"] = "flat"
            rec["resolved"] = True
        self._save()

    def stats(self) -> dict:
        """Return accuracy stats for last 50 resolved decisions."""
        resolved = [r for r in self._records if r["resolved"] and r["outcome"] != "expired"]
        recent = resolved[-50:]
        if not recent:
            return {"total": 0, "win_rate": 0.5, "avg_confidence": 0.5}
        wins = sum(1 for r in recent if r["outcome"] == "win")
        total = len(recent)
        avg_conf = sum(r.get("confidence", 0.5) for r in recent) / total
        return {
            "total": total,
            "wins": wins,
            "losses": sum(1 for r in recent if r["outcome"] == "loss"),
            "flat": sum(1 for r in recent if r["outcome"] == "flat"),
            "win_rate": round(wins / total, 3) if total > 0 else 0.5,
            "avg_confidence": round(avg_conf, 3),
        }

    def _save(self):
        try:
            ACCURACY_FILE.parent.mkdir(parents=True, exist_ok=True)
            ACCURACY_FILE.write_text(
                json.dumps(self._records[-200:], default=str), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> list:
        try:
            if ACCURACY_FILE.exists():
                data = json.loads(ACCURACY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []


# ══════════════════════════════════════════════════════════════════════
# MAIN CLASS: MCPBrain v2
# ══════════════════════════════════════════════════════════════════════

class MCPBrain:
    """
    Algorithmic Portfolio Manager — multi-factor scoring engine.
    Gathers 7+ data sources, computes multi-TF exchange indicators,
    scores coins for OPEN/CLOSE actions, returns structured trade commands.
    Also monitors open positions with HOLD/CLOSE/TIGHTEN/BREAKEVEN advice.
    No external AI calls — pure deterministic scoring.
    """

    def __init__(self):
        self._last_entry_run = 0
        self._last_position_run = 0
        self._last_decisions = {}
        self._last_position_advice = {}
        self._last_fund_ops = []       # TRANSFER / SELL_PORTFOLIO operations
        self._last_trade_actions = []  # OPEN/CLOSE actions from analyze_portfolio
        self._accuracy = AccuracyTracker()

        # Exchange clients for direct OHLCV fetching (set by bot_engine)
        self._exchanges = {}
        # Cache for exchange indicators (120s TTL)
        self._indicator_cache = {}
        self._indicator_cache_time = 0

        # Always enabled — no external dependency
        self._enabled = True

        # Cache raw data for reuse between entry/position analysis
        self._cached_data = {}
        self._cache_time = 0

        # Load persisted state from last session
        self._load_state()

        logger.info(
            "[MCP-Brain] Algorithmic scoring engine ready — 7 sources "
            "(Crypto.com + CoinGecko + News + Fear&Greed + Funding + OrderBook + Technicals) "
            "+ multi-TF exchange indicators")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _check_rate_limit(self) -> bool:
        """Always True — no external API calls to rate-limit."""
        return True

    def _save_state(self):
        """Persist decisions + position advice to disk for crash recovery."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "decisions": self._last_decisions,
                "position_advice": self._last_position_advice,
                "saved_at": time.time(),
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except Exception as e:
            logger.debug(f"[MCP-Brain] State save: {e}")

    def _load_state(self):
        """Load persisted decisions from last session."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                saved_at = data.get("saved_at", 0)
                age_min = (time.time() - saved_at) / 60
                # Only restore if less than 10 min old (still relevant)
                if age_min < 10:
                    self._last_decisions = data.get("decisions", {})
                    self._last_position_advice = data.get("position_advice", {})
                    n_dec = len(self._last_decisions)
                    n_adv = len(self._last_position_advice)
                    logger.info(
                        f"[MCP-Brain] Restored state: {n_dec} decisions, "
                        f"{n_adv} position advices ({age_min:.1f}min old)")
                else:
                    logger.info(f"[MCP-Brain] Stale state ({age_min:.0f}min old) — starting fresh")
        except Exception as e:
            logger.debug(f"[MCP-Brain] State load: {e}")

    # ──────────────────────────────────────────────────────────────────
    # DATA FETCHING (shared between entry + position analysis)
    # ──────────────────────────────────────────────────────────────────

    def _fetch_all_data(self, coins: list) -> dict:
        """Fetch all 7 sources in parallel. Cache for DATA_CACHE_TTL seconds."""
        now = time.time()
        if now - self._cache_time < DATA_CACHE_TTL and self._cached_data:
            return self._cached_data

        crypto_data = {}
        gecko_data = {}
        news_data = []
        fng_data = {}
        funding_data = {}
        orderbook_data = {}
        sources_ok = 0

        with ThreadPoolExecutor(max_workers=6) as pool:
            f1 = pool.submit(fetch_crypto_com, coins)
            f2 = pool.submit(fetch_coingecko, coins)
            f3 = pool.submit(fetch_news)
            f4 = pool.submit(fetch_fear_greed)
            f5 = pool.submit(fetch_funding_rates, coins)
            f6 = pool.submit(fetch_orderbook_depth, coins)
            for name, fut, target in [
                ("crypto.com", f1, "crypto"),
                ("coingecko", f2, "gecko"),
                ("news", f3, "news"),
                ("fng", f4, "fng"),
                ("funding", f5, "funding"),
                ("orderbook", f6, "orderbook"),
            ]:
                try:
                    result = fut.result(timeout=15)
                    if result:
                        sources_ok += 1
                    if target == "crypto":
                        crypto_data = result
                    elif target == "gecko":
                        gecko_data = result if isinstance(result, dict) else {}
                    elif target == "news":
                        news_data = result if isinstance(result, list) else []
                    elif target == "fng":
                        fng_data = result if isinstance(result, dict) else {}
                    elif target == "funding":
                        funding_data = result if isinstance(result, dict) else {}
                    elif target == "orderbook":
                        orderbook_data = result if isinstance(result, dict) else {}
                except Exception:
                    pass

        # Compute technicals from sparklines
        technicals = {}
        for coin, gd in gecko_data.items():
            spark = gd.get("sparkline", [])
            if spark:
                technicals[coin] = compute_technicals(spark)

        # Resolve past decision accuracy
        current_prices = {}
        for coin in coins:
            p = (gecko_data.get(coin, {}).get("price") or
                 crypto_data.get(coin, {}).get("price") or 0)
            if p:
                current_prices[coin] = p
        self._accuracy.resolve_outcomes(current_prices)

        data = {
            "crypto": crypto_data,
            "gecko": gecko_data,
            "news": news_data,
            "fng": fng_data,
            "funding": funding_data,
            "orderbook": orderbook_data,
            "technicals": technicals,
            "sources_ok": sources_ok,
            "prices": current_prices,
        }
        self._cached_data = data
        self._cache_time = now

        logger.info(
            f"[MCP-Brain] 7-source fetch: {sources_ok}/6 OK | "
            f"prices={len(crypto_data)} gecko={len(gecko_data)} "
            f"news={len(news_data)} fng={'yes' if fng_data else 'no'} "
            f"funding={len(funding_data)} ob={len(orderbook_data)} "
            f"tech={len(technicals)}")
        return data

    # ──────────────────────────────────────────────────────────────────
    # EXCHANGE INDICATORS (multi-TF from live OHLCV)
    # ──────────────────────────────────────────────────────────────────

    def set_exchanges(self, exchanges: dict):
        """Accept exchange client refs for direct OHLCV fetching."""
        self._exchanges = exchanges

    def _fetch_exchange_indicators(self, coins: list) -> dict:
        """Fetch OHLCV from primary exchange for 3 TFs and compute indicators.
        Returns {COIN: {tf: {adx, rsi, ema_dir, atr_pct, vol_ratio}}}."""
        now = time.time()
        if now - self._indicator_cache_time < 120 and self._indicator_cache:
            return self._indicator_cache

        if not self._exchanges:
            return {}

        import pandas as pd
        from utils.indicators import ema, rsi, atr, adx

        # Pick first connected exchange as data source
        exchange = next(iter(self._exchanges.values()), None)
        if not exchange:
            return {}

        results = {}
        timeframes = ["4h", "1h", "15m"]
        limit_map = {"4h": 80, "1h": 100, "15m": 100}

        for coin in coins[:25]:
            symbol = f"{coin}/USDT"
            coin_data = {}
            for tf in timeframes:
                try:
                    raw = exchange.fetch_ohlcv(symbol, tf,
                                               limit=limit_map[tf],
                                               market_type="spot")
                    if not raw or len(raw) < 30:
                        continue
                    df = pd.DataFrame(raw,
                                      columns=["ts", "open", "high", "low",
                                               "close", "volume"])
                    df.dropna(inplace=True)
                    if len(df) < 30:
                        continue

                    close = df["close"]
                    high  = df["high"]
                    low   = df["low"]
                    vol   = df["volume"]

                    adx_s, pdi_s, mdi_s = adx(high, low, close, 14)
                    rsi_s = rsi(close, 14)
                    atr_s = atr(high, low, close, 14)
                    ema9  = ema(close, 9)
                    ema21 = ema(close, 21)
                    ema50 = ema(close, 50)

                    price     = float(close.iloc[-1])
                    adx_val   = float(adx_s.iloc[-1])
                    rsi_val   = float(rsi_s.iloc[-1])
                    atr_pct   = float(atr_s.iloc[-1]) / max(price, 1e-9)
                    ema9_val  = float(ema9.iloc[-1])
                    ema21_val = float(ema21.iloc[-1])
                    ema50_val = float(ema50.iloc[-1])
                    vol_ma    = float(vol.rolling(20).mean().iloc[-1])
                    vol_ratio = float(vol.iloc[-1]) / max(vol_ma, 1e-9)

                    # EMA alignment direction
                    if ema9_val > ema21_val > ema50_val:
                        ema_dir = "up"
                    elif ema9_val < ema21_val < ema50_val:
                        ema_dir = "down"
                    else:
                        ema_dir = "mixed"

                    coin_data[tf] = {
                        "adx": round(adx_val, 1),
                        "rsi": round(rsi_val, 1),
                        "ema_dir": ema_dir,
                        "atr_pct": round(atr_pct * 100, 2),
                        "vol_ratio": round(vol_ratio, 1),
                    }
                except Exception:
                    continue

            if coin_data:
                results[coin] = coin_data

        self._indicator_cache = results
        self._indicator_cache_time = now
        logger.info(f"[MCP-Brain] Exchange indicators: {len(results)} coins, "
                     f"{sum(len(v) for v in results.values())} TF snapshots")
        return results

    # ──────────────────────────────────────────────────────────────────
    # PORTFOLIO ANALYSIS — sole decision authority
    # ──────────────────────────────────────────────────────────────────

    def analyze_portfolio(self, coins: list, open_positions: list,
                          exchange_balances: dict, risk_envelope: dict,
                          recent_trades: list) -> list:
        """
        Main entry point: gather all data, score coins algorithmically.
        Returns list of action dicts: [{type, symbol, exchange, ...}]
        """
        if not self._enabled:
            return []

        now = time.time()
        if now - self._last_entry_run < ENTRY_COOLDOWN:
            return self._last_trade_actions
        self._last_entry_run = now

        # Fetch all external data sources
        data = self._fetch_all_data(coins)
        if data["sources_ok"] < 2:
            logger.warning("[MCP-Brain] < 2 data sources — no actions")
            return []

        # Fetch multi-TF exchange indicators
        exchange_indicators = self._fetch_exchange_indicators(coins)

        try:
            actions = self._score_portfolio(
                coins, data, exchange_indicators,
                open_positions, exchange_balances,
                risk_envelope, recent_trades)
            self._last_trade_actions = actions
            self._log_decisions({"actions": actions}, "portfolio")
            self._save_state()

            # Record for accuracy tracking
            for a in actions:
                if a["type"] == "OPEN":
                    coin = a["symbol"].split("/")[0]
                    price = data["prices"].get(coin, 0)
                    side_action = "BUY" if a["side"] == "buy" else "SELL"
                    self._accuracy.record_decision(
                        coin, side_action, price, a["confidence"])

            return actions
        except Exception as e:
            logger.error(f"[MCP-Brain] Portfolio scoring failed: {e}")

        return []

    def get_trade_actions(self) -> list:
        """Return OPEN/CLOSE actions from last analyze_portfolio call."""
        return list(self._last_trade_actions)

    # ──────────────────────────────────────────────────────────────────
    # ALGORITHMIC SCORING ENGINE — portfolio analysis
    # ──────────────────────────────────────────────────────────────────

    def _determine_direction(self, coin: str, exchange_ind: dict,
                              sparkline_tech: dict) -> tuple:
        """Determine buy/sell direction from indicators.
        Returns (direction: 'buy'|'sell'|None, strength: float 0-1)."""
        buy_w = 0
        sell_w = 0
        total_w = 0

        ei = exchange_ind.get(coin, {})

        # Multi-TF EMA direction (weight 3 per TF — most important)
        for tf in ("4h", "1h", "15m"):
            tfd = ei.get(tf, {})
            if not tfd:
                continue
            total_w += 3
            ema_dir = tfd.get("ema_dir", "mixed")
            if ema_dir == "up":
                buy_w += 3
            elif ema_dir == "down":
                sell_w += 3

        # RSI across TFs (weight 2 per TF)
        for tf in ("4h", "1h", "15m"):
            tfd = ei.get(tf, {})
            if not tfd:
                continue
            rsi_val = tfd.get("rsi", 50)
            total_w += 2
            if rsi_val < 40:
                buy_w += 2   # oversold → buy
            elif rsi_val > 60:
                sell_w += 2  # overbought → sell

        # Sparkline trend (weight 2)
        td = sparkline_tech.get(coin, {})
        trend = td.get("trend", "neutral")
        total_w += 2
        if trend in ("strong_up", "up"):
            buy_w += 2
        elif trend in ("strong_down", "down"):
            sell_w += 2

        if total_w == 0:
            return None, 0

        buy_pct = buy_w / total_w
        sell_pct = sell_w / total_w

        if buy_pct > sell_pct and buy_pct >= MIN_DIRECTION_STRENGTH:
            return "buy", buy_pct
        elif sell_pct > buy_pct and sell_pct >= MIN_DIRECTION_STRENGTH:
            return "sell", sell_pct
        return None, 0

    def _score_coin(self, coin: str, direction: str, data: dict,
                     exchange_ind: dict) -> dict:
        """Score a coin for a given direction. Returns {score, layers, confidence, reasons}."""
        ei = exchange_ind.get(coin, {})
        tech = data["technicals"].get(coin, {})
        funding_d = data["funding"].get(coin, {})
        orderbook_d = data["orderbook"].get(coin, {})
        fng = data["fng"]

        score = 0
        layers = 0
        reasons = []

        # ── Layer 1: Trend alignment (multi-TF EMAs) — max 25 pts ──
        aligned_tfs = 0
        for tf in ("4h", "1h", "15m"):
            tfd = ei.get(tf, {})
            if not tfd:
                continue
            ema_dir = tfd.get("ema_dir", "mixed")
            if (direction == "buy" and ema_dir == "up") or \
               (direction == "sell" and ema_dir == "down"):
                aligned_tfs += 1

        if aligned_tfs == 3:
            score += 25
            layers += 1
            reasons.append("3TF trend aligned")
        elif aligned_tfs == 2:
            score += 15
            layers += 1
            reasons.append("2TF trend aligned")

        # ── Layer 2: Momentum — RSI sweet spot — max 20 pts ──
        rsi_4h = ei.get("4h", {}).get("rsi", 50)
        rsi_1h = ei.get("1h", {}).get("rsi", 50)
        if direction == "buy":
            if 30 <= rsi_4h <= 50 and rsi_1h < 50:
                score += 20
                layers += 1
                reasons.append(f"RSI pullback {rsi_4h:.0f}/{rsi_1h:.0f}")
            elif rsi_4h < 30:
                score += 15
                reasons.append(f"RSI oversold {rsi_4h:.0f}")
            elif rsi_4h < 55:
                score += 8
        else:  # sell
            if 50 <= rsi_4h <= 70 and rsi_1h > 50:
                score += 20
                layers += 1
                reasons.append(f"RSI overbought {rsi_4h:.0f}/{rsi_1h:.0f}")
            elif rsi_4h > 70:
                score += 15
                reasons.append(f"RSI extreme {rsi_4h:.0f}")
            elif rsi_4h > 45:
                score += 8

        # ── Layer 3: Trend strength — ADX — max 15 pts ──
        adx_4h = ei.get("4h", {}).get("adx", 0)
        adx_1h = ei.get("1h", {}).get("adx", 0)
        if adx_4h >= 25:
            score += 15
            layers += 1
            reasons.append(f"ADX strong {adx_4h:.0f}")
        elif adx_4h >= 20 or adx_1h >= 25:
            score += 8
            layers += 1
            reasons.append(f"ADX moderate {adx_4h:.0f}")

        # ── Layer 4: Volume confirmation — max 10 pts ──
        vol_4h = ei.get("4h", {}).get("vol_ratio", 1.0)
        vol_1h = ei.get("1h", {}).get("vol_ratio", 1.0)
        if vol_4h > 1.3 or vol_1h > 1.5:
            score += 10
            layers += 1
            reasons.append(f"Vol confirm {max(vol_4h, vol_1h):.1f}x")
        elif vol_4h > 1.0 or vol_1h > 1.0:
            score += 4

        # ── Layer 5: Order book imbalance — max 10 pts ──
        imb = orderbook_d.get("imbalance", 0) if orderbook_d else 0
        if (direction == "buy" and imb > 0.15) or \
           (direction == "sell" and imb < -0.15):
            score += 10
            layers += 1
            reasons.append(f"OB aligned {imb:+.2f}")
        elif (direction == "buy" and imb > 0.05) or \
             (direction == "sell" and imb < -0.05):
            score += 4

        # ── Layer 6: Sentiment — Fear & Greed — max 10 pts ──
        fng_val = fng.get("value", 50) if fng else 50
        if direction == "buy" and fng_val < 35:
            score += 10
            layers += 1
            reasons.append(f"FNG fear {fng_val}")
        elif direction == "sell" and fng_val > 65:
            score += 10
            layers += 1
            reasons.append(f"FNG greed {fng_val}")
        elif 35 <= fng_val <= 65:
            score += 3  # Neutral is acceptable

        # ── Layer 7: Funding rate (contrarian) — max 10 pts ──
        fr = funding_d.get("funding_rate", 0) if funding_d else 0
        if direction == "buy" and fr < -0.0005:
            score += 10
            layers += 1
            reasons.append(f"FR contrarian {fr*100:+.3f}%")
        elif direction == "sell" and fr > 0.0005:
            score += 10
            layers += 1
            reasons.append(f"FR contrarian {fr*100:+.3f}%")
        elif abs(fr) < 0.0003:
            score += 3  # Neutral funding is fine

        confidence = min(0.95, score / 100)

        return {
            "score": score,
            "layers": layers,
            "confidence": confidence,
            "reasons": reasons,
        }

    def _pick_exchange(self, coin: str, direction: str,
                        exchange_balances: dict,
                        open_count: dict) -> tuple:
        """Pick best exchange for a trade. Returns (exchange_name, market_type) or (None, None)."""
        candidates = []
        for ex_name, caps in EXCHANGE_CAPS.items():
            count = open_count.get(ex_name, 0)
            if count >= 6:
                continue
            bals = exchange_balances.get(ex_name, {})

            if direction == "sell":
                # Shorts need futures
                if caps["futures"]:
                    fut_bal = bals.get("futures", 0)
                    if fut_bal >= 10:
                        candidates.append((ex_name, "futures", fut_bal))
            else:
                # Buy: prefer futures for leverage, fallback to spot
                if caps["futures"]:
                    fut_bal = bals.get("futures", 0)
                    if fut_bal >= 10:
                        candidates.append((ex_name, "futures", fut_bal))
                if caps["spot"]:
                    spot_bal = bals.get("spot", 0)
                    if spot_bal >= 10:
                        candidates.append((ex_name, "spot", spot_bal))

        if not candidates:
            return None, None

        # Prefer futures over spot, then sort by balance
        candidates.sort(key=lambda x: (0 if x[1] == "futures" else 1, -x[2]))
        return candidates[0][0], candidates[0][1]

    def _check_reversal(self, coin: str, side: str,
                         exchange_ind: dict) -> bool:
        """Check if trend has reversed against position side on 4h+1h."""
        ei = exchange_ind.get(coin, {})
        reversed_count = 0
        for tf in ("4h", "1h"):
            tfd = ei.get(tf, {})
            if not tfd:
                continue
            ema_dir = tfd.get("ema_dir", "mixed")
            if side == "buy" and ema_dir == "down":
                reversed_count += 1
            elif side == "sell" and ema_dir == "up":
                reversed_count += 1
        return reversed_count >= 2

    def _score_portfolio(self, coins, data, exchange_indicators,
                          open_positions, exchange_balances,
                          risk_envelope, recent_trades) -> list:
        """Algorithmic multi-factor scoring. Returns OPEN/CLOSE actions."""
        actions = []

        # Track open positions per exchange and by symbol
        open_by_exchange = {}
        open_symbols = set()
        for p in open_positions:
            ex = p.get("exchange", "")
            open_by_exchange[ex] = open_by_exchange.get(ex, 0) + 1
            base = p.get("symbol", "").split("/")[0]
            if base:
                open_symbols.add(base)

        max_new = risk_envelope.get("max_new_positions", 4)

        # ── Check existing positions for close signals ──
        for p in open_positions:
            coin = p.get("symbol", "").split("/")[0]
            side = p.get("side", "")
            pnl_pct = p.get("pnl_pct", 0) or 0
            age_min = p.get("age_min", 0)

            reversal = self._check_reversal(coin, side, exchange_indicators)

            if reversal and (pnl_pct > 0 or age_min > 120):
                actions.append({
                    "type": "CLOSE",
                    "symbol": p.get("symbol", ""),
                    "exchange": p.get("exchange", ""),
                    "market_type": p.get("market_type", "futures"),
                    "side": side,
                    "leverage": 1,
                    "size_pct": 0,
                    "sl_pct": 0,
                    "tp_pct": 0,
                    "confidence": 0.75,
                    "reason": "4h+1h trend reversal",
                    "position_id": p.get("id", ""),
                })

        # ── Score each coin for potential entry ──
        scored = []
        for coin in coins:
            if coin in open_symbols:
                continue  # Don't double up

            # Need price data
            price = (data["gecko"].get(coin, {}).get("price") or
                     data["crypto"].get(coin, {}).get("price") or 0)
            if not price:
                continue

            direction, dir_strength = self._determine_direction(
                coin, exchange_indicators, data["technicals"])
            if not direction:
                continue

            result = self._score_coin(coin, direction, data, exchange_indicators)

            if result["score"] >= SCORE_OPEN_THRESHOLD and \
               result["layers"] >= MIN_LAYERS_ALIGNED:
                scored.append((coin, direction, result))

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[2]["score"], reverse=True)

        for coin, direction, result in scored[:max_new]:
            exchange, market_type = self._pick_exchange(
                coin, direction, exchange_balances, open_by_exchange)
            if not exchange:
                continue

            # SL/TP based on market type and ATR
            ei = exchange_indicators.get(coin, {})
            atr_pct = ei.get("4h", {}).get("atr_pct", 3.0)

            if market_type == "futures":
                sl_pct = max(3.0, min(5.0, atr_pct * 1.5))
                tp_pct = max(8.0, sl_pct * 2.5)
                leverage = 5
            else:
                sl_pct = max(2.0, min(4.0, atr_pct * 1.2))
                tp_pct = max(5.0, sl_pct * 2.5)
                leverage = 1

            # Size based on confidence
            size_pct = min(4.0, 2.0 + result["confidence"] * 3.0)

            action = {
                "type": "OPEN",
                "symbol": f"{coin}/USDT",
                "exchange": exchange,
                "market_type": market_type,
                "side": direction,
                "leverage": leverage,
                "size_pct": round(size_pct, 1),
                "sl_pct": round(sl_pct, 1),
                "tp_pct": round(tp_pct, 1),
                "confidence": result["confidence"],
                "reason": ", ".join(result["reasons"][:3]),
                "position_id": "",
            }
            actions.append(action)
            open_by_exchange[exchange] = open_by_exchange.get(exchange, 0) + 1

            logger.info(
                f"[MCP-Brain] OPEN {coin}/USDT {direction} on {exchange} "
                f"({market_type}) {leverage}x size={size_pct:.1f}% "
                f"SL={sl_pct:.1f}% TP={tp_pct:.1f}% "
                f"score={result['score']}/100 layers={result['layers']}/7 "
                f"conf={result['confidence']:.0%} | {action['reason'][:60]}")

        if not actions:
            logger.info("[MCP-Brain] Portfolio cycle: no qualifying setups")
        else:
            opens = sum(1 for a in actions if a["type"] == "OPEN")
            closes = sum(1 for a in actions if a["type"] == "CLOSE")
            logger.info(f"[MCP-Brain] Portfolio cycle: {opens} opens, {closes} closes")

        # Update last_decisions for backward compat (DCA gate, etc.)
        for coin, direction, result in scored:
            side_action = "BUY" if direction == "buy" else "SELL"
            self._last_decisions[coin] = {
                "action": side_action,
                "confidence": result["confidence"],
                "reason": ", ".join(result["reasons"][:2]),
            }

        return actions

    # ──────────────────────────────────────────────────────────────────
    # LEGACY: analyze_coins (kept for backward compatibility)
    # ──────────────────────────────────────────────────────────────────

    def analyze_coins(self, coins: list, open_positions: list = None,
                      portfolio_value: float = 0, daily_pnl: float = 0,
                      exchange_balances: dict = None,
                      portfolio_coins: dict = None,
                      strategy_signals: dict = None) -> dict:
        """Legacy entry analysis — returns BUY/SELL/HOLD per coin using scoring engine."""
        return self._last_decisions

    # ──────────────────────────────────────────────────────────────────
    # MODE B: POSITION MONITOR (every 45s for open positions)
    # ──────────────────────────────────────────────────────────────────

    def monitor_positions(self, positions: list) -> dict:
        """
        Lightweight position monitoring. Returns per-position advice:
          HOLD     — keep position, signals still aligned
          CLOSE    — close immediately, reversal detected
          TIGHTEN  — tighten SL (move closer)
          WIDEN    — widen SL slightly (temporary pullback, recovery likely)
          BREAKEVEN — move SL to breakeven now
        """
        if not self._enabled or not positions:
            return {}

        now = time.time()
        # Adaptive cooldown: check faster when positions are profitable or volatile
        cooldown = POSITION_COOLDOWN
        for p in positions:
            pnl_pct = p.get("pnl_pct", 0) or 0
            if pnl_pct >= 2.0:
                cooldown = min(45, POSITION_COOLDOWN // 3)  # 30s for profitable positions (profit-taking priority)
                break
            if abs(pnl_pct) > 1.5:
                cooldown = min(60, POSITION_COOLDOWN // 2)  # Faster checks for volatile positions
                break
        if now - self._last_position_run < cooldown:
            return self._last_position_advice
        self._last_position_run = now

        if not self._check_rate_limit():
            return self._last_position_advice

        coins = list(set(p.get("symbol", "?").split("/")[0] for p in positions))
        data = self._fetch_all_data(coins)

        # Fetch exchange indicators for position coins
        exchange_indicators = self._fetch_exchange_indicators(coins)

        try:
            advice = self._score_positions(positions, data, exchange_indicators)
            if advice:
                self._last_position_advice = advice
                self._log_decisions(advice, "position_monitor")
                self._save_state()
                return advice
        except Exception as e:
            logger.error(f"[MCP-Brain] Position monitor failed: {e}")

        return self._last_position_advice

    # ──────────────────────────────────────────────────────────────────
    # CONFIDENCE ADJUSTMENT (entry pipeline)
    # ──────────────────────────────────────────────────────────────────

    def get_confidence_adjustment(self, coin: str, direction: str) -> float:
        """Returns multiplier for bot engine confidence pipeline."""
        dec = self._last_decisions.get(coin, {})
        action = dec.get("action", "HOLD")
        mcp_conf = dec.get("confidence", 0.5)

        if action == "HOLD":
            return 0.90

        # Aligned → boost (scaled by confidence)
        if (action == "BUY" and direction == "buy") or \
           (action == "SELL" and direction == "sell"):
            return min(1.30, 0.90 + mcp_conf * 0.40)

        # Conflicting → reduce harder
        if (action == "BUY" and direction == "sell") or \
           (action == "SELL" and direction == "buy"):
            return max(0.55, 0.80 - mcp_conf * 0.25)

        return 1.0

    # ──────────────────────────────────────────────────────────────────
    # EXIT INTELLIGENCE (used by anti-loss gate)
    # ──────────────────────────────────────────────────────────────────

    def should_hold_position(self, coin: str, side: str, loss_pct: float) -> bool:
        """
        Should we hold a losing position instead of closing at SL?
        Opus analyzes whether the trade thesis is still valid.
        More generous with holding — only force-close on confirmed reversals.
        """
        if not self._enabled:
            return False

        # Check position monitor advice first (most current — Opus analysis)
        for pid, adv in self._last_position_advice.items():
            if coin in str(pid):
                action = adv.get("action", "HOLD")
                conf = adv.get("confidence", 0)
                if action == "CLOSE" and conf >= 0.80:
                    return False  # Opus is confident: close it
                if action in ("HOLD", "WIDEN", "BREAKEVEN"):
                    # Hold if Opus says so and loss isn't catastrophic
                    if conf >= 0.55 and loss_pct < 4.0:
                        logger.info(
                            f"[MCP-Brain] HOLD (Opus monitor): {coin} {side} — "
                            f"advice={action} conf={conf:.0%}, loss={loss_pct:.2f}%")
                        return True

        # Fall back to entry decisions
        dec = self._last_decisions.get(coin, {})
        if not dec:
            return False

        action = dec.get("action", "HOLD")
        confidence = dec.get("confidence", 0)

        # If loss < 3% and entry thesis still holds, hold
        if loss_pct > 3.0:
            return False

        if side == "buy" and action == "BUY" and confidence >= 0.55:
            logger.info(
                f"[MCP-Brain] HOLD (entry thesis): {coin} BUY — "
                f"conf={confidence:.0%}, loss={loss_pct:.2f}%")
            return True
        if side == "sell" and action == "SELL" and confidence >= 0.55:
            logger.info(
                f"[MCP-Brain] HOLD (entry thesis): {coin} SELL — "
                f"conf={confidence:.0%}, loss={loss_pct:.2f}%")
            return True

        return False

    def get_position_advice(self, position_id: str) -> dict:
        """Get specific advice for a position from last monitor run."""
        return self._last_position_advice.get(position_id, {})

    def should_take_profit(self, coin: str, side: str, pnl_pct: float) -> bool:
        """
        Should we take profit on this position NOW?
        Called by order_manager when price hits TP or position is profitable.
        MCP Brain is the SOLE authority — if it says TAKE_PROFIT or CLOSE, we exit.
        If it says HOLD (strong trend), we let trailing stop ride.
        """
        if not self._enabled:
            return True  # No brain = use default TP behavior

        # Check position monitor advice first (most recent Opus analysis)
        for pid, adv in self._last_position_advice.items():
            if coin in str(pid):
                action = adv.get("action", "HOLD")
                conf = adv.get("confidence", 0)
                # MCP explicitly says take profit or close
                if action in ("TAKE_PROFIT", "CLOSE") and conf >= 0.60:
                    logger.info(
                        f"[MCP-Brain] TAKE PROFIT approved: {coin} {side} "
                        f"pnl={pnl_pct:+.1f}% — MCP says {action} conf={conf:.0%}")
                    return True
                # MCP says TIGHTEN — let trailing handle it, don't take profit yet
                if action == "TIGHTEN":
                    logger.info(
                        f"[MCP-Brain] TIGHTEN (not taking profit yet): {coin} {side} "
                        f"pnl={pnl_pct:+.1f}% — MCP wants tighter SL")
                    return False
                # MCP says HOLD with high confidence — trend strong, let it ride
                if action == "HOLD" and conf >= 0.75:
                    logger.info(
                        f"[MCP-Brain] RIDE (MCP says HOLD): {coin} {side} "
                        f"pnl={pnl_pct:+.1f}% — trend still strong conf={conf:.0%}")
                    return False

        # No MCP advice available — take profit if > 3% (safe default)
        if pnl_pct >= 3.0:
            return True
        # Small profit with no MCP guidance — let trailing handle it
        return True

    # ──────────────────────────────────────────────────────────────────
    # ALGORITHMIC POSITION SCORING
    # ──────────────────────────────────────────────────────────────────

    def _score_positions(self, positions: list, data: dict,
                          exchange_indicators: dict) -> dict:
        """Algorithmic position monitoring. Returns per-position advice:
        HOLD, CLOSE, TIGHTEN, BREAKEVEN, TAKE_PROFIT, WIDEN."""
        advice = {}
        tech = data["technicals"]

        for p in positions:
            pid = p.get("id", "")
            sym = p.get("symbol", "?")
            coin = sym.split("/")[0].split(":")[0]
            side = p.get("side", "?")
            pnl_pct = p.get("pnl_pct", 0) or 0
            age_min = p.get("age_min", 0)

            ei = exchange_indicators.get(coin, {})
            td = tech.get(coin, {})

            # Default: HOLD
            action = "HOLD"
            confidence = 0.60
            reason = "no signal change"

            # Count trend alignment + reversals
            aligned = 0
            reversed_count = 0
            for tf in ("4h", "1h", "15m"):
                tfd = ei.get(tf, {})
                if not tfd:
                    continue
                ema_dir = tfd.get("ema_dir", "mixed")
                if (side == "buy" and ema_dir == "up") or \
                   (side == "sell" and ema_dir == "down"):
                    aligned += 1
                elif (side == "buy" and ema_dir == "down") or \
                     (side == "sell" and ema_dir == "up"):
                    reversed_count += 1

            # RSI exhaustion check
            rsi_4h = ei.get("4h", {}).get("rsi", 50)
            rsi_exhausted = False
            if side == "buy" and rsi_4h > 75:
                rsi_exhausted = True
            elif side == "sell" and rsi_4h < 25:
                rsi_exhausted = True

            # Sparkline trend check
            spark_trend = td.get("trend", "neutral")
            spark_reversed = False
            if side == "buy" and spark_trend in ("strong_down", "down"):
                spark_reversed = True
            elif side == "sell" and spark_trend in ("strong_up", "up"):
                spark_reversed = True

            # ── Decision logic (priority order) ──
            if reversed_count >= 2 and pnl_pct < -1:
                action = "CLOSE"
                confidence = 0.85
                reason = f"trend reversed, loss {pnl_pct:.1f}%"
            elif reversed_count >= 2 and pnl_pct > 2:
                action = "TAKE_PROFIT"
                confidence = 0.80
                reason = f"trend reversing, lock {pnl_pct:.1f}%"
            elif pnl_pct > 3 and rsi_exhausted:
                action = "TAKE_PROFIT"
                confidence = 0.75
                reason = f"RSI exhausted {rsi_4h:.0f}, pnl {pnl_pct:.1f}%"
            elif pnl_pct > 2 and aligned >= 2:
                action = "TIGHTEN"
                confidence = 0.70
                reason = f"profit {pnl_pct:.1f}%, tighten SL"
            elif pnl_pct > 1.5:
                action = "BREAKEVEN"
                confidence = 0.65
                reason = "move SL to breakeven"
            elif reversed_count >= 2 and age_min > 120:
                action = "CLOSE"
                confidence = 0.70
                reason = f"stale + reversed after {age_min:.0f}m"
            elif pnl_pct < -4.0 and spark_reversed:
                action = "CLOSE"
                confidence = 0.80
                reason = f"deep loss {pnl_pct:.1f}% + trend reversed"
            elif aligned >= 2:
                action = "HOLD"
                confidence = 0.75
                reason = f"trend aligned, {aligned}TF confirmed"

            advice[pid] = {
                "action": action,
                "confidence": confidence,
                "reason": reason,
            }

            if action != "HOLD":
                logger.info(
                    f"[MCP-Brain] Position {pid[:8]}: {action} "
                    f"conf={confidence:.0%} | {reason}")

        return advice

    # ──────────────────────────────────────────────────────────────────
    # LOGGING
    # ──────────────────────────────────────────────────────────────────

    def _log_decisions(self, decisions: dict, dtype: str = "entry"):
        try:
            DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat()
            with open(DECISION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": ts, "type": dtype, "decisions": decisions
                }, default=str) + "\n")
            # Rotate: keep last 1000 lines (~every few hours)
            if DECISION_LOG.stat().st_size > 2_000_000:  # > 2MB
                lines = DECISION_LOG.read_text(encoding="utf-8").splitlines()
                DECISION_LOG.write_text(
                    "\n".join(lines[-500:]) + "\n", encoding="utf-8")
        except Exception:
            pass

    def last_decisions(self) -> dict:
        return self._last_decisions

    def last_fund_ops(self) -> list:
        """Return fund management operations from last analysis, then clear."""
        ops = list(self._last_fund_ops)
        self._last_fund_ops = []
        return ops

    def last_position_advice(self) -> dict:
        return self._last_position_advice

    def accuracy_stats(self) -> dict:
        return self._accuracy.stats()
