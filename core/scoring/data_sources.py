"""External data fetchers and sparkline technicals."""
import json
import math
import urllib.error
import urllib.request

from loguru import logger

from core.scoring.constants import FETCH_TIMEOUT

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
# DATA SOURCE 3: Binance Funding Rates (futures sentiment)
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
                f"https://fapi.binance.com/fapi/v1/depth?symbol={sym}&limit=20",
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


def fetch_open_interest(coins: list) -> dict:
    """Fetch Open Interest trend from Binance futures (public endpoint).

    2026-05-25 (no-edge-forensics): OI was the one microstructure data gap.
    OI + price direction is a clean futures signal — rising price + rising OI
    = real trend (new money); rising price + falling OI = short-covering
    (likely to fade). Returns oi_delta_pct over the last ~6h so the Claude
    prompt can use it as qualitative entry-timing context. Deliberately NOT
    wired into the deterministic algo score (that would change WR and needs
    validation per the WR-floor rule); this is prompt/instrumentation only.
    """
    data = {}
    for coin in coins[:10]:  # per-symbol endpoint — limit like orderbook
        sym = f"{coin}USDT"
        try:
            resp = _http_get(
                f"https://fapi.binance.com/futures/data/openInterestHist"
                f"?symbol={sym}&period=1h&limit=7",
                timeout=5)
            if not isinstance(resp, list) or len(resp) < 2:
                continue
            oi_first = float(resp[0].get("sumOpenInterest", 0) or 0)
            oi_last = float(resp[-1].get("sumOpenInterest", 0) or 0)
            if oi_first <= 0:
                continue
            delta_pct = (oi_last - oi_first) / oi_first
            data[coin] = {
                "oi_current": oi_last,
                "oi_delta_pct": round(delta_pct, 4),  # >0 = OI rising (new money)
                "oi_trend": "rising" if delta_pct > 0.01
                            else ("falling" if delta_pct < -0.01 else "flat"),
            }
        except Exception:
            continue
    return data


def _microstructure_features(coin: str, data: dict) -> dict:
    """Extract scalp-relevant microstructure features for `coin` from the
    already-fetched `data` dict. Returns only keys whose source is present
    (absent -> omitted, so load_dataset's _coerce maps them to a neutral
    0.0). Every feature is defined so 0.0 is its neutral/missing value.
    Never raises."""
    import math as _math
    out: dict = {}
    try:
        fr = (data.get("funding") or {}).get(coin) or {}
        if "funding_rate" in fr:
            out["funding_rate"] = float(fr["funding_rate"])
        mark = float(fr.get("mark_price", 0) or 0)
        index = float(fr.get("index_price", 0) or 0)
        if mark > 0 and index > 0:
            out["basis_bps"] = (mark - index) / index * 1e4

        ob = (data.get("orderbook") or {}).get(coin) or {}
        if "imbalance" in ob:
            out["ob_imbalance"] = float(ob["imbalance"])
        bid = float(ob.get("bid_depth_usd", 0) or 0)
        ask = float(ob.get("ask_depth_usd", 0) or 0)
        if bid > 0 and ask > 0:
            out["depth_ratio"] = _math.log(bid / ask)

        oi = (data.get("oi") or {}).get(coin) or {}
        if "oi_delta_pct" in oi:
            out["oi_delta_6h"] = float(oi["oi_delta_pct"])
    except Exception:
        return {}
    return out


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
