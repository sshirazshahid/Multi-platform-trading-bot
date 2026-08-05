"""
core/order_mgmt/entry.py — OrderManager _EntryMixin mixin (Phase D4).
"""
import time
import uuid

from loguru import logger

from config import DRY_RUN, RISK
from core.order_mgmt.helpers import (
    _is_permission_error,
    _is_position_mode_error,
    _is_skip_pair_error,
    _maker_first_cfg,
    _mcp_confidence_size_multiplier,
    _mid_from_ticker,
    _safe_ticker_px,
    build_sl_tp_order_params,
)
from core.position_tracker import Position
from exchanges.base import BaseExchange
from utils.http_redaction import redact_http_debug as _redact_http_debug

class _EntryMixin:
    def open_position(self, exchange: BaseExchange, symbol: str, side: str,
                      market_type: str, strategy: str, size: float,
                      sl: float, tp: float, leverage: int = 1,
                      order_type: str = "market", price: float = None,
                      candidate_id: int = None, mcp_score: float = None,
                       model_version: str = None,
                       decision_id: str | None = None,
                       execution_snapshot: dict | None = None,
                       authorization_strategy_id: str | None = None,
                       decision_confidence: float | None = None,
                       decision_parent_id: str | None = None,
                       _maker_first_ctx: dict | None = None):

        # Provenance: reset per attempt; every internal reject stashes a
        # reason here before returning None.
        self.last_open_reject = None

        if getattr(self, "enforce_entry_policy", False):
            from core.entry_policy import authorize_runtime_entry

            authorization = authorize_runtime_entry(
                authorization_strategy_id or strategy,
                strategy_version=model_version,
            )
            if not authorization.allowed:
                self.last_open_reject = authorization.reason
                logger.warning(
                    f"[EntryPolicy] blocked "
                    f"{authorization_strategy_id or strategy}:{symbol} open: "
                    f"{authorization.reason}"
                )
                return None

        if getattr(self, "enforce_event_provenance", False):
            try:
                from datetime import datetime, timezone

                from config import EXECUTION_BOOK_MAX_AGE_SEC

                snapshot_payload = execution_snapshot["snapshot"]
                snapshot_venue = str(
                    snapshot_payload["instrument"]["venue"]
                ).lower()
                snapshot_market = str(
                    snapshot_payload["instrument"]["market"]
                ).lower()
                snapshot_symbol = str(
                    snapshot_payload["instrument"]["canonical_symbol"]
                ).upper()
                snapshot_source = str(snapshot_payload["source"]).lower()
                received_at = datetime.fromisoformat(
                    str(snapshot_payload["received_at"]).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                snapshot_quantity = float(execution_snapshot["filled_quantity"])
                expected_venue = self._provenance_venue(exchange.name)
                expected_market = (
                    "perpetual" if market_type == "futures" else "spot"
                )
                if execution_snapshot.get("allowed") is not True:
                    raise ValueError("snapshot was not execution-approved")
                if snapshot_venue != expected_venue:
                    raise ValueError("snapshot venue does not match adapter")
                if snapshot_market != expected_market:
                    raise ValueError("snapshot market does not match order")
                expected_symbol = str(symbol).upper()
                if market_type == "futures" and ":" not in expected_symbol:
                    expected_symbol = f"{expected_symbol}:USDT"
                if snapshot_symbol != expected_symbol:
                    raise ValueError("snapshot symbol does not match order")
                if not snapshot_source.startswith(f"{expected_venue}."):
                    raise ValueError("snapshot source does not match adapter")
                age = (datetime.now(timezone.utc) - received_at).total_seconds()
                if age < -2 or age > float(EXECUTION_BOOK_MAX_AGE_SEC):
                    raise ValueError("execution snapshot is stale or future-dated")
                if snapshot_quantity + 1e-12 < float(size):
                    raise ValueError("snapshot depth is below order quantity")
            except (KeyError, TypeError, ValueError) as exc:
                self.last_open_reject = "execution_snapshot_invalid"
                logger.warning(f"[Orders] {symbol}: execution snapshot denied: {exc}")
                return None

        if self.blacklist.is_blacklisted(symbol):
            logger.warning(f"[Orders] {symbol} blacklisted — skipped.")
            self.last_open_reject = "blacklisted"
            return None

        # Pending maker intents are exposure reservations, not free slots.
        # During resolution the current intent remains in the pending map to
        # prevent a concurrent candidate stealing its headroom, so exclude
        # only that one reservation from this fill-time recheck.
        _reservation_key = (
            _maker_first_ctx.get("reservation_key")
            if isinstance(_maker_first_ctx, dict)
            else None
        )
        _reserved_count = self._pending_maker_count(exclude_key=_reservation_key)
        if not self.risk.can_trade(self.tracker.count_open() + _reserved_count):
            self.last_open_reject = "risk_can_trade_block"
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
            self.last_open_reject = "kelly_block"
            return None

        if size <= 0:
            logger.warning(f"[Orders] Invalid size {size} for {symbol}")
            self.last_open_reject = "invalid_size"
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
            self.last_open_reject = "below_min_notional"
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
                self.last_open_reject = "size_below_exchange_min"
                return None

        # Check if futures is disabled on this exchange (permission error)
        ex_name_lower = exchange.name.lower()
        if market_type == "futures" and ex_name_lower in self._futures_disabled:
            if side == "sell":
                logger.debug(f"[Orders] Futures disabled on {exchange.name}, "
                             f"SHORT not possible on spot — skipping {symbol}")
                self.last_open_reject = "futures_disabled_short"
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
                applied = exchange.set_leverage(symbol, safe_lev)
                # Defensive: any exchange override that pre-dates the
                # int-return contract returns None. Treat None as "assume
                # the requested leverage was applied" — preserves legacy
                # behavior and avoids the NoneType / int crash.
                if applied is None:
                    applied = safe_lev
                if applied == 0:
                    logger.warning(
                        f"[Orders] {symbol}: leverage setup failed entirely on "
                        f"{exchange.name}; aborting trade")
                    self.last_open_reject = "leverage_setup_failed"
                    return None
                if applied != safe_lev:
                    ratio = applied / safe_lev
                    size = size * ratio
                    logger.info(
                        f"[Orders] {symbol}: leverage clamped {safe_lev}x → "
                        f"{applied}x by exchange; size rescaled ×{ratio:.3f} "
                        f"to preserve margin (notional reduces accordingly)")
                    safe_lev = applied
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

        # MCP Brain SL/TP override REMOVED — spec §2 forbids AI from
        # widening stop losses. Deterministic ATR-based SL/TP is authoritative.

        # ── MCP Brain confidence → conservative position-size scaling ──
        # This final layer can reduce exposure, never increase it after risk checks.
        # Skipped on a maker-first finalize (_maker_first_ctx): the intent's
        # size was already adjusted at register time — never adjust twice.
        if self.mcp_brain and _maker_first_ctx is None:
            try:
                base = symbol.split("/")[0].split(":")[0]
                mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                mcp_conf = mcp_dec.get("confidence", 0)
                layers = mcp_dec.get("layers_aligned", 0)
                _mcp_mult = _mcp_confidence_size_multiplier(mcp_conf, layers)
                if _mcp_mult < 1.0:
                    size *= _mcp_mult
                    logger.info(
                        f"[MCP-SIZE] {symbol}: -20% size (conf={mcp_conf:.0%})")
            except Exception:
                pass

        # Get fill price + mid snapshot for attribution.
        # The mid is captured BEFORE applying sim slippage / smart-executor
        # crossing so attribution can decompose fill-vs-mid as `spread`.
        fill_price = price
        entry_mid = 0.0
        if not fill_price or order_type == "market":
            ticker     = exchange.fetch_ticker(symbol, market_type)
            fill_price = ticker.get("last") or ticker.get("close") or price
            entry_mid  = _mid_from_ticker(ticker)
            # Phase C fill-time freshness gate: reject on a DEFINITELY-stale
            # ticker timestamp; a MISSING timestamp is allowed (fail-open).
            _fresh_reason = self._fill_freshness_reason(ticker, exchange)
            if _fresh_reason:
                logger.warning(
                    f"[Orders] {symbol} fill rejected: stale feed ({_fresh_reason})")
                self.last_open_reject = f"stale_feed:{_fresh_reason}"
                return None
        if not fill_price:
            logger.error(f"[Orders] Cannot get price for {symbol}")
            self.last_open_reject = "no_price"
            return None
        fill_price = float(fill_price)

        # ── MAKER-FIRST PAPER ENTRIES (2026-07-10) ──
        # PAPER futures mcp/algorithmic-lane entries: instead of an immediate
        # taker fill, register a VIRTUAL post-only limit at the touch (bid for
        # buys / ask for sells). The position is NOT opened here — the monitor
        # tick resolves the intent (honest strict trade-through -> maker fill;
        # timeout -> taker fallback; runaway -> abandoned). _maker_first_ctx
        # marks a finalize call from the resolver and must never re-intercept.
        # tsmom is a hold-lane with its own exit policy — out of scope.
        # deep_breakout likewise (2026-07-11): the researched Codex config
        # fills TAKER at the next print after the 4h close — waiting at the
        # touch would adverse-select breakout entries (price is running away
        # by definition) and distort the forward test.
        if (_maker_first_ctx is None and self.dry_run
                and market_type == "futures" and order_type == "market"
                and _maker_first_cfg().get("enabled", False)
                and "tsmom" not in str(strategy or "").lower()
                and "deep_breakout" not in str(strategy or "").lower()
                and sl and tp and float(sl) > 0 and float(tp) > 0):
            self._maker_first_boot()
            _mf_key = f"{exchange.name}:{symbol}"
            if _mf_key in self._pending_maker:
                self.last_open_reject = "maker_first_pending"
                return None
            _mf_bid = _safe_ticker_px(ticker, "bid")
            _mf_ask = _safe_ticker_px(ticker, "ask")
            # 2026-07-11: ccxt binanceusdm futures tickers carry bid/ask=None,
            # which made the silent fall-through below fire on EVERY entry —
            # the feature was a no-op on day one. Pull the book top instead.
            if not (_mf_bid > 0 and _mf_ask > 0):
                try:
                    _mf_ob = exchange.fetch_order_book(symbol, 5, market_type)
                    if _mf_ob.get("bids") and _mf_ob.get("asks"):
                        _mf_bid = float(_mf_ob["bids"][0][0] or 0)
                        _mf_ask = float(_mf_ob["asks"][0][0] or 0)
                except Exception as _mfe:
                    logger.debug(f"[MakerFirst] book fetch failed: {_mfe}")
            _mf_ref = float(price) if (price and float(price) > 0) else fill_price
            if _mf_bid > 0 and _mf_ask > 0 and _mf_ref > 0:
                _mf_limit = _mf_bid if side == "buy" else _mf_ask
                _mf_intent = {
                    "exchange": exchange.name, "symbol": symbol, "side": side,
                    "market_type": market_type, "strategy": strategy,
                    "size": float(size), "leverage": int(leverage),
                    "limit_px": float(_mf_limit),
                    # signal_px: the market print at intent time — the
                    # adverse-selection baseline for the 2-week soak.
                    "signal_px": float(fill_price),
                    "sl_pct": abs(_mf_ref - float(sl)) / _mf_ref,
                    "tp_pct": abs(float(tp) - _mf_ref) / _mf_ref,
                    "candidate_id": candidate_id, "mcp_score": mcp_score,
                    "model_version": model_version, "decision_id": decision_id,
                    "execution_snapshot": execution_snapshot,
                    "created_ts": time.time(),
                }
                _mf_intent.update({
                    "strategy_id": authorization_strategy_id or strategy,
                    "parent_decision_id": str(
                        decision_id or f"direct-{uuid.uuid4()}"
                    ),
                    "resolution_decision_id": f"maker-resolution-{uuid.uuid4()}",
                    "decision_confidence": decision_confidence,
                })
                _mf_registered, _mf_reason = self._register_pending_maker(
                    exchange, _mf_key, _mf_intent
                )
                if not _mf_registered:
                    self.last_open_reject = _mf_reason
                    return None
                logger.info(
                    f"[MakerFirst] {symbol} {side.upper()}: virtual post-only "
                    f"limit @ {_mf_limit:.6g} (last={fill_price:.6g}) — "
                    f"awaiting strict trade-through, timeout "
                    f"{_maker_first_cfg().get('timeout_sec', 45)}s")
                self.last_open_reject = "maker_first_pending"
                return None
            # No usable book — a maker price cannot be justified honestly;
            # fall through to the normal taker fill. LOUD: this silent branch
            # hid the bid/ask=None no-op for a whole session (2026-07-11).
            logger.warning(
                f"[MakerFirst] {symbol}: no usable book "
                f"(bid={_mf_bid} ask={_mf_ask}) — falling through to taker")

        # ── DRY_RUN realism: apply slippage + spread (2026-04-11) ──
        # In LIVE, SmartExecutor crosses the book and pays real slippage.
        # In DRY_RUN, paper used midpoint-ish ticker.last → systematically
        # better than any real fill. Apply directional spread + slippage so
        # paper pays what LIVE pays. See core/sim_execution.py for rationale.
        # Maker-first MAKER fills skip this: the fill IS the resting limit
        # price exactly (no slippage on a passive fill); the taker_fallback
        # leg still pays full slippage like any market order.
        if self.dry_run and (_maker_first_ctx is None
                             or _maker_first_ctx.get("fill_type") != "maker"):
            sim_fill = self.sim.paper_fill_price(
                exchange, symbol, side, market_type,
                base_price=fill_price, phase="open", size=size,
                execution_snapshot=execution_snapshot)
            if sim_fill > 0 and sim_fill != fill_price:
                logger.debug(
                    f"[SimExec] {symbol} {side} OPEN slip: "
                    f"{fill_price:.6g} → {sim_fill:.6g}")
                fill_price = sim_fill

        # ── Maker-first finalize: SL/TP off the ACTUAL fill price ──
        # Both the maker and taker_fallback legs re-derive SL/TP from the
        # original signal percentages against the realized fill, so the
        # ACCURACY band geometry (tp-dist / sl-dist ratio) stays exact.
        if _maker_first_ctx is not None:
            if (_maker_first_ctx.get("fill_type") == "maker"
                    and float(_maker_first_ctx.get("fill_px") or 0) > 0):
                # A maker fill happens AT the resting limit price — exactly.
                fill_price = float(_maker_first_ctx["fill_px"])
            _mf_slp = float(_maker_first_ctx.get("sl_pct") or 0.0)
            _mf_tpp = float(_maker_first_ctx.get("tp_pct") or 0.0)
            if _mf_slp > 0 and _mf_tpp > 0 and fill_price > 0:
                if side == "buy":
                    sl = fill_price * (1.0 - _mf_slp)
                    tp = fill_price * (1.0 + _mf_tpp)
                else:
                    sl = fill_price * (1.0 + _mf_slp)
                    tp = fill_price * (1.0 - _mf_tpp)

        # ── Price Band Sanity Check ──
        if not self._check_price_band(symbol, side, fill_price, exchange, market_type):
            self.last_open_reject = "price_band_failed"
            return None

        # Round quantity to exchange precision (Bitget: 0.1)
        try:
            rounded_size = exchange.round_quantity(symbol, size, market_type)
            if rounded_size != size:
                notional_check = rounded_size * fill_price
                if notional_check < 5.0:
                    logger.warning(
                        f"[Orders] {symbol}: rounded qty {rounded_size} "
                        f"(${notional_check:.2f}) too small after rounding")
                    self.last_open_reject = "rounded_size_too_small"
                    return None
                logger.debug(
                    f"[Orders] {symbol}: qty {size:.8f} -> {rounded_size:.8f} "
                    f"(exchange precision)")
                size = rounded_size
        except Exception:
            pass

        # A pending maker may have rested while other portfolio exposure
        # changed. Re-run both hard portfolio rails at the realized fill and
        # rounded quantity, excluding only this intent's own reservation.
        if self.dry_run and _maker_first_ctx is not None:
            _maker_risk_reject = self._maker_fill_risk_rejection(
                reservation_key=_maker_first_ctx.get("reservation_key"),
                size=size,
                fill_price=fill_price,
                stop_loss=sl,
            )
            if _maker_risk_reject:
                self.last_open_reject = _maker_risk_reject
                return None

        # Build Position first so __post_init__ calculates entry_fee
        order_id = f"DRY-{uuid.uuid4().hex[:8]}" if self.dry_run else None
        # Phase 18 (2026-05-04): persist mcp_score / 100 as `confidence`
        # so calibrator.record() at close has data to learn from. Was the
        # second leg of the dead-code pattern (first: Phase 17 adaptive
        # sizing wired into a never-called method; second: calibrator
        # never received predicted_conf because positions.json never
        # stored it).
        _conf_in = mcp_score / 100.0 if mcp_score and mcp_score > 0 else 0.0
        pos = Position(
            id=order_id or uuid.uuid4().hex,
            exchange=exchange.name, symbol=symbol, side=side,
            market_type=market_type, strategy=strategy,
            entry_price=fill_price, size=size,
            stop_loss=sl, take_profit=tp,
            leverage=leverage, order_id=order_id,
            entry_mid=entry_mid,
            confidence=_conf_in,
        )
        # AccBand stamp at construction (2026-07-29): maker-first finalize
        # never returns through bot_engine's post-open stamp, so inverted
        # geometry must be marked here BEFORE tracker.add persists the row.
        try:
            from config import ACCURACY_TARGET_MODE as _acc_stamp
            if (
                _acc_stamp.get("enabled")
                and fill_price > 0
                and float(tp or 0) > 0
                and float(sl or 0) > 0
            ):
                _sl_f = abs(float(fill_price) - float(sl)) / float(fill_price)
                _tp_f = abs(float(tp) - float(fill_price)) / float(fill_price)
                if 0.0 < _tp_f < _sl_f <= 0.20:
                    pos._accuracy_band = True
        except Exception:
            pass

        # Execution fill type ('maker'|'taker'|'maker_partial'); None in dry-run
        # (no real fill) and tagged by the executor on the live path below.
        _fill_type: str | None = None

        # Maker-first finalize: tag the fill type for warehouse attribution
        # and, on a MAKER fill, book the venue MAKER fee (the point of the
        # feature) via the venue+fill-aware fee plumbing — instead of the
        # generic taker rate Position.__post_init__ assumed. Must run BEFORE
        # wallet.on_open so the paper wallet is charged the maker fee too.
        if _maker_first_ctx is not None:
            _fill_type = _maker_first_ctx.get("fill_type")
            if _fill_type == "maker":
                from core.position_tracker import _fee_rate as _mf_fee_rate
                pos.entry_fee = pos.size * pos.entry_price * _mf_fee_rate(
                    market_type, exchange.name, "maker")
                pos.total_fees = pos.entry_fee + pos.exit_fee

        _provenance_intent = (
            _maker_first_ctx.get("provenance_intent")
            if isinstance(_maker_first_ctx, dict)
            else None
        )
        if isinstance(_provenance_intent, dict):
            # Pending-maker intents persist provenance as to_dict() (JSON-safe
            # across restarts). Rebuild the contract object — every downstream
            # consumer accesses .intent_id (2026-07-23 12:05Z: the raw dict
            # raised AttributeError in _append_execution_event and latched an
            # execution-incident HALT on the first maker resolution).
            _provenance_intent = self._maker_provenance_intent(_maker_first_ctx)
        if _provenance_intent is None:
            _provenance_intent = self._append_order_intent(
                exchange,
                symbol,
                market_type,
                side,
                size,
                decision_id,
                strategy,
                candidate_id,
                leverage,
                parent_decision_id=decision_parent_id,
                confidence=decision_confidence,
            )
        if _provenance_intent is False:
            return None

        if self.dry_run:
            ok = self.wallet.on_open(
                exchange.name, symbol, side,
                size, fill_price, pos.entry_fee, market_type,
                leverage=leverage,
            )
            if not ok:
                self.last_open_reject = "paper_wallet_reject"
                self._append_execution_event(
                    _provenance_intent,
                    "rejected",
                    order={"id": pos.order_id},
                    reason="paper_wallet_reject",
                )
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

                # ── Idempotency: attach client order ID ──
                client_oid = self._generate_client_order_id(
                    exchange.name, symbol, side)
                params["clientOrderId"] = client_oid
                params["newClientOrderId"] = client_oid  # Binance alias

                # ── Smart Execution: spread check + limit order + fallback ──
                spread_info = self.executor.check_spread(
                    exchange, symbol, market_type)
                if not spread_info["ok"]:
                    _spread_reason = str(
                        spread_info.get("reason") or "spread_too_wide"
                    )
                    logger.info(
                        f"[Orders] {symbol}: execution book rejected "
                        f"({spread_info['spread_pct']*100:.3f}%) — skip")
                    self.last_open_reject = _spread_reason
                    self._append_execution_event(
                        _provenance_intent,
                        "rejected",
                        reason=_spread_reason,
                    )
                    return None
                # Prefer the order-book mid over the ticker mid for LIVE —
                # check_spread fetches a fresh top-of-book snapshot.
                _book_mid = float(spread_info.get("mid") or 0)
                if _book_mid > 0:
                    pos.entry_mid = _book_mid

                notional_usd = size * fill_price
                if self.executor.should_use_twap(notional_usd):
                    # TWAP for large orders
                    results = self.executor.execute_twap(
                        exchange, symbol, side, size,
                        market_type, params)
                    order = self._aggregate_execution_results(results, size)
                else:
                    # Limit order with market fallback
                    order = self.executor.execute_limit_with_fallback(
                        exchange, symbol, side, size,
                        market_type, params)

                _outcome, _fill_sz = self._interpret_execution_result(order, size)
                if _outcome != "filled":
                    if _outcome == "uncertain":
                        logger.warning(
                            f"[Orders] {symbol}: executor UNCERTAIN (cancel+verify both "
                            f"failed) — NOT registering a position; ghost-reconciler will "
                            f"adopt any real fill next cycle. order={order}")
                    else:
                        logger.info(
                            f"[Orders] {symbol}: no fill "
                            f"({(order or {}).get('status') or 'empty'}) — no position opened")
                    self.last_open_reject = "no_fill"
                    self._append_execution_event(
                        _provenance_intent,
                        "error" if _outcome == "uncertain" else "cancelled",
                        order=order,
                        reason=(
                            "unknown_submission_outcome"
                            if _outcome == "uncertain"
                            else "entry_not_filled"
                        ),
                    )
                    if _outcome == "uncertain":
                        try:
                            self.risk.latch_incident(
                                f"unknown entry submission outcome: "
                                f"{exchange.name}:{symbol}",
                                category="execution",
                            )
                        except Exception:
                            pass
                    return None
                if 0 < _fill_sz < size:
                    logger.warning(
                        f"[Orders] {symbol}: partial fill {_fill_sz}/{size} — sizing "
                        f"position + SL/TP to the actual fill.")
                    size = _fill_sz
                    pos.size = _fill_sz
                pos.order_id   = order.get("id")
                pos.id         = pos.order_id or pos.id
                _fill_type     = order.get("_fill_type")
                fill_price     = order.get("average") or order.get("price") or fill_price
                pos.entry_price = float(fill_price)
                # Recalculate entry_fee based on actual fill price.
                # 2026-06-11: venue+fill-aware — LIVE maker fills were
                # previously booked at Binance taker rate.
                from core.position_tracker import _fee_rate
                pos.entry_fee = pos.size * pos.entry_price * _fee_rate(
                    pos.market_type, pos.exchange,
                    "maker" if _fill_type in ("maker", "maker_partial")
                    else "taker")
                pos.total_fees = pos.entry_fee + pos.exit_fee

                # ── Post-order verification ──
                verified = self._verify_order_on_exchange(
                    exchange, pos.order_id, symbol, market_type)
                if verified:
                    actual_fill = verified.get("average") or verified.get("price")
                    if actual_fill:
                        pos.entry_price = float(actual_fill)
                        fill_price = pos.entry_price

                logger.info(
                    f"[Orders] LIVE ORDER: {side.upper()} {size:.6f} {symbol} "
                    f"@ {fill_price:.4f} | "
                    f"entry_fee={pos.entry_fee:.4f} USDT | "
                    f"id={pos.id} | cid={client_oid[:12]}.. | {strategy}"
                )
            except Exception as e:
                # Skip pair errors (e.g., Binance TradFi agreement for XAU/XAG)
                if _is_skip_pair_error(e):
                    logger.warning(
                        f"[Orders] {symbol} skipped on {exchange.name}: "
                        f"{str(e)[:100]}")
                    # Auto-blacklist to prevent repeated warnings each cycle
                    self.blacklist.add(symbol, reason=f"skip_pair:{str(e)[:60]}")
                    self.last_open_reject = "skip_pair_error"
                    self._append_execution_event(
                        _provenance_intent,
                        "rejected",
                        reason="skip_pair_error",
                    )
                    return None
                # Handle position mode mismatch — retry without positionSide
                if _is_position_mode_error(e) and market_type == "futures":
                    self._oneway_mode.add(ex_name_lower)
                    self._save_order_mode_state()
                    logger.info(
                        f"[Orders] {exchange.name} is in ONE-WAY mode. "
                        f"Retrying without positionSide...")
                    try:
                        order = self.executor.execute_limit_with_fallback(
                            exchange, symbol, side, size,
                            market_type, {},
                        )
                        _o2, _f2 = self._interpret_execution_result(order, size)
                        if _o2 != "filled":
                            logger.info(
                                f"[Orders] {symbol}: one-way retry no fill "
                                f"({(order or {}).get('status') or 'empty'}) — skipping")
                            self.last_open_reject = "no_fill_oneway_retry"
                            self._append_execution_event(
                                _provenance_intent,
                                "cancelled",
                                order=order,
                                reason="no_fill_oneway_retry",
                            )
                            return None
                        if 0 < _f2 < size:
                            logger.warning(
                                f"[Orders] {symbol}: one-way partial fill {_f2}/{size} "
                                f"— sizing position + SL/TP to the actual fill.")
                            size = _f2
                            pos.size = _f2
                        pos.order_id    = order.get("id")
                        pos.id          = pos.order_id or pos.id
                        fill_price      = order.get("average") or order.get("price") or fill_price
                        pos.entry_price = float(fill_price)
                        # Recalculate entry_fee based on actual fill price
                        from core.position_tracker import _fee_rate as _fr
                        pos.entry_fee = pos.size * pos.entry_price * _fr(pos.market_type)
                        pos.total_fees = pos.entry_fee + pos.exit_fee
                        logger.info(
                            f"[Orders] LIVE ORDER (one-way): {side.upper()} {size:.6f} {symbol} "
                            f"@ {fill_price:.4f} | id={pos.id} | {strategy}")
                    except Exception as e2:
                        logger.error(f"[Orders] Order failed (one-way retry): {e2}")
                        self.last_open_reject = "order_failed_oneway_retry"
                        self._append_execution_event(
                            _provenance_intent,
                            "error",
                            reason="order_failed_oneway_retry",
                            context={"error": str(e2)[:500]},
                        )
                        return None
                elif _is_permission_error(e) and market_type == "futures":
                    self._futures_disabled.add(ex_name_lower)
                    self._save_order_mode_state()
                    logger.warning(
                        f"[Orders] Futures PERMISSION DENIED on {exchange.name} "
                        f"— falling back to SPOT for buy signals.")
                    if side == "buy":
                        spot_symbol = symbol.replace(":USDT", "")
                        self._append_execution_event(
                            _provenance_intent,
                            "rejected",
                            reason="futures_permission_spot_fallback",
                        )
                        # E-7: forward ALL provenance kwargs — this retry
                        # previously dropped candidate_id/mcp_score/
                        # model_version (pre-existing data loss).
                        return self.open_position(
                            exchange, spot_symbol, "buy", "spot",
                            strategy, size, sl, tp, leverage=1,
                            candidate_id=candidate_id, mcp_score=mcp_score,
                            model_version=model_version,
                            decision_id=decision_id,
                            execution_snapshot=execution_snapshot)
                    self.last_open_reject = "futures_permission_denied"
                    self._append_execution_event(
                        _provenance_intent,
                        "rejected",
                        reason="futures_permission_denied",
                    )
                    return None
                else:
                    logger.error(f"[Orders] Order failed on {exchange.name}: {e}")
                    self.notifier.error(
                        f"Order FAILED: {symbol} {side.upper()}\n{str(e)[:200]}")
                    self.last_open_reject = "order_failed"
                    self._append_execution_event(
                        _provenance_intent,
                        "error",
                        reason="order_failed",
                        context={"error": str(e)[:500]},
                    )
                    return None

        # Maker-first is resolved outside BotEngine, so BotEngine cannot write
        # the later FILLED terminal decision.  Persist the child resolution
        # immediately after the paper wallet/executor has accepted the fill and
        # before the position/trade row is registered.  The parent candidate
        # remains immutable DEFERRED; the trade points at this child decision.
        if _maker_first_ctx is not None:
            _maker_first_ctx["terminal_recorded"] = (
                self._record_maker_resolution_decision(
                    _maker_first_ctx.get("maker_intent") or {},
                    outcome="filled",
                    reason=f"maker_first_{_fill_type or 'taker'}_fill",
                    execution_snapshot=execution_snapshot,
                    filled_position=pos,
                )
            )

        if self.dry_run:
            self._append_execution_event(
                _provenance_intent,
                "filled",
                order={"id": pos.order_id},
                quantity=float(pos.size),
                price=float(pos.entry_price),
                context={"paper": True, "fill_type": _fill_type or "taker"},
            )
        else:
            requested_quantity = (
                float(_provenance_intent.quantity)
                if _provenance_intent not in (None, False)
                else float(pos.size)
            )
            if float(pos.size) + 1e-12 < requested_quantity:
                self._append_execution_event(
                    _provenance_intent,
                    "partially_filled",
                    order=order,
                    quantity=float(pos.size),
                    price=float(pos.entry_price),
                    context={"requested_quantity": requested_quantity},
                )
                self._append_execution_event(
                    _provenance_intent,
                    "cancelled",
                    order=order,
                    reason="unfilled_remainder_cancelled",
                    context={
                        "unfilled_quantity": requested_quantity - float(pos.size)
                    },
                )
            else:
                self._append_execution_event(
                    _provenance_intent,
                    "filled",
                    order=order,
                    quantity=float(pos.size),
                    price=float(pos.entry_price),
                    context={"fill_type": _fill_type or "taker"},
                )

        # Tag scalp positions for downstream routing (time wall, stale close,
        # trailing skip). Derived from TP distance vs entry; no caller changes
        # needed. Also stores tp_pct so the time-wall fallback check can match.
        try:
            from config import SCALP_MODE as _SM_tag
            if _SM_tag.get("enabled", False) and fill_price > 0 and tp > 0:
                _tp_dist_pct = abs(tp - fill_price) / fill_price * 100.0
                _scalp_tp_pct = _SM_tag.get("tp_pct", 1.8)
                if abs(_tp_dist_pct - _scalp_tp_pct) < 0.5:
                    pos._scalp = True
                    pos.tp_pct = round(_tp_dist_pct, 3)
        except Exception:
            pass
        finally:
            # Attach/save routing metadata atomically with the position.
            self.tracker.add(pos)

        # Daily open counter — feeds RiskManager.can_trade()'s per-day cap.
        # Wired here (and not earlier) so failed orders don't burn the
        # day's quota.
        try:
            self.risk.note_trade_opened()
        except Exception as _ne:
            logger.debug(f"[Risk] note_trade_opened skipped: {_ne}")

        # Warehouse trade-open row (spec §4, §6) — MUST run BEFORE
        # _place_exchange_sl_tp so the fail-closed path has a row to patch
        # via record_trade_close → trade_id_by_key. Previously this write
        # lived in bot_engine._execute_open AFTER open_position returned,
        # so every SL-placement failure silently lost its trade row.
        try:
            from config import OPERATING_MODE as _mode
            from core.warehouse import get_warehouse
            get_warehouse().record_trade_open(
                exchange=exchange.name.lower(),
                symbol=symbol,
                side=side,
                ts_entry=float(getattr(pos, "open_time", time.time())),
                entry_px=float(getattr(pos, "entry_price", fill_price)),
                size=float(getattr(pos, "size", size)),
                leverage=int(leverage),
                candidate_id=candidate_id if (candidate_id or 0) > 0 else None,
                market_type=market_type,
                strategy_family=str(strategy) if strategy else "unknown",
                fee=float(getattr(pos, "entry_fee", 0.0)),
                mode=_mode,
                mcp_score=float(mcp_score) if mcp_score is not None else None,
                model_version=str(model_version) if model_version else None,
                fill_type=_fill_type,
                decision_id=str(decision_id) if decision_id else None,
            )
        except Exception as _we:
            # 2026-05-25 — Bug 3 fix: was logger.debug, so a transient
            # SQLite lock / disk error here produced a stuck-OPEN warehouse
            # row with ZERO operator visibility (the close path logs the
            # resulting trade_id MISS at warning, but you couldn't correlate
            # it without debug logging). Promote to warning for symmetry.
            logger.warning(
                f"[Warehouse] record_trade_open FAILED "
                f"(row will be untrackable at close): {_we}")

        # C6 (2026-07-08): entry-execution audit row — fill price/type were
        # previously only on the trades row; the FILL event pins the
        # execution detail into the lifecycle chain.
        self._record_lifecycle(pos, "FILL", {
            "price": float(fill_price) if fill_price else None,
            "size": float(pos.size), "fill_type": _fill_type})

        # Place SL/TP orders on the exchange for real protection.
        # 2026-04-16 (post-audit): use pos.size (the tracker's canonical size,
        # which reflects any rounding applied at entry), not the pre-round
        # `size` variable. Otherwise SL/TP are placed for the wrong quantity
        # and the exchange rejects them on partial fills.
        if not self.dry_run and market_type == "futures":
            # C5: pass the just-verified fill price so the crossed-SL
            # pre-flight skips its ticker round-trip (fill->protection gap).
            self._place_exchange_sl_tp(exchange, pos, sl, tp, side, symbol,
                                       pos.size, market_type,
                                       mark_hint=fill_price)

        self.compliance.log_trade(
            exchange.name, symbol, side, size, fill_price,
            strategy, order_id=pos.id, reason="entry"
        )
        # Gather rich context for email notification
        _bal = 0.0
        _open_n = None
        _exposure = None
        try:
            if self.dry_run:
                _bal = float(self.wallet.total_balance() or 0.0)
            _open_n = self.tracker.count_open()
            _exposure = sum(
                float(p.size) * float(p.entry_price)
                for p in self.tracker.get_open()
            )
        except Exception:
            pass
        _risk_usd = None
        _rr = None
        try:
            if side == "buy":
                sl_pct = (float(fill_price) - float(sl)) / float(fill_price)
                tp_pct = (float(tp) - float(fill_price)) / float(fill_price)
            else:
                sl_pct = (float(sl) - float(fill_price)) / float(fill_price)
                tp_pct = (float(fill_price) - float(tp)) / float(fill_price)
            _risk_usd = float(size) * float(fill_price) * sl_pct
            _rr = (tp_pct / sl_pct) if sl_pct > 0 else None
        except Exception:
            pass
        # Notifier wrapped: any rendering / SMTP failure here must NOT lose
        # the open position. The position is already created + persisted at
        # this point; the notifier is purely informational.
        try:
            self.notifier.trade_opened(
                exchange.name, symbol, side, fill_price,
                size, sl, tp, strategy, market_type,
                leverage=leverage,
                rr_ratio=_rr,
                risk_usd=_risk_usd,
                balance=_bal if _bal > 0 else None,
                open_positions=_open_n,
                total_exposure=_exposure,
            )
        except Exception as _ne:
            logger.warning(f"[Orders] trade_opened notifier failed: {_ne}")
        return pos

