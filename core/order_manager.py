"""
core/order_manager.py — Order lifecycle with:
  - Fee-aware PnL (entry + exit fees deducted from net profit)
  - Virtual $1000 paper wallet in DRY RUN mode
  - Auto spot↔futures transfer in LIVE mode
  - Fallback to spot when futures permission denied
  - Blacklist / trailing stop / compliance / risk integrations
"""

import uuid
from loguru import logger
from config import DRY_RUN
from exchanges.base             import BaseExchange
from core.position_tracker      import Position, PositionTracker
from core.risk_manager          import RiskManager
from core.trailing_stop_manager import TrailingStopManager
from core.blacklist_manager     import BlacklistManager
from core.compliance_logger     import ComplianceLogger
from core.virtual_wallet        import VirtualWallet
from utils.notifier             import TelegramNotifier
from core.post_mortem           import PostMortem
from core.kelly_sizer           import KellySizer
from core.smart_executor        import SmartExecutor

# Permission-denied error patterns
_PERM_ERRORS = (
    "-2015", "permissions for action", "permission denied",
    "not allowed", "api-key", "IP restriction",
    "unauthorized", "forbidden",
)

# Position mode error — means exchange is in One-Way Mode, not Hedge Mode
_POSITION_MODE_ERRORS = (
    "-4061", "position side does not match",
    "positionSide", "position mode",
    "40774", "unilateral position",
    "unilateral",
    "40773", "two-way positions",   # Bitget: tradeSide close is two-way only
)

# Errors that mean "skip THIS pair, but other pairs are fine"
_SKIP_PAIR_ERRORS = (
    "-4411", "TradFi-Perps agreement",
    "agreement contract", "sign agreement",
    "not supported", "symbol not found",
    "does not have market symbol",
)


def _is_permission_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s.lower() in msg for s in _PERM_ERRORS)


def _is_position_mode_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _POSITION_MODE_ERRORS)


def _is_skip_pair_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _SKIP_PAIR_ERRORS)


class OrderManager:

    def __init__(self, tracker: PositionTracker, risk: RiskManager,
                 notifier: TelegramNotifier):
        self.tracker    = tracker
        self.risk       = risk
        self.notifier   = notifier
        self.dry_run    = DRY_RUN
        self.trailing   = TrailingStopManager()
        self.blacklist  = BlacklistManager()
        self.compliance = ComplianceLogger(dry_run=DRY_RUN)
        self.wallet     = VirtualWallet()
        self.post_mortem = PostMortem()
        self.kelly       = KellySizer()
        self.executor    = SmartExecutor()
        self.mcp_brain   = None  # Set by bot_engine after construction
        # Track exchanges where futures is disabled (don't retry)
        self._futures_disabled = set()
        # Track exchanges using One-Way Mode (no positionSide param)
        # Bitget is ALWAYS one-way — pre-populate to avoid first-order 40774 error
        self._oneway_mode = {"bitget"}
        # Track positions where SL was already widened once (prevent infinite widening)
        self._sl_widened = set()

        if DRY_RUN:
            logger.warning(
                "[Orders] DRY RUN MODE — no real orders placed. "
                "Paper wallet starts at $"
                + str(int(self.wallet._start))
                + " USDT per exchange."
            )
            logger.info(self.wallet.statement())
        else:
            logger.warning("[Orders] LIVE MODE — real orders will be placed.")

    # ── Balance helpers ───────────────────────────────────────────────

    def available_balance(self, exchange: BaseExchange,
                          market_type: str = "spot") -> float:
        """
        In DRY RUN: returns virtual wallet balance.
        In LIVE:    fetches real balance for spot or futures.
        """
        if self.dry_run:
            return self.wallet.balance(exchange.name)
        try:
            bal = exchange.fetch_balance(market_type)
            return self._extract_usdt(bal)
        except Exception as e:
            logger.debug(f"[Orders] balance fetch {market_type}: {e}")
        return 0.0

    def _extract_usdt(self, bal: dict) -> float:
        if not bal:
            return 0.0
        # Prefer total over free for unified accounts (Bybit UTA returns free=0
        # or dust when funds are allocated to unified margin, but total is correct)
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            # Try total first (covers Bybit unified), then free
            v = usdt.get("total") or usdt.get("free") or 0.0
            if v:
                return float(v)
        total = bal.get("total", {})
        if isinstance(total, dict):
            v = total.get("USDT", 0.0)
            if v:
                return float(v)
        free = bal.get("free", {})
        if isinstance(free, dict):
            v = free.get("USDT", 0.0)
            if v:
                return float(v)
        return 0.0

    def auto_transfer_for_trade(self, exchange: BaseExchange,
                                 market_type: str, needed: float) -> bool:
        """
        Auto-transfer USDT between spot and futures when needed.
        If trading futures but balance is in spot → transfer spot→futures.
        If trading spot but balance is in futures → transfer futures→spot.
        """
        if self.dry_run:
            return True
        if not hasattr(exchange, 'transfer'):
            return False

        current = self.available_balance(exchange, market_type)
        if current >= needed:
            return True

        other_type = "futures" if market_type == "spot" else "spot"
        other_bal  = self.available_balance(exchange, other_type)

        if other_bal < 5:
            return False

        transfer_amt = min(needed - current + 5, other_bal * 0.8)
        transfer_amt = round(transfer_amt, 2)

        if transfer_amt < 1:
            return False

        logger.info(f"[Orders] Auto-transfer {transfer_amt:.2f} USDT "
                    f"{other_type} → {market_type} on {exchange.name}")
        return exchange.transfer(transfer_amt, other_type, market_type)

    # ── Open position ─────────────────────────────────────────────────

    def open_position(self, exchange: BaseExchange, symbol: str, side: str,
                      market_type: str, strategy: str, size: float,
                      sl: float, tp: float, leverage: int = 1,
                      order_type: str = "market", price: float = None):

        if self.blacklist.is_blacklisted(symbol):
            logger.warning(f"[Orders] {symbol} blacklisted — skipped.")
            return None

        if not self.risk.can_trade(self.tracker.count_open()):
            return None

        # Kelly criterion: block trade if strategy has negative edge
        # If MCP Brain approved this coin, use lenient Kelly thresholds
        mcp_approved = False
        if self.mcp_brain:
            try:
                base = symbol.split("/")[0].split(":")[0]
                mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                if mcp_dec.get("action") in ("BUY", "SELL"):
                    mcp_approved = True
            except Exception:
                pass
        blocked, block_reason = self.kelly.should_block_trade(
            strategy, mcp_approved=mcp_approved)
        if blocked:
            logger.warning(f"[Orders] KELLY BLOCK: {block_reason}")
            return None

        if size <= 0:
            logger.warning(f"[Orders] Invalid size {size} for {symbol}")
            return None

        # MINIMUM NOTIONAL gate
        _ep = price
        if not _ep:
            try:
                _t = exchange.fetch_ticker(symbol, market_type)
                _ep = float(_t.get("last") or _t.get("close") or 0)
            except Exception: _ep = 0
        if _ep > 0 and size * _ep < 5.0:
            logger.debug(f"[Orders] {symbol}: ${size * _ep:.2f} < $5 min — skip")
            return None

        min_size = exchange.get_min_order_size(symbol)
        if size < min_size:
            # Some exchanges report min in contracts not coins.
            # Check by NOTIONAL VALUE (USD) instead of raw quantity.
            est_price = price or 0
            if not est_price:
                try:
                    t = exchange.fetch_ticker(symbol, market_type)
                    est_price = float(t.get("last") or t.get("close") or 0)
                except Exception:
                    pass
            notional = size * est_price if est_price else 0
            if notional >= 5.0:
                logger.debug(
                    f"[Orders] Size {size:.8f} < exchange min {min_size} "
                    f"but notional ${notional:.2f} OK — proceeding")
            else:
                logger.warning(
                    f"[Orders] Size {size:.8f} (${notional:.2f}) too small "
                    f"for {symbol} — skipping")
                return None

        # Check if futures is disabled on this exchange (permission error)
        ex_name_lower = exchange.name.lower()
        if market_type == "futures" and ex_name_lower in self._futures_disabled:
            if side == "sell":
                logger.debug(f"[Orders] Futures disabled on {exchange.name}, "
                             f"SHORT not possible on spot — skipping {symbol}")
                return None
            # Fallback to spot for BUY signals
            logger.info(f"[Orders] Futures disabled on {exchange.name}, "
                        f"falling back to SPOT for {symbol}")
            market_type = "spot"
            leverage = 1
            # Convert futures symbol to spot (remove :USDT suffix)
            if ":USDT" in symbol:
                symbol = symbol.replace(":USDT", "")

        if market_type == "futures" and leverage > 1:
            safe_lev = self.risk.validate_leverage(leverage)
            if not self.dry_run:
                exchange.set_leverage(symbol, safe_lev)
            leverage = safe_lev

        # LIVE: Auto-transfer if needed
        if not self.dry_run and market_type == "futures":
            notional = size * (price or 0)
            if notional == 0:
                try:
                    t = exchange.fetch_ticker(symbol, market_type)
                    notional = size * float(t.get("last") or t.get("close") or 0)
                except Exception:
                    pass
            margin_needed = notional / max(leverage, 1) + 2  # +2 USDT buffer
            self.auto_transfer_for_trade(exchange, "futures", margin_needed)

        # ── MCP Brain SL/TP override — use AI-recommended levels when available ──
        if self.mcp_brain:
            try:
                base = symbol.split("/")[0].split(":")[0]
                mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                mcp_sl = mcp_dec.get("sl_pct", 0)
                mcp_tp = mcp_dec.get("tp_pct", 0)
                if mcp_sl > 0 and mcp_tp > 0:
                    est_px = price
                    if not est_px:
                        try:
                            t = exchange.fetch_ticker(symbol, market_type)
                            est_px = float(t.get("last") or t.get("close") or 0)
                        except Exception:
                            est_px = 0
                    if est_px > 0:
                        if side == "buy":
                            mcp_sl_px = est_px * (1 - mcp_sl / 100)
                            mcp_tp_px = est_px * (1 + mcp_tp / 100)
                        else:
                            mcp_sl_px = est_px * (1 + mcp_sl / 100)
                            mcp_tp_px = est_px * (1 - mcp_tp / 100)
                        # Only override if MCP gives wider SL (more conservative)
                        # or significantly better TP
                        if side == "buy":
                            if mcp_sl_px < sl:  # wider SL for buy = lower price
                                logger.info(
                                    f"[MCP-SL] {symbol}: SL {sl:.6g} → {mcp_sl_px:.6g} "
                                    f"(MCP {mcp_sl:.1f}%)")
                                sl = mcp_sl_px
                            if mcp_tp_px > tp:  # higher TP target
                                tp = mcp_tp_px
                        else:
                            if mcp_sl_px > sl:  # wider SL for sell = higher price
                                logger.info(
                                    f"[MCP-SL] {symbol}: SL {sl:.6g} → {mcp_sl_px:.6g} "
                                    f"(MCP {mcp_sl:.1f}%)")
                                sl = mcp_sl_px
                            if mcp_tp_px < tp:  # lower TP target for shorts
                                tp = mcp_tp_px
            except Exception:
                pass

        # ── MCP Brain confidence → position size scaling ──
        # High-conviction signals get more capital, marginal ones get less
        if self.mcp_brain:
            try:
                base = symbol.split("/")[0].split(":")[0]
                mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                mcp_conf = mcp_dec.get("confidence", 0)
                layers = mcp_dec.get("layers_aligned", 0)
                if mcp_conf >= 0.85 and layers >= 4:
                    size *= 1.20  # 20% boost for high-conviction (4+ layers agree)
                    logger.info(
                        f"[MCP-SIZE] {symbol}: +20% size (conf={mcp_conf:.0%}, "
                        f"{layers}/5 layers)")
                elif mcp_conf < 0.60 and mcp_conf > 0:
                    size *= 0.80  # 20% reduction for marginal signals
                    logger.info(
                        f"[MCP-SIZE] {symbol}: -20% size (conf={mcp_conf:.0%})")
            except Exception:
                pass

        # Get fill price
        fill_price = price
        if not fill_price or order_type == "market":
            ticker     = exchange.fetch_ticker(symbol, market_type)
            fill_price = ticker.get("last") or ticker.get("close") or price
        if not fill_price:
            logger.error(f"[Orders] Cannot get price for {symbol}")
            return None
        fill_price = float(fill_price)

        # Round quantity to exchange precision (MEXC: whole contracts, Bitget: 0.1)
        try:
            rounded_size = exchange.round_quantity(symbol, size)
            if rounded_size != size:
                notional_check = rounded_size * fill_price
                if notional_check < 5.0:
                    logger.warning(
                        f"[Orders] {symbol}: rounded qty {rounded_size} "
                        f"(${notional_check:.2f}) too small after rounding")
                    return None
                logger.debug(
                    f"[Orders] {symbol}: qty {size:.8f} -> {rounded_size:.8f} "
                    f"(exchange precision)")
                size = rounded_size
        except Exception:
            pass

        # Build Position first so __post_init__ calculates entry_fee
        order_id = f"DRY-{uuid.uuid4().hex[:8]}" if self.dry_run else None
        pos = Position(
            id=order_id or uuid.uuid4().hex,
            exchange=exchange.name, symbol=symbol, side=side,
            market_type=market_type, strategy=strategy,
            entry_price=fill_price, size=size,
            stop_loss=sl, take_profit=tp,
            leverage=leverage, order_id=order_id,
        )

        if self.dry_run:
            ok = self.wallet.on_open(
                exchange.name, symbol, side,
                size, fill_price, pos.entry_fee, market_type,
                leverage=leverage,
            )
            if not ok:
                return None
            logger.info(
                f"[Orders] [DRY] {side.upper()} {size:.6f} {symbol} "
                f"@ {fill_price:.4f} | "
                f"entry_fee={pos.entry_fee:.4f} USDT | "
                f"SL={sl:.4f} TP={tp:.4f} | {strategy} | {market_type}"
            )
        else:
            try:
                params = {}
                if market_type == "futures":
                    # Use Hedge Mode params UNLESS we know this exchange is One-Way
                    if ex_name_lower not in self._oneway_mode:
                        params["positionSide"] = "LONG" if side == "buy" else "SHORT"

                # ── Smart Execution: spread check + limit order + fallback ──
                spread_info = self.executor.check_spread(
                    exchange, symbol, market_type)
                if not spread_info["ok"]:
                    logger.info(
                        f"[Orders] {symbol}: spread too wide "
                        f"({spread_info['spread_pct']*100:.3f}%) — skip")
                    return None

                notional_usd = size * fill_price
                if self.executor.should_use_twap(notional_usd):
                    # TWAP for large orders
                    results = self.executor.execute_twap(
                        exchange, symbol, side, size,
                        market_type, params)
                    order = results[0] if results else None
                else:
                    # Limit order with market fallback
                    order = self.executor.execute_limit_with_fallback(
                        exchange, symbol, side, size,
                        market_type, params)

                if not order or not order.get("id"):
                    logger.warning(f"[Orders] {symbol}: exchange returned empty order — skipping")
                    return None
                pos.order_id   = order.get("id")
                pos.id         = pos.order_id or pos.id
                fill_price     = order.get("average") or order.get("price") or fill_price
                pos.entry_price = float(fill_price)
                logger.info(
                    f"[Orders] LIVE ORDER: {side.upper()} {size:.6f} {symbol} "
                    f"@ {fill_price:.4f} | "
                    f"entry_fee={pos.entry_fee:.4f} USDT | "
                    f"id={pos.id} | {strategy}"
                )
            except Exception as e:
                # Skip pair errors (e.g., Binance TradFi agreement for XAU/XAG)
                if _is_skip_pair_error(e):
                    logger.warning(
                        f"[Orders] {symbol} skipped on {exchange.name}: "
                        f"{str(e)[:100]}")
                    # Auto-blacklist to prevent repeated warnings each cycle
                    self.blacklist.add(symbol, reason=f"skip_pair:{str(e)[:60]}")
                    return None
                # Handle position mode mismatch — retry without positionSide
                if _is_position_mode_error(e) and market_type == "futures":
                    self._oneway_mode.add(ex_name_lower)
                    logger.info(
                        f"[Orders] {exchange.name} is in ONE-WAY mode. "
                        f"Retrying without positionSide...")
                    try:
                        order = exchange.create_order(
                            symbol, order_type, side, size,
                            fill_price if order_type == "limit" else None,
                            params={}, market_type=market_type,
                        )
                        if not order or not order.get("id"):
                            logger.warning(f"[Orders] {symbol}: one-way retry returned empty order")
                            return None
                        pos.order_id    = order.get("id")
                        pos.id          = pos.order_id or pos.id
                        fill_price      = order.get("average") or order.get("price") or fill_price
                        pos.entry_price = float(fill_price)
                        logger.info(
                            f"[Orders] LIVE ORDER (one-way): {side.upper()} {size:.6f} {symbol} "
                            f"@ {fill_price:.4f} | id={pos.id} | {strategy}")
                    except Exception as e2:
                        logger.error(f"[Orders] Order failed (one-way retry): {e2}")
                        return None
                elif _is_permission_error(e) and market_type == "futures":
                    self._futures_disabled.add(ex_name_lower)
                    logger.warning(
                        f"[Orders] Futures PERMISSION DENIED on {exchange.name} "
                        f"— falling back to SPOT for buy signals.")
                    if side == "buy":
                        spot_symbol = symbol.replace(":USDT", "")
                        return self.open_position(
                            exchange, spot_symbol, "buy", "spot",
                            strategy, size, sl, tp, leverage=1)
                    return None
                else:
                    logger.error(f"[Orders] Order failed on {exchange.name}: {e}")
                    self.notifier.error(
                        f"Order FAILED: {symbol} {side.upper()}\n{str(e)[:200]}")
                    return None

        self.tracker.add(pos)

        # Place SL/TP orders on the exchange for real protection
        if not self.dry_run and market_type == "futures":
            self._place_exchange_sl_tp(exchange, pos, sl, tp, side, symbol, size, market_type)

        self.compliance.log_trade(
            exchange.name, symbol, side, size, fill_price,
            strategy, order_id=pos.id, reason="entry"
        )
        self.notifier.trade_opened(
            exchange.name, symbol, side, fill_price,
            size, sl, tp, strategy, market_type
        )
        return pos

    def _place_exchange_sl_tp(self, exchange, pos, sl, tp, side, symbol, size, market_type):
        """Attempt to place SL/TP conditional orders on the exchange.
        Falls back silently to local monitoring if exchange rejects."""
        ex_lower = exchange.name.lower()
        close_side = "sell" if side == "buy" else "buy"
        oneway = ex_lower in self._oneway_mode

        # Round prices to exchange precision (fixes Bitget 40808 "checkBDScale" error)
        sl_rounded = exchange.round_price(symbol, sl)
        tp_rounded = exchange.round_price(symbol, tp)
        size_rounded = exchange.round_quantity(symbol, size)

        # Stop-Loss
        try:
            sl_params = {
                "triggerPrice": sl_rounded,
                "stopLossPrice": sl_rounded,  # ccxt unified param
            }
            if oneway:
                sl_params["tradeSide"] = "close"  # Bitget one-way
            else:
                sl_params["positionSide"] = "LONG" if side == "buy" else "SHORT"
                sl_params["reduceOnly"] = True
            exchange.exchange.create_order(
                symbol, "market", close_side, size_rounded, None, sl_params)
            pos._exchange_sl = True  # Track that exchange SL is set
            logger.info(f"[Orders] SL order on {exchange.name}: {symbol} @ {sl_rounded}")
        except Exception as e:
            logger.debug(f"[Orders] SL order skipped on {exchange.name} {symbol}: {e} — local monitoring active")

        # Take-Profit
        try:
            tp_params = {
                "triggerPrice": tp_rounded,
                "takeProfitPrice": tp_rounded,  # ccxt unified param
            }
            if oneway:
                tp_params["tradeSide"] = "close"
            else:
                tp_params["positionSide"] = "LONG" if side == "buy" else "SHORT"
                tp_params["reduceOnly"] = True
            exchange.exchange.create_order(
                symbol, "market", close_side, size_rounded, None, tp_params)
            pos._exchange_tp = True
            logger.info(f"[Orders] TP order on {exchange.name}: {symbol} @ {tp_rounded}")
        except Exception as e:
            logger.debug(f"[Orders] TP order skipped on {exchange.name} {symbol}: {e} — local monitoring active")

    # ── Close position ────────────────────────────────────────────────

    def close_position(self, exchange: BaseExchange, position: Position,
                       reason: str, price: float = None,
                       order_type: str = "market"):

        close_side = "sell" if position.side == "buy" else "buy"

        if not price:
            ticker = exchange.fetch_ticker(position.symbol, position.market_type)
            price  = ticker.get("last") or ticker.get("close")
        if not price:
            logger.error(f"[Orders] Cannot get close price for {position.symbol}")
            return None
        price = float(price)

        if self.dry_run:
            logger.info(
                f"[Orders] [DRY] CLOSE {position.symbol} "
                f"@ {price:.4f} | reason={reason}"
            )
        else:
            ex_lower = exchange.name.lower()
            order_placed = False

            # Build close params — One-Way mode needs special handling per exchange
            params = {}
            if position.market_type == "futures":
                if ex_lower in self._oneway_mode:
                    # Bitget one-way: needs reduceOnly so client converts to tradeSide="close"
                    # Other one-way (Binance): no positionSide, no reduceOnly
                    if getattr(exchange, '_is_oneway', False):
                        params["reduceOnly"] = True
                else:
                    params["positionSide"] = (
                        "LONG" if position.side == "buy" else "SHORT")
                    params["reduceOnly"] = True

            try:
                exchange.create_order(
                    position.symbol, order_type, close_side,
                    position.size,
                    price if order_type == "limit" else None,
                    params=params, market_type=position.market_type)
                order_placed = True
            except Exception as e:
                err = str(e)
                # Already closed/liquidated on exchange — mark as closed in tracker
                _already_closed = (
                    "No position" in err or "22002" in err
                    or "position does not exist" in err.lower()
                    or "order not found" in err.lower()
                    or "insufficient" in err.lower()
                )
                if _already_closed:
                    logger.info(
                        f"[Orders] {position.symbol} already closed on exchange "
                        f"— syncing tracker")
                    order_placed = True  # Proceed to close in tracker
                # "reduceonly not required" — Binance One-Way mode
                elif "reduceonly" in err.lower() or "-1106" in err:
                    self._oneway_mode.add(ex_lower)
                    logger.info(f"[Orders] {exchange.name} One-Way mode — retrying close without reduceOnly")
                    try:
                        exchange.create_order(
                            position.symbol, order_type, close_side,
                            position.size, None,
                            params={}, market_type=position.market_type)
                        order_placed = True
                    except Exception as e2:
                        err2 = str(e2).lower()
                        if "no position" in err2 or "22002" in err2 or "does not exist" in err2:
                            order_placed = True
                        else:
                            logger.error(f"[Orders] Close failed (retry): {e2}")
                # Position mode mismatch (Bitget 40773/40774 / Binance positionSide)
                elif (_is_position_mode_error(e) or "unilateral" in err.lower()) \
                        and position.market_type == "futures":
                    self._oneway_mode.add(ex_lower)
                    # One-way mode: reduceOnly=True tells the exchange this is a close
                    _ow_close = {"reduceOnly": True}
                    try:
                        exchange.create_order(
                            position.symbol, order_type, close_side,
                            position.size, None,
                            params=_ow_close, market_type=position.market_type)
                        order_placed = True
                    except Exception as e2:
                        err2 = str(e2).lower()
                        if "no position" in err2 or "22002" in err2 or "does not exist" in err2:
                            order_placed = True
                        else:
                            logger.error(f"[Orders] Close failed (one-way): {e2}")
                else:
                    logger.error(f"[Orders] Close failed for {position.id}: {e}")

            if not order_placed:
                return None

        closed = self.tracker.close(position.id, price, reason)
        if not closed:
            return None

        if self.dry_run:
            self.wallet.on_close(
                position.exchange, position.symbol, position.side,
                position.size, price, position.entry_price,
                closed.exit_fee, closed.gross_pnl,
                position.market_type
            )

        is_win  = (closed.pnl or 0) > 0
        pnl_pct = closed.pnl_pct or 0.0

        self.risk.record_trade_pnl(
            closed.pnl, price * position.size,
            is_win=is_win, pnl_pct=pnl_pct
        )
        self.compliance.log_trade(
            position.exchange, position.symbol, close_side,
            position.size, price, position.strategy,
            pnl=closed.pnl, reason=reason, order_id=position.id
        )

        if is_win:
            self.blacklist.record_win(position.symbol)
        elif reason == "stop_loss":
            self.blacklist.record_stop_loss(position.symbol)

        self.trailing.remove(position.id)
        self._sl_widened.discard(position.id)

        # Post-mortem analysis
        try:
            self.post_mortem.analyze_trade({
                "symbol": position.symbol, "exchange": position.exchange,
                "side": position.side, "market_type": position.market_type,
                "strategy": position.strategy,
                "entry_price": position.entry_price, "close_price": price,
                "pnl": closed.pnl, "pnl_pct": pnl_pct,
                "close_reason": reason, "leverage": position.leverage,
                "open_time": getattr(position, 'open_time', 0),
                "close_time": getattr(closed, 'close_time', 0),
            })
        except Exception as pm_err:
            logger.debug(f"[PostMortem] {pm_err}")

        self.notifier.trade_closed(
            position.exchange, position.symbol, position.side,
            position.entry_price, price,
            closed.pnl, pnl_pct, reason
        )
        return closed

    # ── SL / TP / Trailing check ──────────────────────────────────────

    def _net_pnl_at_price(self, pos: Position, price: float) -> tuple:
        """Calculate what net PnL (after fees) would be if we closed at this price.
        Returns (net_pnl_usd, net_pnl_pct, breakeven_price)."""
        from core.position_tracker import _fee_rate
        rate = _fee_rate(pos.market_type)
        entry_fee = pos.size * pos.entry_price * rate
        exit_fee = pos.size * price * rate
        if pos.side == "buy":
            gross = (price - pos.entry_price) * pos.size * pos.leverage
            # Breakeven = entry where gross covers both fees + buffer
            be = pos.entry_price * (1 + rate * 2 + 0.0005)
        else:
            gross = (pos.entry_price - price) * pos.size * pos.leverage
            be = pos.entry_price * (1 - rate * 2 - 0.0005)
        net = gross - entry_fee - exit_fee
        notional = pos.entry_price * pos.size
        net_pct = (net / notional * 100) if notional > 0 else 0.0
        return net, net_pct, be

    def _early_breakeven_move(self, pos: Position, price: float, effective_sl: float) -> float:
        """Once profit covers fees, move SL to breakeven proactively.
        This prevents winners from turning into losers."""
        _, net_pct, be = self._net_pnl_at_price(pos, price)
        if net_pct >= 1.0:  # At least 1.0% net profit — let winners breathe before locking in
            if pos.side == "buy" and effective_sl < be:
                logger.info(
                    f"[Orders] BREAKEVEN MOVE: {pos.symbol} BUY — "
                    f"net profit {net_pct:.2f}%, moving SL {effective_sl:.6g} → {be:.6g}")
                return be
            elif pos.side == "sell" and effective_sl > be:
                logger.info(
                    f"[Orders] BREAKEVEN MOVE: {pos.symbol} SELL — "
                    f"net profit {net_pct:.2f}%, moving SL {effective_sl:.6g} → {be:.6g}")
                return be
        return effective_sl

    def check_sl_tp(self, exchange: BaseExchange, market_type: str = "spot"):
        positions = self.tracker.get_open(exchange=exchange.name)
        for pos in positions:
            if pos.market_type != market_type:
                continue
            ticker = exchange.fetch_ticker(pos.symbol, market_type)
            price  = ticker.get("last")
            if not price:
                continue
            price = float(price)

            should_trail, trail_reason, updated_sl = self.trailing.update(
                pos, price
            )
            if should_trail:
                logger.info(
                    f"[Orders] TRAILING STOP: {pos.symbol} "
                    f"@ {price:.4f} (trail SL={updated_sl:.4f})"
                )
                self.close_position(exchange, pos, "trailing_stop", price)
                continue

            effective_sl = (
                max(updated_sl, pos.stop_loss)
                if pos.side == "buy"
                else min(updated_sl, pos.stop_loss)
            )

            # ── Early breakeven move: lock SL to breakeven once in profit ──
            effective_sl = self._early_breakeven_move(pos, price, effective_sl)

            if pos.side == "buy":
                if price <= effective_sl:
                    # ── ANTI-LOSS GATE ──
                    # Before closing at SL, check if net PnL is negative
                    net_pnl, net_pct, be = self._net_pnl_at_price(pos, price)
                    if net_pnl < 0 and pos.id not in self._sl_widened:
                        # Check MCP Brain: does it predict recovery?
                        coin = pos.symbol.split("/")[0]
                        mcp_hold = False
                        if self.mcp_brain and abs(net_pct) < 1.5:
                            mcp_hold = self.mcp_brain.should_hold_position(
                                coin, pos.side, abs(net_pct))
                        if mcp_hold:
                            # Widen SL by 0.5% — give position room to recover
                            new_sl = effective_sl * (1 - 0.005)
                            self._sl_widened.add(pos.id)
                            pos.stop_loss = new_sl
                            logger.warning(
                                f"[Orders] ANTI-LOSS: {pos.symbol} BUY — "
                                f"net={net_pct:+.2f}%, MCP says HOLD. "
                                f"SL widened {effective_sl:.6g} → {new_sl:.6g}")
                            continue
                    logger.warning(
                        f"[Orders] STOP LOSS: {pos.symbol} "
                        f"@ {price:.4f} (SL={effective_sl:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                elif price >= pos.take_profit:
                    trail_status = self.trailing._tracking.get(pos.id, {})
                    # ── MCP BRAIN PROFIT GATE: consult MCP before taking profit ──
                    coin = pos.symbol.split("/")[0]
                    _, net_pct, _ = self._net_pnl_at_price(pos, price)
                    mcp_take = True  # Default: take profit
                    if self.mcp_brain and trail_status.get("active"):
                        mcp_take = self.mcp_brain.should_take_profit(
                            coin, pos.side, net_pct)
                    if trail_status.get("active") and not mcp_take:
                        logger.info(
                            f"[Orders] {pos.symbol} passed TP {pos.take_profit:.4f} "
                            f"— MCP says RIDE, trailing active (peak={trail_status.get('peak',0):.4f})")
                    else:
                        logger.info(
                            f"[Orders] TAKE PROFIT: {pos.symbol} "
                            f"@ {price:.4f} (TP={pos.take_profit:.4f}) net={net_pct:+.2f}%"
                        )
                        self.close_position(exchange, pos, "take_profit", price)
                        continue

                # ── MCP BRAIN PROACTIVE PROFIT-TAKE: check even BEFORE TP is hit ──
                _, net_pct_check, _ = self._net_pnl_at_price(pos, price)
                if net_pct_check >= 2.0 and self.mcp_brain:
                    coin = pos.symbol.split("/")[0]
                    adv = self.mcp_brain.get_position_advice(pos.id)
                    if adv.get("action") == "TAKE_PROFIT" and adv.get("confidence", 0) >= 0.65:
                        logger.info(
                            f"[Orders] MCP PROFIT-TAKE: {pos.symbol} BUY "
                            f"@ {price:.4f} net={net_pct_check:+.2f}% — "
                            f"MCP says TAKE_PROFIT conf={adv['confidence']:.0%}: "
                            f"{adv.get('reason','')[:50]}")
                        self.close_position(exchange, pos, "mcp_take_profit", price)
                        continue

            else:
                if price >= effective_sl:
                    # ── ANTI-LOSS GATE (shorts) ──
                    net_pnl, net_pct, be = self._net_pnl_at_price(pos, price)
                    if net_pnl < 0 and pos.id not in self._sl_widened:
                        coin = pos.symbol.split("/")[0]
                        mcp_hold = False
                        if self.mcp_brain and abs(net_pct) < 1.5:
                            mcp_hold = self.mcp_brain.should_hold_position(
                                coin, pos.side, abs(net_pct))
                        if mcp_hold:
                            new_sl = effective_sl * (1 + 0.005)
                            self._sl_widened.add(pos.id)
                            pos.stop_loss = new_sl
                            logger.warning(
                                f"[Orders] ANTI-LOSS: {pos.symbol} SELL — "
                                f"net={net_pct:+.2f}%, MCP says HOLD. "
                                f"SL widened {effective_sl:.6g} → {new_sl:.6g}")
                            continue
                    logger.warning(
                        f"[Orders] STOP LOSS (short): {pos.symbol} "
                        f"@ {price:.4f} (SL={effective_sl:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                elif price <= pos.take_profit:
                    trail_status = self.trailing._tracking.get(pos.id, {})
                    # ── MCP BRAIN PROFIT GATE (shorts): consult MCP ──
                    coin = pos.symbol.split("/")[0]
                    _, net_pct, _ = self._net_pnl_at_price(pos, price)
                    mcp_take = True
                    if self.mcp_brain and trail_status.get("active"):
                        mcp_take = self.mcp_brain.should_take_profit(
                            coin, pos.side, net_pct)
                    if trail_status.get("active") and not mcp_take:
                        logger.info(
                            f"[Orders] {pos.symbol} passed TP {pos.take_profit:.4f} "
                            f"— MCP says RIDE, trailing active (trough={trail_status.get('trough',0):.4f})")
                    else:
                        logger.info(
                            f"[Orders] TAKE PROFIT (short): {pos.symbol} "
                            f"@ {price:.4f} (TP={pos.take_profit:.4f}) net={net_pct:+.2f}%"
                        )
                        self.close_position(exchange, pos, "take_profit", price)
                        continue

                # ── MCP BRAIN PROACTIVE PROFIT-TAKE (shorts) ──
                _, net_pct_check, _ = self._net_pnl_at_price(pos, price)
                if net_pct_check >= 2.0 and self.mcp_brain:
                    coin = pos.symbol.split("/")[0]
                    adv = self.mcp_brain.get_position_advice(pos.id)
                    if adv.get("action") == "TAKE_PROFIT" and adv.get("confidence", 0) >= 0.65:
                        logger.info(
                            f"[Orders] MCP PROFIT-TAKE: {pos.symbol} SELL "
                            f"@ {price:.4f} net={net_pct_check:+.2f}% — "
                            f"MCP says TAKE_PROFIT conf={adv['confidence']:.0%}: "
                            f"{adv.get('reason','')[:50]}")
                        self.close_position(exchange, pos, "mcp_take_profit", price)
                        continue

            # ── STALE POSITION TIMEOUT: close losing/stagnant positions open > 24 hours ──
            # Crypto trends take 6-24h to develop — don't kill trades prematurely
            age_min = getattr(pos, "duration_minutes", 0) or 0
            if age_min >= 1440:  # 24 hours
                net_pnl, net_pct, _ = self._net_pnl_at_price(pos, price)
                if net_pnl < 0:
                    logger.warning(
                        f"[Orders] TIMEOUT CLOSE: {pos.symbol} {pos.side} "
                        f"open {age_min:.0f}min, net={net_pct:+.2f}% — cutting loss")
                    self.close_position(exchange, pos, "stale_timeout", price)
                elif net_pct < 0.3:
                    # Flat after 24 hours — free up capital
                    logger.info(
                        f"[Orders] TIMEOUT CLOSE: {pos.symbol} {pos.side} "
                        f"open {age_min:.0f}min, net={net_pct:+.2f}% — freeing capital")
                    self.close_position(exchange, pos, "stale_timeout", price)
