"""
core/pair_discovery.py — Dynamic Trading Pair Discovery.

FIX: Volume filter is now actually applied (was defined but never enforced).
     Low-volume / obscure tokens (DBR, GRASS, etc.) are now excluded.
     Falls back to hardcoded TRADING_PAIRS if discovery finds nothing.
"""

from loguru import logger

MIN_VOLUME_SPOT    = 5_000_000    # $5M 24h volume required for spot
MIN_VOLUME_FUTURES = 10_000_000   # $10M for futures (needs real liquidity)
MAX_SPOT_PAIRS     = 15
MAX_FUTURES_PAIRS  = 12

CORE_SYMBOLS    = {"BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX"}
COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL"}
_SKIP_BASES     = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP",
                   "UST", "WBTC", "WETH", "WBNB", "STETH"}


def discover_pairs(exchange, exchange_name: str) -> dict:
    """
    Discover USDT pairs on an exchange, filtered by 24h volume.
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
            base   = market.get("base",  "")
            active = market.get("active", True)
            mtype  = market.get("type",  "")

            if quote != "USDT" or not active or base in _SKIP_BASES:
                continue

            # Extract 24h USDT volume from any available field
            vol  = 0.0
            info = market.get("info", {}) or {}
            for fld in ("quoteVolume", "volume24h", "turnover24h",
                        "vol24hUsd", "turnover", "baseVolume"):
                raw = info.get(fld)
                if raw:
                    try:
                        v = float(raw)
                        if v > vol: vol = v
                    except (TypeError, ValueError):
                        pass
            if vol == 0:
                raw = market.get("quoteVolume") or market.get("baseVolume")
                if raw:
                    try: vol = float(raw)
                    except (TypeError, ValueError): pass

            is_future = mtype in ("swap", "future", "delivery", "linear", "inverse")
            is_spot   = mtype == "spot" or (not is_future and ":" not in symbol)

            if is_spot and ":" not in symbol:
                if base in CORE_SYMBOLS or vol >= MIN_VOLUME_SPOT:
                    spot_pairs.append((symbol, base, vol))
            elif is_future and ":USDT" in symbol:
                if base in CORE_SYMBOLS or base in COMMODITY_BASES or vol >= MIN_VOLUME_FUTURES:
                    futures_pairs.append((symbol, base, vol))

        except Exception:
            continue

    def _key(item):
        sym, base, vol = item
        return (0 if base in CORE_SYMBOLS else (1 if base in COMMODITY_BASES else 2), -vol)

    spot_pairs.sort(key=_key)
    futures_pairs.sort(key=_key)

    spot_result    = [s for s, _, _ in spot_pairs[:MAX_SPOT_PAIRS]]
    futures_result = [s for s, _, _ in futures_pairs[:MAX_FUTURES_PAIRS]]

    logger.info(f"[Discovery] {exchange_name}: {len(spot_result)} spot + "
                f"{len(futures_result)} futures (from {len(spot_pairs)} + "
                f"{len(futures_pairs)} passing volume filter)")
    return {"spot": spot_result, "futures": futures_result}


def discover_all(active_exchanges: dict) -> dict:
    """
    Discover pairs for all connected exchanges.
    Falls back to hardcoded TRADING_PAIRS if discovery returns nothing.
    """
    from config import TRADING_PAIRS

    all_pairs = {}
    for name, exchange in active_exchanges.items():
        if not getattr(exchange, "_connected", False):
            continue
        try:
            pairs = discover_pairs(exchange, name)

            if getattr(exchange, "_futures_blocked", False):
                pairs["futures"] = []

            if not pairs["spot"] and not pairs["futures"]:
                cfg = TRADING_PAIRS.get(name, {})
                pairs = {"spot": cfg.get("spot", []), "futures": cfg.get("futures", [])}
                logger.warning(f"[Discovery] {name}: nothing found — using hardcoded config")
            elif not pairs["spot"]:
                cfg_spot = TRADING_PAIRS.get(name, {}).get("spot", [])
                merged   = list(dict.fromkeys(cfg_spot + pairs["spot"]))
                pairs["spot"] = merged[:MAX_SPOT_PAIRS]

            all_pairs[name] = pairs

        except Exception as e:
            logger.warning(f"[Discovery] {name}: failed: {e}")
            cfg = TRADING_PAIRS.get(name, {})
            all_pairs[name] = {"spot": cfg.get("spot", []), "futures": cfg.get("futures", [])}

    total_s = sum(len(p["spot"])    for p in all_pairs.values())
    total_f = sum(len(p["futures"]) for p in all_pairs.values())
    logger.info(f"[Discovery] Final: {total_s} spot + {total_f} futures "
                f"across {len(all_pairs)} exchanges")
    return all_pairs
