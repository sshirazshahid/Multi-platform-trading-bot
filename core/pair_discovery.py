"""
core/pair_discovery.py — Dynamic Trading Pair Discovery

Queries each exchange for ALL available USDT-margined markets,
filters by minimum 24h volume, returns the best pairs.

FIX: Volume filter is now actually applied (was defined but never enforced).
     Low-volume / obscure tokens like DBR, GRASS, etc. are now excluded.
     Falls back to hardcoded TRADING_PAIRS if discovery finds nothing.
"""

from loguru import logger


# Minimum 24h volume in USDT to include a pair
MIN_VOLUME_SPOT    = 5_000_000    # $5M — filters out obscure tokens
MIN_VOLUME_FUTURES = 10_000_000   # $10M — futures need strong liquidity

# ALL mode: lower volume thresholds to discover more pairs
ALL_MIN_VOLUME_SPOT    = 1_000_000    # $1M — more aggressive discovery
ALL_MIN_VOLUME_FUTURES = 2_000_000    # $2M — include more futures pairs

# Maximum pairs per exchange (to avoid rate-limit issues)
MAX_SPOT_PAIRS    = 15
MAX_FUTURES_PAIRS = 12

# ALL mode: higher limits — scan everything
ALL_MAX_SPOT_PAIRS    = 30
ALL_MAX_FUTURES_PAIRS = 25

# Always include these core symbols regardless of volume ranking
CORE_SYMBOLS = {"BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX"}

# Commodity symbols to look for on futures
COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL"}

# Stock / equity token bases available on some exchanges (Bitget, Bybit)
STOCK_BASES = {
    "AAPL", "TSLA", "GOOG", "GOOGL", "AMZN", "MSFT", "META", "NVDA",
    "NFLX", "AMD", "COIN", "MSTR", "GME", "AMC", "PLTR", "BABA",
    "TSM", "INTC", "PYPL", "SQ", "SHOP", "UBER", "ABNB", "SNAP",
}

# Extended crypto symbols for ALL mode discovery
EXTENDED_CRYPTO = {
    "LINK", "DOT", "MATIC", "UNI", "AAVE", "ATOM", "FIL", "APT",
    "ARB", "OP", "SUI", "SEI", "TIA", "JUP", "WIF", "PEPE", "SHIB",
    "NEAR", "FTM", "INJ", "TRX", "LTC", "BCH", "ETC", "HBAR",
    "VET", "ALGO", "SAND", "MANA", "AXS", "RENDER", "FET", "TAO",
}

# Skip stablecoins and wrapped tokens
_SKIP_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP",
               "UST", "WBTC", "WETH", "WBNB", "STETH", "HBTC"}


def discover_pairs(exchange, exchange_name: str) -> dict:
    """
    Discover USDT spot + futures pairs on an exchange.
    Applies volume filter — only returns pairs with sufficient liquidity.
    Returns {"spot": [...], "futures": [...]} sorted by volume descending.
    """
    spot_pairs    = []
    futures_pairs = []

    try:
        markets = exchange.exchange.markets or exchange.exchange.load_markets()
    except Exception as e:
        logger.warning(f"[Discovery] {exchange_name}: load_markets failed: {e}")
        return {"spot": [], "futures": []}

    for symbol, market in markets.items():
        try:
            quote  = market.get("quote", "")
            base   = market.get("base", "")
            active = market.get("active", True)
            mtype  = market.get("type", "")

            # Only active USDT pairs
            if quote != "USDT" or not active:
                continue

            # Skip stablecoins and wrapped tokens
            if base in _SKIP_BASES:
                continue

            # Extract 24h volume from market info (varies by exchange)
            vol = 0.0
            info = market.get("info", {}) or {}
            for vol_key in ("quoteVolume", "volume24h", "turnover24h",
                            "vol24hUsd", "turnover", "baseVolume"):
                raw = info.get(vol_key)
                if raw:
                    try:
                        v = float(raw)
                        if v > vol:
                            vol = v
                    except (TypeError, ValueError):
                        pass

            # Fallback: use market-level volume if info doesn't have it
            if vol == 0:
                raw = market.get("quoteVolume") or market.get("baseVolume")
                if raw:
                    try:
                        vol = float(raw)
                    except (TypeError, ValueError):
                        pass

            is_future = mtype in ("swap", "future", "delivery", "linear", "inverse")
            is_spot   = mtype == "spot" or (not is_future and "/" in symbol and ":" not in symbol)

            if is_spot and ":" not in symbol:
                # Apply volume filter — always include CORE symbols
                if base in CORE_SYMBOLS or vol >= MIN_VOLUME_SPOT:
                    spot_pairs.append((symbol, base, vol))

            elif is_future and ":USDT" in symbol:
                # Apply volume filter — always include CORE + COMMODITY symbols
                if base in CORE_SYMBOLS or base in COMMODITY_BASES or vol >= MIN_VOLUME_FUTURES:
                    futures_pairs.append((symbol, base, vol))

        except Exception:
            continue

    # Sort: core symbols first (maintain order), then by volume descending
    def _sort_key(item):
        sym, base, vol = item
        is_core      = base in CORE_SYMBOLS
        is_commodity = base in COMMODITY_BASES
        priority     = 0 if is_core else (1 if is_commodity else 2)
        return (priority, -vol)

    spot_pairs.sort(key=_sort_key)
    futures_pairs.sort(key=_sort_key)

    # Take top N
    spot_result    = [s for s, _, _ in spot_pairs[:MAX_SPOT_PAIRS]]
    futures_result = [s for s, _, _ in futures_pairs[:MAX_FUTURES_PAIRS]]

    logger.info(
        f"[Discovery] {exchange_name}: {len(spot_result)} spot "
        f"+ {len(futures_result)} futures  "
        f"(from {len(spot_pairs)} + {len(futures_pairs)} passing volume filter)")

    if spot_result:
        logger.debug(f"[Discovery] {exchange_name} spot top5: {spot_result[:5]}")
    if futures_result:
        logger.debug(f"[Discovery] {exchange_name} futures top5: {futures_result[:5]}")

    return {"spot": spot_result, "futures": futures_result}


def discover_all(active_exchanges: dict) -> dict:
    """
    Discover pairs for all connected exchanges.
    Returns {exchange_name: {"spot": [...], "futures": [...]}}

    Falls back to hardcoded TRADING_PAIRS if discovery yields no results
    for an exchange, preventing the bot from running with an empty pair list.
    """
    from config import TRADING_PAIRS

    all_pairs = {}

    for name, exchange in active_exchanges.items():
        if not getattr(exchange, "_connected", False):
            continue
        try:
            pairs = discover_pairs(exchange, name)

            # If futures geo-blocked on this exchange, clear futures
            if getattr(exchange, "_futures_blocked", False):
                pairs["futures"] = []
                logger.info(f"[Discovery] {name}: futures disabled (geo-blocked)")

            # Fallback: if discovery found nothing, use hardcoded config
            fallback_used = False
            if not pairs["spot"] and not pairs["futures"]:
                config_pairs = TRADING_PAIRS.get(name, {})
                pairs = {
                    "spot":    config_pairs.get("spot", []),
                    "futures": config_pairs.get("futures", []),
                }
                fallback_used = True
                logger.warning(
                    f"[Discovery] {name}: discovery returned nothing — "
                    f"using hardcoded config ({len(pairs['spot'])} spot, "
                    f"{len(pairs['futures'])} futures)")
            elif not pairs["spot"]:
                # At least merge in hardcoded core spot pairs
                config_spot = TRADING_PAIRS.get(name, {}).get("spot", [])
                merged = list(dict.fromkeys(config_spot + pairs["spot"]))
                pairs["spot"] = merged[:MAX_SPOT_PAIRS]

            all_pairs[name] = pairs

        except Exception as e:
            logger.warning(f"[Discovery] {name}: failed: {e}")
            # Fall back to hardcoded config on exception
            config_pairs = TRADING_PAIRS.get(name, {})
            all_pairs[name] = {
                "spot":    config_pairs.get("spot", []),
                "futures": config_pairs.get("futures", []),
            }

    total_spot = sum(len(p["spot"]) for p in all_pairs.values())
    total_fut  = sum(len(p["futures"]) for p in all_pairs.values())
    logger.info(
        f"[Discovery] Final: {total_spot} spot + {total_fut} futures "
        f"across {len(all_pairs)} exchanges")

    return all_pairs


def _discover_pairs_all_mode(exchange, exchange_name: str) -> dict:
    """
    ALL mode discovery — scans every USDT market with lower volume thresholds
    and higher pair limits. Includes stocks, commodities, and extended crypto.
    """
    spot_pairs    = []
    futures_pairs = []

    try:
        markets = exchange.exchange.markets or exchange.exchange.load_markets()
    except Exception as e:
        logger.warning(f"[Discovery-ALL] {exchange_name}: load_markets failed: {e}")
        return {"spot": [], "futures": []}

    # ALL mode priority symbols: core + extended + commodities + stocks
    priority_bases = CORE_SYMBOLS | EXTENDED_CRYPTO | COMMODITY_BASES | STOCK_BASES

    for symbol, market in markets.items():
        try:
            quote  = market.get("quote", "")
            base   = market.get("base", "")
            active = market.get("active", True)
            mtype  = market.get("type", "")

            if quote != "USDT" or not active:
                continue
            if base in _SKIP_BASES:
                continue

            # Extract 24h volume
            vol = 0.0
            info = market.get("info", {}) or {}
            for vol_key in ("quoteVolume", "volume24h", "turnover24h",
                            "vol24hUsd", "turnover", "baseVolume"):
                raw = info.get(vol_key)
                if raw:
                    try:
                        v = float(raw)
                        if v > vol:
                            vol = v
                    except (TypeError, ValueError):
                        pass
            if vol == 0:
                raw = market.get("quoteVolume") or market.get("baseVolume")
                if raw:
                    try:
                        vol = float(raw)
                    except (TypeError, ValueError):
                        pass

            is_future = mtype in ("swap", "future", "delivery", "linear", "inverse")
            is_spot   = mtype == "spot" or (not is_future and "/" in symbol and ":" not in symbol)

            if is_spot and ":" not in symbol:
                if base in priority_bases or vol >= ALL_MIN_VOLUME_SPOT:
                    spot_pairs.append((symbol, base, vol))

            elif is_future and ":USDT" in symbol:
                if base in priority_bases or vol >= ALL_MIN_VOLUME_FUTURES:
                    futures_pairs.append((symbol, base, vol))

        except Exception:
            continue

    # Sort: core first, then commodities/stocks, then by volume
    def _sort_key(item):
        sym, base, vol = item
        if base in CORE_SYMBOLS:
            priority = 0
        elif base in COMMODITY_BASES:
            priority = 1
        elif base in STOCK_BASES:
            priority = 2
        elif base in EXTENDED_CRYPTO:
            priority = 3
        else:
            priority = 4
        return (priority, -vol)

    spot_pairs.sort(key=_sort_key)
    futures_pairs.sort(key=_sort_key)

    spot_result    = [s for s, _, _ in spot_pairs[:ALL_MAX_SPOT_PAIRS]]
    futures_result = [s for s, _, _ in futures_pairs[:ALL_MAX_FUTURES_PAIRS]]

    # Log what we found by category
    spot_bases    = {s.split("/")[0] for s in spot_result}
    futures_bases = {s.split("/")[0] for s in futures_result}
    commodities   = (spot_bases | futures_bases) & COMMODITY_BASES
    stocks        = (spot_bases | futures_bases) & STOCK_BASES

    logger.info(
        f"[Discovery-ALL] {exchange_name}: "
        f"{len(spot_result)} spot + {len(futures_result)} futures")
    if commodities:
        logger.info(f"[Discovery-ALL] {exchange_name} commodities: {commodities}")
    if stocks:
        logger.info(f"[Discovery-ALL] {exchange_name} stocks: {stocks}")

    return {"spot": spot_result, "futures": futures_result}


def discover_all_mode(active_exchanges: dict) -> dict:
    """
    ALL mode discovery for all connected exchanges.
    Uses lower volume thresholds, higher pair limits, includes
    stocks/commodities/extended crypto.
    """
    from config import TRADING_PAIRS

    all_pairs = {}

    for name, exchange in active_exchanges.items():
        if not getattr(exchange, "_connected", False):
            continue
        try:
            pairs = _discover_pairs_all_mode(exchange, name)

            if getattr(exchange, "_futures_blocked", False):
                pairs["futures"] = []
                logger.info(f"[Discovery-ALL] {name}: futures disabled (geo-blocked)")

            # Fallback to hardcoded if nothing found
            if not pairs["spot"] and not pairs["futures"]:
                config_pairs = TRADING_PAIRS.get(name, {})
                pairs = {
                    "spot":    config_pairs.get("spot", []),
                    "futures": config_pairs.get("futures", []),
                }

            all_pairs[name] = pairs

        except Exception as e:
            logger.warning(f"[Discovery-ALL] {name}: failed: {e}")
            config_pairs = TRADING_PAIRS.get(name, {})
            all_pairs[name] = {
                "spot":    config_pairs.get("spot", []),
                "futures": config_pairs.get("futures", []),
            }

    total_spot = sum(len(p["spot"]) for p in all_pairs.values())
    total_fut  = sum(len(p["futures"]) for p in all_pairs.values())
    logger.info(
        f"[Discovery-ALL] Total: {total_spot} spot + {total_fut} futures "
        f"across {len(all_pairs)} exchanges")

    return all_pairs
