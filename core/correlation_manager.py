"""
core/correlation_manager.py — Correlation-Aware Position Sizing

Prevents over-concentration in correlated assets. If you're already long BTC,
opening a long ETH (correlation ~0.85) effectively doubles your exposure to
the same risk factor.

Features:
  1. Predefined correlation groups (major crypto, L1s, meme coins, commodities)
  2. Dynamic correlation from recent price data when available
  3. Max exposure per correlated group
  4. Position size reduction for correlated additions
"""

import time
import numpy as np
import pandas as pd
from loguru import logger


# ── Predefined correlation groups ────────────────────────────────────
# Assets within the same group tend to move together (corr > 0.7)

CORRELATION_GROUPS = {
    "btc_proxy": {
        "assets": {"BTC", "WBTC"},
        "max_group_pct": 0.20,  # Max 20% portfolio in BTC proxies
    },
    "major_l1": {
        "assets": {"ETH", "SOL", "BNB", "ADA", "AVAX", "DOT", "ATOM",
                   "NEAR", "APT", "SUI", "SEI"},
        "max_group_pct": 0.30,  # Max 30% in L1s
    },
    "defi": {
        "assets": {"UNI", "AAVE", "LINK", "INJ", "FET", "RNDR"},
        "max_group_pct": 0.15,
    },
    "meme": {
        "assets": {"DOGE", "PEPE", "WIF", "BONK", "FLOKI", "TURBO"},
        "max_group_pct": 0.10,  # Max 10% in memes
    },
    "commodities": {
        "assets": {"XAU", "XAG", "WTI", "CL"},
        "max_group_pct": 0.15,
    },
    "l2_scaling": {
        "assets": {"OP", "ARB", "IMX", "MANTA", "STX"},
        "max_group_pct": 0.15,
    },
}


class CorrelationManager:

    def __init__(self):
        self._price_cache: dict = {}  # symbol -> pd.Series of closes
        self._cache_ttl = 3600  # 1 hour
        self._cache_time: dict = {}

    def get_group(self, symbol: str) -> str | None:
        """Return the correlation group name for a symbol, or None."""
        base = symbol.split("/")[0].upper()
        for group_name, group_data in CORRELATION_GROUPS.items():
            if base in group_data["assets"]:
                return group_name
        return None

    def group_exposure(self, symbol: str, open_positions: list,
                       balance: float) -> dict:
        """
        Calculate current exposure to the same correlation group.

        Returns:
            {
                "group": "major_l1" or None,
                "current_pct": 0.15,    # current group exposure as % of balance
                "max_pct": 0.30,        # max allowed
                "positions_in_group": 3, # how many positions in this group
                "can_add": True,         # whether we can add more
                "size_multiplier": 0.5,  # reduce size by this factor
            }
        """
        group = self.get_group(symbol)
        if group is None:
            return {
                "group": None, "current_pct": 0, "max_pct": 1.0,
                "positions_in_group": 0, "can_add": True,
                "size_multiplier": 1.0,
            }

        group_data = CORRELATION_GROUPS[group]
        max_pct = group_data["max_group_pct"]
        group_assets = group_data["assets"]

        # Count positions and notional in this group
        group_positions = []
        group_notional = 0.0
        for pos in open_positions:
            pos_base = pos.symbol.split("/")[0].upper()
            if pos_base in group_assets:
                group_positions.append(pos)
                # size already includes leverage (from risk_manager: qty = balance * pct * leverage / price)
                # so entry_price * size = leveraged notional. Do NOT multiply by leverage again.
                notional = getattr(pos, 'entry_price', 0) * getattr(pos, 'size', 0)
                group_notional += notional

        current_pct = group_notional / balance if balance > 0 else 0
        remaining_pct = max(0, max_pct - current_pct)

        # Can we add more?
        can_add = current_pct < max_pct

        # Size multiplier: reduce position size as group fills up
        if remaining_pct <= 0:
            size_mult = 0.0
        elif remaining_pct < max_pct * 0.3:
            size_mult = 0.3  # Last 30% of group capacity: use 30% size
        elif remaining_pct < max_pct * 0.6:
            size_mult = 0.6  # Mid range: use 60% size
        else:
            size_mult = 1.0  # Plenty of room

        result = {
            "group": group,
            "current_pct": round(current_pct, 4),
            "max_pct": max_pct,
            "positions_in_group": len(group_positions),
            "can_add": can_add,
            "size_multiplier": size_mult,
        }

        if not can_add:
            logger.info(
                f"[Correlation] {symbol} BLOCKED: group '{group}' at "
                f"{current_pct:.1%} >= {max_pct:.1%} max")
        elif size_mult < 1.0:
            logger.debug(
                f"[Correlation] {symbol}: group '{group}' at {current_pct:.1%}, "
                f"size reduced to {size_mult:.0%}")

        return result

    def compute_correlation(self, prices_a: pd.Series,
                            prices_b: pd.Series,
                            window: int = 30) -> float:
        """Compute rolling correlation between two price series."""
        if len(prices_a) < window or len(prices_b) < window:
            return 0.0

        # Align by index
        combined = pd.DataFrame({"a": prices_a, "b": prices_b}).dropna()
        if len(combined) < window:
            return 0.0

        returns_a = combined["a"].pct_change().dropna()
        returns_b = combined["b"].pct_change().dropna()

        if len(returns_a) < window:
            return 0.0

        corr = returns_a.tail(window).corr(returns_b.tail(window))
        return float(corr) if not np.isnan(corr) else 0.0

    def opposite_direction_check(self, symbol: str, direction: str,
                                  open_positions: list) -> bool:
        """
        Check if we already have the OPPOSITE direction on a correlated asset.
        E.g., long BTC + short ETH is a valid pair trade, don't block it.
        But long BTC + long ETH is concentration risk.
        """
        group = self.get_group(symbol)
        if group is None:
            return True  # no group = no conflict

        group_assets = CORRELATION_GROUPS[group]["assets"]

        for pos in open_positions:
            pos_base = pos.symbol.split("/")[0].upper()
            if pos_base in group_assets:
                if pos.side == direction:
                    # Same direction on correlated asset = concentration risk
                    logger.debug(
                        f"[Correlation] {symbol} {direction.upper()} blocked — "
                        f"same direction as {pos.symbol} {pos.side.upper()}")
                    return False
                else:
                    # Opposite direction = pair trade, allowed
                    logger.debug(
                        f"[Correlation] {symbol} {direction.upper()} + "
                        f"{pos.symbol} {pos.side.upper()} = pair trade (allowed)")
                    return True
        return True  # no correlated positions found
