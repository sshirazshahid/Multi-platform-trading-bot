"""
core/order_mgmt/monitor.py — OrderManager _MonitorMixin mixin (Phase D4).
"""
import time

from loguru import logger

from config import RISK
from core.order_mgmt.helpers import (
    _accuracy_band_hold_active,
    _is_accuracy_band_position,
    _maker_first_cfg,
    _position_age_minutes,
    _safe_ticker_px,
    _should_fire_partial_tp,
    _tier_geometry_hold_active,
    _try_soft_close,
    max_hold_force_flat_hours,
)
from core.risk_manager import load_age_cutoffs
from exchanges.base import BaseExchange

class _MonitorMixin:
    def check_sl_tp(self, exchange: BaseExchange, market_type: str = "spot"):
        # Funding settlement piggybacks on the monitor loop
        if self.dry_run and market_type == "futures":
            self.accrue_paper_funding(exchange)
        # MAKER-FIRST PAPER ENTRIES (2026-07-10): the monitor tick resolves
        # pending virtual post-only intents for this venue (strict trade-
        # through → maker fill; timeout → taker fallback / chase abandon).
        if (self.dry_run and market_type == "futures"
                and (self._pending_maker
                     or _maker_first_cfg().get("enabled", False))):
            try:
                self._resolve_pending_maker_entries(exchange)
            except Exception as e:
                logger.warning(f"[MakerFirst] resolve tick failed: {e}")
        positions = self.tracker.get_open(exchange=exchange.name)
        for pos in positions:
            if pos.market_type != market_type:
                continue
            # Flag: exchange holds both SL and TP conditionals. We still
            # need to run trailing-stop advancement, age limits, hard max
            # loss, and entry-invalidation on these positions — only the
            # direct price-vs-SL/TP trigger checks are redundant (the
            # exchange fires those). Prior code `continue`-d here, which
            # silently skipped all local monitoring for LIVE futures
            # positions — trailing never advanced the SL, age limits
            # never fired, and the 3% hard-max-loss circuit breaker was
            # bypassed.
            _exchange_handles_sltp = (
                getattr(pos, '_exchange_sl', False)
                and getattr(pos, '_exchange_tp', False)
            )
            ticker = exchange.fetch_ticker(pos.symbol, market_type)
            last_price = _safe_ticker_px(ticker, "last") or _safe_ticker_px(
                ticker, "close"
            )
            price = last_price
            if market_type == "futures" and self.enforce_mark_price_triggers:
                price = self._required_futures_mark(exchange, pos, ticker)
                if price <= 0:
                    incident_key = f"{exchange.name}:{pos.symbol}"
                    if incident_key not in self._mark_data_incidents:
                        self._mark_data_incidents.add(incident_key)
                        self.risk.latch_incident(
                            f"mark price unavailable for {incident_key}",
                            category="data",
                            metadata={
                                "venue": str(exchange.name).lower(),
                                "symbol": pos.symbol,
                                "market_type": market_type,
                            },
                        )
                    if last_price > 0 and (
                        self.dry_run or not _exchange_handles_sltp
                    ):
                        self.close_position(
                            exchange, pos, "data_feed_failure", last_price
                        )
                    continue
            if not price:
                continue
            price = float(price)

            if self.dry_run and market_type == "futures":
                from core.fill_reality import liquidation_buffer_breach

                mmr = self._maintenance_margin_rate(exchange, pos)
                margin = liquidation_buffer_breach(
                    pos.side,
                    float(pos.entry_price),
                    price,
                    float(pos.leverage or 1),
                    mmr=mmr,
                    buffer_frac=0.0,
                )
                pos._paper_liquidation_price = float(margin["liq_px"])
                if margin["crossed"]:
                    logger.error(
                        f"[Orders] PAPER LIQUIDATION: {pos.symbol} "
                        f"{pos.side.upper()} mark={price:.6g} "
                        f"liq={float(margin['liq_px']):.6g} mmr={mmr:.4%}"
                    )
                    self.close_position(
                        exchange,
                        pos,
                        "liquidation",
                        float(margin["liq_px"]),
                    )
                    continue

            # ── C7 (2026-07-08): running intra-trade extremes ──
            # Feeds trades.mfe/mae so DistFitSL fits real excursions
            # instead of exit-price proxies.
            self._update_trade_extremes(pos, price)

            # ── SL reconciliation (2026-06-20) ──
            # Restore exchange-side SL if a prior placement failed and left the
            # position relying only on polled monitoring. No-op in paper / spot
            # and when the SL is already attached.
            self._reconcile_missing_sl(exchange, pos)

            # ── TP reconciliation (audit 2026-06-21 B7) ──
            # Restore an orphaned exchange-side TP (e.g. a venue-side cancel that
            # left _exchange_tp stale). No-op in paper / spot and when a TP is
            # already resting; throttled to once/minute/position.
            self._reconcile_missing_tp(exchange, pos)

            # ── DRY_RUN realism: intrabar wick SL/TP (2026-04-11) ──
            # LIVE exchange-side SL/TP orders trigger on wick price (high/low
            # within the candle). Paper polling `ticker.last` every ~N seconds
            # misses wicks that dipped below SL and recovered — survivorship
            # bias that turned profitable paper into losing live. Consult the
            # last 1m candle: if it touched SL/TP, fire the close at the
            # trigger level (not at `last`) so paper matches what LIVE would
            # have caught.
            if self.dry_run:
                wick_reason, wick_px = self.sim.check_wick_trigger(
                    exchange, pos.symbol, market_type, pos.side,
                    pos.stop_loss, pos.take_profit,
                    entry_ts=getattr(pos, "open_time", None),
                    price_basis=(
                        "mark"
                        if market_type == "futures"
                        and self.enforce_mark_price_triggers
                        else "trade"
                    ),
                )
                if wick_reason == "stop_loss":
                    logger.warning(
                        f"[Orders] WICK STOP: {pos.symbol} {pos.side.upper()} "
                        f"1m wick touched SL={pos.stop_loss:.6g} "
                        f"(ticker last={price:.6g})")
                    self.close_position(
                        exchange, pos, "stop_loss", wick_px)
                    continue
                if wick_reason == "take_profit":
                    logger.info(
                        f"[Orders] WICK TP: {pos.symbol} {pos.side.upper()} "
                        f"1m wick touched TP={pos.take_profit:.6g} "
                        f"(ticker last={price:.6g})")
                    self._close_take_profit(exchange, pos, wick_px)
                    continue

            # ── TSMOM capital-preservation exit policy (Phase 2b) ───────
            # A position opened by the long-only TSMOM signal is HELD until its
            # own daily momentum-flip CLOSE (emitted from the portfolio cycle).
            # Suppress every scalp early-exit below (partial-TP, scalp wall,
            # trailing, breakeven, fixed TP, entry-staleness, age/stale) but KEEP
            # a wide disaster stop: the wick-stop above already enforced
            # pos.stop_loss (the ~8% entry stop) in paper; here we add the
            # all-modes hard-max-loss backstop and a LIVE polled SL trigger that
            # is independent of take_profit (so zeroing TP can't disable the stop,
            # the coupling the exit audit flagged). Then skip the scalp machinery.
            from core.tsmom_signal import is_tsmom_position as _is_tsmom_pos
            if _is_tsmom_pos(pos):
                from config import TSMOM_HARD_MAX_LOSS_PCT as _TSMOM_HML
                _t_net_pnl, _t_net_pct, _ = self._net_pnl_at_price(pos, price)
                if abs(_t_net_pct) >= _TSMOM_HML and _t_net_pnl < 0:
                    logger.error(
                        f"[Orders] TSMOM HARD MAX LOSS: {pos.symbol} "
                        f"{pos.side.upper()} @ {price:.4f} net={_t_net_pct:+.2f}% "
                        f"— disaster close")
                    self.close_position(exchange, pos, "hard_max_loss", price)
                    continue
                if (not self.dry_run and not _exchange_handles_sltp
                        and pos.stop_loss and float(pos.stop_loss) > 0
                        and pos.side == "buy" and price <= float(pos.stop_loss)):
                    logger.warning(
                        f"[Orders] TSMOM STOP LOSS: {pos.symbol} @ {price:.4f} "
                        f"(SL={float(pos.stop_loss):.4f})")
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                continue  # hold to the momentum-flip CLOSE; skip all scalp exits

            # ── DEEP-BREAKOUT lane exit policy (2026-07-11) ──────────────
            # An ACTIVE-PAPER deep_breakout position exits ONLY via its own
            # 2.2xATR SL / 3R TP (the wick check above in paper; exchange
            # conditionals in live) or the lane's 126-bar max-hold close
            # (core/deep_breakout_lane.py). Suppress the scalp machinery
            # below (partial-TP, trailing, breakeven, entry-staleness,
            # age/stale, the 3% hard-max-loss) which would clip the
            # researched 3R geometry — but KEEP the charter §2 8%
            # Stop-Loss-Guardian backstop and a polled SL/TP fallback so the
            # position is never unprotected. Mirrors the tsmom precedent.
            from core.deep_breakout_lane import (
                is_deep_breakout_position as _is_db_pos,
            )
            if _is_db_pos(pos):
                try:
                    from config import DEEP_BREAKOUT_LANE as _DBL_CFG
                    _db_guard = float(_DBL_CFG.get("hard_max_loss_pct", 8.0))
                except Exception:
                    _db_guard = 8.0
                _db_pnl, _db_pct, _ = self._net_pnl_at_price(pos, price)
                if _db_pnl < 0 and abs(_db_pct) >= _db_guard:
                    logger.error(
                        f"[Orders] DEEP-BREAKOUT GUARDIAN: {pos.symbol} "
                        f"{pos.side.upper()} @ {price:.4f} net={_db_pct:+.2f}% "
                        f"— charter §2 {_db_guard:g}% backstop close")
                    self.close_position(exchange, pos, "hard_max_loss", price)
                    continue
                if not _exchange_handles_sltp:
                    _db_sl = float(pos.stop_loss or 0)
                    _db_tp = float(pos.take_profit or 0)
                    if _db_sl > 0 and (
                            (pos.side == "buy" and price <= _db_sl)
                            or (pos.side == "sell" and price >= _db_sl)):
                        logger.warning(
                            f"[Orders] DEEP-BREAKOUT STOP LOSS: {pos.symbol} "
                            f"{pos.side.upper()} @ {price:.4f} (SL={_db_sl:.6g})")
                        self.close_position(exchange, pos, "stop_loss", price)
                        continue
                    if _db_tp > 0 and self._target_traded_through(pos, price):
                        logger.info(
                            f"[Orders] DEEP-BREAKOUT TAKE PROFIT: {pos.symbol} "
                            f"{pos.side.upper()} @ {price:.4f} (TP={_db_tp:.6g})")
                        self._close_take_profit(exchange, pos, price)
                        continue
                continue  # hold to SL/TP/max-hold; skip scalp early-exits

            # ── B6 (audit 2026-06-21): PLANNED-TP-FIRST (flag-gated) ──────────
            # When near_target_exit is ON, the configured/planned TP is the FIRST
            # profit authority so a winner that has reached target is not pre-empted
            # by partial-TP / trailing / early-BE and clipped below it. Mirrors the
            # late TP check's guards (tp>0, not exchange-held) and reuses the
            # 'take_profit' exit_reason. Flag OFF => this block is skipped entirely,
            # so the dispatch order is byte-identical to today (the late TP check
            # still owns it). Only the TP half is hoisted — SL stays in its later
            # position so trailing/early-BE can still advance it first.
            if RISK.get("near_target_exit_enabled", False):
                _tp_ptf = pos.take_profit
                _sl_ptf = pos.stop_loss
                if (
                    _sl_ptf
                    and float(_sl_ptf) > 0
                    and _tp_ptf
                    and float(_tp_ptf) > 0
                    and not _exchange_handles_sltp
                ):
                    _hit = self._target_traded_through(pos, price)
                    if _hit:
                        _, _ntp_ptf, _ = self._net_pnl_at_price(pos, price)
                        logger.info(
                            f"[Orders] TAKE PROFIT (planned-first): {pos.symbol} "
                            f"{pos.side.upper()} @ {price:.4f} "
                            f"(TP={float(_tp_ptf):.4f}) net={_ntp_ptf:+.2f}%")
                        self._close_take_profit(exchange, pos, price)
                        continue

            # ── PARTIAL TAKE PROFIT ──
            try:
                from config import PARTIAL_TP
                should_fire, take_sz, _level = _should_fire_partial_tp(
                    pos, price, PARTIAL_TP)
                if should_fire:
                    take_at = PARTIAL_TP.get("first_take_at_pct", 0.5)
                    logger.info(
                        f"[Orders] PARTIAL TP: {pos.symbol} {pos.side.upper()} "
                        f"@ {price:.4f} ({take_at:.0%} of TP)")
                    self.partial_close_position(
                        exchange, pos, take_sz, "partial_tp", price)
            except ImportError:
                pass

            # ── SCALP TIME WALL (v4: hard 60-min close) ──────────────
            try:
                from config import SCALP_MODE as _SM_tw
            except ImportError:
                _SM_tw = {}
            if _SM_tw.get("enabled", False):
                _is_scalp = getattr(pos, "_scalp", False) or (
                    hasattr(pos, "tp_pct") and pos.tp_pct and abs(float(pos.tp_pct) - _SM_tw.get("tp_pct", 1.3)) < 0.5
                )
                if _is_scalp:
                    _tw_min = _SM_tw.get("time_wall_min", 60)
                    _pos_age_min = _position_age_minutes(
                        getattr(pos, "open_time", None)
                    )
                    # ACCURACY band (2026-07-10 leak fix): band positions
                    # defer ALL scalp time exits inside max_hold_hours so
                    # first-touch SL/TP governs. False when the flag is off
                    # (byte-identical) or past the horizon.
                    _band_hold_scalp = _accuracy_band_hold_active(
                        pos, _pos_age_min / 60.0)
                    if _band_hold_scalp:
                        logger.debug(
                            f"[Orders] ACCURACY band hold: {pos.symbol} "
                            f"age={_pos_age_min:.0f}m — scalp time exits deferred")
                    if _pos_age_min >= _tw_min and not _band_hold_scalp:
                        logger.warning(
                            f"[Orders] SCALP_TIME_WALL: {pos.symbol} age={_pos_age_min:.0f}m >= {_tw_min}m — force-closing")
                        self.close_position(exchange, pos, "scalp_time_wall")
                        continue

                    # Scalp stale close: flat positions at 45 min
                    _stale_min = _SM_tw.get("stale_close_min", 45)
                    _stale_profit = _SM_tw.get("stale_min_profit", 0.3)
                    if _pos_age_min >= _stale_min and not _band_hold_scalp:
                        # Live fee-aware net. pos.pnl_pct is None on an OPEN
                        # position (only set at close, position_tracker.py:248),
                        # so the old read made `0.0 < stale_profit` ALWAYS true
                        # and force-closed EVERY aged scalp — winners included
                        # (audit 2026-06-21 H3). Use the same fee-aware net the
                        # AGE block uses and spare genuinely-profitable positions.
                        _stale_net_pnl, _net_pct, _ = self._net_pnl_at_price(
                            pos, price)
                        if _stale_net_pnl <= 0 or _net_pct < _stale_profit:
                            logger.info(
                                f"[Orders] SCALP_STALE: {pos.symbol} "
                                f"age={_pos_age_min:.0f}m net={_net_pct:+.2f}% "
                                f"pnl=${_stale_net_pnl:+.2f} < {_stale_profit}% — closing")
                            self.close_position(exchange, pos, "scalp_stale_close")
                            continue

            # ── TRAILING STOP ─────────────────────────────────────────
            # Skip trailing for scalp trades (v4: trailing clips winners at 0.67R)
            # Skip trailing for ACCURACY band (2026-07-28): audited geometry is
            # pure first-touch SL/TP; trailing activation (1.2%) can still clip
            # a gapped winner past TP before the late TP check, or convert a
            # near-TP into trailing_stop instead of take_profit.
            try:
                from config import SCALP_MODE as _SM_trail
            except ImportError:
                _SM_trail = {}
            _skip_trail_for_scalp = False
            if _SM_trail.get("enabled", False) and not _SM_trail.get("trailing_enabled", True):
                _is_scalp_trail = getattr(pos, "_scalp", False) or (
                    hasattr(pos, "tp_pct") and pos.tp_pct and abs(float(pos.tp_pct) - _SM_trail.get("tp_pct", 1.3)) < 0.5
                )
                if _is_scalp_trail:
                    _skip_trail_for_scalp = True
            if _is_accuracy_band_position(pos):
                _skip_trail_for_scalp = True

            if _skip_trail_for_scalp:
                updated_sl = pos.stop_loss
                should_trail = False
                trail_reason = "scalp_trail_skipped"
            else:
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
            # AccBand first-touch (2026-07-29): BE ratchets stop into the TP
            # path and turns near-TP pullbacks into scratch SL exits — same
            # leak class as trailing. Skip for band positions.
            if not _is_accuracy_band_position(pos):
                effective_sl = self._early_breakeven_move(
                    pos, price, effective_sl
                )

            # Persist trailing advance so it survives restarts
            # 2026-04-19 (Fix A2): when the exchange holds SL, also re-place
            # it. Previously the tracker advanced but the exchange kept
            # running the original (wider) SL → trailing was a ghost in live.
            sl_advanced = False
            if pos.side == "buy" and effective_sl > pos.stop_loss:
                pos.stop_loss = effective_sl
                sl_advanced = True
                with self.tracker._lock:
                    self.tracker._save()
            elif pos.side == "sell" and effective_sl < pos.stop_loss:
                pos.stop_loss = effective_sl
                sl_advanced = True
                with self.tracker._lock:
                    self.tracker._save()

            if (sl_advanced
                    and not self.dry_run
                    and getattr(pos, "_exchange_sl", False)
                    and pos.market_type == "futures"):
                try:
                    self._replace_exchange_sl(exchange, pos)
                except Exception as e:
                    logger.warning(
                        f"[Orders] Trailing SL re-place failed for "
                        f"{pos.symbol}: {str(e)[:120]}")

            # ── HARD MAX LOSS GATE — absolute circuit breaker ──
            # 2026-04-12: replaces ANTI-LOSS gate which WIDENED SL on MCP
            # advice, turning -3% losses into -16% catastrophes. No override,
            # no MCP consultation, no exceptions. This is the last line of
            # defense — if price moves 3% against you, you're OUT.
            net_pnl, net_pct, be = self._net_pnl_at_price(pos, price)
            if abs(net_pct) >= 3.0 and net_pnl < 0:
                logger.error(
                    f"[Orders] HARD MAX LOSS: {pos.symbol} {pos.side.upper()} "
                    f"@ {price:.4f} net={net_pct:+.2f}% — forced close, no override")
                self.close_position(exchange, pos, "hard_max_loss", price)
                continue

            # 2026-04-20: Guard against garbage SL/TP values (0.0, None, NaN).
            # Root cause of ALGO naked-short cascade — a position with
            # take_profit=0.0 fires TAKE_PROFIT on the first tick because
            # any positive price >= 0. Skip the SL/TP trigger *only* when
            # invalid, but let age-limit + hard-max-loss enforcement continue
            # below so an unprotected position isn't immortal.
            # 2026-07-27: the two triggers are gated INDEPENDENTLY. A literal
            # take_profit=0.0 is a "no TP" SENTINEL (bot_engine.py:4385 tsmom
            # entries; position_tracker.py:748 manual/reconcile imports), not
            # corruption — conjoining the gates let that sentinel disable the
            # position's real stop, leaving it on the 3% hard-max-loss gate
            # alone. TP stays gated on _tp_ok, so the cascade guard is intact.
            _tp_ok = pos.take_profit is not None and float(pos.take_profit) > 0
            _sl_ok = effective_sl is not None and float(effective_sl) > 0
            if not _sl_ok:
                logger.warning(
                    f"[Orders] Invalid SL for {pos.symbol} "
                    f"(SL={effective_sl}, TP={pos.take_profit}) — "
                    f"skipping SL/TP trigger this cycle (age/hard-loss still active)")
            elif not _tp_ok:
                logger.debug(
                    f"[Orders] No take-profit for {pos.symbol} "
                    f"(TP={pos.take_profit}) — TP trigger inert, stop-loss active")

            # Skip direct SL/TP price-trigger checks when the exchange
            # holds both conditionals — the exchange fires them on its own.
            # All OTHER monitoring (trailing, age limit, hard max loss,
            # entry invalidation) still runs above and below this block.
            if _sl_ok and not _exchange_handles_sltp and pos.side == "buy":
                if price <= effective_sl:
                    # 2026-04-12: ANTI-LOSS gate REMOVED. SL hit = close.
                    # No widening, no MCP hold consultation. Discipline > hope.
                    logger.warning(
                        f"[Orders] STOP LOSS: {pos.symbol} "
                        f"@ {price:.4f} (SL={effective_sl:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                elif _tp_ok and self._target_traded_through(pos, price):
                    # 2026-04-16: MCP TP override REMOVED — always close at TP.
                    # The old "MCP says RIDE" gate collapsed R:R from 2.5:1 to 1.12:1
                    # by letting winning positions ride past TP, only to retrace and
                    # exit at trailing SL for a fraction of the planned profit.
                    _, net_pct, _ = self._net_pnl_at_price(pos, price)
                    logger.info(
                        f"[Orders] TAKE PROFIT: {pos.symbol} "
                        f"@ {price:.4f} (TP={pos.take_profit:.4f}) net={net_pct:+.2f}%"
                    )
                    self._close_take_profit(exchange, pos, price)
                    continue

                # 2026-04-16 (post-audit): Proactive MCP TP-at-+2% REMOVED.
                # Together with the earlier TP override, this gate was exiting
                # winners at +2% (halfway to planned TP), collapsing realized
                # R:R to 0.74:1. Deterministic TP at pos.take_profit is the
                # sole authority for profit-taking; trailing stop handles the
                # retrace case, and MCP advice now only applies to exits below
                # the planned TP line (loss-cut / breakeven).

            elif _sl_ok and not _exchange_handles_sltp and pos.side == "sell":
                if price >= effective_sl:
                    # 2026-04-12: ANTI-LOSS gate REMOVED for shorts too.
                    logger.warning(
                        f"[Orders] STOP LOSS (short): {pos.symbol} "
                        f"@ {price:.4f} (SL={effective_sl:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                elif _tp_ok and self._target_traded_through(pos, price):
                    # 2026-04-16: MCP TP override REMOVED for shorts too.
                    _, net_pct, _ = self._net_pnl_at_price(pos, price)
                    logger.info(
                        f"[Orders] TAKE PROFIT (short): {pos.symbol} "
                        f"@ {price:.4f} (TP={pos.take_profit:.4f}) net={net_pct:+.2f}%"
                    )
                    self._close_take_profit(exchange, pos, price)
                    continue

                # 2026-04-16 (post-audit): Proactive MCP TP-at-+2% REMOVED
                # for shorts too. Deterministic TP is sole authority.

            # ── ENTRY-STALENESS EXIT (2026-05-01 Tier 1.1) ──────────────
            # Re-check the directional hypothesis. If the 4h EMA20/50 has
            # flipped against the position (with margin), the entry rationale
            # is invalid — close at market BEFORE waiting for SL or AGE_LOSS.
            #
            # Why 4h EMA? It's the SIDE-determining signal in
            # mcp_brain._score_coin (line 1976: side='buy' if ema20 > ema50_4h).
            # If that flips, the trade has no hypothesis left.
            #
            # Why a margin (0.15%)? Avoids whipsaw on tight crosses. EMAs
            # touching the cross-line by 0.05% isn't a regime change.
            #
            # Why min_hold_minutes? Entries that fired right before a brief
            # 4h cross deserve the chance to resolve. 30min grace.
            #
            # Skips on transient errors (don't close on a fetch failure).
            try:
                from config import ENTRY_STALENESS_EXIT as _ES
            except ImportError:
                _ES = {"enabled": False}
            age_minutes_for_staleness = (pos.duration_minutes or 0)
            # AccBand first-touch: do not invalidate on 4h EMA flip inside the
            # band hold horizon — audited geometry is pure SL/TP.
            _band_hold_es = _accuracy_band_hold_active(
                pos, float(age_minutes_for_staleness) / 60.0
            )
            if (_ES.get("enabled", True)
                    and not _band_hold_es
                    and age_minutes_for_staleness >= int(_ES.get("min_hold_minutes", 30))
                    and pos.market_type == "futures"
                    and self.mcp_brain is not None):
                try:
                    # 2026-06-11 gap-flip semantics: pass the entry epoch so
                    # born-invalid positions are exempt. Knob off (or missing
                    # open_time) → entry_ts=0 → checker preserves OLD behavior.
                    _es_entry_ts = (
                        float(getattr(pos, "open_time", 0.0) or 0.0)
                        if _ES.get("require_flip_after_entry", True) else 0.0)
                    invalidated, reason = self.mcp_brain.is_entry_invalidated(
                        symbol=pos.symbol,
                        side=pos.side,
                        gap_pct=float(_ES.get("invalidation_gap_pct", 0.15)),
                        entry_ts=_es_entry_ts,
                    )
                    if invalidated:
                        logger.warning(
                            f"[Orders] ENTRY_STALE: {pos.symbol} {pos.side} "
                            f"opened {age_minutes_for_staleness:.0f}m ago, "
                            f"4h EMA hypothesis invalidated ({reason}) — closing")
                        self.close_position(
                            exchange, pos, "entry_invalidated", price)
                        continue
                    elif "born-invalid" in reason:
                        logger.debug(
                            f"[Orders] ENTRY_STALE exempt: {pos.symbol} "
                            f"{pos.side} ({reason})")
                except Exception as _ese:
                    logger.debug(
                        f"[Orders] entry-staleness check skipped ({_ese})")

            # ── POSITION AGE LIMIT ENFORCEMENT ──────────────────────────
            # Three rules, checked in order:
            #   1. AGE_LIMIT: open >= max_position_age_hours AND losing → force-close
            #   2. AGE_LOSS:  open >= max_loss_age_hours      AND net <= -max_loss_age_pct
            #                 (Phase 13.1, 2026-04-28). Closes the 2-4h hold-time bleed:
            #                 warehouse 30d data showed 55 trades / -$17.39 / 35% WR in
            #                 that bucket. Cuts losers before they slide further.
            #   3. STALE:     open >= max_stale_hours          AND PnL ≈ 0
            age_hours = (pos.duration_minutes or 0) / 60.0
            max_age_h = RISK.get("max_position_age_hours", 24)
            max_loss_age_h = RISK.get("max_loss_age_hours", 3.0)
            max_loss_age_pct = RISK.get("max_loss_age_pct", 0.5)
            max_stale_h = RISK.get("max_stale_hours", 4)
            # Patch #2 (2026-05-19): refit-driven per-tier AGE_LIMIT override.
            # scripts/refit_age_cutoffs.py writes data/models/age_cutoffs.json
            # ONLY when the 45d-fit / 15d-holdout split shows strict
            # improvement on the holdout. If the JSON is absent, we keep the
            # RISK config global (the "current cutoff" the refit would have
            # had to beat). If present, we resolve cutoff-by-tier via
            # pos.confidence (= mcp_score / 100, persisted at entry).
            _age_cutoffs = load_age_cutoffs()
            if _age_cutoffs and getattr(pos, "confidence", 0.0) > 0:
                _score = float(pos.confidence) * 100.0
                if _score >= 85 and "AGGRESSIVE" in _age_cutoffs:
                    max_age_h = float(_age_cutoffs["AGGRESSIVE"]) / 60.0
                elif _score >= 75 and "CONVICTION" in _age_cutoffs:
                    max_age_h = float(_age_cutoffs["CONVICTION"]) / 60.0
                elif _score >= 65 and "STANDARD" in _age_cutoffs:
                    max_age_h = float(_age_cutoffs["STANDARD"]) / 60.0
            net_pnl, net_pct, _ = self._net_pnl_at_price(pos, price)

            # Phase 41 (2026-05-10): AGE_LOSS DISABLED, AGE_LIMIT tightened.
            # 6 AGE_LOSS trades all-time, 0% WR, -$1.78. Rule realizes paper
            # losses (-0.5% to -3%) at 3h hold before SL backstop (-2.5%) gets
            # to fire OR position recovers. Math: recovery rate >13% makes
            # "no AGE_LOSS" net positive (avoids realizing -1.5% avg). With
            # 46% overall WR, recovery rate on stale losers is plausibly
            # 20-30% — trust the SL instead.
            # AGE_LIMIT (24h backstop) tightened: only fires when loss
            # >= 1.5% (was: any loss). Tiny -0.1% age-outs realized losses
            # on positions that just needed more time.
            AGE_LOSS_ENABLED = False
            AGE_LIMIT_MIN_LOSS_PCT = 1.5

            # 2026-05-19 Patch #3 — soft-close amplification wrapper.
            # `_try_soft_close` defers STALE / AGE_LIMIT / AGE_LOSS when
            # proximity to mcp_take_profit ≥ threshold; otherwise it
            # invokes the close immediately. The `_close` closure binds
            # the current exchange / pos / price so the helper only
            # needs the reason label.
            def _close(reason_label, _ex=exchange, _p=pos, _px=price):
                self.close_position(_ex, _p, reason_label, _px)
                return True

            # Populate the dynamic attrs the proximity scorer reads.
            # These match Patch #3 plan §5.5 — pos object is mutated so
            # `score_take_profit_proximity` has current mark + uPnL.
            pos.current_px = price
            pos.unrealized_pnl_pct = net_pct / 100.0

            # ACCURACY band (2026-07-10 leak fix): band positions defer the
            # STALE / AGE_LIMIT / AGE_LOSS time exits inside max_hold_hours
            # (default 72h) so first-touch SL/TP governs — the geometry the
            # 60-65% WR band was audited on. False when the flag is off
            # (byte-identical) or past the horizon (zombie protection).
            _band_hold = _accuracy_band_hold_active(pos, age_hours)
            # 2026-08-03 owner-directed STALE fix: tier-geometry positions
            # (planned R:R >= 1) get the same time-exit deferral the band
            # earned on 2026-07-10 — first-touch SL/TP governs inside the
            # hold horizon. Measured trigger: 4/10 post-fix closes were
            # STALE with 0 full TPs; the wide targets never had time.
            _tier_hold = _tier_geometry_hold_active(pos, age_hours)
            _hold = _band_hold or _tier_hold

            # Blueprint Phase 1: hard max-hold force-flat past family horizon.
            # Band/tier: ACCURACY_TARGET_MODE / TIER max_hold_hours.
            # Standard: RISK.max_position_age_hours. Bypasses _try_soft_close.
            try:
                _force_h = max_hold_force_flat_hours(pos, max_age_h)
                if age_hours >= _force_h:
                    logger.warning(
                        f"[Orders] max_hold_force_flat: {pos.symbol} {pos.side} "
                        f"age={age_hours:.1f}h >= {_force_h}h — hard close"
                    )
                    self.close_position(exchange, pos, "max_hold_force_flat", price)
                    continue
            except Exception as _mh_err:
                logger.debug(f"[Orders] max_hold_force_flat skip: {_mh_err}")

            if _hold and age_hours >= min(max_age_h, max_stale_h):
                logger.debug(
                    f"[Orders] {'ACCURACY band' if _band_hold else 'tier-geometry'} "
                    f"hold: {pos.symbol} age={age_hours:.1f}h — STALE/AGE time "
                    f"exits deferred until SL/TP or horizon")

            if (not _hold
                    and age_hours >= max_age_h and net_pnl < 0
                    and abs(net_pct) >= AGE_LIMIT_MIN_LOSS_PCT):
                logger.warning(
                    f"[Orders] AGE_LIMIT: {pos.symbol} {pos.side} "
                    f"open {age_hours:.1f}h (limit {max_age_h}h), "
                    f"net={net_pct:+.2f}% — force-closing losing position")
                _try_soft_close(self, pos, "AGE_LIMIT", proceed_fn=_close)
                continue
            elif (AGE_LOSS_ENABLED and not _hold
                    and age_hours >= max_loss_age_h
                    and net_pct <= -max_loss_age_pct):
                logger.warning(
                    f"[Orders] AGE_LOSS: {pos.symbol} {pos.side} "
                    f"open {age_hours:.1f}h (loss-age limit {max_loss_age_h}h), "
                    f"net={net_pct:+.2f}% — cutting before mid-hold bleed worsens")
                _try_soft_close(self, pos, "AGE_LOSS", proceed_fn=_close)
                continue
            elif (not _hold
                    and age_hours >= max_stale_h and -0.3 <= net_pct <= 0.0):
                # 2026-05-28: only STALE-close flat/losing positions.
                # Profitable trades (net_pct > 0) should run to TP, not get
                # killed as "stale". The old range [-0.3, +0.3] was prematurely
                # closing winners (XRP +0.28%, SOL +0.05% killed today).
                logger.warning(
                    f"[Orders] STALE: {pos.symbol} {pos.side} "
                    f"open {age_hours:.1f}h (stale limit {max_stale_h}h), "
                    f"net={net_pct:+.2f}% — flat/slight loss, freeing capital")
                _try_soft_close(self, pos, "STALE", proceed_fn=_close)
                continue

