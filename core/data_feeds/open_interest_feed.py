"""
core/data_feeds/open_interest_feed.py — CoinDesk Open Interest Time-Series Feed

Fetches OI tick + hourly OHLCV history from CoinDesk Data API.
Computes:
  - OI current (in BTC-denominated contracts, i.e. SETTLEMENT units)
  - OI 6h delta % (rising/falling/flat)
  - OI-price divergence signal (CANDIDATE — UNVALIDATED, see thesis):
      price_up + OI_up   = trend continuation (new money entering)
      price_up + OI_down = exhaustion / short covering (likely reversal)
      price_dn + OI_up   = shorts building (trend continuation down)
      price_dn + OI_down = long capitulation (likely bounce)

Signal thesis (HYPOTHESIS, NOT a proven edge):
  OI-price divergence is a microstructure signal the no-edge-forensics
  (2026-05-25) flagged as untested. ⚠ STATUS (2026-05-30 audit): it is now
  WIRED LIVE (B12 bonus + V1 veto in mcp_brain) but has NEVER cleared the
  frozen edge-screen (IR>=0.50 / DSR>=0.10 / PBO<=0.50) — the H5 OI probe was
  deferred ("Binance OI history rejects 8h period"). So it holds live
  decision-authority WITHOUT validation. The thesis (new capital = continuation
  vs position-closing = reversal) is plausible but unproven; treat as a
  candidate to SCREEN, not established alpha. Pure OHLCV scoring being
  anti-predictive motivates the hypothesis but does not confirm it.

Cache TTL: 180s (3 min). OI updates every ~10s on Binance.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

_COINDESK_INSTRUMENT_FMT = "{base}-USDT-VANILLA-PERPETUAL"
_COINDESK_MARKET = "binance"


def _coin_to_instrument(coin: str) -> str:
    return _COINDESK_INSTRUMENT_FMT.format(base=coin.upper())


class OpenInterestFeed:
    """Fetches and normalizes open interest data."""

    def __init__(self, *, cache_ttl: int = 180):
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._cache_ttl = cache_ttl

    def fetch(
        self, coins: list[str], *, price_changes: dict[str, float] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Fetch OI context for a list of base coins.

        Args:
            coins: List of base coins (e.g. ["BTC", "ETH"])
            price_changes: Optional {coin: 6h_price_change_pct} for divergence.
                           If not provided, divergence is not computed.

        Returns:
            {coin: {
                "oi_current": float,        # current OI in settlement units
                "oi_quote_usd": float,      # current OI in USD
                "oi_delta_6h_pct": float,   # 6h OI change %
                "oi_trend": str,            # "rising"|"falling"|"flat"
                "oi_price_divergence": str,  # "continuation"|"exhaustion"|
                                            # "capitulation"|"building"|"unknown"
                "oi_conviction": float,     # 0-1 strength of the OI signal
                "stale": bool,
            }}
        """
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache

        price_changes = price_changes or {}
        result: dict[str, dict] = {}

        for coin in coins[:10]:  # limit to avoid rate limits
            try:
                data = self._fetch_single(coin)
                if data is None:
                    result[coin] = self._neutral(stale=True)
                    continue

                oi_current = data["oi_current"]
                oi_6h_ago = data["oi_6h_ago"]
                oi_quote = data["oi_quote_usd"]

                # Compute delta
                if oi_6h_ago > 0:
                    delta_pct = (oi_current - oi_6h_ago) / oi_6h_ago
                else:
                    delta_pct = 0.0

                # Classify OI trend
                if delta_pct > 0.02:
                    oi_trend = "rising"
                elif delta_pct < -0.02:
                    oi_trend = "falling"
                else:
                    oi_trend = "flat"

                # OI-price divergence (candidate signal — UNVALIDATED, live-wired)
                price_chg = price_changes.get(coin, 0.0)
                divergence, conviction = self._classify_divergence(
                    delta_pct, price_chg)

                result[coin] = {
                    "oi_current": round(oi_current, 3),
                    "oi_quote_usd": round(oi_quote, 0),
                    "oi_delta_6h_pct": round(delta_pct, 4),
                    "oi_trend": oi_trend,
                    "oi_price_divergence": divergence,
                    "oi_conviction": round(conviction, 3),
                    "stale": False,
                }
            except Exception as e:
                logger.debug(f"[OIFeed] {coin}: {e}")
                result[coin] = self._neutral(stale=True)

        self._cache = result
        self._cache_time = now
        logger.debug(f"[OIFeed] fetched {len(result)} coins")
        return result

    def _fetch_single(self, coin: str) -> dict | None:
        """Fetch OI for a single coin: tick + 6h history."""
        # Try CoinDesk first
        try:
            from core.data_feeds._coindesk_caller import (
                call_coindesk_oi_ohlcv,
                call_coindesk_oi_tick,
            )

            # Current tick
            tick_raw = call_coindesk_oi_tick(
                market=_COINDESK_MARKET,
                instruments=_coin_to_instrument(coin))
            tick_data = (tick_raw or {}).get("Data", {})
            inst_key = _coin_to_instrument(coin)
            tick = tick_data.get(inst_key, {})

            oi_current = float(tick.get("VALUE_SETTLEMENT", 0) or 0)
            oi_quote = float(tick.get("VALUE_QUOTE", 0) or 0)

            # 6h history for delta
            hist_raw = call_coindesk_oi_ohlcv(
                market=_COINDESK_MARKET,
                instrument=inst_key,
                frequency="hours",
                aggregate=1,
                limit=7,  # ~7 hours back
            )
            hist_bars = (hist_raw or {}).get("Data", [])
            if hist_bars and oi_current > 0:
                oi_6h_ago = float(
                    hist_bars[0].get("OPEN_SETTLEMENT", oi_current) or oi_current)
                return {
                    "oi_current": oi_current,
                    "oi_6h_ago": oi_6h_ago,
                    "oi_quote_usd": oi_quote,
                }
            elif oi_current > 0:
                return {
                    "oi_current": oi_current,
                    "oi_6h_ago": oi_current,
                    "oi_quote_usd": oi_quote,
                }
        except Exception as e:
            logger.debug(f"[OIFeed] CoinDesk failed for {coin}: {e}")

        # Fallback: Binance public endpoint
        return self._fetch_binance_oi(coin)

    def _fetch_binance_oi(self, coin: str) -> dict | None:
        """Binance OI history fallback."""
        try:
            import json
            import urllib.request
            sym = f"{coin}USDT"
            url = (f"https://fapi.binance.com/futures/data/openInterestHist"
                   f"?symbol={sym}&period=1h&limit=7")
            req = urllib.request.Request(
                url, headers={"User-Agent": "TradingBot/2.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            if not isinstance(data, list) or len(data) < 2:
                return None
            oi_first = float(data[0].get("sumOpenInterest", 0) or 0)
            oi_last = float(data[-1].get("sumOpenInterest", 0) or 0)
            oi_val = float(data[-1].get("sumOpenInterestValue", 0) or 0)
            if oi_first <= 0:
                return None
            return {
                "oi_current": oi_last,
                "oi_6h_ago": oi_first,
                "oi_quote_usd": oi_val,
            }
        except Exception:
            return None

    @staticmethod
    def _classify_divergence(
        oi_delta_pct: float, price_delta_pct: float
    ) -> tuple[str, float]:
        """Classify OI-price divergence and compute conviction strength.

        Returns (divergence_label, conviction_0_to_1).
        """
        # Thresholds: need meaningful moves to classify
        oi_thresh = 0.015   # 1.5% OI change
        px_thresh = 0.005   # 0.5% price change

        oi_up = oi_delta_pct > oi_thresh
        oi_dn = oi_delta_pct < -oi_thresh
        px_up = price_delta_pct > px_thresh
        px_dn = price_delta_pct < -px_thresh

        # Conviction = magnitude of the combined move
        raw_conviction = (abs(oi_delta_pct) + abs(price_delta_pct)) / 2
        conviction = min(1.0, raw_conviction / 0.05)  # normalize: 5% combined = max

        if px_up and oi_up:
            return "continuation", conviction  # new money backing the move
        elif px_up and oi_dn:
            return "exhaustion", conviction    # short covering, no new buyers
        elif px_dn and oi_up:
            return "building", conviction      # new shorts entering
        elif px_dn and oi_dn:
            return "capitulation", conviction  # longs liquidating, bounce likely
        else:
            return "unknown", 0.0

    @staticmethod
    def _neutral(*, stale: bool = False) -> dict:
        return {
            "oi_current": 0.0,
            "oi_quote_usd": 0.0,
            "oi_delta_6h_pct": 0.0,
            "oi_trend": "flat",
            "oi_price_divergence": "unknown",
            "oi_conviction": 0.0,
            "stale": stale,
        }
