"""
portfolio_scanner.py — Scans exchange wallets and builds trading pairs
from coins you actually hold.

Two modes (TRADING_MODE in .env):
  usdt_only  — use the fixed TRADING_PAIRS from config.py (default)
  portfolio  — scan all coins held, add them as pairs automatically
"""

from __future__ import annotations
from loguru import logger


# Stablecoins, wrapped tokens and LP tokens to always ignore
SKIP_COINS = {
    "USDT", "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD",
    "USDE", "PYUSD", "AEUR", "EUR", "GBP", "BVND",
    "WBTC", "WETH", "WBNB",
    "LDBTC", "STBTC",
}

# Minimum USD value a holding must have to be included as a trading pair
MIN_VALUE_USD = 2.0


def scan_spot_holdings(exchange) -> list[str]:
    """
    Returns COIN/USDT pairs for every coin held in the spot wallet
    with value above MIN_VALUE_USD.
    """
    if not getattr(exchange, "_connected", False):
        return []

    pairs = []
    try:
        bal = exchange.fetch_balance("spot")
        if not bal:
            return []

        total_dict = {}
        t = bal.get("total")
        if isinstance(t, dict):
            total_dict = t
        else:
            for coin, info in bal.items():
                if isinstance(info, dict) and "total" in info:
                    total_dict[coin] = info["total"]

        for coin, amount in total_dict.items():
            if not amount or float(amount) <= 0:
                continue
            if coin in SKIP_COINS:
                continue
            if coin.startswith("LD") or coin.endswith("UP") or coin.endswith("DOWN"):
                continue

            symbol = f"{coin}/USDT"

            # Check the symbol exists on this exchange before trying to price it
            if not _symbol_exists(exchange, symbol):
                logger.debug(
                    f"[Portfolio] Skipping {coin} — {symbol} "
                    f"not listed on {exchange.name}"
                )
                continue

            usd_value = _estimate_value(exchange, symbol, float(amount))
            if usd_value < MIN_VALUE_USD:
                logger.debug(
                    f"[Portfolio] Skipping {coin}: value ~${usd_value:.2f} "
                    f"(below ${MIN_VALUE_USD} threshold)"
                )
                continue

            pairs.append(symbol)
            logger.info(
                f"[Portfolio] Found holding: {float(amount):.6f} {coin} "
                f"(~${usd_value:.2f}) -> will trade {symbol} on {exchange.name}"
            )

    except Exception as e:
        logger.error(f"[Portfolio] Spot scan failed on {exchange.name}: {e}")

    return pairs


def scan_futures_holdings(exchange) -> list[str]:
    """Returns futures pairs for every open position."""
    if not getattr(exchange, "_connected", False):
        return []

    pairs = []
    try:
        positions = exchange.fetch_positions()
        for pos in positions:
            if not pos:
                continue
            size = abs(float(pos.get("contracts", 0) or pos.get("size", 0) or 0))
            if size <= 0:
                continue
            symbol = pos.get("symbol", "")
            if not symbol or "USDT" not in symbol:
                continue
            pairs.append(symbol)
            logger.info(
                f"[Portfolio] Found futures position: {symbol} "
                f"size={size} side={pos.get('side', '?')}"
            )
    except Exception as e:
        logger.debug(f"[Portfolio] Futures scan on {exchange.name}: {e}")

    return pairs


def build_portfolio_pairs(exchanges: dict) -> dict:
    """
    Scans all connected exchanges and returns a merged TRADING_PAIRS dict.
    Always includes the base pairs from config.py.
    """
    from config import TRADING_PAIRS as BASE_PAIRS

    result = {}
    for ex_name, type_dict in BASE_PAIRS.items():
        result[ex_name] = {
            "spot":    list(type_dict.get("spot", [])),
            "futures": list(type_dict.get("futures", [])),
        }

    for ex_name, exchange in exchanges.items():
        if not getattr(exchange, "_connected", False):
            continue

        logger.info(f"[Portfolio] Scanning {ex_name} wallet...")

        if ex_name not in result:
            result[ex_name] = {"spot": [], "futures": []}

        for pair in scan_spot_holdings(exchange):
            if pair not in result[ex_name]["spot"]:
                result[ex_name]["spot"].append(pair)

        for pair in scan_futures_holdings(exchange):
            if pair not in result[ex_name]["futures"]:
                result[ex_name]["futures"].append(pair)

    for ex_name, type_dict in result.items():
        logger.info(
            f"[Portfolio] {ex_name}: "
            f"{len(type_dict['spot'])} spot, "
            f"{len(type_dict['futures'])} futures pairs"
        )
        logger.info(f"[Portfolio]   Spot:    {type_dict['spot']}")
        logger.info(f"[Portfolio]   Futures: {type_dict['futures']}")

    return result


def _symbol_exists(exchange, symbol: str) -> bool:
    """Check if a symbol is listed on the exchange (uses cached market data)."""
    try:
        markets = exchange.exchange.load_markets()
        return symbol in markets
    except Exception:
        return True  # if we can't check, allow it and let fetch_ticker fail silently


def _estimate_value(exchange, symbol: str, amount: float) -> float:
    """Estimate USD value by fetching current price. Returns 0 on any error."""
    try:
        ticker = exchange.fetch_ticker(symbol, "spot")
        price  = ticker.get("last") or ticker.get("close") or 0
        return float(price) * amount
    except Exception:
        return 0.0
