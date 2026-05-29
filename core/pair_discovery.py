"""
core/pair_discovery.py — Dynamic Trading Pair Discovery

Queries each exchange for ALL available USDT-margined markets,
filters by minimum 24h volume, returns the best pairs.

Includes UniverseFilter for runtime filtering:
  - Spread thresholds (bid-ask spread check)
  - Volatility thresholds (ATR-based: too high or too low)
  - Order book depth validation
  - Trading halt / maintenance detection
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
CORE_SYMBOLS = {"BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX"}

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

# Meme coins — excluded from trading (low predictability, high noise)
try:
    from config import MEME_COINS
except ImportError:
    MEME_COINS = {"DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "TURBO", "LOOM"}


def discover_pairs(exchange, exchange_name: str) -> dict:
    """
    Discover USDT spot + futures pairs on an exchange.
    Applies volume filter — only returns pairs with sufficient liquidity.
    Returns {"spot": [...], "futures": [...]} sorted by volume descending.
    """
    spot_pairs    = []
    futures_pairs = []

    try:
        if not hasattr(exchange, 'exchange') or exchange.exchange is None:
            return {"spot": [], "futures": []}
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

            # Skip stablecoins, wrapped tokens, and meme coins
            if base in _SKIP_BASES:
                continue
            if base in MEME_COINS:
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
            if not pairs["spot"] and not pairs["futures"]:
                config_pairs = TRADING_PAIRS.get(name, {})
                pairs = {
                    "spot":    config_pairs.get("spot", []),
                    "futures": config_pairs.get("futures", []),
                }
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


# ── Universe Filter: runtime quality checks before trading a symbol ────

class UniverseFilter:
    """
    Runtime filter applied BEFORE a signal is acted on.
    Checks spread, volatility, order book depth, and trading halts.
    """

    # Configurable thresholds
    # 2026-04-11: relaxed after 12h of log-observed rejections during a
    # quiet weekend session. BTC was being rejected at ATR=0.27% and most
    # mid-cap perpetual books show $2-3k at top 5 levels even in normal
    # conditions. Previous thresholds were tuned for active trending hours.
    MAX_SPREAD_PCT     = 0.005     # 0.5% max bid-ask spread
    MIN_ATR_PCT        = 0.0015    # 0.15% (was 0.3%) — allow BTC in quiet sessions
    MAX_ATR_PCT        = 0.12      # 12% max ATR (too volatile = too risky)
    MIN_DEPTH_USD      = 2_000     # $2k (was $5k) — fits typical USDT perp book depth
    ATR_PERIOD         = 14        # periods for ATR calculation

    # 2026-05-25 — Range-stability / chop filter (freqtrade-inspired, no-edge
    # forensics). The bot is a trend engine; the existing instantaneous ATR
    # check cannot distinguish a trending market from chop with the same ATR.
    # The forensics found the bot bleeds in chop (its EMA/ADX gates pass in
    # whipsaw that still loses). These two DAILY-candle measures filter chop
    # at the universe stage — a dynamic quality gate (UNBLOCK_ALL-safe: no
    # symbol/hour blacklist, just a property test on current market state).
    #   - Kaufman efficiency ratio: |net move| / sum(|bar moves|) over N days.
    #     ~1.0 = clean trend, ~0 = chop. Reject below MIN_TREND_EFFICIENCY.
    #   - Range-of-change: (HH-LL)/LL over N days. Reject dead/flat coins
    #     below MIN_RANGE_OF_CHANGE (a trend engine has nothing to grab).
    # Conservative floors — only remove the worst chop/dead coins so trade
    # frequency isn't gutted. Tunable; disable via RANGE_STABILITY_FILTER off.
    RANGE_LOOKBACK_DAYS   = 10
    MIN_RANGE_OF_CHANGE   = 0.02   # <2% span over 10d = dead/rangebound
    MIN_TREND_EFFICIENCY  = 0.20   # <0.20 ER = severe chop (no net direction)

    def __init__(self):
        self._cache: dict[str, dict] = {}   # symbol -> {result, ts}
        self._cache_ttl = 300               # 5 min cache
        self._reject_counts: dict[str, int] = {}  # tracking rejects

    def check(self, exchange, symbol: str, market_type: str = "spot") -> dict:
        """
        Run all universe quality checks on a symbol.
        Returns {"ok": bool, "reasons": [...], "spread": float, "atr": float, "depth": float}
        """
        import time as _t

        # Check cache
        cache_key = f"{exchange.name}:{symbol}:{market_type}"
        cached = self._cache.get(cache_key)
        if cached and (_t.time() - cached["ts"]) < self._cache_ttl:
            return cached["result"]

        reasons = []
        spread_pct = 0.0
        atr_pct = 0.0
        depth_usd = 0.0

        # 1. Trading halt / active check
        if not self._check_active(exchange, symbol):
            reasons.append("halted_or_inactive")

        # 2. Spread check
        spread_pct = self._check_spread(exchange, symbol, market_type)
        if spread_pct > self.MAX_SPREAD_PCT:
            reasons.append(f"spread_too_wide:{spread_pct*100:.2f}%>{self.MAX_SPREAD_PCT*100:.1f}%")

        # 3. Order book depth
        depth_usd = self._check_depth(exchange, symbol, market_type)
        if 0 < depth_usd < self.MIN_DEPTH_USD:
            reasons.append(f"thin_book:${depth_usd:.0f}<${self.MIN_DEPTH_USD}")

        # 4. Volatility check (ATR-based)
        atr_pct = self._check_volatility(exchange, symbol, market_type)
        if 0 < atr_pct < self.MIN_ATR_PCT:
            reasons.append(f"too_calm:ATR={atr_pct*100:.2f}%<{self.MIN_ATR_PCT*100:.1f}%")
        elif atr_pct > self.MAX_ATR_PCT:
            reasons.append(f"too_volatile:ATR={atr_pct*100:.1f}%>{self.MAX_ATR_PCT*100:.0f}%")

        # 5. Range-stability / chop check (2026-05-25, daily candles).
        # Gated so it can be disabled in one place if it over-filters.
        try:
            from config import RANGE_STABILITY_FILTER_ENABLED as _rsf
        except ImportError:
            _rsf = True
        if _rsf:
            roc, eff = self._check_range_stability(exchange, symbol, market_type)
            # roc==0 / eff==0 means no data → neutral (don't reject on missing data)
            if 0 < roc < self.MIN_RANGE_OF_CHANGE:
                reasons.append(
                    f"dead_range:{roc*100:.1f}%<{self.MIN_RANGE_OF_CHANGE*100:.0f}%")
            if 0 < eff < self.MIN_TREND_EFFICIENCY:
                reasons.append(
                    f"chop:ER={eff:.2f}<{self.MIN_TREND_EFFICIENCY:.2f}")

        ok = len(reasons) == 0
        result = {
            "ok": ok,
            "reasons": reasons,
            "spread_pct": spread_pct,
            "atr_pct": atr_pct,
            "depth_usd": depth_usd,
        }

        if not ok:
            self._reject_counts[symbol] = self._reject_counts.get(symbol, 0) + 1
            logger.info(
                f"[UniverseFilter] {symbol} REJECTED: {', '.join(reasons)}")

        self._cache[cache_key] = {"result": result, "ts": _t.time()}
        return result

    def _check_active(self, exchange, symbol: str) -> bool:
        """Check if the symbol is currently active/tradable on the exchange."""
        try:
            markets = exchange.exchange.markets or {}
            market = markets.get(symbol, {})
            if not market:
                return True  # unknown, assume ok
            active = market.get("active", True)
            info = market.get("info", {}) or {}
            # Check exchange-specific halt/maintenance flags
            status = info.get("status") or info.get("tradingStatus") or ""
            if isinstance(status, str) and status.lower() in (
                "halt", "halted", "break", "maintenance", "suspended"
            ):
                return False
            return bool(active)
        except Exception:
            return True

    def _check_spread(self, exchange, symbol: str,
                      market_type: str) -> float:
        """Return bid-ask spread as fraction of mid price."""
        try:
            ob = exchange.fetch_order_book(symbol, limit=5, market_type=market_type)
            if not ob or not ob.get("bids") or not ob.get("asks"):
                return 0.0
            best_bid = float(ob["bids"][0][0])
            best_ask = float(ob["asks"][0][0])
            mid = (best_bid + best_ask) / 2
            return (best_ask - best_bid) / mid if mid > 0 else 0.0
        except Exception:
            return 0.0

    def _check_depth(self, exchange, symbol: str,
                     market_type: str) -> float:
        """Return total USD liquidity at top 5 order book levels (min of bid/ask side)."""
        try:
            ob = exchange.fetch_order_book(symbol, limit=10, market_type=market_type)
            if not ob or not ob.get("bids") or not ob.get("asks"):
                return 0.0
            bid_depth = sum(float(b[0]) * float(b[1]) for b in ob["bids"][:5])
            ask_depth = sum(float(a[0]) * float(a[1]) for a in ob["asks"][:5])
            return min(bid_depth, ask_depth)
        except Exception:
            return 0.0

    def _check_volatility(self, exchange, symbol: str,
                          market_type: str) -> float:
        """Return ATR as percentage of price (14-period on 1h candles)."""
        try:
            candles = exchange.fetch_ohlcv(
                symbol, timeframe="1h", limit=self.ATR_PERIOD + 5,
                market_type=market_type)
            if not candles or len(candles) < self.ATR_PERIOD:
                return 0.0
            trs = []
            for i in range(1, len(candles)):
                h = float(candles[i][2])
                l = float(candles[i][3])
                c_prev = float(candles[i-1][4])
                tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
                trs.append(tr)
            atr = sum(trs[-self.ATR_PERIOD:]) / self.ATR_PERIOD
            last_close = float(candles[-1][4])
            return atr / last_close if last_close > 0 else 0.0
        except Exception:
            return 0.0

    def _check_range_stability(self, exchange, symbol: str,
                               market_type: str) -> tuple:
        """Return (range_of_change, trend_efficiency) over RANGE_LOOKBACK_DAYS
        daily candles. (0.0, 0.0) on missing data → caller treats as neutral.

        range_of_change = (highest_high - lowest_low) / lowest_low
            freqtrade RangeStabilityFilter — small = dead/flat coin.
        trend_efficiency = |close[-1]-close[0]| / sum(|close[i]-close[i-1]|)
            Kaufman efficiency ratio — ~0 = chop, ~1 = clean trend.
        """
        try:
            candles = exchange.fetch_ohlcv(
                symbol, timeframe="1d", limit=self.RANGE_LOOKBACK_DAYS + 1,
                market_type=market_type)
            if not candles or len(candles) < 3:
                return (0.0, 0.0)
            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            closes = [float(c[4]) for c in candles]
            hh, ll = max(highs), min(lows)
            roc = (hh - ll) / ll if ll > 0 else 0.0
            net = abs(closes[-1] - closes[0])
            path = sum(abs(closes[i] - closes[i - 1])
                       for i in range(1, len(closes)))
            eff = (net / path) if path > 0 else 0.0
            return (roc, eff)
        except Exception:
            return (0.0, 0.0)

    def rank(self, exchange, symbol: str, market_type: str = "spot") -> float:
        """Composite score 0-100 for symbol quality.
        Components: liquidity(25) + spread(25) + volatility(25) + funding(25).

        2026-04-14 learning-first pivot: any symbol outside
        config.UNIVERSE_WHITELIST scores 0 so the meta-filter/engine never
        considers it. Spec §13 — narrow to BTC/ETH until edge is proven.
        """
        try:
            from config import UNIVERSE_WHITELIST as _WL
        except ImportError:
            _WL = None
        if _WL is not None and symbol not in _WL:
            return 0.0

        score = 0.0

        # Spread score (25 pts): tighter = better
        spread = self._check_spread(exchange, symbol, market_type)
        if spread <= 0:
            score += 25  # no data = assume ok
        elif spread < 0.001:
            score += 25
        elif spread < 0.003:
            score += 15
        elif spread < self.MAX_SPREAD_PCT:
            score += 5

        # Depth / liquidity score (25 pts)
        depth = self._check_depth(exchange, symbol, market_type)
        if depth >= 50_000:
            score += 25
        elif depth >= 10_000:
            score += 20
        elif depth >= self.MIN_DEPTH_USD:
            score += 10

        # Volatility score (25 pts): prefer 0.3%-3% ATR sweet spot
        atr = self._check_volatility(exchange, symbol, market_type)
        if 0.003 <= atr <= 0.03:
            score += 25
        elif 0.0015 <= atr <= 0.05:
            score += 15
        elif atr > 0:
            score += 5

        # Funding score (25 pts, futures only): prefer near-zero funding
        if market_type == "futures":
            try:
                funding = exchange.exchange.fetch_funding_rate(symbol)
                rate = abs(float(funding.get("fundingRate", 0)))
                if rate < 0.0001:
                    score += 25
                elif rate < 0.0005:
                    score += 15
                elif rate < 0.001:
                    score += 5
            except Exception:
                score += 12  # unknown = neutral
        else:
            score += 25  # spot doesn't have funding

        return score

    def get_reject_stats(self) -> dict:
        """Return reject counts per symbol for monitoring."""
        return dict(self._reject_counts)
