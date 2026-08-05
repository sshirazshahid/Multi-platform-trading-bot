"""
core/order_mgmt/closing.py — OrderManager _ClosingMixin mixin (Phase D4).
"""
import time

from loguru import logger

from config import RISK
from core.order_mgmt.helpers import (
    _is_accuracy_band_position,
    _is_position_mode_error,
    _mid_from_ticker,
)
from core.position_tracker import Position
from exchanges.base import BaseExchange

class _ClosingMixin:
    def close_position(self, exchange: BaseExchange, position: Position,
                       reason: str, price: float = None,
                       order_type: str = "market",
                       decision_id: str | None = None):
        """B7-P2 per-position serialization wrapper (default-ON via
        PER_POSITION_LOCK_ENABLED). When ON, serializes close + SL-mutation on
        the same position id and no-ops the race loser via tracker.is_position_open.
        Timeout-acquire (5s < the 10s monitor cadence) and fail closed on
        timeout so close/SL mutation never proceeds without serialization."""
        if not getattr(self, "_per_pos_lock_enabled", False):
            return self._close_position_impl(
                exchange, position, reason, price, order_type, decision_id)
        lk = self._position_lock(position.id)
        acquired = lk.acquire(timeout=self._pos_lock_timeout)
        if not acquired:
            logger.error(
                f"[Lock] close_position _position_lock TIMEOUT for "
                f"{position.id} after 5s — refusing unsynchronized close")
            return None
        try:
            if not self.tracker.is_position_open(position.id):
                logger.info(
                    f"[Orders] close race no-op: {position.id[:8]} already closed")
                return None
            return self._close_position_impl(
                exchange, position, reason, price, order_type, decision_id)
        finally:
            if acquired:
                lk.release()

    def _close_position_impl(self, exchange: BaseExchange, position: Position,
                       reason: str, price: float = None,
                       order_type: str = "market",
                       decision_id: str | None = None):

        # Provenance: stash the CLOSE decision's id on the position so
        # _finalize_close (fired via PositionTracker.on_close on EVERY close
        # path) can thread it into record_trade_close(exit_decision_id=...).
        # Deterministic exits (SL/TP/trailing) pass nothing → NULL (plan 0E).
        if decision_id:
            position._exit_decision_id = decision_id

        close_side = "sell" if position.side == "buy" else "buy"

        # Capture exit mid for attribution BEFORE any sim slippage runs.
        # If the caller passed `price`, fetch the ticker just for mid.
        exit_mid = 0.0
        ticker = None
        if not price:
            ticker = exchange.fetch_ticker(position.symbol, position.market_type)
            price  = ticker.get("last") or ticker.get("close")
        else:
            try:
                ticker = exchange.fetch_ticker(position.symbol, position.market_type)
            except Exception:
                ticker = None
        if ticker:
            exit_mid = _mid_from_ticker(ticker)
        if not price:
            logger.error(f"[Orders] Cannot get close price for {position.symbol}")
            return None
        price = float(price)
        if exit_mid > 0:
            position.exit_mid = exit_mid

        # ── DRY_RUN realism: apply slippage on close (2026-04-11) ──
        # The caller's `price` is either a ticker.last (monitor poll) or an
        # SL/TP trigger level. Either way, in LIVE a market close eats the
        # spread in the opposite direction plus slippage. Stop-losses get
        # the wider `pct_stop_loss` because market orders fill worse during
        # the fast moves that trigger SLs.
        _is_paper = position.paper_trade if hasattr(position, 'paper_trade') else self.dry_run
        if _is_paper and order_type != "limit":
            phase = (
                "stop"
                if reason in ("stop_loss", "trailing_stop", "liquidation")
                else "close"
            )
            sim_px = self.sim.paper_fill_price(
                exchange, position.symbol, close_side,
                position.market_type, base_price=price, phase=phase,
                size=position.size)
            if sim_px > 0:
                if phase == "stop":
                    # 2026-07-07: paper_fill_price re-prices at the CURRENT
                    # book — on a dip-and-recover wick that booked the
                    # recovered price instead of the trigger a live stop-market
                    # would have filled at (30d avg: fills 47-85bps BETTER than
                    # the stop). A stop fill must never beat its trigger level.
                    sim_px = self._cap_stop_fill(sim_px, price, close_side)
                if sim_px != price:
                    logger.debug(
                        f"[SimExec] {position.symbol} CLOSE ({reason}) slip: "
                        f"{price:.6g} → {sim_px:.6g}")
                    price = sim_px

        # Route based on the POSITION's origin, not current mode.
        # A paper_trade position was never placed on the exchange — sending
        # a reduce-only close would fail with "position is zero".
        if _is_paper:
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
                close_order = exchange.create_order(
                    position.symbol, order_type, close_side,
                    position.size,
                    price if order_type == "limit" else None,
                    params=params, market_type=position.market_type)
                if not self._close_order_confirmed(close_order):
                    raise RuntimeError(f"close order not confirmed: {close_order}")
                order_placed = True
                # Use actual fill price if available
                fill = close_order.get("average") or close_order.get("price")
                if fill:
                    price = float(fill)
            except Exception as e:
                err = str(e)
                # Already closed/liquidated on exchange — mark as closed in tracker
                _already_closed = (
                    "No position" in err or "22002" in err
                    or "110017" in err
                    or "position is zero" in err.lower()
                    or "position does not exist" in err.lower()
                    or "order not found" in err.lower()
                )
                if _already_closed:
                    # A1 (2026-07-16): the error string is a TRIGGER TO VERIFY,
                    # never proof. "order not found" is an ORDER-level error that
                    # also fires while the POSITION is still open; the other
                    # phrases are substring matches that can false-positive.
                    # Untracking a still-open position leaves it NAKED (no SL, no
                    # monitor) with phantom PnL — the worst state. Only untrack
                    # when fetch_positions POSITIVELY confirms flat; on still-open
                    # OR fetch failure leave order_placed=False so the fail-closed
                    # path below keeps it OPEN + tracked for reconcile. (A
                    # falsely-KEPT closed position reconciles away harmlessly via
                    # ghost-sync; a falsely-UNTRACKED open one does not.)
                    # Fail-direction: only a POSITIVE True untracks. False
                    # (still open) and None (unverifiable) both keep it tracked.
                    if self._position_flat_on_venue(
                            exchange, position.symbol) is True:
                        logger.info(
                            f"[Orders] {position.symbol} confirmed flat on exchange "
                            f"— syncing tracker")
                        order_placed = True  # Proceed to close in tracker
                    else:
                        logger.error(
                            f"[Orders] close error resembled already-closed "
                            f"({err[:90]}) but {position.symbol} is STILL OPEN or "
                            f"unverifiable — NOT untracking; keeping protected.")
                # "reduceonly not required" — Binance One-Way mode
                elif "reduceonly" in err.lower() or "-1106" in err:
                    self._oneway_mode.add(ex_lower)
                    self._save_order_mode_state()
                    # 2026-04-20: Before retrying without reduceOnly, verify the
                    # position still exists on the exchange. Without this guard
                    # the retry opens a NAKED REVERSE position when the target
                    # is already closed (e.g. TP/SL fired, stale list caller).
                    # Root cause of the ALGO naked-short cascade on 2026-04-20.
                    # Fail-direction (opposite of the untrack path above): only a
                    # POSITIVE False (still open) justifies the retry. True and
                    # None (unverifiable) both skip it — never retry into an
                    # unknown venue state, that is how the naked reverse happens.
                    _still_open = self._position_flat_on_venue(
                        exchange, position.symbol) is False
                    if not _still_open:
                        logger.info(
                            f"[Orders] {position.symbol} already flat on exchange "
                            f"— skipping naked-reverse retry (fail-closed guard)")
                        order_placed = True
                    else:
                        logger.info(f"[Orders] {exchange.name} One-Way mode — retrying close without reduceOnly")
                        try:
                            retry_order = exchange.create_order(
                                position.symbol, order_type, close_side,
                                position.size,
                                price if order_type == "limit" else None,
                                params={}, market_type=position.market_type)
                            if not self._close_order_confirmed(retry_order):
                                raise RuntimeError(
                                    f"retry close order not confirmed: {retry_order}")
                            order_placed = True
                        except Exception as e2:
                            err2 = str(e2).lower()
                            if "no position" in err2 or "22002" in err2 or "does not exist" in err2 \
                                    or "110017" in err2 or "position is zero" in err2:
                                order_placed = True
                            else:
                                logger.error(f"[Orders] Close failed (retry): {e2}")
                # Position mode mismatch (Bitget 40773/40774 / Binance positionSide)
                elif (_is_position_mode_error(e) or "unilateral" in err.lower()) \
                        and position.market_type == "futures":
                    self._oneway_mode.add(ex_lower)
                    self._save_order_mode_state()
                    # One-way mode: reduceOnly=True tells the exchange this is a close
                    _ow_close = {"reduceOnly": True}
                    try:
                        retry_order = exchange.create_order(
                            position.symbol, order_type, close_side,
                            position.size, None,
                            params=_ow_close, market_type=position.market_type)
                        if not self._close_order_confirmed(retry_order):
                            raise RuntimeError(
                                f"one-way close order not confirmed: {retry_order}")
                        order_placed = True
                    except Exception as e2:
                        err2 = str(e2).lower()
                        if "no position" in err2 or "22002" in err2 or "does not exist" in err2 \
                                or "110017" in err2 or "position is zero" in err2:
                            order_placed = True
                        else:
                            logger.error(f"[Orders] Close failed (one-way): {e2}")
                else:
                    logger.error(f"[Orders] Close failed for {position.id}: {e}")

            if not order_placed:
                # Track close failures. Do NOT local-close after repeated
                # failures: if the venue close is unconfirmed, hiding the
                # position locally is more dangerous than keeping it visible.
                fc = self._close_fail_count.get(position.id, 0) + 1
                self._close_fail_count[position.id] = fc
                self._save_close_fail_count()
                if fc >= 3:
                    logger.critical(
                        f"[Orders] CLOSE FAILED {fc}x for {position.symbol} "
                        f"{position.side.upper()} id={position.id}: keeping "
                        f"tracker OPEN until exchange close is confirmed. "
                        f"Manual reconciliation required.")
                return None

        # Success — clear any failure counter
        if position.id in self._close_fail_count:
            self._close_fail_count.pop(position.id, None)
            self._save_close_fail_count()

        # 2026-05-02 fix (orphan stop-order accumulation):
        # When a position closes, the OTHER conditional order (TP if SL
        # hit, SL if TP hit, both if market close) becomes an orphan.
        # Pre-fix the close path never cancelled these. Over 5 days, 24
        # orphans accumulated on Bybit, hitting the per-symbol stop-order
        # limit and triggering fail-closed cascades on new entries.
        # Now we cancel all open orders for the symbol after close.
        # cancel_all_orders is best-effort — failure here doesn't break
        # the close. Bybit-specific path handles its conditional ledger
        # via the override in bybit_client.cancel_all_orders.
        if not _is_paper:
            try:
                exchange.cancel_all_orders(position.symbol, position.market_type)
            except Exception as e:
                logger.debug(
                    f"[Orders] post-close cancel_all_orders {position.symbol}: "
                    f"{str(e)[:120]}")

        # tracker.close() now invokes self._finalize_close via its on_close
        # hook (wired in BotEngine.__init__). All post-close work — wallet,
        # risk, Spec §12 streaks, compliance, blacklist, trailing cleanup,
        # warehouse update, post-mortem, notifier — happens there so ghost-
        # closed positions get the same treatment as normal exits.
        closed = self.tracker.close(position.id, price, reason)
        if not closed:
            return None
        return closed

    @property
    def calibrator(self):
        """Lazy-load ProbabilityCalibrator singleton (Phase 18, 2026-05-04).

        Used by _finalize_close to feed (predicted_conf, actual_win) outcomes
        so the isotonic calibration can fit on real data. Was previously
        instantiated only in LearningEngine but never received valid input
        because positions.json never persisted `confidence`.
        """
        cached = getattr(self, "_calibrator_instance", None)
        if cached is not None:
            return cached
        try:
            from core.probability_calibrator import ProbabilityCalibrator
            self._calibrator_instance = ProbabilityCalibrator()
        except Exception:
            self._calibrator_instance = None
        return self._calibrator_instance

    def _finalize_close(self, pos: Position, price: float, reason: str) -> None:
        """Post-close hooks fired by PositionTracker.on_close after EVERY
        close (normal exit, ghost-sync, ghost-reconciled, ghost_force_close,
        STALE, AGE_LIMIT, fail-closed). Centralising here is the fix for the
        2026-04-26 finding that ghost-closed positions were silently bypassing
        warehouse, daily_pnl, Spec §12 streak counters, blacklist, trailing
        cleanup, post-mortem and notifier — meaning a -$0.64 ghost loss
        never reached any safety rail.

        ``pos`` is the closed Position (entry fields preserved, exit fields
        populated). ``price`` is the actual exit price the tracker recorded.
        """
        # B7-P2: evict this id's per-position lock to bound dict growth. Runs
        # here (via tracker.on_close) AFTER tracker.close has already popped the
        # id from _open, so any waiter that re-creates a fresh lock for this id
        # will find is_position_open()==False and no-op. Guard the dict op only;
        # never pop inside close_position's own locked region.
        if getattr(self, "_per_pos_lock_enabled", False):
            with self._pos_locks_guard:
                self._pos_locks.pop(pos.id, None)
        # Phase 29 (2026-05-05) — record stop_loss exits for the post-SL
        # cooldown / StoplossGuard layers in RiskManager. Runs FIRST so
        # ledger is updated even if downstream notifier/warehouse code
        # raises. freqtrade pattern: revenge re-entry on a freshly-stopped
        # pair-side is the dominant pattern in the audit's $-78 stop_loss
        # bleed; cooldown breaks it.
        if reason == "stop_loss":
            try:
                self.risk.note_sl_hit(pos.symbol, pos.side)
            except Exception as _e:
                logger.debug(f"[Risk29] note_sl_hit failed: {_e}")
        # The partial leg was already booked when it filled, but every
        # completed-trade label and report must use partial + runner economics.
        try:
            whole_pnl = float(pos.whole_realized_pnl())
            pnl_pct = pos.whole_realized_pnl_pct()
        except (AttributeError, TypeError, ValueError):
            whole_pnl = float(pos.pnl or 0.0) + float(
                getattr(pos, "realized_partial_pnl", 0.0) or 0.0
            )
            pnl_pct = pos.pnl_pct
        is_win = whole_pnl > 0.0

        # CLAUDE.md §4: structured markdown journal of the exit (best-effort, every
        # close path funnels through here, so one wire captures SL/TP/trailing/flip).
        try:
            from core import journal as _journal
            _journal.log_action(
                "CLOSE", pos.symbol, getattr(pos, "side", ""),
                f"reason={reason} pnl=${whole_pnl:+.2f} "
                f"strat={getattr(pos, 'strategy', '')}")
        except Exception:
            pass
        # 2026-05-24 — Was `pos.pnl_pct or 0.0` which converts None → 0.0
        # and trips the _SPEC12_SCRATCH_PCT (0.5%) gate in
        # record_trade_result, silently neutralizing real losses on rows
        # without a computed pnl_pct (e.g. ghost-reclassified SL fills
        # with missing entry context). record_trade_result accepts None
        # and treats it correctly.
        close_side = "sell" if pos.side == "buy" else "buy"
        _is_paper = pos.paper_trade if hasattr(pos, "paper_trade") else self.dry_run

        if _is_paper:
            try:
                self.wallet.on_close(
                    pos.exchange, pos.symbol, pos.side,
                    pos.size, price, pos.entry_price,
                    pos.exit_fee, pos.gross_pnl,
                    pos.market_type, leverage=pos.leverage,
                )
            except Exception as we:
                logger.debug(f"[Wallet] on_close skipped: {we}")

        try:
            self.risk.record_trade_pnl(
                # The partial dollars entered daily PnL at partial fill. Book
                # only runner dollars here, but label the one completed trade
                # from its whole economic outcome.
                pos.pnl, self.risk._start_balance or 0,
                is_win=is_win, pnl_pct=pnl_pct,
            )
        except Exception as re:
            logger.debug(f"[Risk] record_trade_pnl skipped: {re}")
        # Spec §12 pause policy — needs per-symbol + per-family streaks.
        # Pass pnl_pct + reason so RiskManager can neutralise scratches and
        # infrastructure exits (STALE/AGE_LIMIT/ghost_force_close/etc.) that
        # would otherwise false-trip the 5-consec-loss halt.
        try:
            self.risk.record_trade_result(
                symbol=pos.symbol,
                family=pos.strategy or "unknown",
                is_win=is_win,
                pnl_usd=whole_pnl,
                pnl_pct=pnl_pct,
                reason=reason,
            )
        except Exception as _rte:
            logger.debug(f"[Risk/Spec12] record_trade_result skipped: {_rte}")
        try:
            self.compliance.log_trade(
                pos.exchange, pos.symbol, close_side,
                pos.size, price, pos.strategy,
                pnl=whole_pnl, reason=reason, order_id=pos.id,
            )
        except Exception as ce:
            logger.debug(f"[Compliance] log_trade skipped: {ce}")

        if is_win:
            self.blacklist.record_win(pos.symbol, pos.side)
        elif reason == "stop_loss":
            self.blacklist.record_stop_loss(pos.symbol, pos.side)

        self.trailing.remove(pos.id)
        self._sl_widened.discard(pos.id)
        self._save_sl_widened()

        # Warehouse trade-close update (spec §4, §6) — locate the row by
        # idempotency key and patch in exit fields + r_multiple.
        #
        # 2026-05-21: split into three try/except layers so that:
        #   (1) trade_id_by_key miss surfaces as a WARNING with full context,
        #       since it is the canonical leak signal (orphan OPEN warehouse
        #       rows accumulate when this lookup silently returns None).
        #   (2) record_trade_close failures warn separately.
        #   (3) best-effort calibrator/attribution stay at debug — those are
        #       not load-bearing for the close ledger.
        tid = None
        try:
            from core.warehouse import get_warehouse
            wh = get_warehouse()
            tid = wh.trade_id_by_key(
                exchange=pos.exchange.lower(),
                symbol=pos.symbol,
                ts_entry=float(getattr(pos, "open_time", 0)),
                side=pos.side,
            )
        except Exception as _we:
            logger.warning(
                f"[Warehouse] trade_id_by_key raised for "
                f"{pos.exchange}:{pos.symbol} {pos.side} "
                f"open_time={getattr(pos, 'open_time', 0)}: {_we}"
            )

        if tid is None:
            logger.warning(
                f"[Warehouse] trade_id lookup MISS — warehouse row will stay OPEN. "
                f"exchange={pos.exchange.lower()} symbol={pos.symbol} "
                f"side={pos.side} open_time={getattr(pos, 'open_time', 0)} "
                f"close_reason={reason}. "
                f"Run scripts/backfill_warehouse_closes.py to repair."
            )

        if tid:
            xp = float(price or 0)
            # r_multiple against the IMMUTABLE entry stop (not the trailed/BE
            # stop) and blended over any partial fills (audit 2026-06-04: a
            # BE-moved stop NULLed/inflated R). r_multiple has no ACTIVE live
            # reader, so fixing it is measurement-only.
            #
            # realized_pnl is DELIBERATELY left as the RUNNER leg: it feeds the
            # live recent_expectancy entry gate + health/mcp/promotion readers,
            # so shifting it would silently change gating. The partial-TP
            # fraction is booked SEPARATELY in partial_realized_pnl; whole-trade
            # $ = realized_pnl + partial_realized_pnl. Recalibrating the gate to
            # whole-trade economics is a separate owner decision.
            # Defensive fallback keeps legacy behavior if the Position predates
            # the booking-completeness methods.
            try:
                eff_exit = pos.effective_exit_price(xp)
                r_mult = pos.r_multiple(eff_exit)
                entry_stop_px = pos.entry_risk_stop()
                partial_pnl = float(getattr(pos, "realized_partial_pnl", 0.0) or 0.0)
            except Exception:
                r_mult = None
                entry_stop_px = None
                partial_pnl = 0.0
            ts_exit = float(getattr(pos, "close_time", 0) or time.time())
            try:
                wh.record_trade_close(
                    trade_id=tid,
                    ts_exit=ts_exit,
                    exit_px=xp,
                    realized_pnl=float(pos.pnl or 0.0),
                    r_multiple=round(r_mult, 3) if r_mult is not None else None,
                    hold_sec=ts_exit - float(getattr(pos, "open_time", ts_exit)),
                    exit_reason=reason,
                    fee=float(getattr(pos, "total_fees", 0.0)),
                    entry_stop_px=round(entry_stop_px, 8) if entry_stop_px else None,
                    partial_realized_pnl=round(partial_pnl, 8) if partial_pnl else None,
                    exit_decision_id=getattr(pos, "_exit_decision_id", None),
                )
            except Exception as _wce:
                logger.warning(
                    f"[Warehouse] record_trade_close failed for tid={tid} "
                    f"{pos.symbol} {pos.side}: {_wce}"
                )

        if tid:
            try:
                # Phase 18 (2026-05-04): feed the ProbabilityCalibrator with
                # this trade's (predicted_conf, actual_win) so the isotonic
                # regression can fit on real data. Was dead until now —
                # calibrator's record() received no input because position
                # confidence was never persisted. Sister fix to Phase 17
                # adaptive sizing wire-fix. Skip rows with no confidence
                # (legacy / non-Claude-portfolio strategies).
                _conf = float(getattr(pos, "confidence", 0.0) or 0.0)
                if _conf > 0 and self.calibrator is not None:
                    try:
                        self.calibrator.record(
                            predicted_conf=_conf,
                            actual_win=is_win,
                            strategy=str(pos.strategy or "all"),
                        )
                    except Exception as _ce:
                        logger.debug(f"[Calibrator] record skipped: {_ce}")

                # ── Forward attribution (Phase 2 / Task A) ────────────
                # Decompose realized_pnl into alpha/spread/slippage/funding/fees
                # and persist to the attribution table. Skip rows with zero
                # mids (ghost-sync, pre-Task-A positions, ticker failures) —
                # those produce a misleading zero-cost decomposition.
                try:
                    if (float(getattr(pos, "entry_mid", 0.0) or 0.0) > 0.0
                            and float(getattr(pos, "exit_mid", 0.0) or 0.0) > 0.0):
                        from core.attribution import Trade as _AttrTrade
                        from core.attribution import record as _attr_record

                        # LIVE funding accrual: query exchange for funding
                        # payments over the hold window. Paper positions
                        # already accumulated `funding_paid` in
                        # accrue_paper_funding. Never block close on a
                        # funding-API hiccup.
                        funding_paid = float(getattr(pos, "funding_paid", 0.0) or 0.0)
                        if not _is_paper and funding_paid == 0.0:
                            try:
                                ex_obj = self._exchange_for(pos.exchange)
                                if ex_obj is not None:
                                    open_ms = int(float(getattr(pos, "open_time", 0)) * 1000)
                                    rows = ex_obj.exchange.fetch_funding_history(
                                        pos.symbol, since=open_ms)
                                    close_ms = int(ts_exit * 1000)
                                    # A2 audit: fetchFundingHistory amount is
                                    # ALREADY signed by the venue (side-aware);
                                    # re-applying a manual side sign inverted the
                                    # paper 'positive=paid' convention. Use direct.
                                    funding_paid = sum(
                                        float(r.get("amount") or 0.0)
                                        for r in (rows or [])
                                        if open_ms <= int(r.get("timestamp") or 0) <= close_ms
                                        and (r.get("symbol") in (pos.symbol, None)
                                             or r.get("info", {}).get("symbol") == pos.symbol)
                                    )
                            except Exception as _fe:
                                logger.debug(
                                    f"[Attribution] funding fetch skipped "
                                    f"({pos.symbol}): {_fe}")

                        # Slippage: paper shows the sim-applied slippage as a
                        # diagnostic; live's spread already captures fill-vs-mid.
                        slippage = 0.0

                        _attr_record(wh, trade_id=tid, trade=_AttrTrade(
                            side=pos.side,
                            size=float(pos.size or 0.0),
                            entry_mid=float(pos.entry_mid),
                            entry_fill=float(pos.entry_price or 0.0),
                            exit_mid=float(pos.exit_mid),
                            exit_fill=float(price or 0.0),
                            funding_paid=float(funding_paid),
                            fees=float(getattr(pos, "total_fees", 0.0) or 0.0),
                            slippage_modeled=float(slippage),
                        ))
                    else:
                        logger.debug(
                            f"[Attribution] {pos.symbol} skipped "
                            f"(entry_mid={getattr(pos, 'entry_mid', 0)}, "
                            f"exit_mid={getattr(pos, 'exit_mid', 0)})")
                except Exception as _ae:
                    logger.debug(f"[Attribution] record skipped: {_ae}")
            except Exception as _post_we:
                # Catches calibrator/attribution failures outside their own
                # inner try/excepts. record_trade_close has its own handler
                # above and is not in this scope.
                logger.debug(f"[Warehouse] post-close best-effort step failed: {_post_we}")

        # Post-mortem analysis
        try:
            self.post_mortem.analyze_trade({
                "symbol": pos.symbol, "exchange": pos.exchange,
                "side": pos.side, "market_type": pos.market_type,
                "strategy": pos.strategy,
                "entry_price": pos.entry_price, "close_price": price,
                "pnl": whole_pnl, "pnl_pct": pnl_pct,
                "close_reason": reason, "leverage": pos.leverage,
                "open_time": getattr(pos, "open_time", 0),
                "close_time": getattr(pos, "close_time", 0),
            })
        except Exception as pm_err:
            logger.debug(f"[PostMortem] {pm_err}")

        # Gather rich context for email notification
        _duration = None
        try:
            _duration = float(getattr(pos, "close_time", 0) or 0) \
                      - float(getattr(pos, "open_time", 0) or 0)
        except Exception:
            pass
        _bal_after = None
        _daily_pnl = None
        _daily_trades = None
        _daily_wr = None
        _open_n = None
        try:
            _open_n = self.tracker.count_open()
            if self.dry_run:
                _bal_after = float(self.wallet.total_balance() or 0.0)
            # Today's trade stats — use local-calendar midnight so email
            # "Daily PnL / WR" matches the dashboard's "Today" panel exactly.
            # Old code used (now - 86400) which is a rolling 24h window:
            # at 07:51AM it reached back to yesterday 07:51AM and blended
            # today+yesterday trades, making the email diverge from the
            # dashboard by up to 2× the daily PnL.
            import datetime as _dt
            _today = _dt.date.today()
            _midnight = _dt.datetime(
                _today.year, _today.month, _today.day, 0, 0, 0
            )
            day_start = int(_midnight.timestamp())
            # Also exclude pure-import reconcile closures (reconciled_no_context,
            # reconciled_from_exchange) which are position-tracker sync events,
            # not real capital decisions. This matches _is_real_trade in dashboard.
            _IMPORT_REASONS = frozenset({
                "reconciled_no_context",
                "reconciled_from_exchange",
            })
            todays = [
                p for p in self.tracker._closed
                if (float(getattr(p, "close_time", 0) or 0) >= day_start
                    and (getattr(p, "close_reason", "") or "") not in _IMPORT_REASONS)
            ]
            def _closed_whole_pnl(closed_pos):
                try:
                    return float(closed_pos.whole_realized_pnl())
                except (AttributeError, TypeError, ValueError):
                    return float(closed_pos.pnl or 0.0) + float(
                        getattr(closed_pos, "realized_partial_pnl", 0.0) or 0.0
                    )

            _daily_trades = len(todays)
            _daily_pnl = sum(_closed_whole_pnl(p) for p in todays)
            if _daily_trades:
                _daily_wr = 100.0 * sum(
                    1 for p in todays if _closed_whole_pnl(p) > 0.0
                ) / _daily_trades
        except Exception:
            pass
        try:
            self.notifier.trade_closed(
                pos.exchange, pos.symbol, pos.side,
                pos.entry_price, price,
                whole_pnl, pnl_pct, reason,
                strategy=getattr(pos, "strategy", None),
                size=getattr(pos, "size", None),
                leverage=getattr(pos, "leverage", None),
                gross_pnl=getattr(pos, "gross_pnl", None),
                entry_fee=getattr(pos, "entry_fee", None),
                exit_fee=getattr(pos, "exit_fee", None),
                total_fees=getattr(pos, "total_fees", None),
                stop_loss=getattr(pos, "stop_loss", None),
                take_profit=getattr(pos, "take_profit", None),
                partial_taken=getattr(pos, "partial_taken", False),
                open_time=getattr(pos, "open_time", None),
                close_time=getattr(pos, "close_time", None),
                duration_sec=_duration,
                balance_after=_bal_after,
                daily_pnl=_daily_pnl,
                daily_trades=_daily_trades,
                daily_wr=_daily_wr,
                open_positions=_open_n,
            )
        except Exception as ne:
            # Upgraded from debug → warning so an email failure is visible
            # at normal log levels. The trade close itself already
            # succeeded; this is purely informational delivery.
            logger.warning(f"[Notifier] trade_closed email failed: {ne}")

    def _net_pnl_at_price(self, pos: Position, price: float) -> tuple:
        """Calculate what net PnL (after fees) would be if we closed at this price.
        Returns (net_pnl_usd, net_pnl_pct, breakeven_price)."""
        from core.position_tracker import _fee_rate
        rate = _fee_rate(pos.market_type)
        entry_fee = pos.size * pos.entry_price * rate
        exit_fee = pos.size * price * rate
        if pos.side == "buy":
            # size is already the leveraged quantity — do NOT multiply by leverage again
            gross = (price - pos.entry_price) * pos.size
            # Breakeven = entry where gross covers both fees + buffer
            be = pos.entry_price * (1 + rate * 2 + 0.0005)
        else:
            gross = (pos.entry_price - price) * pos.size
            be = pos.entry_price * (1 - rate * 2 - 0.0005)
        net = gross - entry_fee - exit_fee
        notional = pos.entry_price * pos.size
        net_pct = (net / notional * 100) if notional > 0 else 0.0
        return net, net_pct, be

    def _early_breakeven_move(self, pos: Position, price: float, effective_sl: float) -> float:
        """Once the trade is at least half way to TP, move SL to breakeven.
        2026-04-16 (post-audit): threshold raised 1.0% → half of (TP distance)
        because ratcheting to BE at 1.0% (25% of a 4% TP) combined with the
        already-removed MCP proactive TP meant any 1% pullback cashed the trade
        at BE. Winners never reached TP — that's the 33 trailing_stop exits
        averaging +$0.82 instead of full TP wins averaging +$2.83.
        """
        _, net_pct, be = self._net_pnl_at_price(pos, price)
        # Threshold = halfway to TP (side-aware). Floored at 1.5% to cover
        # taker-fee round-trip (~0.1%) with breathing room. With a 4% TP this
        # fires at +2%, with an 8% TP at +4% — much less likely to stop out
        # on a normal 1h retrace.
        tp_dist_pct = 0.0
        if pos.take_profit and pos.entry_price > 0:
            if pos.side == "buy":
                tp_dist_pct = (pos.take_profit - pos.entry_price) / pos.entry_price * 100.0
            else:
                tp_dist_pct = (pos.entry_price - pos.take_profit) / pos.entry_price * 100.0
        threshold = max(1.5, 0.5 * tp_dist_pct) if tp_dist_pct > 0 else 2.0
        if net_pct >= threshold:
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

