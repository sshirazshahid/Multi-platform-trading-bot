"""
core/order_mgmt/protection.py — OrderManager _ProtectionMixin mixin (Phase D4).
"""
import time

from loguru import logger

from core.order_mgmt.helpers import build_sl_tp_order_params
from core.position_tracker import Position
from exchanges.base import BaseExchange
from utils.http_redaction import redact_http_debug as _redact_http_debug

class _ProtectionMixin:
    def _place_exchange_sl_tp(self, exchange, pos, sl, tp, side, symbol, size,
                              market_type, mark_hint: float = None):
        """Attempt to place SL/TP conditional orders on the exchange.

        ``mark_hint`` (C5, tpbot retrofit 2026-07-08): the just-verified fill
        price from open_position. When provided, the crossed-SL pre-flight
        evaluates against it instead of spending a fetch_ticker HTTP
        round-trip — one less serial hop in the fill→protection gap. The
        guard itself is unchanged (a crossed SL still closes normally), and
        callers without a fresh price (trailing re-place, reconcilers) omit
        the hint and keep the ticker fetch.

        2026-04-20 FIX (-4120 root cause): Binance USD-M rejects
        type="market" + stopLossPrice/takeProfitPrice params on /fapi/v1/order
        (error -4120 "use Algo Order API"). Route Binance through its native
        STOP_MARKET / TAKE_PROFIT_MARKET types with `stopPrice`. Bybit/Bitget
        keep the ccxt unified path (triggerPrice + stopLossPrice).

        2026-05-21 FIX (Bybit 110092 / Bitget 45122 noise): pre-flight check —
        when current mark has already crossed the SL threshold (e.g. short SL
        at 0.1117 with mark at 0.1117), the exchange would correctly reject
        the placement because the SL would trigger immediately. The fail-closed
        path then market-closed the position with an EMERGENCY log + 5 debug
        spam lines. The position would have closed via SL anyway, so we now
        detect this pre-flight, log INFO, and close normally with reason
        "sl_crossed_at_placement". A second guard in the except handler
        catches the rare race past the pre-flight (price moves between check
        and create_order) and downgrades those specific reject codes to INFO.

        Fail-closed policy: if SL placement fails for any other reason, the
        position is closed immediately at market. No naked live positions.
        """
        ex_lower = exchange.name.lower()
        close_side = "sell" if side == "buy" else "buy"
        oneway = ex_lower in self._oneway_mode

        # Round prices to exchange precision (fixes Bitget 40808 "checkBDScale" error)
        sl_rounded = exchange.round_price(symbol, sl, market_type)
        tp_rounded = exchange.round_price(symbol, tp, market_type)
        size_rounded = exchange.round_quantity(symbol, size, market_type)

        # 2026-05-21 pre-flight: mark has already crossed SL → exchange would
        # reject (Bybit 110092 / Bitget 45122). The SL would have triggered
        # anyway; close normally rather than fail-closed-EMERGENCY.
        sl_already_crossed = False
        last = 0.0
        try:
            if mark_hint is not None and float(mark_hint) > 0:
                # C5: fresh verified fill price — skip the ticker round-trip.
                last = float(mark_hint)
        except (TypeError, ValueError):
            last = 0.0
        if last <= 0:
            try:
                tkr = exchange.fetch_ticker(symbol, market_type)
                last = float((tkr or {}).get("last") or (tkr or {}).get("close") or 0)
            except Exception:
                last = 0.0
        if last > 0:
            side_l = (side or "").lower()
            if side_l == "buy" and sl_rounded >= last:
                sl_already_crossed = True
            elif side_l == "sell" and sl_rounded <= last:
                sl_already_crossed = True
        if sl_already_crossed:
            logger.info(
                f"[Orders] SL pre-flight: {symbol} {side.upper()} SL "
                f"{sl_rounded:.6g} already on wrong side of mark {last:.6g} "
                f"— closing {pos.id[:8]} normally (would-have-triggered).")
            pos._exchange_sl = False
            pos._exchange_tp = False
            try:
                self.close_position(exchange, pos, reason="sl_crossed_at_placement")
            except Exception as close_err:
                logger.error(
                    f"[Orders] close_position failed after SL pre-flight for "
                    f"{symbol}: {close_err}")
            return

        # Stop-Loss — spec §14: failed SL must trigger emergency handling.
        # 2026-04-20: fail-closed. If SL cannot be attached, close the
        # position at market instead of leaving it naked.
        try:
            sl_type, sl_params = build_sl_tp_order_params(
                ex_lower, side, oneway, sl_rounded, is_sl=True)
            _sl_res = exchange.create_order(
                symbol, sl_type, close_side, size_rounded, None, sl_params,
                market_type=market_type)
            if not _sl_res:
                # 2026-07-07: a not-connected client returns {} WITHOUT
                # raising — treating that as success marked naked positions
                # as exchange-protected and suppressed the local SL fallback.
                # Raise into the fail-closed path below instead.
                raise RuntimeError(
                    "create_order returned an empty result for the SL order "
                    "(client not connected?)")
            pos._exchange_sl = True
            logger.info(
                f"[Orders] SL order on {exchange.name}: {symbol} "
                f"@ {sl_rounded} (type={sl_type})")
            self._record_lifecycle(pos, "ORDER_PLACED", {
                "leg": "sl", "trigger": float(sl_rounded),
                "order_type": str(sl_type), "size": float(size_rounded)})
        except Exception as e:
            # 2026-05-21: catch the rare race past the pre-flight — mark moved
            # between fetch_ticker and create_order. Bybit 110092 and Bitget
            # 45122 both mean "SL on wrong side of mark"; close normally.
            err_str = str(e)
            is_crossed_race = (
                "110092" in err_str
                or "45122" in err_str
                or "stop loss price please" in err_str.lower()
                or "expect rising" in err_str.lower()
                or "expect falling" in err_str.lower()
            )
            if is_crossed_race:
                logger.info(
                    f"[Orders] SL race-rejection on {exchange.name} {symbol}: "
                    f"{err_str[:160]} — closing {pos.id[:8]} normally "
                    f"(would-have-triggered).")
                pos._exchange_sl = False
                pos._exchange_tp = False
                try:
                    self.close_position(
                        exchange, pos, reason="sl_crossed_during_placement")
                except Exception as close_err:
                    logger.error(
                        f"[Orders] close_position failed after SL race-reject "
                        f"for {symbol}: {close_err}")
                return
            _safe_error = _redact_http_debug(e, max_chars=1000)
            logger.error(
                f"[Orders] EMERGENCY: SL order FAILED on {exchange.name} {symbol}: "
                f"{_safe_error} "
                f"— closing position {pos.id[:8]} at market (fail-closed policy).")
            # Capture ccxt's last HTTP round-trip so the next occurrence of
            # -4120 (or any repeat rejection) leaves enough evidence on disk
            # to diagnose without a second live failure. Raw body bypasses
            # ccxt's error-string truncation.
            try:
                raw = getattr(exchange, "exchange", None)
                req_url    = getattr(raw, "last_request_url", None)
                req_body   = getattr(raw, "last_request_body", None)
                req_hdrs   = getattr(raw, "last_request_headers", None)
                resp_body  = getattr(raw, "last_http_response", None)
                resp_hdrs  = getattr(raw, "last_response_headers", None)
                opts_type  = None
                try:
                    opts_type = raw.options.get("defaultType") if raw else None
                except Exception:
                    pass
                logger.error(
                    f"[Orders] SL-FAIL DEBUG | exchange={exchange.name} symbol={symbol} "
                    f"order_type={sl_type} close_side={close_side} size={size_rounded} "
                    f"sl_params={sl_params} defaultType={opts_type}")
                logger.error(
                    f"[Orders] SL-FAIL DEBUG | "
                    f"request_url={_redact_http_debug(req_url)} "
                    f"request_body={_redact_http_debug(req_body)}")
                logger.error(
                    f"[Orders] SL-FAIL DEBUG | "
                    f"response_body={_redact_http_debug(resp_body)}")
                logger.debug(
                    f"[Orders] SL-FAIL DEBUG hdrs | "
                    f"req={_redact_http_debug(req_hdrs)} "
                    f"resp={_redact_http_debug(resp_hdrs)}")
            except Exception as dbg_err:
                logger.warning(f"[Orders] SL-FAIL debug capture itself failed: {dbg_err}")
            pos._sl_failed = True
            # Codex order_router port (2026-07-12): SL_FAIL_EMERGENCY_CLOSE_ENABLED
            # (default TRUE — charter §2 Stop-Loss Guardian) gates the fail-closed
            # close. This point is only reached after create_order's FULL retry
            # ladder exhausted (incl. Bybit-110072 clientOrderId regen and the
            # Binance -4120 algo routing) — never on a recovered transient error.
            import config as _cfg
            if not bool(getattr(_cfg, "SL_FAIL_EMERGENCY_CLOSE_ENABLED", True)):
                # Operator escape hatch: position stays OPEN without exchange-side
                # protection; local polled SL (check_sl_tp) + _reconcile_missing_sl
                # (once/minute) are the only rails until the SL is restored.
                try:
                    self.notifier.send(
                        f"EMERGENCY: SL placement FAILED for {symbol} on "
                        f"{exchange.name}. Position {pos.id[:8]} is OPEN "
                        f"WITHOUT exchange-side protection "
                        f"(SL_FAIL_EMERGENCY_CLOSE_ENABLED=false); local SL "
                        f"monitoring + per-minute reconcile active. "
                        f"Reason: {_safe_error}")
                except Exception:
                    pass
                return
            # Fail-closed: close FIRST through the NORMAL close path
            # (close_position → tracker.close → on_close hooks — every close
            # path hits all safety hooks), THEN alert with the true outcome.
            closed_ok = False
            try:
                self.close_position(exchange, pos, reason="sl_placement_failed")
                closed_ok = True
            except Exception as close_err:
                logger.critical(
                    f"[Orders] CRITICAL: fail-closed close_position also failed "
                    f"for {symbol} — position is naked and tracker may be stale: "
                    f"{close_err}")
                try:
                    self.notifier.send(
                        f"CRITICAL: {symbol} on {exchange.name} is NAKED and "
                        f"auto-close failed. MANUAL INTERVENTION REQUIRED. "
                        f"Reason: {close_err}")
                except Exception:
                    pass
            if closed_ok:
                try:
                    self.notifier.send(
                        f"EMERGENCY: SL placement FAILED for {symbol} on "
                        f"{exchange.name} — position {pos.id[:8]} closed "
                        f"fail-safe (charter §2 Stop-Loss Guardian). Reason: {e}")
                except Exception:
                    pass
            # Do not attempt TP placement; position is (or should be) closed.
            return

        # Take-Profit
        try:
            tp_type, tp_params = build_sl_tp_order_params(
                ex_lower, side, oneway, tp_rounded, is_sl=False)
            _tp_res = exchange.create_order(
                symbol, tp_type, close_side, size_rounded, None, tp_params,
                market_type=market_type)
            if not _tp_res:
                # 2026-07-07: same not-connected {} pattern as the SL above —
                # raise into the handler below (local monitoring covers TP).
                raise RuntimeError(
                    "create_order returned an empty result for the TP order "
                    "(client not connected?)")
            pos._exchange_tp = True
            logger.info(
                f"[Orders] TP order on {exchange.name}: {symbol} "
                f"@ {tp_rounded} (type={tp_type})")
            self._record_lifecycle(pos, "ORDER_PLACED", {
                "leg": "tp", "trigger": float(tp_rounded),
                "order_type": str(tp_type), "size": float(size_rounded)})
        except Exception as e:
            # Clear the flag so _exchange_handles_sltp evaluates False and the
            # polled SL/TP price-trigger + B6 planned-first path cover the dropped
            # TP until _reconcile_missing_tp restores the exchange TP (audit
            # 2026-06-21 B7). On the fresh-entry path the attr may be unset, so
            # this is not always already-False.
            pos._exchange_tp = False
            logger.warning(
                f"[Orders] TP order FAILED on {exchange.name} {symbol}: {e} "
                f"— local monitoring active (SL is protected)")

    @staticmethod
    def _cap_stop_fill(sim_px: float, trigger_px: float, close_side: str) -> float:
        """Bound a simulated STOP fill so it can never be BETTER than the
        trigger level (2026-07-07). paper_fill_price derives fills from the
        live book at poll time; on a wick-through-and-recover the book has
        already bounced, but a live stop-market would have filled at (or
        beyond) the trigger during the move. Selling to close a long: lower
        is worse. Buying to close a short: higher is worse."""
        if trigger_px <= 0 or sim_px <= 0:
            return sim_px
        if str(close_side).lower() == "sell":
            return min(sim_px, trigger_px)
        return max(sim_px, trigger_px)

    def _replace_exchange_sl(self, exchange: BaseExchange, pos: Position) -> None:
        """B7-P2 per-position serialization wrapper (default-OFF). Serializes
        SL-mutation with close/other SL-moves on the same id via the shared
        per-id RLock; timeout-acquire with proceed-on-timeout so it can never
        freeze the SL monitor. RLock re-entry covers the fail-closed re-call
        into close_position inside _place_exchange_sl_tp."""
        if not getattr(self, "_per_pos_lock_enabled", False):
            return self._replace_exchange_sl_impl(exchange, pos)
        lk = self._position_lock(pos.id)
        acquired = lk.acquire(timeout=self._pos_lock_timeout)
        if not acquired:
            logger.error(
                f"[Lock] _replace_exchange_sl _position_lock TIMEOUT for "
                f"{pos.id} after 5s — proceeding WITHOUT serialization")
        try:
            return self._replace_exchange_sl_impl(exchange, pos)
        finally:
            if acquired:
                lk.release()

    def _replace_exchange_sl_impl(self, exchange: BaseExchange, pos: Position) -> None:
        """Cancel the existing SL/TP conditional orders on the exchange and
        re-place them at the current ``pos.stop_loss`` / ``pos.take_profit``
        for the current ``pos.size``.

        2026-04-25 (post-audit): pre-flight check added — if the target SL
        sits on the wrong side of current price (would be exchange-rejected,
        e.g. Bitget code 40917) the cancel-then-place cycle would strip the
        working SL and fail-closed would market-close an otherwise-healthy
        position. Skip the move when invalid; the existing SL keeps running.

        2026-04-19 (Fix A2/A3): the trailing-advance and partial-TP-breakeven
        paths only mutate ``pos.stop_loss`` in the tracker. In CONTROLLED_LIVE
        the exchange keeps running the original (wider) SL because
        ``_exchange_sl=True`` silences local SL monitoring — so the tighter
        SL was a ghost. This helper mirrors ``bot_engine._replace_exchange_sl``
        so the order-manager's internal loops can sync too.
        """
        if self.dry_run:
            return  # paper mode: no exchange order to cancel/re-place
        if not pos or pos.market_type != "futures":
            return  # spot has no exchange-side SL/TP
        if not pos.stop_loss or pos.stop_loss <= 0:
            return

        # Pre-flight: refuse SL placements the exchange would reject for
        # being on the wrong side of current price. See the 2026-04-25 note
        # above — this prevents fail-closed from killing healthy positions
        # during a normal pullback through a tightened/breakeven SL level.
        try:
            tkr = exchange.fetch_ticker(pos.symbol, pos.market_type)
            last = float((tkr or {}).get("last") or (tkr or {}).get("close") or 0)
        except Exception as e:
            logger.debug(f"[Orders] Replace-SL mark check unavailable for {pos.symbol}: {e}")
            last = 0.0
        if last > 0:
            side = (pos.side or "").lower()
            if side == "buy" and pos.stop_loss >= last:
                logger.info(
                    f"[Orders] Replace-SL SKIP {pos.symbol} BUY: target SL "
                    f"{pos.stop_loss:.6g} >= last {last:.6g} (would be "
                    f"exchange-rejected); keeping current exchange SL")
                return
            if side == "sell" and pos.stop_loss <= last:
                logger.info(
                    f"[Orders] Replace-SL SKIP {pos.symbol} SELL: target SL "
                    f"{pos.stop_loss:.6g} <= last {last:.6g} (would be "
                    f"exchange-rejected); keeping current exchange SL")
                return

        # Cancel ALL conditional orders on the symbol. We do not track the
        # specific SL/TP order IDs at placement time; the symbol scope is
        # narrow enough for one position.
        try:
            if hasattr(exchange, "cancel_all_orders"):
                exchange.cancel_all_orders(pos.symbol, pos.market_type)
            else:
                exchange.exchange.cancel_all_orders(pos.symbol)
        except Exception as e:
            logger.warning(
                f"[Orders] Replace-SL cancel_all_orders failed for "
                f"{pos.symbol}: {str(e)[:120]} — proceeding with re-place")

        # Clear flags so a placement failure falls through to local monitoring
        pos._exchange_sl = False
        pos._exchange_tp = False

        try:
            self._place_exchange_sl_tp(
                exchange, pos, pos.stop_loss, pos.take_profit,
                pos.side, pos.symbol, pos.size, pos.market_type)
            logger.info(
                f"[Orders] Exchange SL synced: {pos.symbol} "
                f"SL={pos.stop_loss:.6g} size={pos.size:.8g}")
        except Exception as e:
            logger.error(
                f"[Orders] Replace-SL re-place failed for {pos.symbol}: "
                f"{str(e)[:150]}")

    def _reconcile_missing_sl(self, exchange: BaseExchange, pos: Position) -> bool:
        """Re-establish a missing exchange-side SL on a live futures position.

        2026-06-20: ``_sl_failed`` was set at placement (see
        ``_place_exchange_sl_tp`` fail-closed path) but never read again — a
        position whose SL placement failed (and whose fail-closed market-close
        also failed, leaving it open) survived only on the slower polled-SL
        checks in ``check_sl_tp`` and never regained exchange-side protection.
        This re-attempts placement once per minute per position. Local polled
        SL still guards the position in the meantime, so this only *restores*
        the always-on, wick-accurate exchange stop; it is not the sole rail.

        Returns True when the position has (or regains) exchange-side SL.
        """
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return True
        if not getattr(pos, "_sl_failed", False):
            return True
        if getattr(pos, "_exchange_sl", False):
            pos._sl_failed = False  # already protected; clear the stale flag
            return True
        import time as _t
        if _t.time() - getattr(pos, "_sl_retry_ts", 0.0) < 60:
            return False  # throttle: don't hammer the venue every cycle
        pos._sl_retry_ts = _t.time()
        logger.warning(
            f"[Orders] SL reconcile: re-attempting exchange SL for "
            f"{pos.symbol} {pos.id[:8]} (naked since placement failure)")
        try:
            self._replace_exchange_sl(exchange, pos)
        except Exception as e:
            logger.error(
                f"[Orders] SL reconcile failed for {pos.symbol}: {str(e)[:120]}")
        if getattr(pos, "_exchange_sl", False):
            pos._sl_failed = False
            logger.info(
                f"[Orders] SL reconcile OK: exchange SL restored for {pos.symbol}")
            return True
        return False

    def _reconcile_missing_tp(self, exchange: BaseExchange, pos: Position) -> bool:
        """Verify venue-side SL/TP coverage for a live futures position.

        Two shapes, ONE throttled open-orders fetch (once/minute/position),
        classified by ``_classify_resting_conditionals`` (C1 single source
        of truth):

        1. SL coverage (C4, tpbot retrofit 2026-07-08): ``_exchange_sl``
           claims the venue holds the stop, but NO loss-side conditional
           conclusively rests there — previously the one UNDETECTABLE naked
           shape (venue-side cancel/expiry with healthy flags; the old code
           explicitly no-op'd on "TP present, SL absent"). Repair through
           ``_replace_exchange_sl``: its per-position RLock serializes
           against trailing advances, its wrong-side pre-flight refuses
           doomed re-places, and the level is ``pos.stop_loss`` — the
           bot-authoritative stop that only ever tightens, so WIDEN is
           structurally impossible from this path. If the SL is absent
           because it just FILLED (position closing), the ghost sync books
           the close and post-close cancel-all + daily orphan cleanup
           remove the re-placed conditional.

        2. TP orphan (audit 2026-06-21 B7, semantics unchanged):
           ``_exchange_tp`` claims True but no profit-side conditional rests
           while an SL does — DOWNGRADE to local polled enforcement; never
           cancel a WORKING SL to restore a TP.

        Empty open-orders list is INCONCLUSIVE (exchanges/base.py returns []
        on error AND when not-ready) — no-op, never churn. Live/futures
        only. Returns True when nothing (further) needs doing this pass.
        """
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return True
        valid_entry = bool(pos.entry_price) and float(pos.entry_price) > 0
        sl_checkable = (bool(getattr(pos, "_exchange_sl", False))
                        and bool(pos.stop_loss) and float(pos.stop_loss) > 0
                        and valid_entry)
        tp_checkable = (bool(getattr(pos, "_exchange_tp", False))
                        and bool(pos.take_profit) and float(pos.take_profit) > 0
                        and valid_entry)
        if not (sl_checkable or tp_checkable):
            return True  # venue not meant to own either leg -> polled path covers
        import time as _t
        if _t.time() - getattr(pos, "_tp_retry_ts", 0.0) < 60:
            return True  # throttle: at most once/minute/position
        pos._tp_retry_ts = _t.time()

        sl_present, tp_present, conclusive = self._classify_resting_conditionals(
            exchange, pos)
        if not conclusive:
            return True  # error/not-ready/empty -> never treat as "leg gone"

        # (1) C4 — venue-side SL vanished with healthy flags. Priority action:
        # _replace_exchange_sl re-establishes BOTH legs at the authoritative
        # levels (and clears the flags itself on failure so polled monitoring
        # takes over), so the TP branch below is intentionally skipped this
        # pass rather than double-mutating flags mid-repair.
        if sl_checkable and not sl_present:
            logger.warning(
                f"[Orders] SL coverage reconcile: exchange SL MISSING for "
                f"{pos.symbol} {pos.id[:8]} (flags healthy, no loss-side "
                f"conditional resting) — re-placing at authoritative SL "
                f"{float(pos.stop_loss):.6g}")
            try:
                self._replace_exchange_sl(exchange, pos)
            except Exception as e:
                logger.error(
                    f"[Orders] SL coverage re-place failed for {pos.symbol}: "
                    f"{str(e)[:120]}")
            return bool(getattr(pos, "_exchange_sl", False))

        # (2) B7 — orphaned exchange TP (SL resting, no profit-side
        # conditional). Do NOT cancel the WORKING SL to re-place both (brief
        # naked window if the SL re-place then fails). DOWNGRADE to polled:
        # clear _exchange_tp so _exchange_handles_sltp evaluates False and the
        # polled price-trigger + B6 planned-first path watch the planned TP.
        if not tp_checkable or tp_present or not sl_present:
            return True
        logger.warning(
            f"[Orders] TP reconcile: exchange TP missing for {pos.symbol} "
            f"{pos.id[:8]} (SL present, no profit-side conditional) — downgrading "
            f"to polled TP enforcement (planned TP {float(pos.take_profit):.6g})")
        pos._exchange_tp = False
        return True

    def _classify_resting_conditionals(self, exchange: BaseExchange,
                                       pos: Position) -> tuple:
        """Classify resting conditional orders on the venue for ``pos``.

        Same side convention as ``_reconcile_missing_tp``: a trigger above
        entry is profit-side for buys and loss-side for sells (mirrored).

        Returns ``(sl_present, tp_present, conclusive)``. ``conclusive`` is
        False when the venue answer cannot be trusted: empty open-orders
        list (exchanges/base.py returns [] on error AND when not-ready) or
        an unusable entry price. Callers must fail closed on inconclusive.
        """
        try:
            ep = float(pos.entry_price or 0)
        except (TypeError, ValueError):
            ep = 0.0
        if ep <= 0:
            return False, False, False
        try:
            open_orders = exchange.fetch_open_orders(pos.symbol, pos.market_type)
        except Exception as e:  # nonconforming client; base.py normally returns []
            logger.warning(
                f"[Orders] conditional classify: fetch_open_orders raised for "
                f"{pos.symbol} on {getattr(exchange, 'name', '?')}: {str(e)[:120]}")
            return False, False, False
        if not isinstance(open_orders, list):
            open_orders = []
        # C5 (2026-07-08): merge the CONDITIONAL book. Plain fetch_open_orders
        # misses SL/TP conditionals on all three venues (Binance algo book,
        # Bybit StopOrder ledger, Bitget plan orders) — without this merge the
        # B7/C4 reconcilers and C1 verifiers only ever saw regular orders and
        # were fail-safe no-ops in live.
        # Review fix 2026-07-09 (MEDIUM): a FAILED conditional-book fetch now
        # returns None (contract change in fetch_open_conditionals) and is
        # INCONCLUSIVE — treating it as empty while any regular order rested
        # (e.g. an owner's manual limit) made C4 cancel a WORKING live SL on a
        # loop for as long as the venue error persisted. Non-list junk (test
        # fakes without the method) keeps the legacy regular-book-only path.
        try:
            extra = exchange.fetch_open_conditionals(pos.symbol, pos.market_type)
        except Exception:
            extra = None
        if extra is None:
            return False, False, False  # conditional book unreachable -> inconclusive
        if isinstance(extra, list) and extra:
            seen_ids = {o.get("id") for o in open_orders
                        if isinstance(o, dict) and o.get("id")}
            for o in extra:
                if isinstance(o, dict) and (
                        not o.get("id") or o.get("id") not in seen_ids):
                    open_orders.append(o)
        if not open_orders:
            return False, False, False  # inconclusive — error or genuinely empty
        side = (pos.side or "").lower()
        # Review fix 2026-07-09 (HIGH): classify against the bot-authoritative
        # levels FIRST. The entry-side heuristic alone misread a breakeven or
        # trailed SL (trigger above entry on longs — routine after
        # _early_breakeven_move / trailing activation) as a TP, so the C4
        # coverage branch declared the SL missing and cancel-all/re-placed
        # every trailing winner once a minute — a naked window each pass.
        # The entry-side heuristic remains only for triggers matching neither
        # level (e.g. stale drifted orders), preserving pre-C4 behavior there.
        sl_level = tp_level = 0.0
        try:
            sl_level = float(getattr(pos, "stop_loss", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            tp_level = float(getattr(pos, "take_profit", 0) or 0)
        except (TypeError, ValueError):
            pass
        _TOL = 0.002  # 0.2% — covers tick rounding between pos level and trigger

        def _near(trig_px: float, level: float) -> bool:
            return level > 0 and abs(trig_px - level) <= level * _TOL

        tp_present = sl_present = False
        for o in open_orders:
            info = o.get("info", {}) or {}
            trig = (o.get("triggerPrice") or o.get("stopPrice")
                    or info.get("triggerPrice") or info.get("stopPrice"))
            try:
                trig = float(trig) if trig is not None else None
            except (TypeError, ValueError):
                trig = None
            if not trig or trig <= 0:
                continue
            if _near(trig, sl_level):
                sl_present = True
            elif _near(trig, tp_level):
                tp_present = True
            elif (trig > ep) if side == "buy" else (trig < ep):
                tp_present = True
            else:
                sl_present = True
        return sl_present, tp_present, True

    def verify_exchange_sl_alive(self, exchange: BaseExchange,
                                 pos: Position) -> bool:
        """True only when a loss-side conditional is VERIFIED resting on the
        venue for ``pos``. Fail-closed: PAPER (no venue orders exist), spot
        (no exchange-side protection by design), fetch failures and the
        inconclusive empty-list shape all return False — consumers (the
        ghost-reroute instrumentation) must never credit unverified
        protection. Read-only: never places or cancels anything."""
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return False
        sl_present, _tp, conclusive = self._classify_resting_conditionals(
            exchange, pos)
        venue = str(getattr(exchange, "name", "?")).lower()
        logger.debug(
            f"[Orders] verify_sl_alive {pos.symbol}@{venue}: "
            f"sl={sl_present} conclusive={conclusive}")
        return bool(conclusive and sl_present)

    def verify_exchange_tp_alive(self, exchange: BaseExchange,
                                 pos: Position) -> bool:
        """True only when a profit-side conditional is VERIFIED resting on
        the venue for ``pos``. Same fail-closed semantics as
        ``verify_exchange_sl_alive``. Read-only."""
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return False
        _sl, tp_present, conclusive = self._classify_resting_conditionals(
            exchange, pos)
        venue = str(getattr(exchange, "name", "?")).lower()
        logger.debug(
            f"[Orders] verify_tp_alive {pos.symbol}@{venue}: "
            f"tp={tp_present} conclusive={conclusive}")
        return bool(conclusive and tp_present)

    def _position_flat_on_venue(self, exchange: BaseExchange, symbol: str):
        """Tri-state venue check: True = POSITIVELY flat, False = POSITIVELY
        still open, None = UNVERIFIABLE (the fetch itself failed).

        Tri-state on purpose: each caller must choose its own fail-direction
        for None, because the safe answer is OPPOSITE depending on what the
        check gates —
          * untracking a position (close path): None -> keep it TRACKED. A
            falsely-untracked open position is naked (no SL, no monitor); a
            falsely-kept closed one reconciles away harmlessly via ghost-sync.
          * retrying a close without reduceOnly: None -> do NOT retry. A retry
            against an already-flat position opens a naked REVERSE (root cause
            of the 2026-04-20 ALGO short cascade).
        Collapsing this into one boolean is what let the two call sites encode
        opposite fail-directions implicitly; keep the choice at the call site.
        """
        try:
            live = exchange.fetch_positions([symbol]) or []
        except Exception as e:
            logger.warning(
                f"[Orders] fetch_positions failed for {symbol}: "
                f"{str(e)[:100]} — venue state UNVERIFIABLE")
            return None
        for p in live:
            try:
                size = abs(float(p.get("contracts") or p.get("contractSize") or 0))
            except (TypeError, ValueError):
                size = 0.0
            if size > 0:
                return False  # positively still open
        return True  # positively flat

    def partial_close_position(self, exchange: BaseExchange, position: Position,
                              close_pct: float, reason: str, price: float):
        """Close a fraction of a position and move SL to breakeven on remainder."""
        partial_size = position.size * close_pct
        # C2 (tpbot retrofit 2026-07-08): quantize to the venue lot step BEFORE
        # the notional check, the order AND the tracker decrement. An unrounded
        # fraction sent to a step-constrained contract (Bitget 0.1-step) fills
        # at the venue-floored size while the tracker decrements the raw
        # fraction — position.size drifts from the venue on every partial.
        # Best-effort: fakes/stubs without lot metadata keep the raw fraction
        # (paper realism unchanged); a fraction that floors to zero is refused.
        try:
            _rq = float(exchange.round_quantity(
                position.symbol, partial_size,
                getattr(position, "market_type", None)))
            partial_size = _rq if _rq > 0 else 0.0
        except Exception:
            pass
        if partial_size <= 0 or partial_size * price < 5.0:
            return False

        close_side = "sell" if position.side == "buy" else "buy"

        if self.dry_run:
            sim_px = self.sim.paper_fill_price(
                exchange, position.symbol, close_side,
                position.market_type, base_price=price, phase="close",
                size=partial_size)
            try:
                sim_px_f = float(sim_px)
            except (TypeError, ValueError):
                sim_px_f = 0.0
            if sim_px_f > 0:
                price = sim_px_f
            logger.info(
                f"[Orders] [DRY] PARTIAL CLOSE {position.symbol} "
                f"{close_pct:.0%} ({partial_size:.8f}) @ {price:.4f} | {reason}")
        else:
            # 2026-05-24 — Futures partial close must carry reduceOnly=True.
            # Without it, if position size races (e.g. exchange-side SL/TP
            # fires between the read and this order), the partial close
            # becomes an opposite-direction entry on Bybit/Bitget instead of
            # reducing the position. Every other close path in this file
            # passes reduceOnly; the partial path was the gap.
            _params: dict = {}
            if position.market_type == "futures":
                _params["reduceOnly"] = True
                # Hedge-mode (not one-way) futures need positionSide so ccxt can disambiguate the
                # leg — mirrors close_position. One-way venues (e.g. Bitget) keep reduceOnly only.
                if exchange.name.lower() not in self._oneway_mode:
                    _params["positionSide"] = "LONG" if position.side == "buy" else "SHORT"
            try:
                exchange.create_order(
                    position.symbol, "market", close_side, partial_size,
                    params=_params,
                    market_type=position.market_type)
            except Exception as e:
                logger.error(f"[Orders] Partial close failed: {e}")
                return False

        position.size -= partial_size
        position.partial_taken = True

        # Compute the partial fill's economics ONCE (mode-agnostic) and record
        # it on the Position so _finalize_close books the WHOLE trade — not just
        # the runner leg (audit 2026-06-04: the taken fraction only ever reached
        # the paper wallet, so the warehouse trade row under-counted realized PnL
        # and biased WR / realized-R downward). book_partial_exit is additive and
        # disjoint from the final (reduced-size) close — NO double-count.
        p_gross = 0.0
        p_exit_fee = 0.0
        try:
            p_gross = ((price - position.entry_price) if position.side == "buy"
                       else (position.entry_price - price)) * partial_size
            try:
                from core.position_tracker import _fee_rate
                p_exit_fee = partial_size * price * _fee_rate(position.market_type)
            except Exception:
                p_exit_fee = partial_size * price * (
                    0.0005 if position.market_type == "futures" else 0.001)
            position.book_partial_exit(partial_size, price, p_gross - p_exit_fee)
        except Exception as _be:
            logger.debug(f"[Orders] book_partial_exit skipped: {_be}")

        # Book the closed fraction in the PAPER wallet (audit 2026-06-03). Without this,
        # partial_close never returned the taken fraction's margin + profit to the paper
        # balance — only _finalize_close booked anything, and on the now-REDUCED size — so
        # every partial TP silently leaked the taken fraction's margin + profit from the
        # paper wallet, biasing the displayed balance/PnL DOWNWARD. The taken fraction is
        # disjoint from the final close (reduced size), so this is additive with NO
        # double-count. Paper-only (live partials settle on the real exchange).
        _is_paper = position.paper_trade if hasattr(position, "paper_trade") else self.dry_run
        if _is_paper:
            try:
                self.wallet.on_close(
                    position.exchange, position.symbol, position.side,
                    partial_size, price, position.entry_price,
                    p_exit_fee, p_gross,
                    position.market_type, leverage=position.leverage,
                )
            except Exception as we:
                logger.debug(f"[Wallet] partial on_close skipped: {we}")

        # 2026-06-11 (owner): the daily-loss breaker must see the partial leg
        # WHEN it banks — _finalize_close records only the runner's pos.pnl,
        # which excludes realized_partial_pnl, so banked partial profits were
        # invisible to the loss budget (breaker erred conservative).
        # is_win=None on purpose: a partial is not a completed trade — Spec §12
        # streaks, recent-results and Kelly history update once, at
        # _finalize_close, on the whole-trade outcome. Mode-agnostic, like
        # book_partial_exit above (live partials realize PnL too).
        try:
            self.risk.record_trade_pnl(
                p_gross - p_exit_fee, self.risk._start_balance or 0,
                is_win=None,
            )
        except Exception as _rp:
            logger.debug(f"[Risk] partial record_trade_pnl skipped: {_rp}")

        # C6 (2026-07-08): partial-take audit row (previously only a trades
        # column — invisible in the event chain).
        self._record_lifecycle(position, "PARTIAL_TP", {
            "size": float(partial_size), "price": float(price),
            "net_pnl": float(p_gross - p_exit_fee), "reason": str(reason)})

        try:
            from config import PARTIAL_TP
            if PARTIAL_TP.get("move_sl_to_breakeven", True):
                _sl_before = float(position.stop_loss or 0)
                position.stop_loss = position.entry_price
                logger.info(f"[Orders] SL moved to breakeven {position.entry_price:.4f}")
                self._record_lifecycle(position, "SL_MOVE", {
                    "from": _sl_before, "to": float(position.entry_price),
                    "trigger": "partial_tp_breakeven"})
        except ImportError:
            position.stop_loss = position.entry_price

        with self.tracker._lock:
            self.tracker._save()

        # 2026-04-19 (Fix A3): if the exchange is holding the SL, the local
        # breakeven move is a ghost until we cancel the old (wider, full-size)
        # SL/TP and re-place at entry for the remaining size. Without this,
        # the exchange keeps running the original SL while the tracker thinks
        # breakeven is locked in.
        if (not self.dry_run
                and getattr(position, "_exchange_sl", False)
                and position.market_type == "futures"):
            try:
                self._replace_exchange_sl(exchange, position)
            except Exception as e:
                logger.warning(
                    f"[Orders] Partial-TP breakeven SL re-place failed "
                    f"for {position.symbol}: {str(e)[:120]}")
        return True

