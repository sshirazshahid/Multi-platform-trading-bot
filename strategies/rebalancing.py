"""
strategies/rebalancing.py — Feature 7: Portfolio Rebalancing

Periodically rebalances your crypto portfolio to maintain target allocations.

Example target:
  BTC  40%
  ETH  30%
  BNB  20%
  USDT 10% (cash reserve)

If BTC runs up to 55% of portfolio, rebalancing sells some BTC and
buys the underweight assets back to target percentages.

Configuration (config.py REBALANCING section):
  targets           — {symbol: target_pct} (must sum to 1.0 or less)
  interval_hours    — how often to check (default 24h)
  threshold_pct     — only rebalance if any asset drifts > this % from target
  min_trade_usdt    — ignore rebalancing trades smaller than this USDT value
"""

import time

from loguru import logger

from config import DRY_RUN
from config import REBALANCING as CFG
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy


class RebalancingStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager,
                 risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "Rebalancing", market_type)
        self.cfg        = CFG
        self._last_run  = 0.0

    def generate_signal(self, df):
        return None   # rebalancing is not signal-based

    def run(self, exchange: BaseExchange, symbol: str = None):
        """
        symbol is ignored for rebalancing — it operates on the whole portfolio.
        Call this once per scheduled interval.
        """
        interval = self.cfg.get("interval_hours", 24) * 3600
        if time.time() - self._last_run < interval:
            return

        logger.info("[Rebalance] Starting portfolio rebalance check...")
        self._last_run = time.time()

        targets   = self.cfg.get("targets", {})
        threshold = self.cfg.get("threshold_pct", 0.05)
        min_trade = self.cfg.get("min_trade_usdt", 10.0)

        if not targets:
            logger.warning("[Rebalance] No targets defined in REBALANCING config.")
            return

        # Step 1: fetch current balances and prices
        holdings = self._get_holdings(exchange, targets)
        if not holdings:
            return

        # Step 2: calculate total portfolio value in USDT
        total_usdt = sum(h["value_usdt"] for h in holdings.values())
        if total_usdt < 10:
            logger.warning(
                f"[Rebalance] Portfolio too small ({total_usdt:.2f} USDT) to rebalance."
            )
            return

        # Step 3: calculate drift from targets
        sells, buys = [], []
        for coin, h in holdings.items():
            if coin == "USDT":
                continue
            target_pct  = targets.get(coin, 0.0)
            current_pct = h["value_usdt"] / total_usdt
            drift       = current_pct - target_pct

            if abs(drift) < threshold:
                logger.debug(
                    f"[Rebalance] {coin}: {current_pct*100:.1f}% "
                    f"(target {target_pct*100:.1f}%) drift {drift*100:.1f}% — OK"
                )
                continue

            diff_usdt = drift * total_usdt
            logger.info(
                f"[Rebalance] {coin}: {current_pct*100:.1f}% "
                f"(target {target_pct*100:.1f}%) drift {drift*100:.1f}% "
                f"{'SELL' if diff_usdt > 0 else 'BUY'} {abs(diff_usdt):.2f} USDT"
            )

            if abs(diff_usdt) < min_trade:
                continue

            if drift > 0:
                sells.append({"coin": coin, "usdt": diff_usdt, "price": h["price"]})
            else:
                buys.append({"coin": coin, "usdt": -diff_usdt, "price": h["price"]})

        if not sells and not buys:
            logger.info("[Rebalance] Portfolio is balanced — no trades needed.")
            return

        # Step 4: execute sells first, then buys
        for trade in sells:
            self._execute(exchange, trade["coin"], "sell",
                           trade["usdt"], trade["price"])

        for trade in buys:
            self._execute(exchange, trade["coin"], "buy",
                           trade["usdt"], trade["price"])

        logger.info("[Rebalance] Rebalancing complete.")

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_holdings(self, exchange: BaseExchange, targets: dict) -> dict:
        """Returns {coin: {qty, price, value_usdt}} for all target coins."""
        try:
            bal  = exchange.fetch_balance("spot")
            total = bal.get("total", {})
            if not isinstance(total, dict):
                return {}

            holdings = {}
            for coin in list(targets.keys()) + ["USDT"]:
                qty = float(total.get(coin, 0.0))
                if coin == "USDT":
                    holdings["USDT"] = {
                        "qty": qty, "price": 1.0, "value_usdt": qty
                    }
                    continue
                if qty <= 0:
                    holdings[coin] = {"qty": 0, "price": 0, "value_usdt": 0}
                    continue
                symbol = f"{coin}/USDT"
                ticker = exchange.fetch_ticker(symbol)
                price  = float(ticker.get("last") or ticker.get("close") or 0)
                holdings[coin] = {
                    "qty":        qty,
                    "price":      price,
                    "value_usdt": qty * price,
                }
            return holdings
        except Exception as e:
            logger.error(f"[Rebalance] get_holdings failed: {e}")
            return {}

    def _execute(self, exchange: BaseExchange, coin: str,
                 side: str, usdt_amount: float, price: float):
        """Execute a single rebalancing trade."""
        symbol = f"{coin}/USDT"
        if price <= 0:
            logger.warning(f"[Rebalance] Invalid price {price} for {coin} — skipping")
            return
        qty    = usdt_amount / price
        min_q  = exchange.get_min_order_size(symbol)
        if qty < min_q:
            return

        if DRY_RUN:
            logger.info(
                f"[Rebalance] [DRY] {side.upper()} {qty:.6f} {coin} "
                f"@ {price:.4f} ({usdt_amount:.2f} USDT)"
            )
        else:
            try:
                exchange.create_order(
                    symbol, "market", side, qty,
                    market_type=self.market_type
                )
                logger.info(
                    f"[Rebalance] {side.upper()} {qty:.6f} {coin} "
                    f"@ {price:.4f} ({usdt_amount:.2f} USDT)"
                )
            except Exception as e:
                logger.error(
                    f"[Rebalance] {side.upper()} {coin} failed: {e}"
                )
