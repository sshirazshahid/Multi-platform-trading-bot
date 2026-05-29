"""
core/data_feeds/orderbook_depth_feed.py — Enhanced Order Book Depth Feed

Enhances the existing Binance orderbook depth fetch (mcp_brain.fetch_orderbook_depth)
with deeper analysis: multi-level depth, slippage estimation, and wall detection.

Note: CoinDesk orderbook_metrics endpoint requires paid subscription (verified 403).
This feed uses Binance public depth API as primary, with Bybit as fallback.

Computes:
  - Depth ratio (bid_vol / ask_vol) at multiple levels
  - Estimated slippage for a $500 market order (bot's typical notional)
  - Buy/sell wall detection (single level > 25% of side depth)
  - Depth imbalance momentum (comparing current vs cached prior reading)
  - Spread width in bps (basis points)

Integration: Enhances existing B6 microstructure condition + adds a pre-trade
slippage filter (skip if estimated slippage > 30 bps).

Cache TTL: 60s. Orderbook is the most volatile data source.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any

from loguru import logger


class OrderBookDepthFeed:
    """Enhanced orderbook depth analysis."""

    def __init__(self, *, cache_ttl: int = 60, target_notional_usd: float = 500.0):
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._cache_ttl = cache_ttl
        self._target_notional = target_notional_usd
        # Prior reading for momentum computation
        self._prior: dict[str, dict] = {}

    def fetch(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch enhanced orderbook metrics for a list of base coins.

        Returns:
            {coin: {
                "bid_depth_usd": float,
                "ask_depth_usd": float,
                "imbalance": float,           # [-1, 1], >0 = buyers dominate
                "imbalance_momentum": float,  # change vs prior reading
                "spread_bps": float,          # spread in basis points
                "slippage_buy_bps": float,    # estimated buy slippage
                "slippage_sell_bps": float,   # estimated sell slippage
                "bid_wall": bool,
                "ask_wall": bool,
                "depth_ratio_log": float,     # log(bid/ask), >0 = bid-heavy
                "stale": bool,
            }}
        """
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache

        result: dict[str, dict] = {}

        for coin in coins[:15]:  # limit to control latency
            try:
                data = self._fetch_binance_depth(coin)
                if data is None:
                    data = self._fetch_bybit_depth(coin)
                if data is None:
                    result[coin] = self._neutral(stale=True)
                    continue

                bids = data["bids"]
                asks = data["asks"]

                if not bids or not asks:
                    result[coin] = self._neutral(stale=True)
                    continue

                # Depth at multiple levels (top 10 and top 20)
                bid_vol_10 = sum(
                    float(b[1]) * float(b[0]) for b in bids[:10])
                ask_vol_10 = sum(
                    float(a[1]) * float(a[0]) for a in asks[:10])
                bid_vol_20 = sum(
                    float(b[1]) * float(b[0]) for b in bids[:20])
                ask_vol_20 = sum(
                    float(a[1]) * float(a[0]) for a in asks[:20])

                total_10 = bid_vol_10 + ask_vol_10
                imbalance = ((bid_vol_10 - ask_vol_10) / total_10
                             if total_10 > 0 else 0.0)

                # Spread
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                mid = (best_bid + best_ask) / 2
                spread_bps = ((best_ask - best_bid) / mid * 10000
                              if mid > 0 else 0.0)

                # Slippage estimation: walk the book for target_notional
                slippage_buy = self._estimate_slippage(
                    asks, self._target_notional, mid, direction="buy")
                slippage_sell = self._estimate_slippage(
                    bids, self._target_notional, mid, direction="sell")

                # Wall detection (single level > 25% of side depth in top 5)
                bid_wall = any(
                    float(b[1]) * float(b[0]) > bid_vol_10 * 0.25
                    for b in bids[:5]) if bid_vol_10 > 0 else False
                ask_wall = any(
                    float(a[1]) * float(a[0]) > ask_vol_10 * 0.25
                    for a in asks[:5]) if ask_vol_10 > 0 else False

                # Log depth ratio
                depth_ratio_log = (math.log(bid_vol_10 / ask_vol_10)
                                   if bid_vol_10 > 0 and ask_vol_10 > 0
                                   else 0.0)

                # Imbalance momentum vs prior
                prior = self._prior.get(coin, {})
                prior_imb = prior.get("imbalance", imbalance)
                imb_momentum = imbalance - prior_imb

                entry = {
                    "bid_depth_usd": round(bid_vol_20, 0),
                    "ask_depth_usd": round(ask_vol_20, 0),
                    "imbalance": round(imbalance, 4),
                    "imbalance_momentum": round(imb_momentum, 4),
                    "spread_bps": round(spread_bps, 2),
                    "slippage_buy_bps": round(slippage_buy, 2),
                    "slippage_sell_bps": round(slippage_sell, 2),
                    "bid_wall": bid_wall,
                    "ask_wall": ask_wall,
                    "depth_ratio_log": round(depth_ratio_log, 4),
                    "stale": False,
                }
                result[coin] = entry
                self._prior[coin] = {"imbalance": imbalance}

            except Exception as e:
                logger.debug(f"[OBFeed] {coin}: {e}")
                result[coin] = self._neutral(stale=True)

        self._cache = result
        self._cache_time = now
        return result

    def _estimate_slippage(
        self,
        levels: list,
        notional_usd: float,
        mid_price: float,
        direction: str,
    ) -> float:
        """Walk the book to estimate slippage in bps for a given notional.

        For buys: walk ask side upward.
        For sells: walk bid side downward.
        """
        remaining = notional_usd
        cost = 0.0

        for level in levels:
            price = float(level[0])
            qty = float(level[1])
            level_value = price * qty

            if level_value >= remaining:
                # Partial fill at this level
                fill_qty = remaining / price
                cost += fill_qty * price
                remaining = 0
                break
            else:
                cost += level_value
                remaining -= level_value

        if remaining > 0:
            # Book too thin — couldn't fill, return high slippage
            return 100.0  # 100 bps = 1% (signals illiquid)

        avg_fill = cost / (notional_usd / mid_price) if mid_price > 0 else mid_price
        if direction == "buy":
            slippage = (avg_fill - mid_price) / mid_price * 10000
        else:
            slippage = (mid_price - avg_fill) / mid_price * 10000

        return max(0.0, slippage)

    def _fetch_binance_depth(self, coin: str) -> dict | None:
        """Fetch depth from Binance."""
        sym = f"{coin}USDT"
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={sym}&limit=20"
            req = urllib.request.Request(
                url, headers={"User-Agent": "TradingBot/2.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if bids and asks:
                return {"bids": bids, "asks": asks}
        except Exception:
            pass
        return None

    def _fetch_bybit_depth(self, coin: str) -> dict | None:
        """Fetch depth from Bybit as fallback."""
        sym = f"{coin}USDT"
        try:
            url = (f"https://api.bybit.com/v5/market/orderbook"
                   f"?category=linear&symbol={sym}&limit=25")
            req = urllib.request.Request(
                url, headers={"User-Agent": "TradingBot/2.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            result = data.get("result", {})
            bids = result.get("b", [])  # [[price, qty], ...]
            asks = result.get("a", [])
            if bids and asks:
                return {"bids": bids, "asks": asks}
        except Exception:
            pass
        return None

    @staticmethod
    def _neutral(*, stale: bool = False) -> dict:
        return {
            "bid_depth_usd": 0.0,
            "ask_depth_usd": 0.0,
            "imbalance": 0.0,
            "imbalance_momentum": 0.0,
            "spread_bps": 0.0,
            "slippage_buy_bps": 0.0,
            "slippage_sell_bps": 0.0,
            "bid_wall": False,
            "ask_wall": False,
            "depth_ratio_log": 0.0,
            "stale": stale,
        }
