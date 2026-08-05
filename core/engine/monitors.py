"""
core/engine/monitors.py — BotEngine _MonitorsMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import (
    _STRUCTURAL_ERRORS,
    _deployable_total,
    _effective_tp_threshold,
    _live_entry_clock_drift_rejection,
)


class _MonitorsMixin:
    def _check_all_sl_tp(self):
        """Check SL/TP for all open positions — parallel across exchanges."""
        # Maker-first (2026-07-11): pending virtual intents are entries WITHOUT
        # positions — on an empty book the old zero-open early-return starved
        # the resolver forever (INJ/ARB intents hung 2h past their 45s timeout;
        # the entries were silently lost). Run the monitor whenever intents
        # are pending, even with zero open positions.
        if (self.tracker.count_open() == 0
                and not getattr(self.order_mgr, "_pending_maker", None)):
            return

        def _check_one(ex_name, exchange, mtype):
            try:
                self.order_mgr.check_sl_tp(exchange, mtype)
            except _STRUCTURAL_ERRORS as e:
                # 2026-07-26: a consumer/producer skew (order_manager
                # importing a name funding_history does not define) killed
                # the entire paper SL/TP monitor tick and hid at DEBUG,
                # which also disarmed the f.result() rail below — a bare
                # `except Exception` means the future never holds one.
                logger.error(f"[Engine] SL/TP check {ex_name}/{mtype} "
                             f"STRUCTURAL {type(e).__name__}: {e}")
                raise
            except Exception as e:
                # Operational (venue timeout / transient API error): stay
                # isolated and quiet so one venue cannot abort the others.
                logger.debug(f"[Engine] SL/TP check {ex_name}/{mtype}: {e}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = []
            for ex_name, exchange in self.active_exchanges.items():
                for mtype in ("spot", "futures"):
                    futs.append(pool.submit(_check_one, ex_name, exchange, mtype))
            for f in as_completed(futs):
                f.result()  # Propagate any unhandled exception

    def _sltp_monitor_loop(self, stop_event: threading.Event):
        """Dedicated thread: monitors SL/TP every 10 seconds, never blocked by scans."""
        while not stop_event.is_set():
            try:
                self._check_all_sl_tp()
            except _STRUCTURAL_ERRORS as e:
                # Loud, but NEVER re-raised here: this is the thread top
                # level, so raising would kill the 10s monitor for the rest
                # of the process lifetime.
                logger.error(f"[Engine] SL/TP monitor STRUCTURAL "
                             f"{type(e).__name__}: {e}")
            except Exception as e:
                logger.debug(f"[Engine] SL/TP monitor: {e}")
            stop_event.wait(10)  # Check every 10 seconds

    def _heartbeat_portfolio_es(self):
        """Live ES snapshot of the open book for the dashboard.
        Fail-quiet → None (2026-06-11 Portfolio ES soft-cap)."""
        try:
            from config import ES_RISK as _cfg
            if not _cfg.get("enabled", False):
                return None
            _pr = getattr(self, "_portfolio_risk", None)
            if _pr is None:
                from core.portfolio_risk import PortfolioRisk
                _pr = PortfolioRisk(self.active_exchanges, _cfg)
                self._portfolio_risk = _pr
            _eq = _deployable_total(self._balances)
            _pr.evaluate_book(self.tracker.get_open(), _eq)
            return _pr.last_snapshot()
        except Exception:
            return None

    def _write_heartbeat(self):
        """Write heartbeat file for external monitoring."""
        try:
            import os

            import psutil
            process = psutil.Process(os.getpid())
            mem_mb = process.memory_info().rss / 1024 / 1024
        except Exception:
            mem_mb = 0

        # Find last trade time from tracker
        last_trade_time = None
        try:
            closed = self.tracker.get_closed_positions()
            if closed:
                last_trade_time = max(
                    p.close_time for p in closed if getattr(p, "close_time", None)
                ) if any(getattr(p, "close_time", None) for p in closed) else None
        except Exception:
            pass

        try:
            from config import (
                ENTRY_POLICY as _ENTRY_POLICY,
            )
            from config import (
                MCP_DIRECTIONAL_ECONOMIC_GATE as _ECON_GATE,
            )
            from config import (
                MCP_ENTRY_MIN_SCORE as _ENTRY_FLOOR,
            )
            from config import (
                MODE_PROFILE as _MODE_PROFILE,
            )
            from config import (
                OPERATING_MODE as _OPERATING_MODE,
            )
            from config import (
                PAPER_PROFILE_STARTED_AT as _PAPER_PROFILE_STARTED_AT,
            )
            from config import (
                PAPER_TRADING_PROFILE as _PAPER_TRADING_PROFILE,
            )
            from config import (
                SIGNAL_SOURCE as _SIGNAL_SOURCE,
            )
        except Exception:
            _ENTRY_POLICY = "UNKNOWN"
            _ECON_GATE = {}
            _ENTRY_FLOOR = None
            _MODE_PROFILE = None
            _OPERATING_MODE = "UNKNOWN"
            _PAPER_PROFILE_STARTED_AT = ""
            _PAPER_TRADING_PROFILE = "UNKNOWN"
            _SIGNAL_SOURCE = "UNKNOWN"

        heartbeat = {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "boot_id": f"{int(self._start_time)}-{os.getpid()}",
            "uptime_seconds": int(time.time() - self._start_time),
            "cycle_count": self._cycle,
            "last_successful_cycle": self._cycle,
            "operating_mode": _OPERATING_MODE,
            "dry_run": bool(DRY_RUN),
            "entry_policy": _ENTRY_POLICY,
            "signal_source": _SIGNAL_SOURCE,
            "paper_trading_profile": _PAPER_TRADING_PROFILE,
            "paper_profile_started_at": _PAPER_PROFILE_STARTED_AT or None,
            "effective_config": {
                "econ_gate_mode": (
                    _ECON_GATE.get("mode") if isinstance(_ECON_GATE, dict) else None
                ),
                "mcp_entry_min_score": _ENTRY_FLOOR,
                "paper_trading_profile": _PAPER_TRADING_PROFILE,
            },
            "risk_profile": (
                {
                    "max_open_positions": _MODE_PROFILE.max_open_positions,
                    "risk_per_trade_pct": _MODE_PROFILE.risk_per_trade_pct,
                    "aggregate_open_risk_pct": _MODE_PROFILE.aggregate_open_risk_pct,
                    "gross_exposure_pct": _MODE_PROFILE.gross_exposure_pct,
                    "max_leverage": _MODE_PROFILE.max_leverage,
                }
                if _MODE_PROFILE is not None
                else None
            ),
            "open_positions": self.tracker.count_open(),
            "per_exchange_positions": {
                ex_name: self.tracker.count_open(exchange=ex.name)
                for ex_name, ex in self.active_exchanges.items()
            },
            # 2026-06-11: live portfolio ES vs budget (None = unavailable)
            "portfolio_es": self._heartbeat_portfolio_es(),
            "last_trade_time": last_trade_time,
            "active_exchanges": list(self.active_exchanges.keys()),
            "halted_exchanges": list(self._exchange_halted),
            "api_latency_ms": {k: round(v, 0) for k, v in self._api_latency.items()},
            # C8 (2026-07-08): per-venue clock offset (ms, NTP-style sample);
            # null = venue lacks fetch_time or the sample failed this cycle.
            "clock_drift_ms": {
                k: (round(v, 1) if isinstance(v, (int, float)) else None)
                for k, v in getattr(self, "_clock_drift_ms", {}).items()
            },
            "is_halted": self.risk.is_halted,
            "halt_reason": self.risk.halt_reason if self.risk.is_halted else None,
            "daily_pnl": getattr(self.risk, "_daily_pnl", 0),
            "spot_manager_active": self.spot_manager is not None,
            "capital_allocator_active": self.capital_allocator is not None,
            "memory_mb": round(mem_mb, 1),
            "realtime_streams": (
                self.realtime_streams.health()
                if self.realtime_streams is not None
                else {}
            ),
        }
        try:
            atomic_write_json(Path("data/heartbeat.json"), heartbeat, indent=2)
        except Exception as exc:
            logger.warning(f"[Health] heartbeat write failed: {exc}")

    def _try_reconnect(self, ex_name: str, exchange) -> None:
        """Rebuild ccxt client + clear halt if rebuild succeeds.
        Used both at first-halt transition and during periodic retries."""
        try:
            exchange._init_exchange()
            if getattr(exchange, '_connected', False):
                logger.info(f"[Health] {ex_name} reconnected")
                self._consecutive_api_fails[ex_name] = 0
                self._exchange_halted.discard(ex_name)
                if ex_name not in self.active_exchanges:
                    self.active_exchanges[ex_name] = exchange
                    logger.info(
                        f"[Health] {ex_name} re-added to active_exchanges")
                try:
                    self.notifier.alert(
                        f"{ex_name.upper()} reconnected successfully after outage.")
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[Health] {ex_name} reconnect attempt failed: {e}")

    def _retry_inactive_exchanges(self) -> None:
        """Throttled reconnect for venues that never authenticated at startup.

        _check_exchange_health iterates only active_exchanges, so a venue whose
        very first _init_exchange failed (e.g. bitget 40018 while this IP was
        off the API-key allowlist) was unreachable by _try_reconnect until a
        full restart — measured 2026-08-03: engine-side bitget stayed inert
        for the whole outage and would never have self-healed. Sweep the
        inactive set at most once per venue per 15 minutes; _try_reconnect
        already re-adds a recovered venue to active_exchanges.
        """
        if not hasattr(self, "_inactive_reconnect_at"):
            self._inactive_reconnect_at: dict[str, float] = {}
        now = time.time()
        for ex_name, exchange in list(self.exchanges.items()):
            if ex_name in self.active_exchanges:
                continue
            if ex_name in self._exchange_halted:
                continue  # halted venues already have their own retry path
            if now - self._inactive_reconnect_at.get(ex_name, 0.0) < 900.0:
                continue
            self._inactive_reconnect_at[ex_name] = now
            self._try_reconnect(ex_name, exchange)

    def _check_exchange_health(self):
        """Verify exchanges are reachable + measure latency.
        Auto-halt trading on exchange after 3 consecutive failures.
        Auto-resume when exchange recovers."""
        self._last_health_check = time.time()
        self._retry_inactive_exchanges()
        for ex_name, exchange in list(self.active_exchanges.items()):
            try:
                t0 = time.time()
                ticker = exchange.fetch_ticker("BTC/USDT", "spot")
                latency_ms = (time.time() - t0) * 1000
                self._api_latency[ex_name] = latency_ms
                # Accept any price field — some exchanges populate `close`
                # or bid/ask but not `last` on every ticker
                price = None
                if ticker:
                    price = (ticker.get("last") or ticker.get("close")
                             or ticker.get("bid") or ticker.get("ask"))
                if price:
                    self._consecutive_api_fails.get(ex_name, 0)
                    self._consecutive_api_fails[ex_name] = 0
                    # C8 (2026-07-08): sample venue clock drift each healthy
                    # cycle. None = venue lacks fetch_time / call failed —
                    # never a health failure. Sustained drift (2 consecutive
                    # samples over threshold) warns, mirroring the
                    # high-latency counter pattern; the health_watchdog
                    # additionally edge-alerts off the stored map.
                    if not hasattr(self, "_clock_drift_ms"):
                        self._clock_drift_ms = {}
                    _drift = sample_clock_drift_ms(exchange)
                    self._clock_drift_ms[ex_name] = _drift
                    _drift_key = f"{ex_name}_clock_drift"
                    try:
                        from config import CLOCK_DRIFT_ALERT_MS as _drift_thr
                    except ImportError:
                        _drift_thr = 500
                    if _drift is not None and abs(_drift) > _drift_thr:
                        _dcnt = self._consecutive_api_fails.get(_drift_key, 0) + 1
                        self._consecutive_api_fails[_drift_key] = _dcnt
                        if _dcnt >= 2:
                            logger.warning(
                                f"[Health] {ex_name} CLOCK DRIFT "
                                f"{_drift:+.0f}ms (>{_drift_thr}ms) for "
                                f"{_dcnt} consecutive samples — signed "
                                f"requests at risk; check NTP/w32tm sync")
                    else:
                        self._consecutive_api_fails.pop(_drift_key, None)
                    # Auto-resume if previously halted
                    if ex_name in self._exchange_halted:
                        self._exchange_halted.discard(ex_name)
                        logger.info(
                            f"[Health] {ex_name} RECOVERED — resuming trading "
                            f"(latency={latency_ms:.0f}ms)")
                        self.notifier.alert(
                            f"{ex_name.upper()} recovered after outage — "
                            f"trading resumed (latency={latency_ms:.0f}ms)")
                    elif latency_ms > 5000:
                        logger.warning(
                            f"[Health] {ex_name}: HIGH LATENCY {latency_ms:.0f}ms")
                        high_lat_key = f"{ex_name}_high_latency"
                        cnt = self._consecutive_api_fails.get(high_lat_key, 0) + 1
                        self._consecutive_api_fails[high_lat_key] = cnt
                        if cnt >= 3:
                            logger.warning(
                                f"[Health] {ex_name} DEGRADED — latency >5s "
                                f"for {cnt} consecutive checks")
                            self.notifier.alert(
                                f"{ex_name.upper()} degraded: latency >5s "
                                f"for {cnt} checks — consider pausing")
                    else:
                        self._consecutive_api_fails.pop(
                            f"{ex_name}_high_latency", None)
                else:
                    keys = list(ticker.keys()) if ticker else []
                    raise Exception(f"Empty ticker (keys={keys[:5]})")
            except Exception as e:
                if not hasattr(self, "_clock_drift_ms"):
                    self._clock_drift_ms = {}
                self._clock_drift_ms[ex_name] = None
                fails = self._consecutive_api_fails.get(ex_name, 0) + 1
                self._consecutive_api_fails[ex_name] = fails
                self._api_latency[ex_name] = -1
                # Area 2 (2026-05-20): first-attempt failure is usually a
                # transient API blip — the existing retry path handles it.
                # Only escalate to WARNING on the 2nd+ failure.
                _level_fn = logger.debug if fails == 1 else logger.warning
                _level_fn(
                    f"[Health] {ex_name} health check FAILED "
                    f"(attempt {fails}): {e}")
                if fails >= 3 and ex_name not in self._exchange_halted:
                    self._exchange_halted.add(ex_name)
                    open_on_ex = self.tracker.count_open(exchange=exchange.name)
                    logger.error(
                        f"[Health] {ex_name} HALTED — {fails} consecutive failures. "
                        f"No new trades will be placed on {ex_name} until recovered.")
                    self.notifier.error(
                        f"EXCHANGE HALTED: {ex_name.upper()} unreachable for "
                        f"{fails} consecutive checks.\n"
                        f"Open positions on {ex_name}: {open_on_ex}\n"
                        f"Trading HALTED on this exchange. "
                        f"Will auto-resume when API recovers.\n"
                        f"Attempting auto-reconnect...")
                    self._try_reconnect(ex_name, exchange)
                # 2026-05-03: prior code only retried once at fails==3.
                # If a stale ccxt session keeps returning empty tickers,
                # the bot would stay halted forever (observed: 519+ fails
                # over 8.5h on a stuck process). Retry every 5 fails so
                # the client gets recycled periodically without a full
                # bot restart.
                elif (
                    ex_name in self._exchange_halted
                    and fails % 5 == 0
                ):
                    logger.info(
                        f"[Health] {ex_name} stale-halt retry "
                        f"(fail #{fails}, every 5 attempts)")
                    self._try_reconnect(ex_name, exchange)
                # 5+ fails: optionally close losing positions (default OFF —
                # blueprint Phase 1: soft outage blocks entries via halt /
                # soft-stale latch; do not flatten on soft staleness).
                if fails >= 5:
                    import os as _os

                    if _os.getenv(
                        "OUTAGE_FLATTEN_LOSERS_ENABLED", "false"
                    ).lower() in ("true", "1", "yes", "on"):
                        try:
                            positions = self.tracker.get_open(exchange=exchange.name)
                            for pos in positions:
                                if getattr(pos, "unrealized_pnl", 0) < 0:
                                    logger.warning(
                                        f"[Health] Force-closing losing position "
                                        f"{pos.symbol} on {ex_name} (5+ API fails)"
                                    )
                                    self.order_mgr.close_position(
                                        exchange, pos, reason="exchange_outage_5_fails"
                                    )
                        except Exception as close_err:
                            logger.error(f"[Health] Force-close error: {close_err}")
                    else:
                        try:
                            from core.soft_stale_latch import set_soft_stale_latch

                            set_soft_stale_latch(
                                reason=f"exchange_outage:{ex_name}",
                                detail={"fails": fails, "exchange": ex_name},
                            )
                        except Exception as _lse:
                            logger.debug(f"[Health] soft-stale latch skip: {_lse}")

    def is_exchange_halted(self, exchange_name: str) -> bool:
        """Check if an exchange is currently halted due to health failures."""
        return exchange_name.lower() in self._exchange_halted

    def _sync_positions(self):
        """Periodically verify tracked LIVE positions still exist on exchange."""
        try:
            imported = self.tracker.sync_with_exchanges(self.active_exchanges)
            if imported:
                self._protect_imported_positions(imported)
        except Exception as e:
            logger.debug(f"[Engine] Position sync: {e}")

    def _replace_exchange_sl(self, exchange, pos, new_sl: float) -> bool:
        """B7-P2 wrapper: serialize this TIGHTEN/BREAKEVEN SL re-place with the
        OrderManager's close/SL-mutation on the same position id, via the ONE
        shared per-id RLock (self.order_mgr._position_lock). Default-OFF; timeout
        backstop so it can never freeze the 30s monitor. RLock-safe: this path
        re-enters om.close_position via the fail-closed _place_exchange_sl_tp."""
        om = self.order_mgr
        if not getattr(om, "_per_pos_lock_enabled", False):
            return self._replace_exchange_sl_impl(exchange, pos, new_sl)
        lk = om._position_lock(pos.id)
        acquired = lk.acquire(timeout=om._pos_lock_timeout)
        if not acquired:
            logger.error(
                f"[Lock] engine _replace_exchange_sl _position_lock TIMEOUT for "
                f"{pos.id} after 5s — proceeding WITHOUT serialization")
        try:
            return self._replace_exchange_sl_impl(exchange, pos, new_sl)
        finally:
            if acquired:
                lk.release()

    def _replace_exchange_sl_impl(self, exchange, pos, new_sl: float) -> bool:
        """Cancel the existing SL conditional order on the exchange and place
        a new one at `new_sl`. Used by TIGHTEN/BREAKEVEN actions.

        Returns True iff the replace actually succeeded — callers should
        only mutate `pos.stop_loss` on True so in-memory SL stays consistent
        with the exchange.

        2026-04-25 (post-audit): pre-flight check added. BREAKEVEN moves
        targeted entry+fees and Bitget rejected the new SL whenever mark
        price had pulled back through that level (code 40917 "Stop price
        for long positions please < mark price"). Cancel-then-place had
        already removed the working SL, so the fail-closed policy market-
        closed otherwise-healthy winning trades. The check now refuses
        replaces that would be rejected, keeping the existing SL intact.

        2026-04-16 (post-audit): Previously these actions only mutated
        `pos.stop_loss` in memory. The exchange kept running the wider SL,
        and because `_exchange_sl=True` silences local SL monitoring, the
        tightened value was never actually enforced anywhere.
        """
        if not pos or not new_sl or new_sl <= 0:
            return False
        if pos.market_type != "futures":
            return False  # spot has no exchange-side SL to replace

        # Pre-flight: refuse SL placements that the exchange would reject
        # for being on the wrong side of current price. For longs the SL
        # must sit BELOW current price; for shorts ABOVE. Otherwise the
        # cancel-then-place cycle below would strip the working SL and
        # the fail-closed policy in _place_exchange_sl_tp would market-
        # close the position. Skip the move when the new level is invalid;
        # the next monitor cycle will try again with a refreshed price.
        try:
            tkr = exchange.fetch_ticker(pos.symbol, pos.market_type)
            last = float((tkr or {}).get("last") or (tkr or {}).get("close") or 0)
        except Exception as e:
            logger.debug(f"[Replace-SL] mark check unavailable for {pos.symbol}: {e}")
            last = 0.0
        if last > 0:
            side = (pos.side or "").lower()
            if side == "buy" and new_sl >= last:
                logger.info(
                    f"[Replace-SL] SKIP {pos.symbol} BUY: target SL {new_sl:.6g} "
                    f">= last {last:.6g} (would be exchange-rejected); keeping current SL")
                return False
            if side == "sell" and new_sl <= last:
                logger.info(
                    f"[Replace-SL] SKIP {pos.symbol} SELL: target SL {new_sl:.6g} "
                    f"<= last {last:.6g} (would be exchange-rejected); keeping current SL")
                return False

        # 2026-05-31 (CRITICAL fix): PAPER must never write to the live venue.
        # A simulated position has no real order to cancel/replace; issuing a
        # real cancel_all_orders + SL placement here was leaking onto live
        # accounts (Bitget 43023 -> fail-closed paper winners; Binance/Bybit
        # accepted -> phantom resting orders). Mirror the open_position gate
        # (order_manager.py:1045): clear the exchange-SL flags so the dry_run
        # check_sl_tp wick-trigger enforces the moved level, and report success
        # so the caller updates pos.stop_loss in-memory only.
        from config import DRY_RUN as _dry_run

        if _dry_run:
            pos._exchange_sl = False
            pos._exchange_tp = False
            return True

        # Cancel existing conditional orders for this symbol. We cancel
        # ALL conditional orders on the symbol because we did not store
        # the specific SL order ID at placement time — the symbol is a
        # narrow enough scope for one position.
        try:
            if hasattr(exchange, "cancel_all_orders"):
                exchange.cancel_all_orders(pos.symbol, pos.market_type)
            else:
                # ccxt-level fallback
                exchange.exchange.cancel_all_orders(pos.symbol)
        except Exception as e:
            logger.warning(
                f"[Replace-SL] cancel_all_orders failed for {pos.symbol}: "
                f"{str(e)[:120]} — proceeding with replacement")

        # Clear flags so a placement failure falls back to local monitoring
        pos._exchange_sl = False
        pos._exchange_tp = False

        # Re-place both SL and TP at current tracker values
        try:
            self.order_mgr._place_exchange_sl_tp(
                exchange, pos, new_sl, pos.take_profit,
                pos.side, pos.symbol, pos.size, pos.market_type)
            return True
        except Exception as e:
            logger.error(
                f"[Replace-SL] re-place failed for {pos.symbol}: {str(e)[:150]}")
            return False

    def _run_dca(self):
        """Run DCA strategy on top coins across all exchanges.
        Gates (in order):
          1. Static BLACKLIST_HARD — never accumulate a repeat loser
          2. AutoMutator dynamic blacklist — post-mortem-driven
          3. MCP Brain — don't DCA-buy coins MCP says to SELL
        BTC macro trend is intentionally NOT checked: DCA is meant to
        accumulate on dips, so bear macro is the *desired* environment.
        """
        from config import BLACKLIST_HARD
        dca = self.pool.get("dca_spot")
        if not dca:
            return
        dca_coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        dyn_bl = (self.auto_mutator.get_effective_blacklist()
                  if self.auto_mutator else set())

        for ex_name, exchange in self.active_exchanges.items():
            for symbol in dca_coins:
                # Normalize to futures key for blacklist check (futures keys
                # are authoritative: repeat losses on futures also gate spot
                # accumulation of the same base asset).
                sym_fut_key = f"{symbol}:USDT"
                if (symbol in BLACKLIST_HARD or sym_fut_key in BLACKLIST_HARD
                    or symbol in dyn_bl or sym_fut_key in dyn_bl):
                    logger.debug(
                        f"[DCA] {symbol}: BLOCKED by blacklist — skipping")
                    continue

                # MCP Brain gate: don't DCA-buy coins MCP says to SELL
                if self.mcp_brain and self.mcp_brain.is_enabled:
                    base = symbol.split("/")[0]
                    mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                    if mcp_dec.get("action") == "SELL":
                        logger.debug(
                            f"[DCA] {symbol}: MCP says SELL — skipping DCA buy")
                        continue
                try:
                    dca.run(exchange, symbol)
                    # 2026-05-03: track today's DCA buys so SpotPortfolioManager
                    # can avoid SELLing the same asset DCA just bought.
                    # Same-day opposing-order risk surfaced by Ruflo strategy review.
                    if not hasattr(self, "_dca_buys_today"):
                        self._dca_buys_today: dict = {}
                    base = symbol.split("/")[0]  # "BTC/USDT" → "BTC"
                    self._dca_buys_today[base] = time.time()
                except Exception as e:
                    logger.debug(f"[DCA] {symbol} on {ex_name}: {e}")

    def _run_rebalance(self):
        """Run portfolio rebalancing once per day."""
        rebal = self.pool.get("rebalance")
        if not rebal:
            return
        for ex_name, exchange in self.active_exchanges.items():
            try:
                rebal.run(exchange)
            except Exception as e:
                logger.debug(f"[Rebalance] {ex_name}: {e}")

    def _run_spot_evaluation(self):
        """Run spot portfolio evaluation cycle (every 30 min).

        2026-05-01: when SPOT_PORTFOLIO['recommendation_only'] is False,
        execute SELL/SCALE_OUT actions. SPOT-PROTECT-V1 emits these only
        on peak-drawdown >= 25% (half) / 40% (full). Recommendation-only
        was the 2026-04-14 learning-first pivot default; flipping to
        execute requires explicit user authorization.
        """
        if not self.spot_manager:
            return
        try:
            actions = self.spot_manager.run_cycle()
            if not actions:
                return
            logger.info(f"[SpotMgr] Cycle: {len(actions)} actions "
                        f"({', '.join(a.get('action','?') for a in actions)})")

            from config import SPOT_PORTFOLIO as _SP
            if _SP.get("recommendation_only", True):
                # Recommendation-only mode: spot_manager already wrote
                # to data/spot_recommendations.jsonl; nothing to execute.
                return

            for a in actions:
                # 2026-05-03: Multi-strategy coordination gate.
                # If DCA bought this asset within the last 24h, skip
                # SELL/SCALE_OUT — opposing-order risk surfaced by
                # the Ruflo strategy review.
                act = a.get("action", "")
                sym = a.get("symbol", "")
                base = sym.split("/")[0] if sym else ""
                dca_buys = getattr(self, "_dca_buys_today", {}) or {}
                last_dca = dca_buys.get(base, 0)
                if act in ("SELL", "SCALE_OUT") and last_dca > 0:
                    age_h = (time.time() - last_dca) / 3600
                    if age_h < 24:
                        logger.warning(
                            f"[SpotMgr] BLOCKED {act} {sym}: DCA bought "
                            f"{base} {age_h:.1f}h ago — opposing-order skip")
                        continue
                self._execute_spot_action(a)
        except Exception as e:
            logger.error(f"[SpotMgr] Cycle error: {e}")

    def _execute_spot_action(self, action: dict) -> None:
        """Execute one SPOT-PROTECT-V1 action (SELL full or SCALE_OUT half).

        Safety guards:
          * Only handles SELL and SCALE_OUT (no buys, no rotations)
          * Verifies free balance from the exchange before sizing
          * Skips if free balance is dust (< $5)
          * Market-sells only — no limit orders, simplest path
          * Wraps failures so one symbol's error doesn't stall the cycle
        """
        try:
            kind = action.get("action")
            if kind not in ("SELL", "SCALE_OUT"):
                return  # ignore HOLD / unknown actions defensively
            # 2026-05-31: PAPER must not touch REAL spot holdings. This path
            # market-sells the owner's actual coins; gate it off in DRY_RUN so
            # paper-research mode never trades real money. (Reversible.)
            from config import DRY_RUN as _dry_run

            if _dry_run:
                logger.info(
                    f"[SpotMgr] [DRY] would {kind} {action.get('exchange')}:{action.get('coin')} "
                    f"— skipped (PAPER: no real spot trades)")
                return

            ex_name = action.get("exchange")
            coin    = action.get("coin")
            symbol  = action.get("symbol") or f"{coin}/USDT"
            reason  = action.get("reason", "")

            exchange = self.exchanges.get(ex_name)
            if exchange is None:
                logger.warning(f"[SpotMgr] no exchange '{ex_name}' for {symbol}")
                return

            bal = exchange.fetch_balance(market_type="spot") or {}
            free = float((bal.get(coin, {}) or {}).get("free", 0.0))
            if free <= 0:
                logger.warning(f"[SpotMgr] {ex_name}:{coin} no free balance")
                return

            # Resolve current price for dust-check; reuse manager state.
            holdings = self.spot_manager._holdings.get(ex_name, {})  # noqa: SLF001
            h = holdings.get(coin)
            px = float(getattr(h, "current_price", 0.0)) if h else 0.0
            if px <= 0:
                logger.warning(f"[SpotMgr] {ex_name}:{coin} no current price")
                return

            sell_qty = free if kind == "SELL" else free * 0.5
            sell_value_usd = sell_qty * px
            if sell_value_usd < 5.0:
                logger.info(
                    f"[SpotMgr] {ex_name}:{coin} skipping {kind} — "
                    f"sell value ${sell_value_usd:.2f} < $5 dust floor")
                return

            logger.warning(
                f"[SpotMgr] EXECUTING {kind} {ex_name}:{coin}: "
                f"qty={sell_qty:.6g} ≈ ${sell_value_usd:.2f} | reason={reason}")
            order = exchange.create_order(
                symbol=symbol, order_type="market", side="sell",
                amount=sell_qty, price=None, market_type="spot",
            )
            order_id = (order or {}).get("id", "?")
            logger.info(
                f"[SpotMgr] {kind} placed {ex_name}:{coin} | order_id={order_id}")
            # Reset the drawdown peak ONLY here — after the trim is confirmed
            # placed (audit 2026-07-07). A SELL fully exits so its peak is moot;
            # SCALE_OUT keeps half the position and must re-baseline the peak so
            # the next drawdown rule fires on a NEW decline, not the same one.
            if kind == "SCALE_OUT" and order:
                try:
                    self.spot_manager.reset_peak(coin, ex_name)
                except Exception as _rpe:
                    logger.debug(f"[SpotMgr] reset_peak skipped: {_rpe}")
        except Exception as e:
            logger.error(
                f"[SpotMgr] execution failed for {action.get('exchange')}:"
                f"{action.get('coin')}: {e}")

    def _run_capital_allocation(self):
        """Run capital allocation cycle (every 15 min)."""
        if not self.capital_allocator:
            return
        try:
            executed = self.capital_allocator.run_cycle()
            if executed:
                logger.info(f"[CapAlloc] Cycle: {len(executed)} actions executed")
        except Exception as e:
            logger.error(f"[CapAlloc] Cycle error: {e}")

    def _execute_fund_ops(self, fund_ops: list):
        """Execute MCP Brain fund management operations: TRANSFER, SELL_PORTFOLIO, BUY_PORTFOLIO."""
        # 2026-05-31: PAPER must not move real money or trade real spot. These
        # ops hit live exchange transfer/spot endpoints; gate off in DRY_RUN so
        # paper-research mode is fully simulated. (Reversible.)
        from config import DRY_RUN as _dry_run

        if _dry_run:
            if fund_ops:
                logger.info(
                    f"[MCP-FundOps] [DRY] {len(fund_ops)} fund op(s) skipped "
                    f"(PAPER: no real transfers / spot trades)")
            return
        for op in fund_ops:
            op_type = op.get("op", "")
            if op_type in {"TRANSFER", "BUY_PORTFOLIO"}:
                from core.entry_policy import authorize_runtime_entry

                authorization = authorize_runtime_entry(
                    "mcp_fund_ops", strategy_version="mcp-fund-ops-v1"
                )
                if not authorization.allowed:
                    logger.info(
                        f"[EntryPolicy] fund operation {op_type} blocked: "
                        f"{authorization.reason}"
                    )
                    continue
            ex_name = op.get("exchange", "").lower()
            exchange = self.active_exchanges.get(ex_name)
            if not exchange:
                logger.warning(f"[MCP-FundOps] Exchange '{ex_name}' not active — skip {op_type}")
                continue
            if op_type in {"TRANSFER", "BUY_PORTFOLIO"}:
                from config import CLOCK_DRIFT_ALERT_MS, OPERATING_MODE

                clock_rejection = _live_entry_clock_drift_rejection(
                    OPERATING_MODE,
                    ex_name,
                    getattr(self, "_clock_drift_ms", None),
                    CLOCK_DRIFT_ALERT_MS,
                )
                if clock_rejection:
                    logger.warning(
                        f"[ClockDrift] BLOCKED fund operation {op_type} "
                        f"on {ex_name}: {clock_rejection}"
                    )
                    continue

            try:
                if op_type == "TRANSFER":
                    from_acct = op.get("from", "spot")
                    to_acct = op.get("to", "futures")
                    amount = float(op.get("amount", 0))
                    if amount < 1:
                        continue
                    # Binance, Bitget support transfers; Bybit is unified (no need)
                    if ex_name not in ("binance", "bitget"):
                        logger.debug(f"[MCP-FundOps] {ex_name} unified or no transfer support")
                        continue
                    logger.info(
                        f"[MCP-FundOps] TRANSFER {ex_name.upper()}: "
                        f"${amount:.2f} USDT {from_acct}→{to_acct} | {op.get('reason','')[:50]}")
                    success = exchange.transfer(amount, from_acct, to_acct)
                    if success:
                        # Update cached balance
                        bals = self._balances.get(ex_name, {"spot": 0, "futures": 0})
                        bals[from_acct] = max(0, bals.get(from_acct, 0) - amount)
                        bals[to_acct] = bals.get(to_acct, 0) + amount
                        self._balances[ex_name] = bals
                        logger.info(f"[MCP-FundOps] Transfer OK: {ex_name} balances updated")
                    else:
                        logger.warning(f"[MCP-FundOps] Transfer FAILED on {ex_name}")

                elif op_type == "SELL_PORTFOLIO":
                    coin = op.get("coin", "")
                    if not coin or coin in ("USDT", "BUSD", "USDC"):
                        continue
                    symbol = f"{coin}/USDT"
                    reason = op.get("reason", "MCP fund rebalance")
                    # Get current holding
                    try:
                        bal = exchange.fetch_balance("spot")
                        held = float(bal.get("free", {}).get(coin, 0) or 0)
                    except Exception:
                        held = 0
                    if held <= 0:
                        logger.debug(f"[MCP-FundOps] No {coin} on {ex_name} to sell")
                        continue
                    # Check minimum notional
                    try:
                        t = exchange.fetch_ticker(symbol, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0 or held * px < 5.0:
                        logger.debug(f"[MCP-FundOps] {coin} on {ex_name}: ${held*px:.2f} < $5 min")
                        continue
                    logger.info(
                        f"[MCP-FundOps] SELL {coin} on {ex_name.upper()}: "
                        f"{held:.6g} ~${held*px:.2f} | {reason[:50]}")
                    try:
                        exchange.create_order(symbol, "market", "sell", held,
                                              None, {}, "spot")
                        logger.info(f"[MCP-FundOps] Sold {held:.6g} {coin} on {ex_name}")
                    except Exception as e:
                        logger.warning(f"[MCP-FundOps] Sell {coin} on {ex_name} failed: {e}")

                elif op_type == "BUY_PORTFOLIO":
                    coin = op.get("coin", "")
                    amount = float(op.get("amount", 0))
                    if not coin or amount < 5:
                        continue
                    symbol = f"{coin}/USDT"
                    try:
                        t = exchange.fetch_ticker(symbol, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0:
                        continue
                    qty = amount / px
                    logger.info(
                        f"[MCP-FundOps] BUY {coin} on {ex_name.upper()}: "
                        f"${amount:.2f} ({qty:.6g} {coin}) | {op.get('reason','')[:50]}")
                    try:
                        exchange.create_order(symbol, "market", "buy", qty,
                                              px, {}, "spot")
                        logger.info(f"[MCP-FundOps] Bought {qty:.6g} {coin} on {ex_name}")
                    except Exception as e:
                        logger.warning(f"[MCP-FundOps] Buy {coin} on {ex_name} failed: {e}")

            except Exception as e:
                logger.error(f"[MCP-FundOps] {op_type} on {ex_name} error: {e}")

    def _fetch_all_exchange_positions(self) -> list[dict]:
        """Fetch ALL open positions from ALL connected exchanges.
        Returns futures positions + spot holdings as a unified list.
        Cached for 60 seconds to avoid redundant API calls."""
        now = time.time()
        if now - self._exchange_positions_time < 60 and self._exchange_positions_cache:
            return list(self._exchange_positions_cache)

        results = []

        def _fetch_one_exchange(ex_name, exchange):
            positions = []
            # ── Futures positions ──
            if True:
                try:
                    raw = exchange.fetch_positions()
                    for ep in (raw or []):
                        size = float(ep.get("contracts") or ep.get("contractSize") or 0)
                        if size <= 0:
                            continue
                        symbol = ep.get("symbol", "")
                        side_raw = (ep.get("side") or "").lower()
                        if side_raw not in ("long", "short"):
                            continue
                        side = "buy" if side_raw == "long" else "sell"
                        entry = float(ep.get("entryPrice") or 0)
                        mark = float(ep.get("markPrice") or ep.get("lastPrice") or 0)
                        upnl = float(ep.get("unrealizedPnl") or 0)
                        lev = int(ep.get("leverage") or 1)
                        liq = float(ep.get("liquidationPrice") or 0)
                        pnl_pct = 0
                        if entry > 0:
                            if side == "buy":
                                pnl_pct = (mark - entry) / entry * 100
                            else:
                                pnl_pct = (entry - mark) / entry * 100
                        base = symbol.split("/")[0].split(":")[0]
                        asset_class = "commodity" if base in self._COMMODITY_BASES else "crypto_futures"
                        positions.append({
                            "id": f"EX-{ex_name}-{symbol}-{side}",
                            "symbol": symbol,
                            "side": side,
                            "entry_price": entry,
                            "current_price": mark,
                            "pnl": round(upnl, 4),
                            "pnl_pct": round(pnl_pct, 2),
                            "stop_loss": 0,
                            "take_profit": 0,
                            "age_min": 0,
                            "exchange": ex_name,
                            "market_type": "futures",
                            "leverage": lev,
                            "liquidation_price": liq,
                            "source": "exchange",
                            "size": size,
                            "usdt_value": round(mark * size, 2) if mark else 0,
                            "asset_class": asset_class,
                        })
                except Exception as e:
                    logger.debug(f"[ExScan] {ex_name} futures fetch: {e}")

            # ── Spot holdings ──
            try:
                bal = exchange.fetch_balance("spot")
                total_bal = bal.get("total", {})
                for asset, amt in total_bal.items():
                    amt = float(amt or 0)
                    if amt <= 0 or asset in self._STABLECOINS:
                        continue
                    # Get current price
                    sym = f"{asset}/USDT"
                    try:
                        t = exchange.fetch_ticker(sym, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0:
                        continue
                    usdt_val = amt * px
                    if usdt_val < 5.0:
                        continue
                    asset_class = "commodity" if asset in self._COMMODITY_BASES else "crypto_spot"
                    positions.append({
                        "id": f"SPOT-{ex_name}-{asset}",
                        "symbol": sym,
                        "side": "buy",
                        "entry_price": 0,
                        "current_price": px,
                        "pnl": 0,
                        "pnl_pct": 0,
                        "stop_loss": 0,
                        "take_profit": 0,
                        "age_min": 0,
                        "exchange": ex_name,
                        "market_type": "spot",
                        "leverage": 1,
                        "liquidation_price": 0,
                        "source": "exchange",
                        "size": amt,
                        "usdt_value": round(usdt_val, 2),
                        "asset_class": asset_class,
                    })
            except Exception as e:
                logger.debug(f"[ExScan] {ex_name} spot balance: {e}")
            return positions

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one_exchange, name, ex): name
                for name, ex in self.active_exchanges.items()
            }
            for fut in as_completed(futures, timeout=30):
                try:
                    results.extend(fut.result())
                except Exception as e:
                    logger.debug(f"[ExScan] fetch error: {e}")

        self._exchange_positions_cache = results
        self._exchange_positions_time = time.time()
        # 2026-04-16: Persist the universal snapshot so the dashboard
        # (a separate process) can display the same unified view instead
        # of running its own parallel LiveFetcher that can silently fail
        # when a client init errors at startup.
        try:
            import json as _json
            from pathlib import Path as _Path
            _path = _Path("data/exchange_positions.json")
            _path.parent.mkdir(parents=True, exist_ok=True)
            _path.write_text(_json.dumps({
                "ts": self._exchange_positions_time,
                "positions": results,
            }, indent=2), encoding="utf-8")
        except Exception as _e:
            logger.debug(f"[ExScan] cache write failed: {_e}")
        return results

    def _unrealized_pnl_frac(self, p) -> float:
        """Compute unrealized PnL as a fraction (e.g. 0.012 = +1.2%) using
        the current mark price from the exchange. Returns 0.0 on any error.

        Notes:
        - Fraction is computed against entry_price (unleveraged) so the
          [0%, 2%) bands in Areas 4 + 5 refer to PRICE move, not equity move.
          A +1% price move on a 2x position = +2% on equity but only +1% by
          this fraction. This matches the existing SL/TP percentages.
        """
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return 0.0
        try:
            ticker = ex.fetch_ticker(p.symbol, p.market_type)
            info = ticker.get("info", {}) or {}
            mark = info.get("markPrice") or info.get("mark")
            try:
                price = float(mark) if mark else 0.0
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                price = float(ticker.get("last") or 0.0)
            if price <= 0 or p.entry_price <= 0:
                return 0.0
            if p.side == "buy":
                return (price - p.entry_price) / p.entry_price
            else:
                return (p.entry_price - price) / p.entry_price
        except Exception:
            return 0.0

    def _maybe_capture_small_tp(self, p) -> bool:
        """Area 5 — Deterministic small-TP capture.

        Fires when:
          - config.AUTO_SMALL_TP_ENABLED is True
          - position is futures (spot keeps existing logic)
          - symbol NOT in STAR_SYMBOLS (those ride per Phase 46)
          - age >= AUTO_SMALL_TP_MIN_AGE_MIN
          - pnl_frac in [AUTO_SMALL_TP_MIN_PNL_FRAC, AUTO_SMALL_TP_MAX_PNL_FRAC)

        Action: market close via order_mgr.close_position with reason
        'auto_small_tp_1pct'. Returns True iff a close was actually fired.

        NOT a re-enable of Phase 39 disabled CLOSE — that was Claude's
        discretionary, narrative-driven decision (1W/17L over 388 trades).
        This is a deterministic threshold rule, equivalent in nature to a
        take-profit limit order placed at entry+1%.
        """
        try:
            from config import (
                AUTO_SMALL_TP_ENABLED,
                AUTO_SMALL_TP_MAX_PNL_FRAC,
                AUTO_SMALL_TP_MIN_AGE_MIN,
                AUTO_SMALL_TP_MIN_PNL_FRAC,
                STAR_SYMBOLS,
            )
        except ImportError:
            return False
        if not AUTO_SMALL_TP_ENABLED:
            return False
        if getattr(p, "market_type", "") != "futures":
            return False
        if p.symbol in (STAR_SYMBOLS or set()):
            return False
        try:
            age_min = float(p.duration_minutes)
        except Exception:
            return False
        if age_min < AUTO_SMALL_TP_MIN_AGE_MIN:
            return False

        pnl_frac = self._unrealized_pnl_frac(p)
        if not (AUTO_SMALL_TP_MIN_PNL_FRAC <= pnl_frac < AUTO_SMALL_TP_MAX_PNL_FRAC):
            return False

        # Resolve the exchange and fire close_position
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return False

        logger.info(
            f"[AutoSmallTP] {p.symbol} {p.side.upper()} age={age_min:.0f}m "
            f"pnl=+{pnl_frac*100:.2f}% — capturing at market")
        self.order_mgr.close_position(ex, p, "auto_small_tp_1pct")
        return True

    def _maybe_tighten_aged_position(self, p) -> bool:
        """Area 4 — Age-aware SL→breakeven tightener.

        Fires when:
          - config.AGE_AWARE_SL_ENABLED is True
          - position is futures (spot has no leverage-side ghost problem)
          - age >= AGE_AWARE_SL_MIN_AGE_MIN
          - pnl_frac in [AGE_AWARE_SL_MIN_PNL_FRAC, AGE_AWARE_SL_MAX_PNL_FRAC)
          - new breakeven SL is TIGHTER than current SL (never widens — Spec §2)

        Action: replaces exchange-side SL with entry × (1 ± 2·fee_rate ± 0.0005).

        Returns True iff an SL replacement actually fired.
        """
        try:
            from config import (
                AGE_AWARE_SL_ENABLED,
                AGE_AWARE_SL_MAX_PNL_FRAC,
                AGE_AWARE_SL_MIN_AGE_MIN,
                AGE_AWARE_SL_MIN_PNL_FRAC,
                FEE,
            )
        except ImportError:
            return False
        if not AGE_AWARE_SL_ENABLED:
            return False
        if getattr(p, "market_type", "") != "futures":
            return False
        try:
            age_min = float(p.duration_minutes)
        except Exception:
            return False
        if age_min < AGE_AWARE_SL_MIN_AGE_MIN:
            return False

        pnl_frac = self._unrealized_pnl_frac(p)
        if not (AGE_AWARE_SL_MIN_PNL_FRAC <= pnl_frac < AGE_AWARE_SL_MAX_PNL_FRAC):
            return False

        # Breakeven SL — covers round-trip fee + 5bp buffer
        rate = FEE.get("futures_taker", 0.0005)
        if p.side == "buy":
            new_sl = p.entry_price * (1 + 2 * rate + 0.0005)
            # Refuse to widen: must be ABOVE current SL for a long
            if new_sl <= float(p.stop_loss or 0):
                return False
        else:
            new_sl = p.entry_price * (1 - 2 * rate - 0.0005)
            # Refuse to widen: must be BELOW current SL for a short
            if new_sl >= float(p.stop_loss or 0):
                return False

        # Replace the exchange-side SL
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return False
        if not self._replace_exchange_sl(ex, p, new_sl):
            return False
        old_sl = p.stop_loss
        p.stop_loss = new_sl
        logger.info(
            f"[AgeAwareSL] {p.symbol} {p.side.upper()} age={age_min:.0f}m "
            f"pnl={pnl_frac*100:+.2f}% SL→breakeven "
            f"({old_sl:.6g}→{new_sl:.6g})")
        return True

    def _run_mcp_position_monitor(self):
        """Run MCP Brain position monitor — scans ALL positions across ALL exchanges.
        Merges bot-tracked positions with exchange-discovered positions (futures + spot).
        MCP Brain is the SOLE authority on hold/close/take-profit for the entire portfolio."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
            return
        # 2026-06-15: under SIGNAL_SOURCE=tsmom the bot makes NO Claude/MCP decisions.
        # tsmom owns entries + its own momentum-flip exits, and the deterministic SL/TP
        # loop (its own 10s thread) keeps disaster stops live. Skip the MCP advisory
        # monitor entirely so the bot is purely tsmom. Logged once to avoid 90s spam.
        from config import SIGNAL_SOURCE as _SIG_MON
        if _SIG_MON in ("tsmom", "machine", "none"):
            _logged_attr = f"_{_SIG_MON}_monitor_off_logged"
            if not getattr(self, _logged_attr, False):
                logger.info(
                    f"[Engine] {_SIG_MON.upper()} mode - MCP position monitor disabled "
                    "(deterministic SL/TP still active; no Claude/MCP advisory).")
                setattr(self, _logged_attr, True)
            return
        try:
            # ── Phase 1: Bot-tracked positions ──
            open_positions = self.tracker.get_open()
            tracker_map = {}          # id -> Position object
            tracked_keys = set()      # (exchange, symbol_norm, side) for dedup
            pos_data = []

            for p in open_positions:
                current_price = 0
                for ex_name, exchange in self.active_exchanges.items():
                    if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                        try:
                            ticker = exchange.fetch_ticker(p.symbol, p.market_type)
                            current_price = float(ticker.get("last", 0) or 0)
                        except Exception:
                            pass
                        break
                if not current_price:
                    # Fallback to entry price so position is still visible to MCP
                    current_price = p.entry_price
                lev = max(1, getattr(p, "leverage", 1) or 1)
                if p.side == "buy":
                    unrealized_pnl = (current_price - p.entry_price) * p.size
                    pnl_pct = (current_price - p.entry_price) / p.entry_price * 100 * lev
                else:
                    unrealized_pnl = (p.entry_price - current_price) * p.size
                    pnl_pct = (p.entry_price - current_price) / p.entry_price * 100 * lev

                base = p.symbol.split("/")[0].split(":")[0]
                asset_class = "commodity" if base in self._COMMODITY_BASES else (
                    "crypto_futures" if p.market_type == "futures" else "crypto_spot")

                pd = {
                    "id": p.id,
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "current_price": current_price,
                    "pnl": round(unrealized_pnl, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "age_min": p.duration_minutes,
                    "exchange": p.exchange,
                    "market_type": p.market_type,
                    "leverage": p.leverage,
                    "liquidation_price": 0,
                    "source": "tracker",
                    "size": p.size,
                    "usdt_value": round(current_price * p.size, 2),
                    "asset_class": asset_class,
                }
                pos_data.append(pd)
                tracker_map[p.id] = p
                sym_norm = p.symbol.split(":")[0]
                tracked_keys.add((p.exchange.lower(), sym_norm, p.side))

            # ── Phase 2: Exchange-discovered positions (futures + spot) ──
            exchange_positions = self._fetch_all_exchange_positions()

            # ── Phase 3: Deduplicate and merge ──
            for ep in exchange_positions:
                sym_norm = ep["symbol"].split(":")[0]
                key = (ep["exchange"].lower(), sym_norm, ep["side"])
                if key in tracked_keys:
                    continue  # already tracked by bot — skip exchange duplicate
                pos_data.append(ep)

            if not pos_data:
                return

            # ── Priority cap: max 20 positions for MCP Brain ──
            # Tracked first, then external futures by value, then external spot by value
            tracked_data = [d for d in pos_data if d["source"] == "tracker"]
            ext_futures = sorted(
                [d for d in pos_data if d["source"] == "exchange" and d["market_type"] == "futures"],
                key=lambda x: x.get("usdt_value", 0), reverse=True)
            ext_spot = sorted(
                [d for d in pos_data if d["source"] == "exchange" and d["market_type"] == "spot"],
                key=lambda x: x.get("usdt_value", 0), reverse=True)
            capped = tracked_data[:20]
            remaining = 20 - len(capped)
            if remaining > 0:
                capped.extend(ext_futures[:remaining])
                remaining = 20 - len(capped)
            if remaining > 0:
                capped.extend(ext_spot[:remaining])
            pos_data = capped

            n_tracked = len(tracked_data)
            n_ext_fut = len(ext_futures)
            n_ext_spot = len(ext_spot)
            if n_ext_fut or n_ext_spot:
                logger.info(
                    f"[MCP-Monitor] {n_tracked} tracked + {n_ext_fut} ext-futures "
                    f"+ {n_ext_spot} ext-spot = {len(pos_data)} sent to MCP Brain")

            # ── Phase 3.5 (2026-05-20): Deterministic exits BEFORE MCP brain ──
            # Areas 5 + 4 are pure threshold rules (no judgment, no narrative).
            # Running them here means they fire even on cycles where Claude
            # would have said HOLD. Ordering matters: Area 5 (close) is decisive
            # over Area 4 (SL move) — check Area 5 first, return early on fire.
            #
            # Only TRACKER positions get this treatment — external (exchange-
            # discovered) positions don't have full Position state (stop_loss,
            # entry_price, side as a Position attr) and are handled in the
            # source=='exchange' branch of the MCP-advice loop below.
            from core.deep_breakout_lane import (
                is_deep_breakout_position as _is_db_pos,
            )
            from core.tsmom_signal import is_tsmom_position as _is_tsmom_pos
            for pid_local, p_local in list(tracker_map.items()):
                try:
                    # Phase 2b: tsmom positions own their exit (momentum flip) —
                    # skip the deterministic small-TP capture + age→BE tightener.
                    if _is_tsmom_pos(p_local):
                        continue
                    # Deep-breakout lane (2026-07-11): SL/TP + 126-bar max-hold
                    # own the exit — small-TP capture / age→BE would clip the
                    # researched 3R geometry.
                    if _is_db_pos(p_local):
                        continue
                    if self._maybe_capture_small_tp(p_local):
                        continue  # Area 5 fired — position closing; skip Area 4
                    self._maybe_tighten_aged_position(p_local)
                except Exception as _de:
                    logger.debug(
                        f"[DeterministicExits] {p_local.symbol}: {_de}")

            # ── Phase 4: Send to MCP Brain ──
            advice = self.mcp_brain.monitor_positions(pos_data)
            if not advice:
                return

            # ── Phase 5: Apply actions ──
            for pos_entry in pos_data:
                pid = pos_entry["id"]
                adv = advice.get(pid, {})
                # Also try short ID (MCP Brain returns first 8 chars as keys)
                if not adv and len(pid) > 8:
                    adv = advice.get(pid[:8], {})
                action = adv.get("action", "")
                if not action or action == "HOLD":
                    continue

                source = pos_entry["source"]
                conf = adv.get("confidence", 0)
                reason = adv.get("reason", "")[:60]
                _pnl_pct = pos_entry.get("pnl_pct", 0)

                # ═══ TRACKER POSITIONS — use existing close_position path ═══
                if source == "tracker":
                    p = tracker_map.get(pid)
                    if not p:
                        continue

                    # Phase 2b: tsmom positions are held to their momentum-flip
                    # CLOSE — no MCP advice (TAKE_PROFIT/TIGHTEN/BREAKEVEN) may
                    # clip or ratchet them. The wide disaster stop is the only rail.
                    if _is_tsmom_pos(p):
                        continue
                    # Deep-breakout lane: no MCP advice may clip or ratchet a
                    # lane hold either — its rails are SL/TP, the §2 8%
                    # guardian, and the lane's 126-bar max-hold close.
                    if _is_db_pos(p):
                        continue

                    if action == "TAKE_PROFIT":
                        # Fee-aware: estimate round-trip fees and require net profit >= threshold
                        # Phase 46 (2026-05-10): STAR symbols get HIGHER threshold (1.5%
                        # vs 0.5%) so they're allowed to ride to bigger wins. Historical
                        # R:R for STARs (ATOM, ARB) is 1.85+ — capping their profits at
                        # +0.5% leaves big upside on the table. Non-STAR symbols keep
                        # the +0.5% threshold (their realized R:R is closer to 1:1).
                        # The 4 historical full-TP hits (avg $2.83) demonstrate that
                        # bigger wins are achievable when positions are given room.
                        from config import FEE
                        try:
                            from config import STAR_SYMBOLS as _STAR
                        except ImportError:
                            _STAR = set()
                        if p.market_type == "futures":
                            rt_fee_pct = (FEE.get("futures_maker", 0.0002) + FEE.get("futures_taker", 0.0005)) * 100
                        else:
                            rt_fee_pct = (FEE.get("spot_maker", 0.001) + FEE.get("spot_taker", 0.001)) * 100
                        net_pnl_pct = _pnl_pct - rt_fee_pct
                        # Phase 46: STAR threshold 1.5%, non-STAR 0.5%
                        is_star = p.symbol in _STAR
                        _base_threshold = 1.5 if is_star else 0.5
                        # B5 (audit 2026-06-21): when near_target_exit is ON,
                        # require a near-PLANNED-TP exit instead of the bare floor.
                        # net_pnl_pct is LEVERAGED, so the helper scales the
                        # price-% TP distance by leverage. Flag OFF => returns
                        # _base_threshold (byte-identical to today).
                        # ⚠ UNITS INVARIANT: net_pnl_pct is leveraged ONLY because
                        # this block is fenced by `source == "tracker"` above (that
                        # builder applies *lev). Do NOT route the exchange-sourced
                        # UNLEVERAGED pnl_pct here, or the threshold becomes lev×
                        # too strict and winners never exit.
                        from config import RISK as _RISK_NT
                        tp_threshold = _effective_tp_threshold(
                            take_profit=p.take_profit,
                            entry_price=p.entry_price,
                            side=p.side,
                            leverage=p.leverage,
                            base_threshold=_base_threshold,
                            near_target_enabled=_RISK_NT.get("near_target_exit_enabled", False),
                            near_target_frac=_RISK_NT.get("near_target_frac", 0.8),
                        )
                        if net_pnl_pct >= tp_threshold and conf >= 0.60:
                            for ex_name, exchange in self.active_exchanges.items():
                                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                    star_tag = " [STAR ride]" if is_star else ""
                                    logger.warning(
                                        f"[MCP-Brain] TAKE PROFIT{star_tag}: {p.symbol} {p.side} "
                                        f"pnl={_pnl_pct:+.1f}% net~{net_pnl_pct:+.1f}% "
                                        f"conf={conf:.0%} (threshold={tp_threshold}%) — {reason}")
                                    self.order_mgr.close_position(
                                        exchange, p, "mcp_take_profit")
                                    break
                        else:
                            star_note = " [STAR — letting it run]" if is_star else ""
                            logger.debug(
                                f"[MCP-Brain] TAKE_PROFIT skipped{star_note}: {p.symbol} "
                                f"pnl={_pnl_pct:+.1f}% net~{net_pnl_pct:+.1f}% conf={conf:.0%} "
                                f"(need net>={tp_threshold}% + conf>=60%)")

                    elif action == "CLOSE":
                        # 2026-04-24: systematic_close disabled. Historical 1W/17L
                        # over 388 trades ($-16.42 total). The monitor was closing
                        # positions with adverse-indicator narratives but the
                        # subsequent price action rarely confirmed — net-negative
                        # drag. SL, trailing_stop, and mcp_take_profit own exits
                        # now. Suggestion is logged but not actioned; if a real
                        # hard-loss case appears, the scan-CLOSE rules in
                        # mcp_brain (leveraged-aware -12%/-3% thresholds) still
                        # fire and hit close_position with `systematic_close`
                        # from there — but only as a catastrophic safety rail,
                        # not a discretionary close.
                        logger.info(
                            f"[MCP-Brain] CLOSE suggested but systematic_close "
                            f"disabled (2026-04-24 policy): {p.symbol} "
                            f"conf={conf:.0%} pnl={_pnl_pct:+.1f}% — {reason[:60]}")

                    elif action == "TIGHTEN":
                        for ex_name, exchange in self.active_exchanges.items():
                            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                try:
                                    t = exchange.fetch_ticker(p.symbol, p.market_type)
                                    cur = float(t.get("last", 0))
                                    if cur > 0 and p.stop_loss > 0:
                                        new_sl = None
                                        if p.side == "buy":
                                            candidate = p.stop_loss + (cur - p.stop_loss) * 0.3
                                            if candidate > p.stop_loss:
                                                new_sl = candidate
                                        else:
                                            candidate = p.stop_loss - (p.stop_loss - cur) * 0.3
                                            if candidate < p.stop_loss:
                                                new_sl = candidate
                                        if new_sl is not None:
                                            logger.info(
                                                f"[MCP-Brain] TIGHTEN: {p.symbol} {p.side.upper()} "
                                                f"SL {p.stop_loss:.6g} → {new_sl:.6g}")
                                            if self._replace_exchange_sl(exchange, p, new_sl):
                                                p.stop_loss = new_sl
                                except Exception as _e:
                                    logger.debug(f"[MCP-Brain] TIGHTEN {p.symbol}: {_e}")
                                break

                    elif action == "BREAKEVEN":
                        from core.position_tracker import _fee_rate
                        rate = _fee_rate(p.market_type)
                        new_be = None
                        if p.side == "buy":
                            be = p.entry_price * (1 + rate * 2 + 0.0005)
                            if be > p.stop_loss:
                                new_be = be
                        else:
                            be = p.entry_price * (1 - rate * 2 - 0.0005)
                            if be < p.stop_loss:
                                new_be = be
                        if new_be is not None:
                            logger.info(
                                f"[MCP-Brain] BREAKEVEN: {p.symbol} {p.side.upper()} "
                                f"SL {p.stop_loss:.6g} → {new_be:.6g}")
                            for ex_name, exchange in self.active_exchanges.items():
                                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                    try:
                                        if self._replace_exchange_sl(exchange, p, new_be):
                                            p.stop_loss = new_be
                                    except Exception as _e:
                                        logger.debug(f"[MCP-Brain] BREAKEVEN replace {p.symbol}: {_e}")
                                    break

                    # WIDEN removed — spec §2 forbids widening stop losses.

                # ═══ EXTERNAL POSITIONS — direct exchange execution ═══
                elif source == "exchange":
                    ex_name = pos_entry["exchange"]
                    mtype = pos_entry["market_type"]

                    if action in ("TAKE_PROFIT", "CLOSE"):
                        # Phase 39 (2026-05-09): CLOSE disabled for external positions.
                        # mcp_brain_close accumulated -$20.09 over 44 trades (30% WR).
                        # The monitor was cutting positions early against subsequent
                        # price action. SL, trailing_stop, and mcp_take_profit own
                        # all exits now — CLOSE is demoted to logged-only.
                        if action == "CLOSE":
                            logger.info(
                                f"[MCP-Brain] EXT CLOSE suppressed (Phase 39 policy): "
                                f"{pos_entry['symbol']} on {ex_name} conf={conf:.0%} — {reason[:60]}")
                            continue
                        if action == "TAKE_PROFIT":
                            if mtype == "futures" and (_pnl_pct < 1.0 or conf < 0.70):
                                logger.debug(
                                    f"[MCP-Brain] EXT TP skipped: {pos_entry['symbol']} on {ex_name} "
                                    f"pnl={_pnl_pct:+.1f}% conf={conf:.0%}")
                                continue
                            if mtype == "spot" and conf < 0.80:
                                logger.debug(
                                    f"[MCP-Brain] EXT SPOT TP skipped: {pos_entry['symbol']} on {ex_name} "
                                    f"conf={conf:.0%} < 80%")
                                continue

                        from config import DRY_RUN as _dry_run

                        if _dry_run:
                            logger.info(
                                f"[MCP-Brain] [DRY] EXT {action}: {pos_entry['symbol']} {pos_entry['side']} "
                                f"on {ex_name} ({mtype}) — {reason}")
                        else:
                            self._close_external_position(ex_name, pos_entry, reason)

                    elif action in ("TIGHTEN", "BREAKEVEN"):
                        logger.debug(
                            f"[MCP-Brain] EXT {action} N/A: {pos_entry['symbol']} on {ex_name} "
                            f"(no tracked SL for external positions)")

            # Persist SL changes for tracker positions
            self.tracker._save()

        except Exception as e:
            logger.debug(f"[Engine] MCP Position Monitor: {e}")

    def _ext_position_still_open(self, exchange, symbol: str) -> bool:
        """Confirm a non-zero live position for `symbol` before a reduceOnly-stripped
        close retry. Fail-CLOSED: any fetch error returns False so the caller aborts
        the retry — a stripped market order on an already-flat symbol would FILL and
        open an untracked NAKED REVERSE (no SL, no tracker entry). Ported from
        order_manager._close_position_impl's 2026-04-20 naked-short guard."""
        try:
            live = exchange.fetch_positions([symbol]) or []
        except Exception as fe:
            logger.warning(
                f"[MCP-Brain] fetch_positions failed during ext close-retry guard "
                f"for {symbol}: {str(fe)[:120]} — treating as closed to avoid naked reverse")
            return False
        for p in live:
            try:
                sz = abs(float(p.get("contracts") or p.get("contractSize") or 0))
            except (TypeError, ValueError):
                sz = 0.0
            if sz > 0:
                return True
        return False

    def _close_external_position(self, ex_name: str, pos: dict, reason: str):
        """Close an exchange-discovered position NOT tracked internally.
        Futures: market close order. Spot: market sell coins."""
        # Defense-in-depth: this places REAL reduceOnly futures closes / spot sells
        # of the owner's actual coins. The sole caller (the MCP position monitor) is
        # already DRY_RUN-gated, but a real-money side effect must never rely on
        # caller governance alone — self-gate so PAPER/OBSERVATION can never reach
        # the exchange even via a future call site. No-op in CONTROLLED_LIVE.
        from config import DRY_RUN as _dry_run

        if _dry_run:
            logger.info(
                f"[MCP-Brain] [DRY] EXT CLOSE skipped (self-gate): "
                f"{pos.get('symbol')} {pos.get('side')} on {ex_name} "
                f"({pos.get('market_type')}) | {reason}")
            return
        exchange = self.active_exchanges.get(ex_name)
        if not exchange:
            return

        symbol = pos["symbol"]
        market_type = pos["market_type"]
        side = pos["side"]
        size = pos["size"]

        if market_type == "spot":
            coin = symbol.split("/")[0]
            try:
                bal = exchange.fetch_balance("spot")
                held = float(bal.get("free", {}).get(coin, 0) or 0)
            except Exception:
                held = 0
            if held <= 0:
                return
            try:
                t = exchange.fetch_ticker(symbol, "spot")
                px = float(t.get("last") or 0)
            except Exception:
                px = 0
            if px <= 0 or held * px < 5.0:
                return
            logger.warning(
                f"[MCP-Brain] SELL SPOT {coin} on {ex_name}: "
                f"{held:.6g} ~${held * px:.2f} | {reason}")
            try:
                exchange.create_order(symbol, "market", "sell", held, None, {}, "spot")
            except Exception as e:
                logger.error(f"[MCP-Brain] Spot sell {coin} on {ex_name} failed: {e}")

        elif market_type == "futures":
            close_side = "sell" if side == "buy" else "buy"
            ex_lower = ex_name.lower()
            params = {}
            if ex_lower in self.order_mgr._oneway_mode:
                if getattr(exchange, '_is_oneway', False):
                    params["reduceOnly"] = True
            else:
                params["positionSide"] = "LONG" if side == "buy" else "SHORT"
                params["reduceOnly"] = True

            logger.warning(
                f"[MCP-Brain] CLOSE EXT FUTURES {symbol} {side} on {ex_name}: "
                f"size={size} | {reason}")
            try:
                exchange.create_order(
                    symbol, "market", close_side, size, None, params, "futures")
            except Exception as e:
                err = str(e)
                if "reduceonly" in err.lower() or "-1106" in err:
                    self.order_mgr._oneway_mode.add(ex_lower)
                    if not self._ext_position_still_open(exchange, symbol):
                        logger.info(
                            f"[MCP-Brain] {symbol} on {ex_name} already flat — "
                            f"skipping naked-reverse retry (fail-closed guard) — "
                            f"external position already closed")
                        return
                    try:
                        exchange.create_order(
                            symbol, "market", close_side, size, None, {}, "futures")
                    except Exception as e2:
                        logger.error(f"[MCP-Brain] Ext futures close retry: {e2}")
                elif "no position" in err.lower() or "does not exist" in err.lower():
                    logger.info(f"[MCP-Brain] Ext position already closed: {symbol}")
                elif any(s in err.lower() for s in (
                        "unilateral", "position mode", "positionside", "-4061", "40774")):
                    self.order_mgr._oneway_mode.add(ex_lower)
                    _ow = {"reduceOnly": True} if getattr(exchange, '_is_oneway', False) else {}
                    if not self._ext_position_still_open(exchange, symbol):
                        logger.info(
                            f"[MCP-Brain] {symbol} on {ex_name} already flat — "
                            f"skipping naked-reverse retry (fail-closed guard) — "
                            f"external position already closed")
                        return
                    try:
                        exchange.create_order(
                            symbol, "market", close_side, size, None, _ow, "futures")
                    except Exception as e2:
                        logger.error(f"[MCP-Brain] Ext futures close (one-way): {e2}")
                else:
                    logger.error(f"[MCP-Brain] Ext futures close failed: {e}")

