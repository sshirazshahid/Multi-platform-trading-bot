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

10-Layer Scoring Framework:
  Trend(25) | Momentum(20) | Strength(15) | Volume(10) | OrderBook(10) | Sentiment(10) | Funding(10)
  + Smart Money: SMC Structure(10) | SMC Entry(10) | SMC Confirmation(8)
  Smart Money integrates: FVG, Fibonacci, Supply/Demand, Liquidity Sweeps,
  Order Blocks, BOS/ChoCH, Volume Profile, ICT OTE, Price Action patterns.
  Requires score >= 65 AND 4+/10 layers for entry. Asymmetric R:R 2.0:1 minimum.

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
from utils.claude_client import call_claude_cli, _check_claude_code
import utils.claude_client as _claude_mod

try:
    from core.warehouse import get_warehouse
except ImportError:
    get_warehouse = None

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
# 2026-04-11: dropped ENTRY_COOLDOWN 900→300 to match config.py
# CLAUDE_PORTFOLIO.scan_interval_min=5. The old 15-min cooldown was
# silently overriding the engine's scan cadence — the engine would
# re-enter every 5 min but receive the SAME cached actions, meaning
# fresh market moves weren't re-evaluated for 15 min.
ENTRY_COOLDOWN    = 270   # ~4.5 min — just under the 5-min engine cycle so
                          # tiny scheduler drift never pushes the cooldown
                          # past the next call. Must stay < scan_interval_min*60.
POSITION_COOLDOWN = 90    # 90s between position monitor checks
DATA_CACHE_TTL    = 120   # Cache raw data for 2 min

# ── Claude CLI config ───────────────────────────────────────────────
MODEL_ENTRY    = "sonnet"   # Portfolio analysis model
MODEL_MONITOR  = "haiku"    # Position monitor — fast model for simple classification
MAX_API_CALLS_HR = 25       # Rate budget: 25 calls/hour max

# ══════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS — the core intelligence of the trading system
# ══════════════════════════════════════════════════════════════════════

_PORTFOLIO_SYSTEM_PROMPT = (
    "Crypto portfolio manager. 3 exchanges, real money. Maximize risk-adjusted returns.\n"
    "RULES: Min 1.8:1 RR. 4h=direction,1h=confirm,15m=entry. "
    "RSI>78/<22 on 4h=reversal risk. Skip only on clear conflicting signals.\n"
    "EXCHANGES: Binance/Bybit/Bitget=spot+futures.\n"
    "DISTRIBUTE trades across exchanges — use whichever has the best balance/liquidity. "
    "DO NOT default to one exchange. Check BAL line and spread positions.\n"
    "LIMITS: Spot:SL>=2%,TP>=4%,side=buy. Futures:SL>=2.5%,TP>=5%,max 3x. Max 6/exchange,8 total,5% max size.\n"
    "ENTRY: Prefer 4h EMA aligned + 1h RSI confirming. Volume preferred but OPTIONAL "
    "in low-F&G/quiet regimes — trend+RSI alone can qualify. Do NOT hard-require vol>1x.\n"
    "SIZING: size_pct = 1.5% risk / (sl_pct * leverage / 100). Cap at 1%. Max leverage 3x. Example: sl=3%, lev=3x → size=1%.\n"
    "SL/TP: SL=max(2%,1.5*ATR_1h%). TP=2*SL. Min 2:1 R:R always.\n"
    "IMPORTANT: Empty actions=do nothing, but if ANY coin has score>=60 with trend+momentum "
    "aligned, PROPOSE it even in low-volume sessions. Bot idle >12h is worse than cautious loss.\n"
    "RESPOND ONLY WITH COMPACT JSON, no markdown.\n"
    '{"actions":[{"type":"OPEN","symbol":"SOL/USDT","exchange":"bitget",'
    '"market_type":"futures","side":"buy","leverage":5,"size_pct":4.0,'
    '"sl_pct":3.5,"tp_pct":8.0,"confidence":0.72,'
    '"reason":"3TF aligned","position_id":""}],'
    '"market_assessment":"one sentence","skip_reason":""}\n'
    "Keep reasons under 10 words."
)

_POSITION_MONITOR_SYSTEM_PROMPT = (
    "Position risk manager. Classify each position. Cut losers, ride winners.\n"
    "ACTIONS: HOLD|CLOSE|TAKE_PROFIT|TIGHTEN|BREAKEVEN\n"
    "NOTE: pnl shown is LEVERAGED (already multiplied by leverage). "
    "SL is ~1.5-3.5% price move = ~4.5-10.5% leveraged at 3x. "
    "Do NOT close on small leveraged losses — let SL do its job.\n"
    "pnl>3%+exhausted=TAKE_PROFIT. pnl>2%+trend=TIGHTEN. pnl>1.5%=BREAKEVEN. "
    "pnl>5%+strong=HOLD. 4h+1h reversed+loss>3%=CLOSE. loss>12%=CLOSE.\n"
    "RESPOND WITH COMPACT JSON ONLY. No markdown, no whitespace, no newlines. "
    "Reasons max 5 words.\n"
    '{"positions":{"id":{"action":"HOLD","confidence":0.8,"reason":"trend ok"}}}\n'
    "Every position must appear. Default HOLD."
)



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

    # MACD (12, 26, 9) — compute full series for proper signal line
    macd_line = macd_signal = macd_hist = 0
    if len(prices) >= 35:  # Need at least 26+9 for meaningful signal
        # Build full MACD line series
        macd_series = []
        k12 = 2 / (12 + 1)
        k26 = 2 / (26 + 1)
        # Compute EMA-12 and EMA-26, advance together from index 26 onward
        ema12_s = sum(prices[:12]) / 12
        ema26_s = sum(prices[:26]) / 26
        # Advance EMA-12 over prices[12..25] to catch up to index 26
        for i in range(12, 26):
            ema12_s = prices[i] * k12 + ema12_s * (1 - k12)
        # Now both EMAs are seeded at index 25; advance together from 26
        for i in range(26, len(prices)):
            ema12_s = prices[i] * k12 + ema12_s * (1 - k12)
            ema26_s = prices[i] * k26 + ema26_s * (1 - k26)
            macd_series.append(ema12_s - ema26_s)
        macd_line = macd_series[-1] if macd_series else 0
        # Signal line = 9-period EMA of MACD series
        if len(macd_series) >= 9:
            k9 = 2 / (9 + 1)
            sig = sum(macd_series[:9]) / 9
            for v in macd_series[9:]:
                sig = v * k9 + sig * (1 - k9)
            macd_signal = sig
        macd_hist = macd_line - macd_signal
    elif len(prices) >= 26:
        ema_12 = _calc_ema(prices, 12)
        ema_26 = _calc_ema(prices, 26)
        macd_line = ema_12 - ema_26

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
    """RSI using Wilder's smoothed moving average (industry standard)."""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    # Seed with SMA for the first period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder's exponential smoothing for remaining values
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
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

        # Claude availability: CLI only (Opus 4.7), algorithmic fallback
        self._cli_available = _check_claude_code()
        self._claude_available = self._cli_available
        # ALWAYS enabled — algorithmic scoring works without Claude
        self._enabled = True
        self._api_calls_this_hour = 0
        self._hour_start = time.time()
        # Claude failure cooldown: after N consecutive failures, skip Claude for M seconds
        self._claude_consecutive_fails = 0
        self._claude_backoff_until = 0.0

        # Cache raw data for reuse between entry/position analysis
        self._cached_data = {}
        self._cache_time = 0

        # Load persisted state from last session
        self._load_state()

        if self._cli_available:
            logger.info(
                "[MCP-Brain] Claude Code CLI (Opus 4.7) + Algorithmic engine ready — "
                "7 sources + multi-TF exchange indicators")
        else:
            logger.info(
                "[MCP-Brain] Algorithmic-only mode — Claude Code CLI unavailable. "
                "Trading via 7-layer scoring engine.")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _check_rate_limit(self) -> bool:
        """Enforce MAX_API_CALLS_HR budget."""
        now = time.time()
        if now - self._hour_start >= 3600:
            self._api_calls_this_hour = 0
            self._hour_start = now
        if self._api_calls_this_hour >= MAX_API_CALLS_HR:
            logger.debug("[MCP-Brain] Rate limit — skipping call")
            return False
        return True

    def _save_state(self):
        """Persist decisions, position advice, and timing state for crash recovery."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "decisions": self._last_decisions,
                "position_advice": self._last_position_advice,
                "saved_at": time.time(),
                # ── Timing state for restart resilience (2026-04-16) ──
                "last_entry_run": self._last_entry_run,
                "last_position_run": self._last_position_run,
                "api_calls_this_hour": self._api_calls_this_hour,
                "hour_start": self._hour_start,
                "claude_consecutive_fails": self._claude_consecutive_fails,
                "claude_backoff_until": self._claude_backoff_until,
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except Exception as e:
            logger.debug(f"[MCP-Brain] State save: {e}")

    def _load_state(self):
        """Load persisted decisions and timing state from last session."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                saved_at = data.get("saved_at", 0)
                age_min = (time.time() - saved_at) / 60

                # ── Timing state: restore regardless of age ──────────
                # Cooldowns and rate limits stay valid even after long restarts.
                # If hour_start is >1h old, _check_rate_limit() will reset it
                # naturally on next call — safe to restore as-is.
                if "last_entry_run" in data:
                    self._last_entry_run = data["last_entry_run"]
                    self._last_position_run = data.get("last_position_run", 0)
                    self._api_calls_this_hour = data.get("api_calls_this_hour", 0)
                    self._hour_start = data.get("hour_start", time.time())
                    self._claude_consecutive_fails = data.get("claude_consecutive_fails", 0)
                    self._claude_backoff_until = data.get("claude_backoff_until", 0.0)
                    logger.info(
                        f"[MCP-Brain] Restored timing: "
                        f"API calls={self._api_calls_this_hour}, "
                        f"Claude fails={self._claude_consecutive_fails}, "
                        f"backoff={'active' if self._claude_backoff_until > time.time() else 'none'}")

                # ── Decisions/advice: only restore if recent (<10 min) ──
                if age_min < 10:
                    self._last_decisions = data.get("decisions", {})
                    self._last_position_advice = data.get("position_advice", {})
                    n_dec = len(self._last_decisions)
                    n_adv = len(self._last_position_advice)
                    logger.info(
                        f"[MCP-Brain] Restored state: {n_dec} decisions, "
                        f"{n_adv} position advices ({age_min:.1f}min old)")
                else:
                    logger.info(f"[MCP-Brain] Stale decisions ({age_min:.0f}min old) — starting fresh")
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
        from utils.indicators import ema, rsi, atr, adx, macd, bbands, rolling_vwap
        from utils.smart_money import compute_smart_money_signals

        # Build ordered list of exchanges to try per coin
        exchange_list = list(self._exchanges.values())
        if not exchange_list:
            return {}

        results = {}
        timeframes = ["4h", "1h", "15m"]
        limit_map = {"4h": 80, "1h": 100, "15m": 100}

        for coin in coins[:25]:
            symbol = f"{coin}/USDT"
            coin_data = {}
            for tf in timeframes:
                try:
                    # Try each exchange until one returns valid data
                    raw = None
                    for exchange in exchange_list:
                        raw = exchange.fetch_ohlcv(symbol, tf,
                                                    limit=limit_map[tf],
                                                    market_type="spot")
                        if raw and len(raw) >= 30:
                            break
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
                    ema20 = ema(close, 20)
                    ema21 = ema(close, 21)
                    ema50_s = ema(close, 50)

                    # MACD (12, 26, 9) — momentum direction + acceleration
                    macd_line, macd_sig, macd_hist = macd(close, 12, 26, 9)

                    # Bollinger Band width — regime detection (squeeze = chop)
                    bb_lo, bb_mid, bb_hi = bbands(close, 20, 2.0)

                    price     = float(close.iloc[-1])
                    adx_val   = float(adx_s.iloc[-1])
                    pdi_val   = float(pdi_s.iloc[-1])
                    mdi_val   = float(mdi_s.iloc[-1])
                    rsi_val   = float(rsi_s.iloc[-1])
                    atr_pct   = float(atr_s.iloc[-1]) / max(price, 1e-9)
                    atr_abs   = float(atr_s.iloc[-1])
                    ema9_val  = float(ema9.iloc[-1])
                    ema21_val = float(ema21.iloc[-1])
                    ema50_val = float(ema50_s.iloc[-1])
                    ema20_val = float(ema20.iloc[-1])
                    ema20_prev = float(ema20.iloc[-2]) if len(ema20) > 1 else ema20_val
                    ema50_prev = float(ema50_s.iloc[-2]) if len(ema50_s) > 1 else ema50_val
                    vol_ma    = float(vol.rolling(20).mean().iloc[-1])
                    vol_ratio = float(vol.iloc[-1]) / max(vol_ma, 1e-9)

                    # MACD histogram: current, previous, and direction
                    macd_hist_val  = float(macd_hist.iloc[-1])
                    macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else 0.0
                    macd_hist_rising = macd_hist_val > macd_hist_prev

                    # EMA20 slope: normalized rate of change (positive = rising)
                    ema20_slope = (ema20_val - ema20_prev) / max(abs(ema20_prev), 1e-9)

                    # Bollinger Band width %  (narrow = squeeze/chop)
                    bb_width = 0.0
                    if bb_mid.iloc[-1] > 0:
                        bb_width = float((bb_hi.iloc[-1] - bb_lo.iloc[-1]) / bb_mid.iloc[-1]) * 100

                    # EMA alignment direction
                    if ema9_val > ema21_val > ema50_val:
                        ema_dir = "up"
                    elif ema9_val < ema21_val < ema50_val:
                        ema_dir = "down"
                    else:
                        ema_dir = "mixed"

                    # EMA 20/50 crossover detection
                    ema_cross_up = (ema20_val > ema50_val and ema20_prev <= ema50_prev)
                    ema_cross_down = (ema20_val < ema50_val and ema20_prev >= ema50_prev)
                    ema20_above_50 = ema20_val > ema50_val

                    # Market structure: 5-bar swing high/low detection
                    # (replaces single-bar comparison which was pure noise)
                    hh = hl = ll = lh = False
                    n_swing = min(5, len(high) - 1)
                    if n_swing >= 3:
                        recent_highs = [float(high.iloc[-i]) for i in range(1, n_swing + 1)]
                        recent_lows  = [float(low.iloc[-i])  for i in range(1, n_swing + 1)]
                        curr_h, prev_h = float(high.iloc[-1]), max(recent_highs)
                        curr_l, prev_l = float(low.iloc[-1]),  min(recent_lows)
                        # Swing high = current high exceeds all recent N highs
                        hh = curr_h > prev_h
                        # Swing low = current low above lowest of recent N lows
                        hl = curr_l > min(recent_lows)
                        ll = curr_l < prev_l
                        lh = curr_h < max(recent_highs)

                    price_above_ema20 = price > ema20_val
                    price_above_ema50 = price > ema50_val

                    rvwap = rolling_vwap(high, low, close, vol, 20)
                    vwap_val = float(rvwap.iloc[-1]) if not pd.isna(rvwap.iloc[-1]) else price
                    price_vs_vwap = (price - vwap_val) / max(vwap_val, 1e-9) * 100

                    # Candle body analysis (news candle detection)
                    bodies = (df["close"] - df["open"]).abs()
                    last_body = float(bodies.iloc[-1]) if len(bodies) > 0 else 0
                    avg_body_20 = float(bodies.tail(20).mean()) if len(bodies) >= 20 else float(bodies.mean())

                    # ── Smart Money / ICT indicators (2026-04-16) ──────────
                    try:
                        smc = compute_smart_money_signals(
                            df["open"], high, low, close, vol)
                    except Exception:
                        smc = {"smc_structure": 0, "smc_entry": 0,
                               "smc_confirmation": 0,
                               "fibonacci": {}, "fvg": {}, "supply_demand": {},
                               "liquidity_sweep": {}, "order_blocks": {},
                               "market_structure": {}, "volume_profile": {},
                               "ote": {}, "price_action": {}}

                    coin_data[tf] = {
                        "adx": round(adx_val, 1),
                        "pdi": round(pdi_val, 1),
                        "mdi": round(mdi_val, 1),
                        "rsi": round(rsi_val, 1),
                        "ema_dir": ema_dir,
                        "atr_pct": round(atr_pct * 100, 2),
                        "atr_abs": round(atr_abs, 6),
                        "vol_ratio": round(vol_ratio, 2),
                        "price": round(price, 6),
                        # MACD momentum
                        "macd_hist": round(macd_hist_val, 6),
                        "macd_hist_prev": round(macd_hist_prev, 6),
                        "macd_hist_rising": macd_hist_rising,
                        # EMA slope & Bollinger
                        "ema20_slope": round(ema20_slope, 6),
                        "bb_width": round(bb_width, 2),
                        # Systematic entry data
                        "ema20": round(ema20_val, 6),
                        "ema50": round(ema50_val, 6),
                        "ema20_above_50": ema20_above_50,
                        "ema_cross_up": ema_cross_up,
                        "ema_cross_down": ema_cross_down,
                        "price_above_ema20": price_above_ema20,
                        "price_above_ema50": price_above_ema50,
                        "hh": hh, "hl": hl,
                        "ll": ll, "lh": lh,
                        "last_body": round(last_body, 6),
                        "avg_body_20": round(avg_body_20, 6),
                        "vwap": round(vwap_val, 6),
                        "price_vs_vwap": round(price_vs_vwap, 4),
                        # Smart Money / ICT composite scores
                        "smc_structure": smc.get("smc_structure", 0),
                        "smc_entry": smc.get("smc_entry", 0),
                        "smc_confirmation": smc.get("smc_confirmation", 0),
                        # Smart Money detail (for logging/debug)
                        "smc_detail": {
                            "fib": smc.get("fibonacci", {}),
                            "fvg": smc.get("fvg", {}),
                            "sd": smc.get("supply_demand", {}),
                            "sweep": smc.get("liquidity_sweep", {}),
                            "ob": smc.get("order_blocks", {}),
                            "ms": smc.get("market_structure", {}),
                            "vp": smc.get("volume_profile", {}),
                            "ote": smc.get("ote", {}),
                            "pa": smc.get("price_action", {}),
                        },
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
    # CLAUDE CLI INTERFACE
    # ──────────────────────────────────────────────────────────────────

    def _call_claude(self, prompt: str, system_prompt: str,
                      call_type: str = "portfolio") -> dict:
        """Call Claude CLI with rate limiting, JSON parsing, and repair."""
        if not self._check_rate_limit():
            return {}

        # Skip Claude if in failure cooldown — go straight to algorithmic
        now = time.time()
        if now < self._claude_backoff_until:
            remaining = int(self._claude_backoff_until - now)
            logger.debug(
                f"[MCP-Brain] Claude in cooldown ({remaining}s left), using algorithmic")
            return {}

        # Cap prompt size — smaller = faster first-token
        max_prompt = 8000 if call_type == "portfolio" else 5000
        if len(prompt) > max_prompt:
            prompt = prompt[:max_prompt] + "\n[TRUNCATED]"

        model = MODEL_ENTRY if call_type == "portfolio" else MODEL_MONITOR
        # Monitor uses haiku (fast), portfolio uses sonnet (smarter)
        timeout = 90 if call_type == "monitor" else 120
        # Medium effort for both — "low" truncates multi-position JSON responses
        effort = "medium"

        # Claude Code CLI only — no API fallback
        raw = call_claude_cli(prompt, system_prompt, model, timeout, effort)
        if raw is None:
            self._claude_consecutive_fails += 1
            reason = getattr(_claude_mod, '_last_error', 'unknown')
            # Gentle backoff: 30s, 60s, 120s, max 5min
            backoff = min(300, 30 * (2 ** (self._claude_consecutive_fails - 1)))
            self._claude_backoff_until = now + backoff
            logger.warning(
                f"[MCP-Brain] Claude CLI failed ({call_type}): {reason} "
                f"— backing off {backoff}s (fail #{self._claude_consecutive_fails}), "
                f"algorithmic fallback active")
            return {}

        # Claude succeeded — reset failure tracking
        self._claude_consecutive_fails = 0
        self._claude_backoff_until = 0.0
        self._api_calls_this_hour += 1

        # Strip markdown fences
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(
                l for l in text.split("\n")
                if not l.strip().startswith("```")
            ).strip()

        # Parse JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = self._repair_json(text)
            if repaired:
                logger.info("[MCP-Brain] JSON repaired from truncated response")
                return repaired
            logger.warning(f"[MCP-Brain] Unparseable response: {text[:200]}")
            return {}

    @staticmethod
    def _repair_json(text: str) -> dict:
        """Attempt to repair truncated/malformed JSON.
        Handles mid-string truncation by finding the last complete }
        and closing remaining braces."""
        if not text:
            return {}
        start = text.find("{")
        if start < 0:
            return {}
        fragment = text[start:]

        # Strategy 1: naive close (works if truncation is between entries)
        opens_b = fragment.count("{") - fragment.count("}")
        opens_a = fragment.count("[") - fragment.count("]")
        if opens_b >= 0 and opens_a >= 0:
            trimmed = fragment.rstrip()
            if trimmed.endswith(","):
                trimmed = trimmed[:-1]
            suffix = "]" * opens_a + "}" * opens_b
            try:
                return json.loads(trimmed + suffix)
            except json.JSONDecodeError:
                pass

        # Strategy 2: truncate to last complete } then close
        # This handles mid-string truncation (e.g. "reason": "trend in...)
        last_brace = fragment.rfind("}")
        while last_brace > 0:
            candidate = fragment[:last_brace + 1].rstrip()
            if candidate.endswith(","):
                candidate = candidate[:-1]
            ob = candidate.count("{") - candidate.count("}")
            oa = candidate.count("[") - candidate.count("]")
            if ob >= 0 and oa >= 0:
                suffix = "]" * oa + "}" * ob
                try:
                    result = json.loads(candidate + suffix)
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass
            last_brace = fragment.rfind("}", 0, last_brace)
        return {}

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(val, default: int = 0) -> int:
        if val is None:
            return default
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default

    # ──────────────────────────────────────────────────────────────────
    # PORTFOLIO ANALYSIS — Claude Code as sole decision authority
    # ──────────────────────────────────────────────────────────────────

    def analyze_portfolio(self, coins: list, open_positions: list,
                          exchange_balances: dict, risk_envelope: dict,
                          recent_trades: list, news_context: dict = None) -> list:
        """
        Main entry point: gather all data, score coins algorithmically.
        Returns list of action dicts: [{type, symbol, exchange, ...}]
        """
        if not self._enabled:
            return []

        now = time.time()
        if now - self._last_entry_run < ENTRY_COOLDOWN:
            # 2026-04-11: was `return self._last_trade_actions` — that caused
            # duplicate execution because bot_engine._claude_portfolio_cycle
            # has no dedupe, so replaying the cached list placed the same
            # orders multiple times. Return empty on cooldown; the engine
            # will just log "No actions this cycle" and try again next tick.
            return []
        self._last_entry_run = now

        # Fetch all external data sources
        data = self._fetch_all_data(coins)
        if data["sources_ok"] < 2:
            logger.warning("[MCP-Brain] < 2 data sources — no actions")
            return []

        # Merge news context into data
        if news_context:
            data["news_context"] = news_context

        # Fetch multi-TF exchange indicators
        exchange_indicators = self._fetch_exchange_indicators(coins)

        actions = []

        # Try Claude first (CLI → API), then fall back to algorithmic scoring
        if self._claude_available:
            try:
                actions = self._ask_claude_portfolio(
                    coins, data, exchange_indicators,
                    open_positions, exchange_balances,
                    risk_envelope, recent_trades)
            except Exception as e:
                logger.error(f"[MCP-Brain] Claude portfolio analysis failed: {e}")

        # Algorithmic fallback: pure scoring engine when Claude is unavailable/failed
        if not actions and risk_envelope.get("max_new_positions", 0) > 0:
            try:
                actions = self._algorithmic_portfolio(
                    coins, data, exchange_indicators,
                    open_positions, exchange_balances, risk_envelope)
                if actions:
                    logger.info(
                        f"[MCP-Brain] Algorithmic fallback: {len(actions)} actions")
            except Exception as e:
                logger.error(f"[MCP-Brain] Algorithmic scoring failed: {e}")

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

    def get_trade_actions(self) -> list:
        """Return OPEN/CLOSE actions from last analyze_portfolio call."""
        return list(self._last_trade_actions)

    # ──────────────────────────────────────────────────────────────────
    # CLAUDE PORTFOLIO PROMPT BUILDER
    # ──────────────────────────────────────────────────────────────────

    def _ask_claude_portfolio(self, coins, data, exchange_indicators,
                               open_positions, exchange_balances,
                               risk_envelope, recent_trades) -> list:
        """Build comprehensive portfolio prompt, send to Claude, parse actions."""
        sections = []

        # ── Fear & Greed ──
        fng = data.get("fng", {})
        if fng:
            sections.append(
                f"F&G:{fng.get('value','?')}/100 ({fng.get('label','?')}) "
                f"trend={fng.get('trend','?')}")

        # ── Accuracy feedback ──
        stats = self._accuracy.stats()
        if stats.get("total", 0) >= 5:
            sections.append(
                f"YOUR TRACK RECORD: {stats['total']} decisions, "
                f"WR={stats.get('win_rate',0):.0%}, "
                f"avg_conf={stats.get('avg_confidence',0):.0%}")

        # ── Risk envelope ──
        sections.append(
            f"RISK: {risk_envelope.get('total_open',0)} open, "
            f"max {risk_envelope.get('max_new_positions',0)} new, "
            f"daily_loss={risk_envelope.get('daily_loss_pct',0):.1%}, "
            f"balance=${risk_envelope.get('total_balance',0):.0f}")

        # ── Exchange balances ──
        bal_parts = []
        for ex in ("binance", "bybit", "bitget"):
            bals = exchange_balances.get(ex, {})
            s, f = bals.get("spot", 0), bals.get("futures", 0)
            if s > 0 or f > 0:
                bal_parts.append(f"{ex}:s${s:.0f}/f${f:.0f}")
        if bal_parts:
            sections.append("BAL: " + " | ".join(bal_parts))

        # ── Open positions ──
        if open_positions:
            pos_lines = []
            for p in open_positions[:8]:
                pnl = p.get("pnl_pct", 0) or 0
                pos_lines.append(
                    f"  [{p.get('id','?')[:8]}] {p.get('symbol','?')} "
                    f"{p.get('side','?')} {p.get('exchange','?')} "
                    f"({p.get('market_type','?')}) {p.get('leverage',1)}x "
                    f"pnl={pnl:+.1f}% age={p.get('age_min',0):.0f}m")
            sections.append("POSITIONS:\n" + "\n".join(pos_lines))
        else:
            sections.append("POSITIONS: none")

        # ── Recent trades (last 5 for brevity) ──
        if recent_trades:
            tlines = []
            for t in recent_trades[-5:]:
                pnl = t.get("pnl", 0)
                tlines.append(
                    f"  {t.get('symbol','?')} {t.get('side','?')} "
                    f"{'WIN' if pnl > 0 else 'LOSS'} ${pnl:+.2f}")
            sections.append("RECENT TRADES:\n" + "\n".join(tlines))

        # ── News + Sentiment Context (trimmed for speed) ──
        news = data.get("news", [])
        if news:
            headlines = [
                f"  [{n.get('sentiment','?')}] {n.get('title','')[:50]}"
                for n in news[:3]]
            sections.append("NEWS:\n" + "\n".join(headlines))

        nc = data.get("news_context", {})
        if nc:
            sent_line = f"SENTIMENT: {nc.get('overall_sentiment', 0):+.2f} "
            sent_line += f"F&G={nc.get('fear_greed_zone', '?')}"
            sections.append(sent_line)
            # Breaking news only — highest signal value
            breaking = nc.get("breaking_news", [])
            if breaking:
                brk_lines = [f"  *** {b.get('title','')[:60]}" for b in breaking[:2]]
                sections.append("BREAKING:\n" + "\n".join(brk_lines))
            # Strong coin sentiments only
            coin_sents = nc.get("coin_sentiments", {})
            if coin_sents:
                strong = {c: s for c, s in coin_sents.items() if abs(s) >= 0.4}
                if strong:
                    parts = [f"{c}={s:+.2f}" for c, s in sorted(
                        strong.items(), key=lambda x: abs(x[1]), reverse=True)[:6]]
                    sections.append("COIN SENT: " + " | ".join(parts))

        # ── Per-coin data (top 15 for prompt speed) ──
        coin_lines = []
        for coin in coins[:15]:
            gecko = data.get("gecko", {}).get(coin, {})
            crypto = data.get("crypto", {}).get(coin, {})
            price = gecko.get("price") or crypto.get("price", 0)
            if not price:
                continue

            tech = data.get("technicals", {}).get(coin, {})
            ei = exchange_indicators.get(coin, {})
            fr_d = data.get("funding", {}).get(coin, {})
            ob = data.get("orderbook", {}).get(coin, {})

            line = f"\n{coin} ${price:.6g}"
            if gecko:
                line += (f" 1h={gecko.get('change_1h',0):+.1f}% "
                         f"24h={gecko.get('change_24h',0):+.1f}%")

            # Sparkline technicals
            if tech:
                line += (f" RSI={tech.get('rsi_14','?')} "
                         f"trend={tech.get('trend','?')}({tech.get('trend_score',0)}) "
                         f"MACD={tech.get('macd_hist',0):+.5f} "
                         f"ATR%={tech.get('atr_pct','?')}")

            # Multi-TF exchange indicators
            for tf in ("4h", "1h", "15m"):
                tfd = ei.get(tf, {})
                if tfd:
                    line += (f"\n  {tf}: ADX={tfd.get('adx','?')} "
                             f"RSI={tfd.get('rsi','?')} "
                             f"EMA={tfd.get('ema_dir','?')} "
                             f"ATR%={tfd.get('atr_pct','?')} "
                             f"vol={tfd.get('vol_ratio','?')}x")

            if fr_d:
                line += f"\n  funding={fr_d.get('funding_rate',0)*100:+.4f}%"
            if ob:
                line += (f"\n  OB: imb={ob.get('imbalance',0):+.3f} "
                         f"spread={ob.get('spread_pct',0):.4f}%")

            coin_lines.append(line)

        sections.append("COINS:" + "\n".join(coin_lines))

        prompt = "\n\n".join(sections)

        # Call Claude
        result = self._call_claude(prompt, _PORTFOLIO_SYSTEM_PROMPT, "portfolio")
        if not result:
            return []

        # Parse actions
        actions = []
        for a in result.get("actions", []):
            if not isinstance(a, dict):
                continue
            atype = (a.get("type") or "").upper()
            if atype not in ("OPEN", "CLOSE"):
                continue
            claude_conf = self._safe_float(a.get("confidence"), 0)
            reason = a.get("reason", "")

            # Blend with algorithmic scorer for OPEN actions (2026-04-17).
            # final_conf = average(claude_conf, algo_conf). If algo's 4 required
            # gates fail (algo_conf=0), blended conf is halved — effectively a
            # veto via the tier min_confidence floor. CLOSE actions bypass the
            # blend (algo only scores entries).
            algo_conf = None
            algo_score = None  # 2026-04-21: propagate MCP score so warehouse
                               # and learning engine can segment WR by entry
                               # quality. Previously only algo-fallback path
                               # wrote mcp_score; Claude path left it NULL.
            if atype == "OPEN":
                symbol = a.get("symbol", "")
                coin = symbol.split("/")[0] if symbol else ""
                ei = exchange_indicators.get(coin, {}) if coin else {}
                if ei:
                    try:
                        algo = self._score_coin(coin, data, ei)
                        algo_conf = float(algo.get("confidence", 0) or 0)
                        _s = algo.get("score")
                        algo_score = float(_s) if _s is not None else None
                    except Exception as _e:
                        logger.debug(f"[MCP-Blend] algo score failed for {coin}: {_e}")
                        algo_conf = None

            if algo_conf is not None:
                final_conf = (claude_conf + algo_conf) / 2.0
                reason = f"{reason} | blend(c={claude_conf:.2f},a={algo_conf:.2f})"
            else:
                final_conf = claude_conf

            actions.append({
                "type":        atype,
                "symbol":      a.get("symbol", ""),
                "exchange":    (a.get("exchange") or "").lower(),
                "market_type": a.get("market_type", "futures"),
                "side":        (a.get("side") or "").lower(),
                "leverage":    self._safe_int(a.get("leverage"), 5),
                "size_pct":    self._safe_float(a.get("size_pct"), 3.0),
                "sl_pct":      self._safe_float(a.get("sl_pct"), 4.0),
                "tp_pct":      self._safe_float(a.get("tp_pct"), 10.0),
                "confidence":  final_conf,
                "claude_confidence": claude_conf,
                "algo_confidence":   algo_conf,
                "mcp_score":   algo_score,
                "reason":      reason,
                "position_id": a.get("position_id", ""),
            })

        # Log assessment
        assessment = result.get("market_assessment", "")
        if assessment:
            logger.info(f"[Claude] Market: {assessment[:80]}")
        if not actions:
            skip = result.get("skip_reason", "no qualifying setups")
            logger.info(f"[Claude] No trades: {skip[:80]}")
        for a in actions:
            if a["type"] == "OPEN":
                logger.info(
                    f"[Claude] OPEN {a['symbol']} {a['side']} on {a['exchange']} "
                    f"({a['market_type']}) {a['leverage']}x size={a['size_pct']}% "
                    f"SL={a['sl_pct']}% TP={a['tp_pct']}% "
                    f"conf={a['confidence']:.0%} | {a['reason'][:60]}")
            else:
                logger.info(
                    f"[Claude] CLOSE {a.get('position_id','?')[:8]} | "
                    f"{a['reason'][:60]}")

        # Update last_decisions for backward compat (DCA gate)
        for a in actions:
            if a["type"] == "OPEN":
                coin = a["symbol"].split("/")[0]
                self._last_decisions[coin] = {
                    "action": "BUY" if a["side"] == "buy" else "SELL",
                    "confidence": a["confidence"],
                    "reason": a["reason"],
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

        advice = {}

        # Try Claude first, then fall back to algorithmic monitor
        if self._claude_available:
            try:
                advice = self._ask_claude_positions(positions, data, exchange_indicators)
            except Exception as e:
                logger.error(f"[MCP-Brain] Claude position monitor failed: {e}")

        # Algorithmic fallback for position monitoring
        if not advice:
            try:
                advice = self._algorithmic_position_monitor(
                    positions, data, exchange_indicators)
                if advice:
                    logger.info(
                        f"[MCP-Brain] Algorithmic monitor: "
                        f"{sum(1 for a in advice.values() if a.get('action') != 'HOLD')} actions")
            except Exception as e:
                logger.error(f"[MCP-Brain] Algorithmic monitor failed: {e}")

        if advice:
            self._last_position_advice = advice
            self._log_decisions(advice, "position_monitor")
            self._save_state()
            return advice

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
                if action in ("HOLD", "BREAKEVEN"):
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
        # Small profit with no MCP guidance — let trailing stop handle it
        return False

    # ──────────────────────────────────────────────────────────────────
    # CLAUDE POSITION MONITOR
    # ──────────────────────────────────────────────────────────────────

    def _ask_claude_positions(self, positions: list, data: dict,
                               exchange_indicators: dict) -> dict:
        """Build position monitor prompt, send to Claude, parse advice."""
        sections = []

        # ── Market sentiment ──
        fng = data.get("fng", {})
        if fng:
            sections.append(
                f"F&G:{fng.get('value','?')}/100 ({fng.get('label','?')})")

        # ── BTC/ETH anchors ──
        for anchor in ("BTC", "ETH"):
            gd = data.get("gecko", {}).get(anchor, {})
            cd = data.get("crypto", {}).get(anchor, {})
            price = gd.get("price") or cd.get("price")
            chg = cd.get("change_24h") or gd.get("change_24h", 0)
            if price:
                sections.append(f"{anchor}=${price:.0f} 24h={chg:+.1f}%")

        # ── Each position with full context ──
        pos_lines = []
        for p in positions:
            coin = p.get("symbol", "?").split("/")[0].split(":")[0]
            pid = p.get("id", "?")[:8]

            line = (
                f"[{pid}] {p.get('symbol','?')} {p.get('side','?')} "
                f"on {p.get('exchange','?')} ({p.get('market_type','?')}) "
                f"{p.get('leverage',1)}x\n"
                f"  entry={p.get('entry_price',0):.6g} "
                f"now={p.get('current_price',0):.6g} "
                f"pnl={p.get('pnl_pct',0):+.2f}% "
                f"age={p.get('age_min',0):.0f}m")

            # Key indicators only (4h + 1h — skip 15m for brevity)
            ei = exchange_indicators.get(coin, {})
            for tf in ("4h", "1h"):
                tfd = ei.get(tf, {})
                if tfd:
                    line += (
                        f"\n  {tf}: RSI={tfd.get('rsi','?')} "
                        f"EMA={tfd.get('ema_dir','?')} "
                        f"ADX={tfd.get('adx','?')}")

            pos_lines.append(line)

        sections.append(
            f"POSITIONS ({len(positions)}):\n" + "\n".join(pos_lines))

        prompt = "\n".join(sections)

        result = self._call_claude(prompt, _POSITION_MONITOR_SYSTEM_PROMPT, "monitor")
        if not result:
            return {}

        # Parse position advice — Claude returns {positions: {id: {action, confidence, reason}}}
        raw_positions = result.get("positions", result)  # fallback: result itself might be the dict
        advice = {}
        for p in positions:
            pid = p.get("id", "")
            short_id = pid[:8]
            # Match by short ID or full ID
            adv = raw_positions.get(short_id) or raw_positions.get(pid, {})
            if isinstance(adv, dict) and adv.get("action"):
                action = adv["action"].upper()
                if action not in ("HOLD", "CLOSE", "TAKE_PROFIT",
                                  "TIGHTEN", "BREAKEVEN"):
                    action = "HOLD"
                advice[pid] = {
                    "action": action,
                    "confidence": min(1.0, max(0.0,
                        self._safe_float(adv.get("confidence"), 0.6))),
                    "reason": (adv.get("reason") or "")[:100],
                }
                if action != "HOLD":
                    logger.info(
                        f"[Claude] Position {short_id}: {action} "
                        f"conf={advice[pid]['confidence']:.0%} | "
                        f"{advice[pid]['reason'][:60]}")
            else:
                # Default to HOLD if Claude didn't provide advice
                advice[pid] = {
                    "action": "HOLD",
                    "confidence": 0.5,
                    "reason": "no Claude response",
                }

        return advice

    # ──────────────────────────────────────────────────────────────────
    # ALGORITHMIC SCORING ENGINE — works without Claude
    # ──────────────────────────────────────────────────────────────────

    def _score_coin(self, coin: str, data: dict, ei: dict) -> dict:
        """
        Systematic rules-based scoring engine v3.1 (relaxed).

        2026-04-14 v3.1: v3 was too strict — 25 trades in 60 days across
        10 symbols (not enough data to build an edge). Relaxed:
        - EMA slope moved from REQUIRED to BONUS
        - ADX required 25 → 20
        - Volume 1.5x → 1.2x
        - Regime filter: ADX < 15, BB < 1.0%
        - 4 REQUIRED + 2+ BONUS (was 5 + 2)

        ═══════════════════════════════════════════════════════════════
        LONG ENTRY (side='buy'):
          REQUIRED (all 4 must be true):
            R1. 4h EMA 20 > EMA 50                    (trend direction)
            R2. 1h EMA 20 > EMA 50                    (confirmation TF)
            R3. 1h RSI in [38, 68]                     (not exhausted)
            R4. 1h ADX >= 20                           (some trend present)
          BONUS (need 2+ for entry, score >= 66):
            B1. 1h MACD histogram > 0 AND rising   +12 (momentum)
            B2. 4h EMA 20 slope > 0                +8  (trend accel)
            B3. 15m EMA cross or alignment         +10 (entry timing)
            B4. 1h vol >= 1.2x average             +8  (participation)
            B5. 1h swing HH + HL (5-bar)           +8  (structure)
            B6. Funding + orderbook combined        +5  (microstructure)
            B7. VWAP confirmation (price near VWAP)  +5  (value area)
            B8. SMC Structure (OB + BOS/ChoCH)     +10 (smart money structure)
            B9. SMC Entry (FVG + Fib + S/D + OTE)  +10 (institutional entry zone)
            B10. SMC Confirmation (sweep+vol+PA)    +8  (smart money confirmation)

        REGIME FILTER:
          4h ADX < 15 → SKIP  |  4h BB width < 1.0% → SKIP

        SL/TP:
          SL = max(1.5%, min(3%, 1.2 * ATR_1h_pct))
          TP = 2.0 * SL
        ═══════════════════════════════════════════════════════════════
        """
        ei_4h = ei.get("4h", {})
        ei_1h = ei.get("1h", {})
        ei_15m = ei.get("15m", {})
        fr = data.get("funding", {}).get(coin, {})
        ob = data.get("orderbook", {}).get(coin, {})
        _zero = {"score": 0, "side": "buy", "layers_ok": 0, "confidence": 0,
                 "sl_pct": 3.0, "tp_pct": 6.0, "reason": ""}

        if not ei_4h or not ei_1h:
            _zero["reason"] = "no_data"
            return _zero

        # ═══ REGIME FILTER — relaxed from v3 ═══════════════════════
        adx_4h = ei_4h.get("adx", 0)
        bb_width_4h = ei_4h.get("bb_width", 99)
        if adx_4h < 15:
            _zero["reason"] = f"regime_chop(4h_adx={adx_4h:.0f}<15)"
            return _zero
        if bb_width_4h < 1.0:
            _zero["reason"] = f"regime_squeeze(4h_bb={bb_width_4h:.1f}%<1.0%)"
            return _zero

        # ═══ NO-TRADE CONDITIONS — extreme volatility, news candle, thin liquidity
        atr_1h_pct = ei_1h.get("atr_pct", 0)
        if atr_1h_pct > 5.0:
            _zero["reason"] = f"extreme_volatility(1h_atr={atr_1h_pct:.1f}%>5%)"
            return _zero

        avg_body = ei_1h.get("avg_body_20", 0)
        last_body = ei_1h.get("last_body", 0)
        if avg_body > 0 and last_body > 3 * avg_body:
            _zero["reason"] = f"news_candle(body={last_body:.2f}>3x_avg={avg_body:.2f})"
            return _zero

        ob_depth = (
            min(ob.get("bid_depth_usd", 0), ob.get("ask_depth_usd", 0))
            if ob else 0
        ) or (ob.get("depth_usd", 0) if ob else 0)
        if ob_depth > 0 and ob_depth < 5000:
            _zero["reason"] = f"thin_liquidity(depth=${ob_depth:.0f}<$5000)"
            return _zero

        # ── Determine side from 4h EMA 20/50 ───────────────────────
        ema20_above_50_4h = ei_4h.get("ema20_above_50", False)
        ema20_above_50_1h = ei_1h.get("ema20_above_50", False)

        # Clear direction required — if EMAs are within 0.1%, skip
        ema20_4h = ei_4h.get("ema20", 0)
        ema50_4h = ei_4h.get("ema50", 1)
        if ema50_4h > 0:
            ema_gap_pct = abs(ema20_4h - ema50_4h) / ema50_4h * 100
            if ema_gap_pct < 0.1:
                _zero["reason"] = f"4h_emas_flat(gap={ema_gap_pct:.2f}%)"
                return _zero

        side = "buy" if ema20_above_50_4h else "sell"

        # ═══ CHECK 4 REQUIRED CONDITIONS ═════════════════════════════
        req_pass = 0
        req_reasons = []

        # R1: 4h EMA gap >= 0.15% (meaningful trend separation, not just crossing)
        # Note: side is derived from ema20_above_50_4h, so checking the bool
        # alone would be tautological. The gap threshold ensures the EMA cross
        # is decisive, not noise.
        r1 = ema_gap_pct >= 0.15
        if r1:
            req_pass += 1
            req_reasons.append(f"4h_ema_gap={ema_gap_pct:.2f}%")

        # R2: 1h EMA 20 > 50 confirms direction
        r2 = ema20_above_50_1h if side == "buy" else not ema20_above_50_1h
        if r2:
            req_pass += 1
            req_reasons.append("1h_ema")

        # R3: RSI in sweet spot — slightly wider than v3
        rsi_1h = ei_1h.get("rsi", 50)
        if side == "buy":
            r3 = 38 <= rsi_1h <= 68
        else:
            r3 = 32 <= rsi_1h <= 62
        if r3:
            req_pass += 1
            req_reasons.append(f"rsi={rsi_1h:.0f}")

        # R4: 1h ADX >= 20 (some trend present)
        adx_1h = ei_1h.get("adx", 0)
        if adx_1h >= 20:
            req_pass += 1
            req_reasons.append(f"adx={adx_1h:.0f}")

        # ALL 4 required must pass
        if req_pass < 4:
            _zero["reason"] = f"req_fail({req_pass}/4: {', '.join(req_reasons)})"
            return _zero

        # ═══ COUNT BONUS CONDITIONS ═══════════════════════════════════
        score = 50  # Base score for passing all 4 required
        bonus_count = 0
        bonus_reasons = []

        # B1: 1h MACD histogram confirms momentum direction AND accelerating
        macd_hist = ei_1h.get("macd_hist", 0)
        macd_rising = ei_1h.get("macd_hist_rising", False)
        if side == "buy":
            b1 = macd_hist > 0 and macd_rising
        else:
            b1 = macd_hist < 0 and not macd_rising
        if b1:
            bonus_count += 1
            score += 12
            bonus_reasons.append(f"macd={'+'if macd_hist>0 else '-'}{abs(macd_hist):.4f}")

        # B2: 4h EMA 20 slope (was REQUIRED in v3, now BONUS)
        ema20_slope_4h = ei_4h.get("ema20_slope", 0)
        if side == "buy" and ema20_slope_4h > 0:
            bonus_count += 1
            score += 8
            bonus_reasons.append("4h_slope_up")
        elif side == "sell" and ema20_slope_4h < 0:
            bonus_count += 1
            score += 8
            bonus_reasons.append("4h_slope_dn")

        # B3: 15m EMA alignment or fresh crossover (entry timing)
        if ei_15m:
            if side == "buy":
                b3 = ei_15m.get("ema20_above_50", False) or ei_15m.get("ema_cross_up", False)
            else:
                b3 = not ei_15m.get("ema20_above_50", True) or ei_15m.get("ema_cross_down", False)
            if b3:
                bonus_count += 1
                score += 10
                bonus_reasons.append("15m_timing")

        # B4: 1h volume >= 1.2x average (participation)
        vol_1h = ei_1h.get("vol_ratio", 0)
        if vol_1h >= 1.2:
            bonus_count += 1
            score += 8
            bonus_reasons.append(f"vol={vol_1h:.1f}x")

        # B5: Market structure — 5-bar swing HH+HL (longs) or LL+LH (shorts)
        if side == "buy":
            b5 = ei_1h.get("hh", False) and ei_1h.get("hl", False)
        else:
            b5 = ei_1h.get("ll", False) and ei_1h.get("lh", False)
        if b5:
            bonus_count += 1
            score += 8
            bonus_reasons.append("structure")

        # B6: Funding + orderbook combined (microstructure edge)
        funding_rate = fr.get("funding_rate", 0)
        imb = ob.get("imbalance", 0)
        micro_ok = False
        if side == "buy" and funding_rate <= 0.0003 and imb > 0.05:
            micro_ok = True
        elif side == "sell" and funding_rate >= -0.0003 and imb < -0.05:
            micro_ok = True
        if micro_ok:
            bonus_count += 1
            score += 5
            bonus_reasons.append(f"micro(f={funding_rate*100:+.3f}%,ob={imb:+.2f})")

        # B7: VWAP confirmation — price above VWAP for longs, below for shorts
        pvw = ei_1h.get("price_vs_vwap", 0)
        if side == "buy" and 0.05 < pvw < 2.0:
            bonus_count += 1
            score += 5
            bonus_reasons.append(f"vwap=+{pvw:.2f}%")
        elif side == "sell" and -2.0 < pvw < -0.05:
            bonus_count += 1
            score += 5
            bonus_reasons.append(f"vwap={pvw:.2f}%")

        # ═══ SMART MONEY / ICT BONUS CONDITIONS (2026-04-16) ═══════
        # Three composite scores from utils/smart_money.py, computed from
        # the same OHLCV data (no extra API calls). Each score is 0-100.
        # Uses 1h TF as primary, with 4h for higher-TF structure confirmation.

        smc_struct_1h  = ei_1h.get("smc_structure", 0)
        smc_entry_1h   = ei_1h.get("smc_entry", 0)
        smc_conf_1h    = ei_1h.get("smc_confirmation", 0)
        smc_struct_4h  = ei_4h.get("smc_structure", 0)

        # Smart money detail for side-alignment checks
        smc_d = ei_1h.get("smc_detail", {})
        ms_data = smc_d.get("ms", {})
        sweep_data = smc_d.get("sweep", {})
        pa_data = smc_d.get("pa", {})

        # B8: SMC Structure — order blocks + BOS/ChoCH (10 pts)
        # Requires alignment: bullish structure for longs, bearish for shorts.
        b8 = False
        if smc_struct_1h >= 40:
            ms_trend = ms_data.get("trend", "ranging")
            if side == "buy" and ms_trend == "bullish":
                b8 = True
            elif side == "sell" and ms_trend == "bearish":
                b8 = True
            # 4h fallback: must also check side alignment via 4h market structure
            if not b8 and smc_struct_4h >= 50:
                ms_4h = ei_4h.get("smc_detail", {}).get("ms", {})
                ms_trend_4h = ms_4h.get("trend", "ranging")
                if side == "buy" and ms_trend_4h == "bullish":
                    b8 = True
                elif side == "sell" and ms_trend_4h == "bearish":
                    b8 = True
        if b8:
            bonus_count += 1
            score += 10
            bonus_reasons.append(f"smc_struct={smc_struct_1h:.0f}")

        # B9: SMC Entry Zone — FVG + Fibonacci + S/D + ICT OTE (10 pts)
        # Price at institutional entry zone: fib confluence, FVG, or demand/supply.
        # Side-aligned: bullish zones for longs, bearish for shorts.
        b9 = False
        if smc_entry_1h >= 40:
            fvg_data = smc_d.get("fvg", {})
            sd_data = smc_d.get("sd", {})
            ote_data = smc_d.get("ote", {})
            fib_data = smc_d.get("fib", {})
            if side == "buy":
                b9 = (fvg_data.get("price_in_bullish_fvg", False)
                      or sd_data.get("price_in_demand", False)
                      or (ote_data.get("in_ote_zone", False) and ote_data.get("ote_bias") == "bullish")
                      or (fib_data.get("at_fib_zone", False) and fib_data.get("fib_bias") == "bullish"))
            else:
                b9 = (fvg_data.get("price_in_bearish_fvg", False)
                      or sd_data.get("price_in_supply", False)
                      or (ote_data.get("in_ote_zone", False) and ote_data.get("ote_bias") == "bearish")
                      or (fib_data.get("at_fib_zone", False) and fib_data.get("fib_bias") == "bearish"))
            # 2026-04-16 (post-audit): side-blind fallback REMOVED.
            # Previously: `if not b9 and smc_entry_1h >= 60: b9 = True`,
            # which awarded +10 to a SHORT entry in a bullish FVG or
            # demand zone — the exact context that should deny the bonus.
            # This was a contributor to the 36% WR on shorts vs 44% on longs.
        if b9:
            bonus_count += 1
            score += 10
            bonus_reasons.append(f"smc_entry={smc_entry_1h:.0f}")

        # B10: SMC Confirmation — liquidity sweep + volume profile + price action (8 pts)
        # Extra conviction: recent stop-hunt, price at value node, bullish/bearish pattern.
        b10 = False
        if smc_conf_1h >= 40:
            if side == "buy":
                b10 = (sweep_data.get("bullish_sweep", False)
                       or pa_data.get("pattern_score", 0) >= 1)
            else:
                b10 = (sweep_data.get("bearish_sweep", False)
                       or pa_data.get("pattern_score", 0) <= -1)
            # 2026-04-16 (post-audit): side-blind fallback REMOVED (same
            # reason as B9). A bullish sweep on a short trade was pushing
            # B10 to True.
        if b10:
            bonus_count += 1
            score += 8
            bonus_reasons.append(f"smc_conf={smc_conf_1h:.0f}")

        layers_ok = req_pass + bonus_count

        # ── Distribution-fitted SL/TP (Phase 2 / Task B) ───────────
        # Fit to realized MAE distribution per symbol; falls back to
        # ATR×1.8 / ATR×4.5 when the cell has < 30 warehouse rows. Both
        # halves of the bot now route SL/TP through DistFitSL so there
        # is a single source of truth (the previous split between this
        # block and risk_manager.get_sl_tp produced inconsistent stops).
        atr_1h_pct = ei_1h.get("atr_pct", 2.0) or 2.0
        try:
            from core.dist_fit_sl import DistFitSL
            _entry_px = ei_1h.get("price", 0) or 0
            _fit = DistFitSL().compute(
                symbol=coin if "/" in coin else f"{coin}/USDT:USDT",
                side=side, atr_pct=atr_1h_pct / 100.0,
            )
            sl_pct = max(1.5, _fit.sl_pct * 100.0)  # convert back to percent + floor
            tp_pct = _fit.tp_pct * 100.0
        except Exception:
            sl_pct = max(1.5, min(3.5, atr_1h_pct * 1.5))
            tp_pct = sl_pct * 2.0

        # ── Smart Money SL/TP refinement ──────────────────────────
        # If strong S/D or OB zone provides a tighter SL reference, use it.
        # Only tighten — never widen SL beyond ATR-based calculation.
        sd_1h = smc_d.get("sd", {})
        if side == "buy":
            # Demand zone below entry → SL just below zone bottom
            # Pick the NEAREST demand zone below price (highest zone_low < price)
            zones = sd_1h.get("demand_zones", [])
            price_val = ei_1h.get("price", 0) or 1
            below = [z for z in zones if z[1] < price_val]
            if below:
                nearest_zone_low = max(z[1] for z in below)  # closest demand floor
                zone_sl_pct = max(0, (price_val - nearest_zone_low) / price_val * 100 + 0.15)
                # 2026-04-18: honor the 1.5% floor — zone-tightening must not
                # push SL inside the ATR clamp (LINK bitget hit a 1.34% SL).
                if 1.5 <= zone_sl_pct < sl_pct:
                    sl_pct = zone_sl_pct
                    tp_pct = sl_pct * 2.5  # tighter SL → wider R:R
        else:
            # Supply zone above entry → SL just above zone top
            # Pick the NEAREST supply zone above price (lowest zone_high > price)
            zones = sd_1h.get("supply_zones", [])
            price_val = ei_1h.get("price", 0) or 1
            above = [z for z in zones if z[0] > price_val]
            if above:
                nearest_zone_high = min(z[0] for z in above)  # closest supply ceiling
                zone_sl_pct = max(0, (nearest_zone_high - price_val) / price_val * 100 + 0.15)
                # 2026-04-18: honor the 1.5% floor — zone-tightening must not
                # push SL inside the ATR clamp.
                if 1.5 <= zone_sl_pct < sl_pct:
                    sl_pct = zone_sl_pct
                    tp_pct = sl_pct * 2.5

        # Confidence: base 0.66 for 4 required, +0.08 per bonus, cap 0.95
        # (2026-04-17: stepped from 0.04 → 0.08 at user request; saturates at 4 bonuses)
        confidence = min(0.95, 0.66 + bonus_count * 0.08)

        reason = " | ".join(req_reasons + bonus_reasons)
        return {
            "score": score,
            "layers_ok": layers_ok,
            "side": side,
            "sl_pct": round(sl_pct, 2),
            "tp_pct": round(tp_pct, 2),
            "confidence": round(confidence, 2),
            "reason": reason,
        }

    def _algorithmic_portfolio(self, coins, data, exchange_indicators,
                                open_positions, exchange_balances,
                                risk_envelope) -> list:
        """Pure algorithmic scoring v3.1 — no Claude needed.
        Score all coins, pick top entries with score >= 66 AND 6+/10 layers.

        2026-04-14 v3.1: 4 required + 2+ bonus = score >= 66, layers >= 6.
        Regime filter (ADX/BB) kills entries before scoring even starts.
        Risk: 1.0% per trade, $4 hard cap.
        """
        actions = []
        max_new = risk_envelope.get("max_new_positions", 0)
        if max_new <= 0:
            return []

        # Build the union of static + dynamic blacklist bases to pre-filter
        blacklist_bases: set = set()
        try:
            import config as _cfg
            for sym in getattr(_cfg, "BLACKLIST_HARD", []) or []:
                blacklist_bases.add(sym.split("/")[0].upper())
        except Exception:
            pass
        try:
            from core.auto_mutator import AutoMutator as _AM
            dyn = _AM().get_effective_blacklist()
            for sym in dyn:
                blacklist_bases.add(sym.split("/")[0].upper())
        except Exception:
            pass

        # Score all coins
        scored = []
        skipped_bl = 0
        open_bases = {p.get("symbol", "").split("/")[0] for p in open_positions}
        for coin in coins:
            if coin in open_bases:
                continue  # Already have a position
            if coin.upper() in blacklist_bases:
                skipped_bl += 1
                continue  # Guaranteed to be rejected downstream
            ei = exchange_indicators.get(coin, {})
            if not ei:
                continue  # No indicator data
            result = self._score_coin(coin, data, ei)

            # Warehouse candidate emission — every scored symbol (spec §4, §6).
            # ALLOW if it passes the entry gate; otherwise SKIP with reason.
            gate_pass = result["score"] >= 66 and result["layers_ok"] >= 6
            decision = "ALLOW" if gate_pass else "SKIP"
            skip_reason = "" if gate_pass else (result.get("reason") or "gate_fail")
            cand_id = -1
            if get_warehouse is not None:
                try:
                    wh = get_warehouse()
                    ei_1h = ei.get("1h", {})
                    ei_4h = ei.get("4h", {})
                    feat = {
                        "score":       result.get("score"),
                        "layers_ok":   result.get("layers_ok"),
                        "confidence":  result.get("confidence"),
                        "sl_pct":      result.get("sl_pct"),
                        "tp_pct":      result.get("tp_pct"),
                        "rsi_1h":      ei_1h.get("rsi"),
                        "adx_1h":      ei_1h.get("adx"),
                        "adx_4h":      ei_4h.get("adx"),
                        "atr_pct_1h":  ei_1h.get("atr_pct"),
                        "bb_width_4h": ei_4h.get("bb_width"),
                        "vol_ratio":   ei_1h.get("vol_ratio"),
                        "ema20_above_50_4h": ei_4h.get("ema20_above_50"),
                        "ema20_above_50_1h": ei_1h.get("ema20_above_50"),
                        "funding_rate": (data.get("funding", {}).get(coin, {}) or {}).get("funding_rate"),
                        "ob_imbalance": (data.get("orderbook", {}).get(coin, {}) or {}).get("imbalance"),
                        "reason":       result.get("reason"),
                    }
                    cand_id = wh.record_candidate(
                        exchange="*",  # exchange picked later
                        symbol=f"{coin}/USDT",
                        market_type="futures",
                        side=result.get("side"),
                        strategy_family="systematic_v3_1",
                        entry_px=None,
                        stop_px=None,
                        target_px=None,
                        leverage=None,
                        size_pct=None,
                        confidence=result.get("confidence"),
                        decision=decision,
                        skip_reason=skip_reason,
                        features=feat,
                    )
                    # Phase 13.5 — shadow LR prediction. Logs to warehouse
                    # `predictions` table only; does NOT influence the
                    # gate_pass below. The point is to accumulate
                    # forward-attributed (p_win, outcome) pairs for the
                    # next re-fit (Phase 13.3 was bottlenecked at n=66).
                    if gate_pass:  # only log for trades we're actually about to take
                        try:
                            import time as _time
                            from core.shadow_predictor import ShadowPredictor
                            ShadowPredictor.get().log_entry(
                                ts=_time.time(),
                                symbol=f"{coin}/USDT",
                                side=str(result.get("side", "")),
                                features=feat,
                                warehouse=wh,
                            )
                        except Exception as _se:
                            logger.debug(f"[Shadow] log_entry skipped: {_se}")
                except Exception as _we:
                    logger.debug(f"[MCP-Algo] warehouse emit failed for {coin}: {_we}")

            if gate_pass:
                result["_candidate_id"] = cand_id
                scored.append((coin, result))

        if skipped_bl:
            logger.debug(f"[MCP-Algo] pre-filtered {skipped_bl} blacklisted coins")

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[1]["score"], reverse=True)

        # Track how many positions we've assigned to each exchange this cycle
        _ex_assigned = {}

        for coin, result in scored[:max_new]:
            # Pick best exchange — distribute across exchanges, prefer highest balance
            candidates = []
            for ex_name, bals in exchange_balances.items():
                assigned = _ex_assigned.get(ex_name, 0)
                fut_bal = bals.get("futures", 0)
                spot_bal = bals.get("spot", 0)
                if fut_bal >= 8 and result["side"] in ("buy", "sell"):
                    candidates.append((ex_name, "futures", fut_bal, assigned))
                elif spot_bal >= 8 and result["side"] == "buy":
                    candidates.append((ex_name, "spot", spot_bal, assigned))

            if not candidates:
                continue

            # Sort: least assigned first, then highest balance
            candidates.sort(key=lambda c: (c[3], -c[2]))
            best_ex = candidates[0][0]
            best_mtype = candidates[0][1]
            _ex_assigned[best_ex] = _ex_assigned.get(best_ex, 0) + 1

            # Spot: side must be buy, adjust SL/TP minimums
            side = result["side"]
            from config import RISK as _RISK_CFG
            leverage = min(3, _RISK_CFG.get("futures_max_leverage", 3)) if best_mtype == "futures" else 1
            if best_mtype == "spot":
                if side == "sell":
                    continue  # Can't short on spot
                sl_pct = max(result["sl_pct"], 2.0)
                tp_pct = max(result["tp_pct"], 4.0)
            else:
                sl_pct = max(result["sl_pct"], 2.0)
                tp_pct = max(result["tp_pct"], 4.0)

            # ── RISK-BASED SIZING v3 ───────────────────────────────
            # 2026-04-14 v3: 1.0% risk + $4 hard cap.
            # Previous 1.5%/$5 still produced -$11 and -$8 fat-tail
            # losses. On a $420 account, 1% = $4.20 which aligns with
            # the dollar cap. Combined with tighter SL [1.5%, 3%] this
            # keeps max theoretical loss per trade under control.
            #
            # Formula: size_pct = RISK% * 100 / (sl% * leverage)
            RISK_PER_TRADE_PCT = 1.0  # 1.0% of account per trade
            from config import MAX_LOSS_PER_TRADE_USD
            MAX_LOSS_USD = MAX_LOSS_PER_TRADE_USD  # $2.00 from config

            # Get total portfolio value for dollar-cap conversion
            total_bal = sum(
                b.get("futures", 0) + b.get("spot", 0)
                for b in exchange_balances.values()
            )

            size_pct = RISK_PER_TRADE_PCT * 100.0 / (sl_pct * leverage)

            # Dollar cap: max_size_pct where loss = total_bal * size_pct/100 * leverage * sl_pct/100 <= MAX_LOSS_USD
            if total_bal > 0 and leverage > 0 and sl_pct > 0:
                max_size_from_usd = MAX_LOSS_USD / (total_bal * leverage * sl_pct / 100.0) * 100.0
                size_pct = min(size_pct, max_size_from_usd)

            max_pos_pct = _RISK_CFG.get("max_position_pct", 0.05) * 100  # 1% live, 5% paper
            size_pct = min(max_pos_pct, max(0.5, size_pct))

            actions.append({
                "type": "OPEN",
                "symbol": f"{coin}/USDT",
                "exchange": best_ex,
                "market_type": best_mtype,
                "side": side,
                "leverage": leverage,
                "size_pct": size_pct,
                "sl_pct": sl_pct,
                "tp_pct": tp_pct,
                "confidence": result["confidence"],
                "mcp_score": result.get("score", 0),
                "reason": f"ALGO score={result['score']} layers={result['layers_ok']}/7 {result['reason']}",
                "position_id": "",
                "candidate_id": result.get("_candidate_id", -1),
            })
            logger.info(
                f"[MCP-Algo] OPEN {coin}/USDT {side} on {best_ex} "
                f"({best_mtype}) score={result['score']} "
                f"layers={result['layers_ok']}/7 conf={result['confidence']:.0%}")

        # Also generate CLOSE actions for losing positions
        for p in open_positions:
            pnl_pct = p.get("pnl_pct", 0) or 0
            age_min = p.get("age_min", 0) or 0
            coin = p.get("symbol", "").split("/")[0].split(":")[0]
            ei = exchange_indicators.get(coin, {})
            ei_4h = ei.get("4h", {})
            ei_1h = ei.get("1h", {})
            side = p.get("side", "buy")

            # 2026-04-12: Hard exit rules. Trend reversal = 1h EMA 20 crosses
            # below EMA 50 (not just ema_dir which uses 9/21/50 alignment).
            trend_against = False
            if side == "buy" and not ei_1h.get("ema20_above_50", True):
                trend_against = True
            elif side == "sell" and ei_1h.get("ema20_above_50", False):
                trend_against = True

            should_close = False
            reason = ""
            # 2026-04-21: pnl_pct is LEVERAGED. Scan-CLOSE thresholds were
            # aligned with the monitor (-12 / -3) so they can no longer fire
            # inside the SL's own leveraged-price range. Prior thresholds
            # (-5 / -1.5 / any-loss+aged) tripped at 0.17-1.67% price moves
            # at 3x leverage — well inside SL distance — and closed trades
            # at 0W/10L (systematic_close in warehouse, 2026-04-14..21).
            # HARD RULE 1: Absolute max loss — catastrophic overshoot only.
            if pnl_pct < -12.0:
                should_close = True
                reason = f"ALGO HARD MAX LOSS {pnl_pct:+.1f}%"
            # RULE 2 REMOVED 2026-04-25: "pnl < -3% leveraged + trend reversed"
            # produced 0W/10L all-time (firings on 2026-04-20..23 averaged
            # -1% PRICE i.e. inside the 1.5%-3.5% SL band — SL would have
            # caught the real losses, the rule just exited noise pullbacks
            # before recovery). Same pathology as the already-removed Rule 3.
            # SL/trailing/Rule 1 own normal exits; Rule 1 alone is enough as
            # the catastrophic-overshoot safety rail.
            # RULE 3 REMOVED 2026-04-21: "any loss + trend_against + aged>90m"
            # was a stale-close variant firing on tiny leveraged drawdowns and
            # produced the 0W/10L systematic_close bucket. Leveraged-aware
            # Rule 2 above catches real trend-reversed losses; monitor's own
            # rules handle the rest.
            # RULE 4: Stale flat position — DISABLED 2026-04-20.
            # Warehouse data: 15 trades closed by systematic_close stale rules
            # at 7% WR, avg -0.18 PnL each — the rule kills breakeven positions
            # before they can resolve. Let SL / trend-reversal / TP catch real
            # outcomes; funding cost (~0.02%/8h) is trivial vs the -0.18 avg
            # bleed this rule produces. Re-enable only after scored data shows
            # stale positions have negative forward expectancy.

            if should_close and p.get("id"):
                actions.append({
                    "type": "CLOSE",
                    "symbol": p.get("symbol", ""),
                    "exchange": (p.get("exchange") or "").lower(),
                    "market_type": p.get("market_type", "futures"),
                    "side": side,
                    "leverage": 1,
                    "size_pct": 0,
                    "sl_pct": 0,
                    "tp_pct": 0,
                    "confidence": 0.80,
                    "reason": reason,
                    "position_id": p["id"],
                })
                logger.info(f"[MCP-Algo] CLOSE {p.get('symbol','')} — {reason}")

        return actions

    def _algorithmic_position_monitor(self, positions: list, data: dict,
                                       exchange_indicators: dict) -> dict:
        """Systematic position monitor v3 — hard rules, no override.

        2026-04-14 v3.1: tighter exits to prevent fat-tail losses.
        Added MACD momentum flip and momentum exhaustion exits.
        Reduced max loss -5% to -3%, stale 3h to 6h (trend-following needs
        time), trend reversal triggers at any loss (not just -0.5%).

        Priority order (first match wins):
          1. HARD MAX LOSS:  pnl < -3%               -> CLOSE (no exceptions)
          2. TREND REVERSED: 1h EMA20 flipped + loss  -> CLOSE (> -0.5%)
          3. EXHAUSTION:     momentum exhaustion       -> CLOSE/TP
          4. STALE:          > 6h, |pnl| < 0.3%       -> CLOSE
          6. BIG WINNER:     pnl >= 4%, trend ok       -> HOLD (ride it)
          7. RSI EXTREME:    pnl >= 2%, RSI extreme    -> TAKE_PROFIT
          8. PROFIT LOCK:    pnl >= 2.0%               -> BREAKEVEN
          9. DEFAULT:                                   -> HOLD
        """
        advice = {}
        for p in positions:
            pid = p.get("id", "")
            pnl_pct = p.get("pnl_pct", 0) or 0
            age_min = p.get("age_min", 0) or 0
            side = p.get("side", "buy")
            coin = p.get("symbol", "?").split("/")[0].split(":")[0]

            ei = exchange_indicators.get(coin, {})
            ei_1h = ei.get("1h", {})
            rsi_1h = ei_1h.get("rsi", 50)

            # Trend check using EMA 20/50
            trend_with = False
            trend_against = False
            if side == "buy":
                trend_with = ei_1h.get("ema20_above_50", False)
                trend_against = not trend_with
            else:
                trend_with = not ei_1h.get("ema20_above_50", True)
                trend_against = not trend_with

            # MACD momentum check
            macd_hist = ei_1h.get("macd_hist", 0)
            macd_rising = ei_1h.get("macd_hist_rising", False)
            if side == "buy":
                macd_with = macd_hist > 0
                macd_fading = macd_hist > 0 and not macd_rising  # positive but shrinking
                macd_flipped = macd_hist < 0  # crossed below zero
            else:
                macd_with = macd_hist < 0
                macd_fading = macd_hist < 0 and macd_rising  # negative but shrinking
                macd_flipped = macd_hist > 0  # crossed above zero

            action = "HOLD"
            conf = 0.5
            reason = "default hold"

            # 1. HARD MAX LOSS backstop — strictly worse than max possible SL.
            # pnl_pct here is LEVERAGED; SL is unleveraged price move clamped
            # 1.5-3.5%. At 3x leverage, max loss at SL = 10.5% leveraged; at
            # 5x = 17.5%. Threshold -12% lets SL run at 3x/4x without being
            # pre-empted but still catches catastrophic overshoot.
            # (OLD -3.0 fired at r≈-0.3 of SL distance — cut winners short.)
            if pnl_pct < -12.0:
                action = "CLOSE"
                conf = 0.99
                reason = f"HARD MAX LOSS {pnl_pct:+.1f}% (limit -12%)"

            # 2. (MACD flip exit REMOVED — backtested at 0% WR across all
            #    symbols. 1h MACD is too noisy for exit signals. SL + trend
            #    reversal + stale handle downside protection adequately.)

            # 3. TREND REVERSED (1h EMA) + meaningful leveraged loss (> -3%).
            # Was -0.5% but pnl_pct is leveraged — at 3x that's only 0.17%
            # price move, well inside normal noise. -3% leveraged = ~1%
            # price move = a real, trend-confirmed reversal.
            elif pnl_pct < -3.0 and trend_against:
                action = "CLOSE"
                conf = 0.88
                reason = f"trend reversed + loss {pnl_pct:+.1f}%"

            # 5. STALE POSITION — DISABLED 2026-04-20.
            # Same rationale as _algorithmic_scan's RULE 4: 7% WR, -0.18 avg PnL
            # across 15 samples shows stale-close is a net-negative trigger.
            # Breakeven positions need room to resolve; SL handles real losses,
            # trend-reversal rule above handles real reversals.

            # 6. BIG WINNER riding trend + MACD with — protect first
            # 2026-04-16 (post-audit): moved ABOVE exhaustion rule because
            # a position at +5% with fading MACD and RSI 72 was hitting the
            # exhaustion rule (old rule 4) and exiting instead of riding to
            # full TP. Big winners with trend+MACD alignment get priority.
            elif pnl_pct >= 4.0 and trend_with and macd_with:
                action = "HOLD"
                conf = 0.85
                reason = f"winner pnl={pnl_pct:+.1f}% trend+MACD aligned, ride"

            # 4. MOMENTUM EXHAUSTION — RSI divergence / fading momentum
            # (Only applies when NOT already a big protected winner — see rule 6.)
            elif 0 < pnl_pct < 4.0 and macd_fading and (
                (side == "buy" and rsi_1h > 70) or
                (side == "sell" and rsi_1h < 30)
            ):
                action = "TAKE_PROFIT"
                conf = 0.82
                reason = f"exhaustion: pnl={pnl_pct:+.1f}% MACD fading RSI={rsi_1h:.0f}"

            # 7. RSI exhaustion with decent profit
            elif pnl_pct >= 2.0 and (
                (side == "buy" and rsi_1h > 72) or
                (side == "sell" and rsi_1h < 28)
            ):
                action = "TAKE_PROFIT"
                conf = 0.80
                reason = f"pnl={pnl_pct:+.1f}% RSI={rsi_1h:.0f} overbought/sold"

            # 8. Substantial profit -> lock to breakeven
            # 2026-04-14: raised threshold +1% -> +2%. At 2% SL / 4% TP,
            # breakeven at +1% fired at 1/3 of the way to TP — any small
            # retrace triggered the stop and collapsed realized R:R to
            # 1.12:1 on 58 v3 trades. +2% is halfway to TP, giving winners
            # room to breathe while still locking in meaningful protection.
            elif pnl_pct >= 2.0:
                action = "BREAKEVEN"
                conf = 0.72
                reason = f"pnl={pnl_pct:+.1f}% move SL to entry"

            # 9. (MACD-fading-breakeven REMOVED — 1h MACD histogram is too
            #    noisy for exit signals, same problem as the removed MACD
            #    flip exit. Was constantly tagging positions at breakeven
            #    before TP could be reached.)

            # 10. Default hold
            else:
                action = "HOLD"
                conf = 0.50
                reason = f"pnl={pnl_pct:+.1f}% trend={trend_with} macd={macd_with}"

            advice[pid] = {"action": action, "confidence": conf, "reason": reason}
            if action != "HOLD":
                logger.info(
                    f"[MCP-Algo] Position {pid[:8]}: {action} "
                    f"conf={conf:.0%} | {reason}")

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
