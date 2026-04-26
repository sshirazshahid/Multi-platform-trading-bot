"""
strategies/dca_strategy.py — Feature 5: Dollar-Cost Averaging (DCA)

Automatically buys a fixed USDT amount of a coin at regular intervals,
regardless of price. Optionally adds extra buys on significant dips.

Why DCA works:
  - Averages down the cost basis over time
  - Removes emotion from buying decisions
  - Low risk — no leverage, small fixed amounts per interval
  - Ideal for long-term accumulation of BTC, ETH, BNB etc.

Configuration (config.py DCA section):
  interval_hours    — how often to buy (e.g. 4 = every 4 hours)
  amount_usdt       — fixed USDT to spend each interval
  dip_buy_pct       — buy extra if price drops by this % (0 = disabled)
  dip_multiplier    — spend this × amount_usdt on dip buys
  max_daily_buys    — circuit breaker (max buys per day per symbol)
  take_profit_pct   — sell entire DCA stack if profit reaches this %
"""

import time
from collections import defaultdict
from datetime import date
from loguru import logger

from strategies.base_strategy import BaseStrategy
from exchanges.base           import BaseExchange
from core.order_manager       import OrderManager
from core.risk_manager        import RiskManager
from config                   import DCA as CFG, DRY_RUN


class DCAStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager,
                 risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "DCA", market_type)
        self.cfg          = CFG
        self._last_buy    : dict = {}   # symbol -> timestamp of last buy
        self._avg_cost    : dict = {}   # symbol -> weighted average cost
        self._total_qty   : dict = {}   # symbol -> total qty accumulated
        self._total_cost  : dict = {}   # symbol -> total USDT spent
        self._daily_buys  : dict = defaultdict(int)  # symbol_date -> count
        self._today        = date.today()

    # ── Main run ─────────────────────────────────────────────────────

    def run(self, exchange: BaseExchange, symbol: str):
        ex_name = exchange.name
        logger.debug(f"[DCA] Checking {symbol} on {ex_name}")

        # Balance check — use paper wallet in DRY RUN, real balance in LIVE
        if self.order_manager.dry_run:
            _usdt = self.get_usdt_balance(exchange)
        else:
            try:
                _bal = exchange.fetch_balance(self.market_type)
                _usdt = 0.0
                _u = _bal.get("USDT")
                if isinstance(_u, dict):
                    _usdt = float(_u.get("free", 0) or _u.get("total", 0) or 0)
                elif isinstance(_bal.get("free"), dict):
                    _usdt = float(_bal["free"].get("USDT", 0) or 0)
            except Exception:
                return  # Can't check — skip safely
        if _usdt < 6:
            return  # Not enough balance — skip silently

        # Reset daily counters on new day
        today = date.today()
        if today != self._today:
            self._daily_buys.clear()
            self._today = today

        ticker = exchange.fetch_ticker(symbol, self.market_type)
        price  = ticker.get("last") or ticker.get("close")
        if not price:
            return

        price = float(price)
        # Per-exchange keys to avoid cross-exchange state sharing
        ekey      = f"{ex_name}:{symbol}"
        daily_key = f"{ekey}_{today.isoformat()}"

        # Check daily buy limit
        if self._daily_buys[daily_key] >= self.cfg.get("max_daily_buys", 6):
            logger.debug(f"[DCA] {symbol} on {ex_name}: daily buy limit reached.")
            return

        # Check take-profit on accumulated position
        if self._check_take_profit(exchange, symbol, price, ekey):
            return

        # Determine if we should buy now
        should_buy, reason, amount_usdt = self._should_buy(ekey, price)
        if not should_buy:
            return

        # Check balance before buying
        balance = self.get_usdt_balance(exchange)
        if balance < amount_usdt:
            logger.debug(
                f"[DCA] {symbol} on {ex_name}: balance {balance:.2f} < "
                f"{amount_usdt:.2f} USDT needed — skipped")
            return

        # Convert USDT amount to coin quantity
        qty = amount_usdt / price
        min_qty = exchange.get_min_order_size(symbol)
        if qty < min_qty:
            logger.warning(
                f"[DCA] {symbol}: qty {qty:.8f} below min {min_qty} — "
                f"increase DCA amount_usdt in config."
            )
            return

        # Place buy
        if DRY_RUN:
            logger.info(
                f"[DCA] [DRY] BUY {qty:.6f} {symbol} @ {price:.4f} "
                f"({amount_usdt:.2f} USDT) on {ex_name} — {reason}"
            )
            # Debit paper wallet so balance reflects the spend
            wallet = getattr(self.order_manager, "wallet", None)
            if wallet is not None:
                try:
                    fee = amount_usdt * 0.001  # spot taker fee
                    wallet.on_open(ex_name, symbol, "buy", qty, price, fee,
                                   market_type=self.market_type)
                except Exception:
                    pass
        else:
            try:
                # Pass price for exchanges that require it (e.g. Bitget spot market buys)
                exchange.create_order(
                    symbol, "market", "buy", qty, price=price,
                    market_type=self.market_type
                )
                logger.info(
                    f"[DCA] BUY {qty:.6f} {symbol} @ {price:.4f} "
                    f"({amount_usdt:.2f} USDT) on {ex_name} — {reason}"
                )
            except Exception as e:
                err = str(e)
                # Auto-skip pairs that need TradFi agreement or are unsupported
                if any(s in err for s in ("TradFi-Perps", "agreement", "-4411",
                                           "not supported", "symbol not found")):
                    logger.warning(f"[DCA] {symbol} skipped on {ex_name}: {err[:100]}")
                    # Blacklist to prevent repeated failures
                    if hasattr(self, 'order_manager') and hasattr(self.order_manager, 'blacklist'):
                        self.order_manager.blacklist.add(symbol, reason=f"dca_skip:{err[:60]}")
                else:
                    logger.error(f"[DCA] Buy failed for {symbol} on {ex_name}: {e}")
                return

        # Update tracking (per-exchange)
        self._last_buy[ekey]     = time.time()
        self._daily_buys[daily_key] += 1
        prev_cost = self._total_cost.get(ekey, 0.0)
        prev_qty  = self._total_qty.get(ekey, 0.0)
        self._total_cost[ekey] = prev_cost + amount_usdt
        self._total_qty[ekey]  = prev_qty  + qty
        self._avg_cost[ekey]   = (
            self._total_cost[ekey] / self._total_qty[ekey]
        )
        logger.info(
            f"[DCA] {symbol} on {ex_name} avg cost: {self._avg_cost[ekey]:.4f} USDT "
            f"total invested: {self._total_cost[ekey]:.2f} USDT "
            f"qty: {self._total_qty[ekey]:.6f}"
        )

    # ── Decision logic ────────────────────────────────────────────────

    def _should_buy(self, symbol: str, price: float) -> tuple:
        """Returns (buy: bool, reason: str, amount_usdt: float)"""
        interval_secs = self.cfg.get("interval_hours", 4) * 3600
        amount        = self.cfg.get("amount_usdt", 5.0)
        dip_pct       = self.cfg.get("dip_buy_pct", 0.0)
        dip_mult      = self.cfg.get("dip_multiplier", 2.0)

        last = self._last_buy.get(symbol, 0)
        now  = time.time()
        avg  = self._avg_cost.get(symbol, price)

        # Dip buy check (takes priority, but still requires interval cooldown
        # to prevent runaway buys every cycle while price stays below threshold)
        if dip_pct > 0 and avg > 0 and (now - last) >= interval_secs:
            drop_pct = (avg - price) / avg
            if drop_pct >= dip_pct:
                return True, f"dip buy -{drop_pct*100:.1f}%", amount * dip_mult

        # Regular interval buy
        if (now - last) >= interval_secs:
            return True, f"scheduled interval", amount

        return False, "", 0.0

    def _check_take_profit(self, exchange: BaseExchange,
                            symbol: str, price: float,
                            ekey: str = None) -> bool:
        """Sell entire DCA stack if take-profit target hit."""
        tp_pct = self.cfg.get("take_profit_pct", 0.0)
        if tp_pct <= 0:
            return False

        k = ekey or symbol
        avg = self._avg_cost.get(k, 0.0)
        qty = self._total_qty.get(k, 0.0)
        if avg <= 0 or qty <= 0:
            return False

        profit_pct = (price - avg) / avg
        if profit_pct >= tp_pct:
            logger.info(
                f"[DCA] TAKE PROFIT triggered for {symbol}: "
                f"+{profit_pct*100:.1f}% above avg cost {avg:.4f}"
            )
            if not DRY_RUN:
                try:
                    exchange.create_order(
                        symbol, "market", "sell", qty,
                        market_type=self.market_type
                    )
                except Exception as e:
                    logger.error(f"[DCA] TP sell failed: {e}")
                    return False
            else:
                logger.info(
                    f"[DCA] [DRY] SELL {qty:.6f} {symbol} @ {price:.4f} "
                    f"(DCA take-profit)"
                )

            # Reset tracking for this key
            self._avg_cost.pop(k, None)
            self._total_qty.pop(k, None)
            self._total_cost.pop(k, None)
            self._last_buy.pop(k, None)
            return True

        return False

    def generate_signal(self, df):
        """DCA does not use signal-based entry — always returns None."""
        return None

    def get_status(self) -> dict:
        return {
            "avg_costs":   dict(self._avg_cost),
            "total_qty":   dict(self._total_qty),
            "total_cost":  dict(self._total_cost),
        }
