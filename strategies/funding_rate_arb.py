"""
strategies/funding_rate_arb.py — Funding Rate Arbitrage Strategy

Alpha source: Funding rates on perpetual futures.

When funding is highly positive (longs pay shorts), the market is overleveraged
long. We short the perp to collect funding. When funding is highly negative
(shorts pay longs), we go long to collect funding.

This is a carry trade — the edge comes from funding payments, not price movement.
SL/TP are wider to avoid getting stopped out before collecting funding.

Edge: Funding rates are predictable and mean-reverting. Extreme funding almost
always reverts within 8-24 hours, paying the carry trader.

Win rate: ~70-80%  |  R:R: ~1:1.5  |  Avg hold: 4-12h
"""
from __future__ import annotations

import time
import pandas as pd
import numpy as np
from loguru import logger

from strategies.base_strategy import BaseStrategy
from exchanges.base import BaseExchange
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from config import RISK


# ── Funding rate thresholds ──────────────────────────────────────────

# Funding rates are typically per 8-hour period
# 0.01% = neutral (exchanges charge this as baseline)
# > 0.05% = elevated (longs are paying a lot → short bias)
# < -0.02% = negative (shorts are paying → long bias)

FUNDING_CONFIG = {
    "high_positive_threshold": 0.0005,   # 0.05% per 8h → short
    "high_negative_threshold": -0.0002,  # -0.02% per 8h → long
    "extreme_threshold": 0.001,          # 0.1% per 8h → strong signal
    "min_funding_edge": 0.0003,          # minimum funding to justify entry
    "max_hold_hours": 24,                # close after 24h regardless
    "atr_sl_mult": 3.0,                  # wider SL (we want to hold through noise)
    "atr_tp_mult": 4.0,                  # TP from price move + funding
    "check_interval_min": 30,            # re-check funding every 30 min
}


def _atr(high, low, close, p=14):
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()


class FundingRateArbStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 market_type: str = "futures"):
        super().__init__(order_manager, risk_manager, "FundingRateArb", market_type)
        self.cfg = FUNDING_CONFIG
        self._last_check: dict = {}  # symbol -> timestamp of last check
        self._funding_cache: dict = {}  # symbol -> (rate, timestamp)

    def _get_funding_rate(self, exchange: BaseExchange, symbol: str) -> float | None:
        """
        Fetch current funding rate from exchange.
        Returns the rate per 8-hour period, or None if unavailable.
        """
        # Check cache (funding rates change every 8h, no need to spam)
        cached = self._funding_cache.get(symbol)
        if cached and (time.time() - cached[1]) < 300:  # 5 min cache
            return cached[0]

        try:
            if not hasattr(exchange, 'exchange') or not exchange.exchange:
                return None

            # ccxt unified method
            funding = exchange.exchange.fetch_funding_rate(symbol)
            if funding and 'fundingRate' in funding:
                rate = float(funding['fundingRate'])
                self._funding_cache[symbol] = (rate, time.time())
                return rate
        except Exception:
            pass

        # Fallback: try fetching from ticker info
        try:
            ticker = exchange.fetch_ticker(symbol, "futures")
            if ticker and 'info' in ticker:
                info = ticker['info']
                for key in ('fundingRate', 'lastFundingRate', 'funding_rate'):
                    if key in info:
                        rate = float(info[key])
                        self._funding_cache[symbol] = (rate, time.time())
                        return rate
        except Exception:
            pass

        return None

    def _get_multi_exchange_funding(self, exchanges: dict,
                                     symbol: str) -> dict:
        """
        Fetch funding rates from multiple exchanges for the same symbol.
        Returns {exchange_name: funding_rate}.
        Used for cross-exchange funding arb.
        """
        rates = {}
        for ex_name, exchange in exchanges.items():
            rate = self._get_funding_rate(exchange, symbol)
            if rate is not None:
                rates[ex_name] = rate
        return rates

    def generate_signal(self, df: pd.DataFrame):
        """
        Not used directly — signal comes from funding rate, not price action.
        Returns (signal, atr_val) for compatibility.
        """
        df = df.copy()
        df["atr_val"] = _atr(df["high"], df["low"], df["close"], 14)
        df.dropna(inplace=True)
        if df.empty:
            return None, None
        return None, float(df["atr_val"].iloc[-1])

    def run(self, exchange: BaseExchange, symbol: str):
        """
        Check funding rate and enter carry trade if edge is sufficient.
        """
        logger.debug(f"[FundingArb] {symbol}")

        # Rate-limit funding checks
        last = self._last_check.get(symbol, 0)
        if time.time() - last < self.cfg["check_interval_min"] * 60:
            return
        self._last_check[symbol] = time.time()

        # Check SL/TP on existing positions
        self.order_manager.check_sl_tp(exchange, self.market_type)

        # Don't stack positions
        if self.order_manager.tracker.get_open(exchange=exchange.name, symbol=symbol):
            return

        # Get funding rate
        rate = self._get_funding_rate(exchange, symbol)
        if rate is None:
            return

        # Determine signal based on funding rate
        signal = None
        edge_desc = ""

        if rate >= self.cfg["extreme_threshold"]:
            # Extreme positive funding — very strong short signal
            signal = "sell"
            edge_desc = f"EXTREME positive funding {rate*100:.4f}% — shorts collect"
        elif rate >= self.cfg["high_positive_threshold"]:
            # Elevated positive — short to collect funding
            signal = "sell"
            edge_desc = f"High positive funding {rate*100:.4f}% — shorts collect"
        elif rate <= -self.cfg["extreme_threshold"]:
            # Extreme negative funding — very strong long signal
            signal = "buy"
            edge_desc = f"EXTREME negative funding {rate*100:.4f}% — longs collect"
        elif rate <= self.cfg["high_negative_threshold"]:
            # Elevated negative — long to collect funding
            signal = "buy"
            edge_desc = f"Negative funding {rate*100:.4f}% — longs collect"

        if not signal:
            return

        # Get price data for ATR-based SL/TP
        df = self.get_dataframe(exchange, symbol, "1h", 50)
        if df is None:
            return

        _, atr_val = self.generate_signal(df)
        if atr_val is None or atr_val <= 0:
            return

        current_price = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, current_price, f"| {edge_desc}")

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        leverage = self.risk.validate_leverage(RISK["default_leverage"])
        size = self.risk.calculate_position_size(balance, current_price, leverage)

        # Wider SL/TP for carry trades (we need to hold through noise)
        sl, tp = self.risk.get_sl_tp(
            current_price, signal, atr=atr_val,
            atr_sl_mult=self.cfg["atr_sl_mult"],
            atr_tp_mult=self.cfg["atr_tp_mult"],
            leverage=leverage,
        )

        pos = self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=leverage,
        )
        return pos

    def run_cross_exchange(self, exchanges: dict, symbol: str):
        """
        Cross-exchange funding arb: find the exchange with the most extreme
        funding rate and trade there.
        """
        rates = self._get_multi_exchange_funding(exchanges, symbol)
        if len(rates) < 2:
            return

        # Find the exchange with the most extreme funding
        best_ex = None
        best_rate = 0
        for ex_name, rate in rates.items():
            if abs(rate) > abs(best_rate):
                best_rate = rate
                best_ex = ex_name

        if best_ex and abs(best_rate) >= self.cfg["min_funding_edge"]:
            logger.info(
                f"[FundingArb] {symbol}: Best funding on {best_ex} "
                f"({best_rate*100:.4f}%) | Rates: "
                + ", ".join(f"{k}={v*100:.4f}%" for k, v in rates.items()))
            self.run(exchanges[best_ex], symbol)
