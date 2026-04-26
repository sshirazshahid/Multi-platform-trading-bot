"""
strategies/grid_trading.py — Grid Trading Strategy.
Places a ladder of limit BUY/SELL orders and profits from price oscillation.
"""

import uuid
import time
from loguru import logger
from strategies.base_strategy import BaseStrategy
from exchanges.base import BaseExchange
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from config import GRID_TRADING as CFG


class GridTradingStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "GridTrading", market_type)
        self.cfg    = CFG
        self._grids = {}

    def generate_signal(self, df=None):
        return None

    def _build_grid(self, exchange: BaseExchange, symbol: str, price: float) -> dict:
        n_levels    = self.cfg["grid_levels"]
        upper       = price * (1 + self.cfg["upper_offset"])
        lower       = price * (1 - self.cfg["lower_offset"])
        step        = (upper - lower) / n_levels
        buy_levels  = [exchange.round_price(symbol, lower + step * i) for i in range(n_levels // 2)]
        sell_levels = [exchange.round_price(symbol, price + step * i) for i in range(1, n_levels // 2 + 1)]

        balance     = self.get_usdt_balance(exchange)
        order_pct   = self.cfg["order_size_pct"]
        grid_orders = []

        for level in buy_levels:
            size     = (balance * order_pct) / level
            # Round to exchange precision (critical for CL/USDT, XAU etc.)
            try:
                size = exchange.round_quantity(symbol, size)
            except Exception:
                pass
            min_size = exchange.get_min_order_size(symbol)
            if size < min_size or size * level < 5.0:
                continue
            try:
                if not self.order_manager.dry_run:
                    order    = exchange.create_order(symbol, "limit", "buy", size, level,
                                                     market_type=self.market_type)
                    order_id = order.get("id")
                else:
                    order_id = f"DRY-GRID-BUY-{uuid.uuid4().hex[:6]}"
                    logger.info(f"[Grid] [DRY] BUY limit {size:.6f} {symbol} @ {level:.4f}")
                grid_orders.append({"side": "buy", "price": level, "size": size,
                                    "order_id": order_id, "filled": False})
            except Exception as e:
                logger.debug(f"[Grid] Failed to place buy @ {level}: {e}")

        # On spot: skip sell orders — can't sell what we don't own yet.
        # Sell orders are only valid after buy fills (handled by grid replenishment).
        # On futures: both sides are valid.
        if self.market_type == "spot":
            sell_levels = []

        for level in sell_levels:
            size     = (balance * order_pct) / level
            try:
                size = exchange.round_quantity(symbol, size)
            except Exception:
                pass
            min_size = exchange.get_min_order_size(symbol)
            if size < min_size or size * level < 5.0:
                continue
            try:
                if not self.order_manager.dry_run:
                    order    = exchange.create_order(symbol, "limit", "sell", size, level,
                                                     market_type=self.market_type)
                    order_id = order.get("id")
                else:
                    order_id = f"DRY-GRID-SELL-{uuid.uuid4().hex[:6]}"
                    logger.info(f"[Grid] [DRY] SELL limit {size:.6f} {symbol} @ {level:.4f}")
                grid_orders.append({"side": "sell", "price": level, "size": size,
                                    "order_id": order_id, "filled": False})
            except Exception as e:
                logger.debug(f"[Grid] Failed to place sell @ {level}: {e}")

        logger.info(f"[Grid] Grid built for {symbol} | lower={lower:.4f} upper={upper:.4f} | "
                    f"{len(grid_orders)} orders placed")
        return {"orders": grid_orders, "lower": lower, "upper": upper,
                "center": price, "last_build": time.time(), "total_profit": 0.0}

    def _should_rebuild(self, gkey: str, price: float) -> bool:
        if gkey not in self._grids:
            return True
        g         = self._grids[gkey]
        age_hours = (time.time() - g["last_build"]) / 3600
        return age_hours >= self.cfg["rebalance_after"] or price < g["lower"] or price > g["upper"]

    def _cancel_grid(self, exchange: BaseExchange, symbol: str, gkey: str):
        if not self.order_manager.dry_run:
            exchange.cancel_all_orders(symbol, self.market_type)
        logger.info(f"[Grid] Cancelled all grid orders for {symbol}")

    def _check_filled_orders(self, exchange: BaseExchange, symbol: str, gkey: str):
        if gkey not in self._grids:
            return
        grid = self._grids[gkey]

        if self.order_manager.dry_run:
            # DRY RUN: simulate fills based on current price touching grid levels
            ticker = exchange.fetch_ticker(symbol, self.market_type)
            price  = ticker.get("last") or ticker.get("close")
            if not price:
                return
            price = float(price)
            for order in grid["orders"]:
                if order["filled"]:
                    continue
                # Buy fills when price drops to/below level, sell fills when price rises to/above
                if order["side"] == "buy" and price <= order["price"]:
                    order["filled"] = True
                    logger.info(
                        f"[Grid] [DRY] BUY FILLED {order['size']:.6f} {symbol} @ "
                        f"{order['price']:.4f}")
                elif order["side"] == "sell" and price >= order["price"]:
                    order["filled"] = True
                    step = (grid["upper"] - grid["lower"]) / self.cfg["grid_levels"]
                    profit = order["size"] * step
                    grid["total_profit"] += profit
                    logger.info(
                        f"[Grid] [DRY] SELL FILLED {order['size']:.6f} {symbol} @ "
                        f"{order['price']:.4f} | profit: {profit:.4f} USDT")
            return

        # LIVE: check actual exchange order fills
        open_order_ids = {o["id"] for o in exchange.fetch_open_orders(symbol, self.market_type)}
        for order in grid["orders"]:
            if order["filled"] or order["order_id"] in open_order_ids:
                continue
            # Verify the order was actually filled (not cancelled)
            try:
                order_info = exchange.fetch_order(order["order_id"], symbol)
                if order_info.get("status") != "closed" or float(order_info.get("filled", 0)) <= 0:
                    continue  # Cancelled or unfilled — not a real fill
            except Exception:
                continue  # Can't verify — skip
            order["filled"] = True
            # Only credit profit on SELL fills (completing a round trip)
            if order["side"] == "sell":
                step              = (grid["upper"] - grid["lower"]) / self.cfg["grid_levels"]
                profit            = order["size"] * step
                grid["total_profit"] += profit
                logger.info(f"[Grid] SELL FILLED {order['size']:.6f} {symbol} @ "
                            f"{order['price']:.4f} | profit: {profit:.4f} USDT | "
                            f"total: {grid['total_profit']:.4f} USDT")
            else:
                logger.info(f"[Grid] BUY FILLED {order['size']:.6f} {symbol} @ "
                            f"{order['price']:.4f}")

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[Grid] Running on {symbol} ({self.market_type})")
        # Key by exchange:symbol to avoid cross-exchange state collision
        gkey = f"{exchange.name}:{symbol}"
        ticker = exchange.fetch_ticker(symbol, self.market_type)
        price  = ticker.get("last")
        if not price:
            logger.warning(f"[Grid] Could not get price for {symbol}")
            return
        price = float(price)
        self._check_filled_orders(exchange, symbol, gkey)
        if self._should_rebuild(gkey, price):
            if gkey in self._grids:
                self._cancel_grid(exchange, symbol, gkey)
            self._grids[gkey] = self._build_grid(exchange, symbol, price)
        else:
            g     = self._grids[gkey]
            age_h = (time.time() - g["last_build"]) / 3600
            logger.debug(f"[Grid] {symbol}: grid active | age={age_h:.1f}h | "
                         f"profit={g['total_profit']:.4f} USDT")
