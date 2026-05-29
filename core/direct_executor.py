"""
core/direct_executor.py  --  Execute TradeOpportunity directly

HOW IT WORKS:
  The strategy selector produces a TradeOpportunity (symbol, direction,
  market_type, confidence, regime). DirectExecutor immediately converts it
  into a real position WITHOUT going back through a strategy's run() cycle.

  This eliminates the #1 reason bots miss signals: the strategy's run()
  re-generates its own independent signal from candle data, which may not
  match the multi-TF analysis that produced the opportunity.

WIN RATE IMPROVEMENTS APPLIED:
  1. Minimum R:R enforced: 1.5:1 or better (default 2.5:1 via config)
  2. ATR-based SL/TP: wider stops in volatile conditions = fewer premature SLs
  3. Minimum notional $10 (up from $5) to avoid fee-dominated trades on $100 capital
  4. Duplicate guard: no same symbol+side+market on same exchange
  5. Blacklist guard: symbols with 3 consecutive SLs are skipped automatically
  6. All close reasons are recorded for the blacklist (win/stop-loss tracking)

ATTRIBUTE NAMES (important — arbitrage engine looks for these):
  self._open_fn  = open_position_fn   ← used by ArbitrageEngine
  self._close_fn = close_position_fn  ← used by ArbitrageEngine
"""

from __future__ import annotations

from loguru import logger


class DirectExecutor:

    def __init__(self, profile_name: str, risk, wallet, tracker,
                 blacklist, trailing, open_position_fn, close_position_fn):
        self.name       = profile_name
        self.risk       = risk
        self.wallet     = wallet
        self.tracker    = tracker
        self.blacklist  = blacklist
        self.trailing   = trailing
        # Public names used by ArbitrageEngine
        self._open_fn   = open_position_fn
        self._close_fn  = close_position_fn

    def execute(self, exchange, opp) -> bool:
        """
        Open a position for a TradeOpportunity.

        opp.direction = "buy"  → LONG  (spot or futures)
        opp.direction = "sell" → SHORT (futures ONLY — spot cannot short)

        Returns True if position opened successfully.
        """
        symbol   = opp.trade_symbol   # correct ccxt symbol incl futures suffix
        base_sym = opp.symbol         # e.g. "BTC/USDT" for blacklist checks
        side     = opp.direction      # "buy" or "sell"
        mtype    = opp.market_type    # "spot" or "futures"
        strategy = opp.strategy

        # Guard: spot cannot short
        if mtype == "spot" and side == "sell":
            logger.debug(f"[{self.name}] Spot SHORT blocked for {base_sym}")
            return False

        # Guard: blacklist
        if self.blacklist.is_blacklisted(base_sym):
            logger.debug(f"[{self.name}] {base_sym} blacklisted")
            return False

        # Guard: max open positions
        if not self.risk.can_trade(self.tracker.count_open()):
            logger.debug(f"[{self.name}] max positions reached")
            return False

        # Guard: no duplicate symbol+side+market on this exchange
        for pos in self.tracker.get_open(exchange=exchange.name):
            if (pos.symbol == symbol and pos.side == side
                    and pos.market_type == mtype):
                logger.debug(f"[{self.name}] already have {mtype} {side} {base_sym}")
                return False

        # Fetch current price
        try:
            ticker = exchange.fetch_ticker(symbol, mtype)
            price  = float(ticker.get("last") or ticker.get("close") or 0)
        except Exception as e:
            logger.debug(f"[{self.name}] price fetch {symbol}: {e}")
            return False

        if price <= 0:
            return False

        # Position sizing
        leverage = self.risk.default_leverage if mtype == "futures" else 1
        leverage = self.risk.validate_leverage(leverage)
        balance  = self.wallet.balance(exchange.name)
        size     = self.risk.calculate_position_size(balance, price, leverage)

        # Minimum $10 notional (raised from $5 to ensure fees don't dominate)
        notional = size * price
        if size <= 0 or notional < 10.0:
            logger.debug(f"[{self.name}] notional ${notional:.2f} too small for {base_sym} (min $10)")
            return False

        # SL/TP: distribution-fitted per (symbol, side) when ≥30 warehouse
        # rows in the cell, falls back to ATR×1.8 / ATR×4.5 otherwise.
        # (`get_sl_tp` will pick the fit branch only if `symbol` is supplied
        # and no explicit multipliers are given — both true here.)
        atr    = self._fetch_atr(exchange, symbol, mtype)
        sl, tp = self.risk.get_sl_tp(price, side, atr=atr, symbol=symbol)

        # R:R check — minimum 1.5:1 (config may enforce stricter)
        risk_d   = abs(price - sl)
        reward_d = abs(tp - price)
        rr = reward_d / max(risk_d, 1e-9)
        if rr < 1.5:
            logger.debug(f"[{self.name}] R:R {rr:.2f}:1 too low for {base_sym} — skipped")
            return False

        # Open position via the profile's open_position_fn
        pos = self._open_fn(
            exchange=exchange, symbol=symbol, side=side,
            market_type=mtype, strategy=strategy,
            size=size, sl=sl, tp=tp, leverage=leverage, price=price,
        )

        if pos:
            logger.info(
                "[{}] OPENED {} {} {} @ {:.6f} | "
                "size={:.6f} notional=${:.2f} | "
                "SL={:.6f} TP={:.6f} R:R={:.1f}:1 | "
                "conf={:.0%} regime={}".format(
                    self.name.upper(),
                    "LONG" if side == "buy" else "SHORT",
                    mtype.upper(), base_sym, price,
                    size, notional, sl, tp, rr,
                    opp.confidence, opp.regime))
            return True

        return False

    def check_exits(self, exchange):
        """
        Check SL / TP / trailing stop on every open position for this exchange.
        Called every scan cycle before opening new positions.
        """
        for pos in list(self.tracker.get_open(exchange=exchange.name)):
            try:
                ticker = exchange.fetch_ticker(pos.symbol, pos.market_type)
                price  = float(ticker.get("last") or 0)
                if not price:
                    continue

                # Trailing stop check
                fired, _, new_sl = self.trailing.update(pos, price)
                if fired:
                    self._close_fn(exchange, pos, "trailing_stop", price)
                    self.blacklist.record_win(pos.symbol, pos.side)
                    continue

                if pos.side == "buy":
                    eff_sl = max(new_sl, pos.stop_loss)
                    if price <= eff_sl:
                        self._close_fn(exchange, pos, "stop_loss", price)
                        self.blacklist.record_stop_loss(pos.symbol, pos.side)
                    elif price >= pos.take_profit:
                        self._close_fn(exchange, pos, "take_profit", price)
                        self.blacklist.record_win(pos.symbol, pos.side)
                else:  # short
                    eff_sl = min(new_sl, pos.stop_loss)
                    if price >= eff_sl:
                        self._close_fn(exchange, pos, "stop_loss", price)
                        self.blacklist.record_stop_loss(pos.symbol, pos.side)
                    elif price <= pos.take_profit:
                        self._close_fn(exchange, pos, "take_profit", price)
                        self.blacklist.record_win(pos.symbol, pos.side)

            except Exception as e:
                logger.debug(f"[{self.name}] exit check {pos.symbol}: {e}")

    def _fetch_atr(self, exchange, symbol: str, mtype: str,
                    period: int = 14) -> float | None:
        """
        Fetch ATR from 1h candles for SL/TP placement.
        Returns None on failure — caller falls back to config defaults.
        """
        try:
            import pandas as pd
            raw = exchange.fetch_ohlcv(symbol, "1h", limit=50, market_type=mtype)
            if not raw or len(raw) < period + 5:
                return None
            df  = pd.DataFrame(raw, columns=["ts","o","h","l","c","v"])
            tr  = pd.concat([
                df["h"] - df["l"],
                (df["h"] - df["c"].shift(1)).abs(),
                (df["l"] - df["c"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.ewm(com=period-1, adjust=False).mean().iloc[-1])
            return atr if atr > 0 else None
        except Exception:
            return None
