"""Metadata-driven trading-pair discovery and runtime universe checks.

Discovery and UniverseFilter deliberately fail closed. A venue contributes no
new symbols when its current market metadata cannot be loaded, while position
monitoring remains independent and can continue from persisted positions.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Mapping
from typing import Optional

from loguru import logger

# Minimum 24h quote volume in USDT to include a pair.
MIN_VOLUME_SPOT = 5_000_000
MIN_VOLUME_FUTURES = 10_000_000

# ALL mode uses lower volume thresholds and larger limits.
ALL_MIN_VOLUME_SPOT = 1_000_000
ALL_MIN_VOLUME_FUTURES = 2_000_000

MAX_SPOT_PAIRS = 15
MAX_FUTURES_PAIRS = 12
ALL_MAX_SPOT_PAIRS = 30
ALL_MAX_FUTURES_PAIRS = 25

CORE_SYMBOLS = {"BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX"}
EXTENDED_CRYPTO = {
    "LINK",
    "DOT",
    "MATIC",
    "UNI",
    "AAVE",
    "ATOM",
    "FIL",
    "APT",
    "ARB",
    "OP",
    "SUI",
    "SEI",
    "TIA",
    "JUP",
    "NEAR",
    "FTM",
    "INJ",
    "TRX",
    "LTC",
    "BCH",
    "ETC",
    "HBAR",
    "VET",
    "ALGO",
    "SAND",
    "MANA",
    "AXS",
    "RENDER",
    "FET",
    "TAO",
}

# These asset classes are intentionally disabled for this crypto-only universe.
COMMODITY_BASES = set()
_COMMODITY_BASES_DISABLED = {
    "XAU",
    "XAG",
    "WTI",
    "CL",
    "BRENT",
    "UKOIL",
    "USOIL",
    "GOLD",
    "SILVER",
    "COPPER",
    "NATGAS",
}
STOCK_BASES = set()
_STOCK_BASES_DISABLED = {
    "AAPL",
    "TSLA",
    "GOOG",
    "GOOGL",
    "AMZN",
    "MSFT",
    "META",
    "NVDA",
    "NFLX",
    "AMD",
    "COIN",
    "MSTR",
    "GME",
    "AMC",
    "PLTR",
    "BABA",
    "TSM",
    "INTC",
    "PYPL",
    "SQ",
    "SHOP",
    "UBER",
    "ABNB",
    "SNAP",
    "SPY",
    "QQQ",
}
# Symbols quarantined after data incidents (e.g. recurring mark-price feed
# failures) until the underlying path is fixed — env-driven so the quarantine
# is reversible without code changes (added 2026-07-18, first use: AXS).
_INCIDENT_QUARANTINE_BASES = {
    b.strip().upper()
    for b in os.getenv("INCIDENT_QUARANTINE_BASES", "").split(",")
    if b.strip()
}
_DISABLED_BASES = (
    _COMMODITY_BASES_DISABLED | _STOCK_BASES_DISABLED | _INCIDENT_QUARANTINE_BASES
)

_STABLE_BASES = {
    "USDT",
    "USDC",
    "BUSD",
    "DAI",
    "TUSD",
    "FDUSD",
    "USDP",
    "UST",
    "USTC",
    "USDE",
    "USDS",
    "PYUSD",
    "GUSD",
    "FRAX",
    "LUSD",
    "USDD",
    "CRVUSD",
    "USD1",
    "SUSDE",
    "SDAI",
}
_WRAPPED_BASES = {
    "WBTC",
    "WETH",
    "WBNB",
    "STETH",
    "WSTETH",
    "WEETH",
    "HBTC",
    "BTCB",
    "CBETH",
    "RETH",
    "WBETH",
    "BETH",
}
_SKIP_BASES = _STABLE_BASES | _WRAPPED_BASES

try:
    from config import MEME_COINS
except ImportError:
    MEME_COINS = {"DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "TURBO", "LOOM"}

_MEME_BASES = {str(base).upper() for base in MEME_COINS}
_LEVERAGED_BASE_RE = re.compile(r"^[A-Z0-9]{2,}(?:UP|DOWN|BULL|BEAR|[235](?:L|S))$")
_STATUS_FIELDS = (
    "status",
    "tradingStatus",
    "state",
    "marketStatus",
    "symbolStatus",
    "contractStatus",
)
_TRADING_FLAG_FIELDS = (
    "isTrading",
    "tradingEnabled",
    "enableTrading",
    "apiTradeEnabled",
)


def _upper(value) -> str:
    return str(value or "").strip().upper()


def _status_token(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _false_flag(value) -> bool:
    if value is False or value == 0:
        return True
    return str(value).strip().lower() in {"false", "0", "no", "off", "disabled"}


def _status_rejection_reason(market: Mapping) -> Optional[str]:
    info = market.get("info")
    if not isinstance(info, Mapping):
        info = {}

    statuses = [market.get("status")]
    statuses.extend(info.get(field) for field in _STATUS_FIELDS)
    for status in statuses:
        token = _status_token(status)
        if not token:
            continue
        if any(
            marker in token for marker in ("prelaunch", "premarket", "prelisting", "pretrading")
        ):
            return "prelaunch_or_premarket"
        if any(marker in token for marker in ("delist", "expired", "settled")):
            return "delisted_market"
        if any(
            marker in token
            for marker in (
                "halt",
                "suspend",
                "maintenance",
                "nottrading",
                "posttrading",
                "endofday",
                "auctionmatch",
                "break",
                "inactive",
                "offline",
                "closed",
                "disabled",
                "restricted",
                "pending",
                "cancelonly",
                "limitonly",
                "reduceonly",
                "settling",
            )
        ):
            return "inactive_market"

    for field in _TRADING_FLAG_FIELDS:
        if field in info and _false_flag(info[field]):
            return "inactive_market"
    return None


def _is_leveraged_token(base: str, market: Mapping) -> bool:
    if market.get("leveraged") is True:
        return True

    info = market.get("info")
    if not isinstance(info, Mapping):
        info = {}
    for field in ("isLeveragedToken", "leveragedToken"):
        value = info.get(field)
        if value is True or str(value).strip().lower() in {"true", "1", "yes"}:
            return True
    for field in ("symbolType", "type", "category", "productType"):
        if "leveraged" in str(info.get(field, "")).lower():
            return True
    return bool(_LEVERAGED_BASE_RE.fullmatch(base))


def _asset_rejection_reason(base: str, market: Mapping) -> Optional[str]:
    if not base:
        return "invalid_market_metadata"
    if base in _DISABLED_BASES:
        return f"disabled_asset:{base}"
    if base in _SKIP_BASES:
        return f"stable_or_wrapped:{base}"
    if base in _MEME_BASES:
        return f"meme_token:{base}"
    if _is_leveraged_token(base, market):
        return f"leveraged_token:{base}"
    return None


def _market_has_lane_shape(market: Mapping, market_type: str) -> bool:
    venue_type = str(market.get("type") or "").lower()
    if market_type == "spot":
        return market.get("spot") is True or venue_type == "spot"
    return (
        market.get("swap") is True
        or market.get("future") is True
        or market.get("contract") is True
        or venue_type in {"swap", "future", "delivery"}
    )


def _market_rejection_reason(market: Mapping, market_type: str) -> Optional[str]:
    if not isinstance(market, Mapping):
        return "invalid_market_metadata"

    base = _upper(market.get("base"))
    quote = _upper(market.get("quote"))
    if quote != "USDT":
        return "unsupported_quote"

    asset_reason = _asset_rejection_reason(base, market)
    if asset_reason:
        return asset_reason

    status_reason = _status_rejection_reason(market)
    if status_reason:
        return status_reason
    if market.get("active") is not True:
        return "inactive_market"

    venue_type = str(market.get("type") or "").lower()
    if market_type == "spot":
        if not _market_has_lane_shape(market, "spot"):
            return "wrong_market_type"
        if (
            market.get("contract") is True
            or market.get("swap") is True
            or market.get("future") is True
        ):
            return "wrong_market_type"
        return None

    expiry = market.get("expiry")
    expiry_datetime = market.get("expiryDatetime")
    if market.get("future") is True or venue_type in {"future", "delivery"}:
        return "dated_future"
    if expiry not in (None, "", 0) or expiry_datetime not in (None, ""):
        return "dated_future"
    if not (market.get("swap") is True or venue_type == "swap"):
        return "not_perpetual_swap"
    settle = _upper(market.get("settle"))
    if settle and settle != "USDT":
        return "unsupported_settle"
    return None


def _market_symbol(market_key, market: Mapping) -> str:
    symbol = market.get("symbol") or market_key
    return str(symbol).strip() if isinstance(symbol, str) else ""


def _load_current_markets(exchange) -> Mapping:
    raw_exchange = getattr(exchange, "exchange", None)
    if raw_exchange is None:
        raise RuntimeError("exchange client is unavailable")
    load_markets = getattr(raw_exchange, "load_markets", None)
    if not callable(load_markets):
        raise RuntimeError("venue does not expose load_markets")

    markets = load_markets(reload=True)
    if not isinstance(markets, Mapping):
        raise RuntimeError("load_markets returned invalid metadata")
    return markets


def _quote_volume(market: Mapping) -> float:
    """Read only quote-denominated volume; base volume is not comparable to USD."""
    info = market.get("info")
    if not isinstance(info, Mapping):
        info = {}

    values = []
    for source in (market, info):
        for key in (
            "quoteVolume",
            "quoteVolume24h",
            "turnover24h",
            "turnover24hUsd",
            "vol24hUsd",
            "volumeUsd24h",
            "turnover",
        ):
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed) and parsed >= 0:
                values.append(parsed)
    return max(values, default=0.0)


def _discover_with_limits(
    exchange,
    exchange_name: str,
    *,
    min_spot_volume: float,
    min_futures_volume: float,
    max_spot_pairs: int,
    max_futures_pairs: int,
    all_mode: bool,
) -> dict:
    label = "Discovery-ALL" if all_mode else "Discovery"
    try:
        markets = _load_current_markets(exchange)
    except Exception as exc:
        logger.warning(f"[{label}] {exchange_name}: current market metadata failed: {exc}")
        return {"spot": [], "futures": []}

    if not markets:
        logger.warning(f"[{label}] {exchange_name}: venue returned no market metadata")
        return {"spot": [], "futures": []}

    spot_pairs = []
    futures_pairs = []
    priority_bases = CORE_SYMBOLS | (EXTENDED_CRYPTO if all_mode else set())

    for market_key, market in markets.items():
        if not isinstance(market, Mapping):
            continue
        symbol = _market_symbol(market_key, market)
        if not symbol:
            continue

        base = _upper(market.get("base"))
        volume = _quote_volume(market)
        if _market_rejection_reason(market, "spot") is None:
            if base in priority_bases or volume >= min_spot_volume:
                spot_pairs.append((symbol, base, volume))
        elif _market_rejection_reason(market, "futures") is None:
            if base in priority_bases or volume >= min_futures_volume:
                futures_pairs.append((symbol, base, volume))

    def sort_key(item):
        _symbol, base, volume = item
        if base in CORE_SYMBOLS:
            priority = 0
        elif all_mode and base in EXTENDED_CRYPTO:
            priority = 1
        else:
            priority = 2
        return (priority, -volume)

    spot_pairs.sort(key=sort_key)
    futures_pairs.sort(key=sort_key)
    spot_result = [symbol for symbol, _, _ in spot_pairs[:max_spot_pairs]]
    futures_result = [symbol for symbol, _, _ in futures_pairs[:max_futures_pairs]]

    logger.info(
        f"[{label}] {exchange_name}: {len(spot_result)} spot + "
        f"{len(futures_result)} futures from current venue metadata"
    )
    return {"spot": spot_result, "futures": futures_result}


def discover_pairs(exchange, exchange_name: str) -> dict:
    """Discover eligible USDT spot markets and USDT perpetual swaps."""
    return _discover_with_limits(
        exchange,
        exchange_name,
        min_spot_volume=MIN_VOLUME_SPOT,
        min_futures_volume=MIN_VOLUME_FUTURES,
        max_spot_pairs=MAX_SPOT_PAIRS,
        max_futures_pairs=MAX_FUTURES_PAIRS,
        all_mode=False,
    )


def discover_all(active_exchanges: dict) -> dict:
    """Discover each connected venue without static-pair fallbacks."""
    all_pairs = {}
    for name, exchange in active_exchanges.items():
        if not getattr(exchange, "_connected", False):
            continue
        try:
            pairs = discover_pairs(exchange, name)
            if getattr(exchange, "_futures_blocked", False):
                pairs["futures"] = []
                logger.info(f"[Discovery] {name}: futures disabled (geo-blocked)")
            all_pairs[name] = pairs
        except Exception as exc:
            logger.warning(f"[Discovery] {name}: failed closed: {exc}")
            all_pairs[name] = {"spot": [], "futures": []}

    total_spot = sum(len(pairs["spot"]) for pairs in all_pairs.values())
    total_futures = sum(len(pairs["futures"]) for pairs in all_pairs.values())
    logger.info(
        f"[Discovery] Final: {total_spot} spot + {total_futures} futures "
        f"across {len(all_pairs)} exchanges"
    )
    return all_pairs


def _discover_pairs_all_mode(exchange, exchange_name: str) -> dict:
    """Discover a larger crypto universe under the same strict eligibility rules."""
    return _discover_with_limits(
        exchange,
        exchange_name,
        min_spot_volume=ALL_MIN_VOLUME_SPOT,
        min_futures_volume=ALL_MIN_VOLUME_FUTURES,
        max_spot_pairs=ALL_MAX_SPOT_PAIRS,
        max_futures_pairs=ALL_MAX_FUTURES_PAIRS,
        all_mode=True,
    )


def discover_all_mode(active_exchanges: dict) -> dict:
    """Run ALL-mode discovery without falling back to configured symbols."""
    all_pairs = {}
    for name, exchange in active_exchanges.items():
        if not getattr(exchange, "_connected", False):
            continue
        try:
            pairs = _discover_pairs_all_mode(exchange, name)
            if getattr(exchange, "_futures_blocked", False):
                pairs["futures"] = []
                logger.info(f"[Discovery-ALL] {name}: futures disabled (geo-blocked)")
            all_pairs[name] = pairs
        except Exception as exc:
            logger.warning(f"[Discovery-ALL] {name}: failed closed: {exc}")
            all_pairs[name] = {"spot": [], "futures": []}

    total_spot = sum(len(pairs["spot"]) for pairs in all_pairs.values())
    total_futures = sum(len(pairs["futures"]) for pairs in all_pairs.values())
    logger.info(
        f"[Discovery-ALL] Total: {total_spot} spot + {total_futures} futures "
        f"across {len(all_pairs)} exchanges"
    )
    return all_pairs


class UniverseFilter:
    """Fail-closed runtime quality checks before opening a new position."""

    MAX_SPREAD_PCT = 0.005
    MIN_ATR_PCT = 0.0015
    MAX_ATR_PCT = 0.12
    MIN_DEPTH_USD = 2_000
    ATR_PERIOD = 14

    RANGE_LOOKBACK_DAYS = 10
    MIN_RANGE_OF_CHANGE = 0.02
    MIN_TREND_EFFICIENCY = 0.20

    def __init__(self):
        # (venue, market lane, canonical exchange symbol) -> cached result.
        self._cache: dict[tuple[str, str, str], dict] = {}
        self._cache_ttl = 300
        self._reject_counts: dict[str, int] = {}
        try:
            from config import MIN_TREND_EFFICIENCY as configured_min_efficiency

            self.MIN_TREND_EFFICIENCY = float(configured_min_efficiency)
        except Exception:
            pass

    @staticmethod
    def _normalise_market_type(market_type: str) -> Optional[str]:
        value = str(market_type or "").strip().lower()
        if value == "spot":
            return "spot"
        if value in {"future", "futures", "swap", "perpetual"}:
            return "futures"
        return None

    @staticmethod
    def _venue_name(exchange) -> str:
        try:
            name = getattr(exchange, "name", "")
        except Exception:
            name = ""
        raw_exchange = getattr(exchange, "exchange", None)
        raw_id = getattr(raw_exchange, "id", "") if raw_exchange is not None else ""
        return str(name or raw_id or exchange.__class__.__name__).strip().lower()

    def _market_catalog(self, exchange) -> Optional[Mapping]:
        raw_exchange = getattr(exchange, "exchange", None)
        markets = getattr(raw_exchange, "markets", None) if raw_exchange is not None else None
        if isinstance(markets, Mapping) and markets:
            return markets
        try:
            markets = _load_current_markets(exchange)
        except Exception as exc:
            logger.warning(f"[UniverseFilter] market metadata unavailable: {exc}")
            return None
        return markets if markets else None

    def _resolve_market(self, exchange, symbol: str, market_type: str):
        markets = self._market_catalog(exchange)
        if not markets or not isinstance(symbol, str) or not symbol.strip():
            return None

        requested = symbol.strip()
        candidate_keys = [requested]
        if market_type == "futures" and ":" not in requested and "/" in requested:
            candidate_keys.insert(0, f"{requested}:USDT")

        for key in candidate_keys:
            market = markets.get(key)
            if isinstance(market, Mapping) and _market_has_lane_shape(market, market_type):
                return key, market

        requested_folded = requested.casefold()
        requested_plain = requested.split(":", 1)[0].casefold()
        for key, market in markets.items():
            if not isinstance(market, Mapping) or not _market_has_lane_shape(market, market_type):
                continue
            identifiers = {
                str(key).casefold(),
                str(market.get("symbol") or "").casefold(),
                str(market.get("id") or "").casefold(),
            }
            canonical = _market_symbol(key, market)
            canonical_plain = canonical.split(":", 1)[0].casefold()
            if requested_folded in identifiers or requested_plain == canonical_plain:
                return key, market
        return None

    @staticmethod
    def _result(
        *,
        ok: bool,
        reasons: list[str],
        exchange_symbol: Optional[str],
        spread_pct: Optional[float] = None,
        atr_pct: Optional[float] = None,
        depth_usd: Optional[float] = None,
        range_of_change: Optional[float] = None,
        trend_efficiency: Optional[float] = None,
    ) -> dict:
        return {
            "ok": ok,
            "reasons": reasons,
            "exchange_symbol": exchange_symbol,
            "spread_pct": spread_pct,
            "atr_pct": atr_pct,
            "depth_usd": depth_usd,
            "range_of_change": range_of_change,
            "trend_efficiency": trend_efficiency,
        }

    def _record_rejection(self, symbol: str, reasons: list[str]) -> None:
        self._reject_counts[symbol] = self._reject_counts.get(symbol, 0) + 1
        logger.info(f"[UniverseFilter] {symbol} REJECTED: {', '.join(reasons)}")

    def check(self, exchange, symbol: str, market_type: str = "spot") -> dict:
        """Run metadata, book, volatility, and range checks for one market."""
        lane = self._normalise_market_type(market_type)
        if lane is None:
            reasons = ["invalid_market_type"]
            self._record_rejection(symbol, reasons)
            return self._result(ok=False, reasons=reasons, exchange_symbol=None)

        resolved = self._resolve_market(exchange, symbol, lane)
        if resolved is None:
            reasons = ["unknown_market"]
            self._record_rejection(symbol, reasons)
            return self._result(ok=False, reasons=reasons, exchange_symbol=None)

        market_key, market = resolved
        exchange_symbol = _market_symbol(market_key, market)
        metadata_reason = _market_rejection_reason(market, lane)
        if metadata_reason:
            reasons = [metadata_reason]
            self._record_rejection(exchange_symbol or symbol, reasons)
            return self._result(
                ok=False,
                reasons=reasons,
                exchange_symbol=exchange_symbol or None,
            )

        cache_key = (self._venue_name(exchange), lane, exchange_symbol)
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached["ts"]) < self._cache_ttl:
            return cached["result"]

        reasons = []
        spread_pct, depth_usd, book_reason = self._fetch_book_metrics(
            exchange, exchange_symbol, lane, market
        )
        if book_reason:
            reasons.append(book_reason)
        else:
            if spread_pct > self.MAX_SPREAD_PCT:
                reasons.append(
                    f"spread_too_wide:{spread_pct * 100:.2f}%>{self.MAX_SPREAD_PCT * 100:.1f}%"
                )
            if depth_usd < self.MIN_DEPTH_USD:
                reasons.append(f"thin_book:${depth_usd:.0f}<${self.MIN_DEPTH_USD}")

        atr_pct = self._check_volatility(exchange, exchange_symbol, lane)
        if atr_pct is None:
            reasons.append("missing_volatility_data")
        elif atr_pct < self.MIN_ATR_PCT:
            reasons.append(f"too_calm:ATR={atr_pct * 100:.2f}%<{self.MIN_ATR_PCT * 100:.1f}%")
        elif atr_pct > self.MAX_ATR_PCT:
            reasons.append(f"too_volatile:ATR={atr_pct * 100:.1f}%>{self.MAX_ATR_PCT * 100:.0f}%")

        range_of_change = None
        trend_efficiency = None
        try:
            from config import RANGE_STABILITY_FILTER_ENABLED as range_filter_enabled
        except ImportError:
            range_filter_enabled = True
        if range_filter_enabled:
            range_of_change, trend_efficiency = self._check_range_stability(
                exchange, exchange_symbol, lane
            )
            if range_of_change is None or trend_efficiency is None:
                reasons.append("missing_range_data")
            else:
                if range_of_change < self.MIN_RANGE_OF_CHANGE:
                    reasons.append(
                        f"dead_range:{range_of_change * 100:.1f}%<"
                        f"{self.MIN_RANGE_OF_CHANGE * 100:.0f}%"
                    )
                if trend_efficiency < self.MIN_TREND_EFFICIENCY:
                    reasons.append(
                        f"chop:ER={trend_efficiency:.2f}<{self.MIN_TREND_EFFICIENCY:.2f}"
                    )

        result = self._result(
            ok=not reasons,
            reasons=reasons,
            exchange_symbol=exchange_symbol,
            spread_pct=spread_pct,
            atr_pct=atr_pct,
            depth_usd=depth_usd,
            range_of_change=range_of_change,
            trend_efficiency=trend_efficiency,
        )
        if reasons:
            self._record_rejection(exchange_symbol, reasons)
        self._cache[cache_key] = {"result": result, "ts": now}
        return result

    def _check_active(self, exchange, symbol: str, market_type: str = "spot") -> bool:
        """Return False for unknown, ineligible, or inactive venue markets."""
        lane = self._normalise_market_type(market_type)
        if lane is None:
            return False
        resolved = self._resolve_market(exchange, symbol, lane)
        if resolved is None:
            return False
        _market_key, market = resolved
        return _market_rejection_reason(market, lane) is None

    @staticmethod
    def _positive_float(value) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    def _derive_book_metrics(self, order_book, market: Mapping):
        if not isinstance(order_book, Mapping):
            return None, None, "missing_order_book"
        bids = order_book.get("bids")
        asks = order_book.get("asks")
        if not bids or not asks:
            return None, None, "missing_order_book"
        if not isinstance(bids, (list, tuple)) or not isinstance(asks, (list, tuple)):
            return None, None, "invalid_order_book"

        def normalise(levels):
            output = []
            for level in levels:
                if not isinstance(level, (list, tuple)) or len(level) < 2:
                    return None
                price = self._positive_float(level[0])
                amount = self._positive_float(level[1])
                if price is None or amount is None:
                    return None
                output.append((price, amount))
            return output

        normalised_bids = normalise(bids)
        normalised_asks = normalise(asks)
        if not normalised_bids or not normalised_asks:
            return None, None, "invalid_order_book"

        normalised_bids.sort(key=lambda level: level[0], reverse=True)
        normalised_asks.sort(key=lambda level: level[0])
        best_bid = normalised_bids[0][0]
        best_ask = normalised_asks[0][0]
        if best_bid >= best_ask:
            return None, None, "crossed_order_book"

        mid = (best_bid + best_ask) / 2.0
        spread_pct = (best_ask - best_bid) / mid
        contract_size = 1.0
        raw_contract_size = market.get("contractSize")
        if raw_contract_size is not None:
            parsed_contract_size = self._positive_float(raw_contract_size)
            if parsed_contract_size is None:
                return None, None, "invalid_market_metadata"
            contract_size = parsed_contract_size

        bid_depth = sum(price * amount * contract_size for price, amount in normalised_bids[:5])
        ask_depth = sum(price * amount * contract_size for price, amount in normalised_asks[:5])
        depth_usd = min(bid_depth, ask_depth)
        if not math.isfinite(spread_pct) or not math.isfinite(depth_usd) or depth_usd <= 0:
            return None, None, "invalid_order_book"
        return spread_pct, depth_usd, None

    def _fetch_book_metrics(self, exchange, symbol: str, market_type: str, market: Mapping):
        try:
            order_book = exchange.fetch_order_book(symbol, limit=10, market_type=market_type)
        except Exception:
            return None, None, "missing_order_book"
        return self._derive_book_metrics(order_book, market)

    def _check_spread(self, exchange, symbol: str, market_type: str) -> Optional[float]:
        lane = self._normalise_market_type(market_type)
        resolved = self._resolve_market(exchange, symbol, lane) if lane else None
        if resolved is None:
            return None
        market_key, market = resolved
        spread, _depth, _reason = self._fetch_book_metrics(
            exchange, _market_symbol(market_key, market), lane, market
        )
        return spread

    def _check_depth(self, exchange, symbol: str, market_type: str) -> Optional[float]:
        lane = self._normalise_market_type(market_type)
        resolved = self._resolve_market(exchange, symbol, lane) if lane else None
        if resolved is None:
            return None
        market_key, market = resolved
        _spread, depth, _reason = self._fetch_book_metrics(
            exchange, _market_symbol(market_key, market), lane, market
        )
        return depth

    def _check_volatility(self, exchange, symbol: str, market_type: str) -> Optional[float]:
        """Return 14-period 1h ATR/price, or None when data is unusable."""
        try:
            candles = exchange.fetch_ohlcv(
                symbol,
                timeframe="1h",
                limit=self.ATR_PERIOD + 5,
                market_type=market_type,
            )
        except Exception:
            return None
        if not isinstance(candles, (list, tuple)) or len(candles) < self.ATR_PERIOD + 1:
            return None

        window = candles[-(self.ATR_PERIOD + 1) :]
        true_ranges = []
        for index in range(1, len(window)):
            current = window[index]
            previous = window[index - 1]
            if (
                not isinstance(current, (list, tuple))
                or not isinstance(previous, (list, tuple))
                or len(current) < 5
                or len(previous) < 5
            ):
                return None
            high = self._positive_float(current[2])
            low = self._positive_float(current[3])
            previous_close = self._positive_float(previous[4])
            if high is None or low is None or previous_close is None or high < low:
                return None
            true_ranges.append(
                max(high - low, abs(high - previous_close), abs(low - previous_close))
            )

        last_close = self._positive_float(window[-1][4])
        if last_close is None or len(true_ranges) != self.ATR_PERIOD:
            return None
        atr_pct = (sum(true_ranges) / self.ATR_PERIOD) / last_close
        return atr_pct if math.isfinite(atr_pct) and atr_pct >= 0 else None

    def _check_range_stability(
        self, exchange, symbol: str, market_type: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Return range-of-change and efficiency, or explicit None values."""
        required = self.RANGE_LOOKBACK_DAYS + 1
        try:
            candles = exchange.fetch_ohlcv(
                symbol,
                timeframe="1d",
                limit=required,
                market_type=market_type,
            )
        except Exception:
            return None, None
        if not isinstance(candles, (list, tuple)) or len(candles) < required:
            return None, None

        highs = []
        lows = []
        closes = []
        for candle in candles[-required:]:
            if not isinstance(candle, (list, tuple)) or len(candle) < 5:
                return None, None
            high = self._positive_float(candle[2])
            low = self._positive_float(candle[3])
            close = self._positive_float(candle[4])
            if high is None or low is None or close is None or high < low:
                return None, None
            highs.append(high)
            lows.append(low)
            closes.append(close)

        highest_high = max(highs)
        lowest_low = min(lows)
        range_of_change = (highest_high - lowest_low) / lowest_low
        net_move = abs(closes[-1] - closes[0])
        path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
        trend_efficiency = net_move / path if path > 0 else 0.0
        if not math.isfinite(range_of_change) or not math.isfinite(trend_efficiency):
            return None, None
        return range_of_change, trend_efficiency

    def rank(self, exchange, symbol: str, market_type: str = "spot") -> float:
        """Return a 0-100 quality score, using the cached one-book check."""
        try:
            from config import UNIVERSE_WHITELIST as whitelist
        except ImportError:
            whitelist = None
        if whitelist is not None and symbol not in whitelist:
            return 0.0

        result = self.check(exchange, symbol, market_type)
        if not result["ok"]:
            return 0.0

        spread = result["spread_pct"]
        depth = result["depth_usd"]
        atr = result["atr_pct"]
        score = 0.0

        if spread < 0.001:
            score += 25
        elif spread < 0.003:
            score += 15
        elif spread < self.MAX_SPREAD_PCT:
            score += 5

        if depth >= 50_000:
            score += 25
        elif depth >= 10_000:
            score += 20
        elif depth >= self.MIN_DEPTH_USD:
            score += 10

        if 0.003 <= atr <= 0.03:
            score += 25
        elif 0.0015 <= atr <= 0.05:
            score += 15
        else:
            score += 5

        if self._normalise_market_type(market_type) == "futures":
            try:
                funding = exchange.exchange.fetch_funding_rate(result["exchange_symbol"])
                rate = abs(float(funding.get("fundingRate", 0)))
                if rate < 0.0001:
                    score += 25
                elif rate < 0.0005:
                    score += 15
                elif rate < 0.001:
                    score += 5
            except Exception:
                score += 12
        else:
            score += 25
        return score

    def get_reject_stats(self) -> dict:
        """Return reject counts per canonical exchange symbol."""
        return dict(self._reject_counts)
