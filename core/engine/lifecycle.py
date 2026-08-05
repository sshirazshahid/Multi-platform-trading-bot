"""
core/engine/lifecycle.py — BotEngine _LifecycleMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import _UNIFIED_EXCHANGES


class _LifecycleMixin:
    async def _reconcile_realtime_stream(self, request) -> None:
        """Reconcile a private-stream gap against the same venue's REST state.

        The callback runs on the hub event loop, so blocking exchange wrappers
        are moved to a worker thread.  Any unavailable venue or failed snapshot
        raises and keeps the private stream unhealthy instead of falsely fresh.
        """
        import asyncio

        venue = str(request.key.venue).lower()
        exchange = (self.active_exchanges or {}).get(venue)
        if exchange is None:
            raise RuntimeError(f"reconciliation venue unavailable: {venue}")

        def _reconcile() -> None:
            orders = exchange.fetch_open_orders(request.key.symbol, "futures")
            if orders is None:
                raise RuntimeError("open-order reconciliation returned no snapshot")
            if not DRY_RUN:
                self.tracker.sync_with_exchanges({venue: exchange})

        await asyncio.to_thread(_reconcile)

    def _complete_authorized_live_startup_reconciliation(
        self,
        operating_mode: str,
    ) -> None:
        """Reconcile/protect live positions after authorization and preflight.

        This is idempotent because future startup refactors may invoke it
        defensively more than once. PAPER and OBSERVATION never touch the
        exchange reconciliation path.
        """
        from config import DRY_RUN as _dry_run

        if (operating_mode or "").upper() != "CONTROLLED_LIVE" or _dry_run:
            return
        if not getattr(self, "_live_startup_reconciliation_pending", False):
            return

        imported = self.tracker.sync_with_exchanges(self.active_exchanges)
        if imported:
            self._protect_imported_positions(imported)
        self._live_startup_reconciliation_pending = False

    def run(self):
        # Spec Appendix B: refuse to run CONTROLLED_LIVE without a signed checklist.
        _op_mode = None
        try:
            from config import (
                CONTROLLED_LIVE_ENABLED as _live_enabled,
            )
            from config import (
                OPERATING_MODE as _op_mode,
            )
            from config import (
                SIGNAL_SOURCE as _live_signal_source,
            )
            from core.live_gate import (
                enforce_controlled_live_gate,
                enforce_live_runtime_invariants,
                enforce_model_gate_readiness,
                enforce_strategy_readiness_gate,
            )
            enforce_controlled_live_gate(
                _op_mode,
                controlled_live_enabled=_live_enabled,
            )
            enforce_live_runtime_invariants(_op_mode)
            _strategy_family = (
                "machine" if _live_signal_source == "machine"
                else ("tsmom" if _live_signal_source == "tsmom" else None)
            )
            enforce_strategy_readiness_gate(
                _op_mode,
                strategy_family=_strategy_family,
            )
            enforce_model_gate_readiness(_op_mode)
            # Codex preflight port (2026-07-12): ADDITIONAL live-latch
            # condition — venue capability preflight (clock skew, position
            # mode, margin mode, protective-order path, min-notional floors).
            # Never replaces the signed-checklist latch above; dormant in
            # OBSERVATION/PAPER (guard keeps those startups untouched).
            if (_op_mode or "").upper() == "CONTROLLED_LIVE":
                from config import WHITELIST_SYMBOLS as _wl_syms
                from core.live_gate import enforce_live_preflight_gate
                # Connected venues only: an intentionally unconfigured venue
                # must not block live startup; ZERO connected venues still
                # fails closed inside run_live_preflight.
                enforce_live_preflight_gate(
                    _op_mode,
                    exchanges=self.active_exchanges,
                    symbols=sorted(_wl_syms),
                    notify=getattr(self.notifier, "send", None),
                    expect_one_way={
                        str(_exn).lower():
                            (str(_exn).lower() in self.order_mgr._oneway_mode)
                        for _exn in self.active_exchanges
                    },
                )
                # Reconciliation may place protection for imported positions,
                # so it is strictly after every authorization check and the
                # read-only venue preflight above.
                self._complete_authorized_live_startup_reconciliation(_op_mode)
        except SystemExit:
            raise
        except Exception as _e:
            if ((_op_mode or "").upper() == "CONTROLLED_LIVE") or not DRY_RUN:
                raise SystemExit(
                    "[LiveGate] REFUSING TO START: live safety checks errored: "
                    f"{_e}"
                ) from _e
            logger.warning(f"[LiveGate] check error (non-fatal): {_e}")

        logger.info("[Engine] Bot started — Ctrl+C to stop")
        # `schedule` is module-global. Clear jobs left by a prior in-process
        # watchdog restart before registering this engine's jobs.
        schedule.clear()
        # 2026-06-15: announce the active signal source up front so it's unambiguous
        # which engine is deciding trades (the cycle/monitor logs alone read "[Claude]").
        from config import SIGNAL_SOURCE as _SIG_RUN
        if _SIG_RUN == "tsmom":
            logger.info(
                "[Engine] Signal source: TSMOM (long-only majors, capital-preservation) "
                "— Claude/MCP entry scoring AND position monitor DISABLED")
        elif _SIG_RUN == "machine":
            logger.info(
                "[Engine] Signal source: MACHINE (deterministic OHLCV ensemble) "
                "- Claude/MCP entry scoring AND position monitor DISABLED")
        else:
            logger.info(f"[Engine] Signal source: {_SIG_RUN} (Claude/MCP scoring path)")
        if _SIG_RUN != "machine":
            # The self-healing strategy-adaptation and promotion loops tune the
            # MACHINE signal and write data/adaptive_machine_config.json — which is
            # ONLY read by MachineSignal. Under any other source those writes are
            # never consumed, so their "promoted" reports are DORMANT (no live
            # effect). Say so plainly to avoid false "self-improvement is acting" reads.
            logger.info(
                f"[Engine] NOTE: machine-strategy self-improvement (self-healing adapt + "
                f"promotion loop) is DORMANT under SIGNAL_SOURCE={_SIG_RUN} — adaptive "
                f"config writes are not consumed by the live signal.")
        self.notifier.alert(
            f"Bot started | {TRADING_MODE.upper()} | "
            f"signal={_SIG_RUN} | "
            f"{'DRY RUN $' + str(int(self.order_mgr.wallet.start_balance)) if DRY_RUN else 'LIVE'}"
        )

        # ── CLAUDE PORTFOLIO: single unified cycle (replaces per-exchange scans + MCP brain)
        schedule.every(PORTFOLIO_CYCLE_SEC).seconds.do(self._portfolio_cycle)
        _pos_mon_sec = CLAUDE_PORTFOLIO.get("position_monitor_sec", 120)
        schedule.every(_pos_mon_sec).seconds.do(self._run_mcp_position_monitor)
        schedule.every(LEARN_INTERVAL).seconds.do(self._run_learning)
        schedule.every(6).hours.do(self._run_promotion_funnel)
        try:
            from config import ENABLE_DCA, ENABLE_REBALANCE
        except ImportError:
            ENABLE_DCA = False
            ENABLE_REBALANCE = False
        if ENABLE_DCA:
            schedule.every(4).hours.do(self._run_dca)
        if ENABLE_REBALANCE:
            schedule.every(24).hours.do(self._run_rebalance)

        # Spot portfolio evaluation (30 min) + capital allocation (15 min)
        if self.spot_manager:
            schedule.every(30).minutes.do(self._run_spot_evaluation)
        if self.capital_allocator:
            schedule.every(15).minutes.do(self._run_capital_allocation)

        # Daily self-check at midnight UTC
        schedule.every().day.at("00:00", "UTC").do(self._daily_self_check)
        schedule.every().day.at("00:00", "UTC").do(self._daily_summary)
        # Health watchdog tick — once per minute. Cheap, in-process.
        if self.watchdog is not None:
            schedule.every(60).seconds.do(self.watchdog.tick)
        if getattr(self, "self_healer", None) is not None:
            schedule.every(15).minutes.do(self._run_self_healing)
        # Autonomous PAPER self-improvement (WS4): mutate->validate->promote, hard-latched
        # to PAPER by core.decision.guardrails. Default-on in PAPER via SELF_IMPROVE_ENABLED;
        # never armed off-PAPER (the check is re-evaluated inside _run_self_improve too).
        try:
            from core.decision.guardrails import self_improve_enabled as _si_enabled

            if _si_enabled():
                schedule.every(LEARN_INTERVAL).seconds.do(self._run_self_improve)
                logger.info("[Engine] Self-improvement loop scheduled (PAPER, learn cadence)")
        except Exception:
            pass
        # ── Deep-breakout ACTIVE PAPER lane (owner directive 2026-07-11) ──
        # Places real PAPER orders (cohort strategy_family='deep_breakout';
        # ~33% WR by design — excluded from the accuracy-band metrics). The
        # log-only BreakoutProbeAgent keeps collecting frozen-gate evidence in
        # parallel. Scheduled ONLY in PAPER; the lane additionally re-refuses
        # every tick via assert_paper_only().
        try:
            from config import DEEP_BREAKOUT_LANE as _DBL_SCHED
            if _DBL_SCHED.get("enabled"):
                if DRY_RUN:
                    _dbl_tick = max(60, int(_DBL_SCHED.get("tick_sec", 300)))
                    schedule.every(_dbl_tick).seconds.do(self._run_deep_breakout_lane)
                    logger.info(
                        f"[Engine] Deep-breakout PAPER lane scheduled "
                        f"(venues={list(_DBL_SCHED.get('venues', ()))}, "
                        f"tick={_dbl_tick}s, 4h-boundary evaluation)")
                else:
                    logger.error(
                        "[Engine] DEEP_BREAKOUT_LANE_ENABLED but mode is "
                        "CONTROLLED_LIVE — lane REFUSED (PAPER-only hard gate; "
                        "going live requires an owner decision + code change)")
        except Exception as _dbl_e:
            logger.debug(f"[Engine] deep-breakout lane scheduling skipped: {_dbl_e}")

        # Balance refresh happens inside _claude_portfolio_cycle, but also on schedule
        schedule.every(15).minutes.do(self._log_balances)
        if not DRY_RUN:
            # Ghost detection cadence — was 5 minutes, which left up to a
            # 5-minute window between an exchange-side SL/TP fill and the
            # bot noticing the position vanished. That window is the root
            # cause of the high ghost_sync rate (memory:
            # project_ghost_closes_bypass_safety_rails_2026_04_26).
            # 15s is well within rate-limit budgets for fetch_positions
            # across all three exchanges (12 calls/min/venue ≪ any
            # documented limit) and brings ghost detection close enough
            # to the SL/TP monitor's own 10s cadence that warehouse PnL
            # tracks live state in near real time.
            schedule.every(15).seconds.do(self._sync_positions)

        if TRADING_MODE in ("portfolio", "all"):
            schedule.every(PORTFOLIO_RESCAN_MINUTES).minutes.do(
                self._rescan_portfolio)

        logger.info("[Engine] Running initial Claude portfolio cycle + learn...")
        self._run_learning()
        self._portfolio_cycle()   # First deterministic cycle
        self._run_mcp_position_monitor() # Position monitor after first cycle

        # Start dedicated SL/TP monitor thread — runs every 10s, never blocked by scans
        self._stop_event = threading.Event()
        self._sltp_thread = threading.Thread(
            target=self._sltp_monitor_loop,
            args=(self._stop_event,),
            daemon=True, name="sltp-monitor")
        self._sltp_thread.start()
        logger.info("[Engine] SL/TP monitor thread started (10s interval)")

        # Phase A multi-agent shadow runner — daemon thread, daemon=True so
        # bot exits cleanly even if shadow loop hangs. Live trading is never
        # blocked by shadow work.
        if self._shadow_runner is not None:
            self._shadow_thread = threading.Thread(
                target=self._shadow_loop,
                args=(self._stop_event,),
                daemon=True, name="shadow-runner")
            self._shadow_thread.start()
            try:
                from config import SHADOW_MODE
                _interval = SHADOW_MODE.get("tick_interval_s", 300)
            except Exception:
                _interval = 300
            logger.info(f"[Engine] Shadow runner thread started ({_interval}s interval)")

        # Register signal handlers for clean shutdown
        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else signum
            logger.info(f"[Engine] Signal {sig_name} received — shutting down")
            self._stop_event.set()
            self._shutdown()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        atexit.register(self._shutdown)

        try:
            while True:
                schedule.run_pending()

                # Watchdog: restart SL/TP thread if it died (but not during shutdown)
                if not self._sltp_thread.is_alive() and not getattr(self, '_shutdown_done', False) and not self._stop_event.is_set():
                    logger.warning("[Engine] SL/TP thread DIED — restarting")
                    self._stop_event = threading.Event()
                    self._sltp_thread = threading.Thread(
                        target=self._sltp_monitor_loop,
                        args=(self._stop_event,),
                        daemon=True, name="sltp-monitor")
                    self._sltp_thread.start()
                    open_count = self.tracker.count_open()
                    self.notifier.error(
                        f"CRITICAL: SL/TP monitor thread crashed and was restarted.\n"
                        f"Open positions: {open_count}\n"
                        f"All positions were UNMONITORED until restart.\n"
                        f"Check logs for the crash cause.")

                # Watchdog: check exchange connectivity every 60s (time-based)
                if time.time() - self._last_health_check >= 60:
                    self._check_exchange_health()

                # Heartbeat: write status file every 60s for external monitors
                if time.time() - self._last_heartbeat >= 60:
                    self._last_heartbeat = time.time()
                    self._write_heartbeat()

                self._print_live_status()
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("[Engine] Shutdown received")
            self._stop_event.set()
            self._shutdown()
        except SystemExit as exc:
            logger.info(f"[Engine] SystemExit received (code={exc.code!r})")
            self._stop_event.set()
            self._shutdown()
            if exc.code not in (None, 0):
                raise
        except Exception as e:
            logger.critical(f"[Engine] FATAL ERROR in main loop: {e}", exc_info=True)
            self._stop_event.set()
            try:
                open_count = self.tracker.count_open()
                self.notifier.error(
                    f"FATAL: Main loop crashed!\n"
                    f"Error: {e}\n"
                    f"Open positions: {open_count}\n"
                    f"Bot will attempt restart in 30s...")
            except Exception:
                pass
            self._shutdown()
            # Exit with non-zero code so auto_restart.bat restarts the process
            # (avoids recursive self.run() which would overflow the stack on repeated crashes)
            logger.info("[Engine] Exiting for auto-restart in 10s...")
            import sys
            sys.exit(1)

    def _shutdown(self):
        if getattr(self, '_shutdown_done', False):
            return
        self._shutdown_done = True
        schedule.clear()
        logger.info("[Engine] Shutting down...")
        s = self.tracker.summary()
        try:
            extras = self._build_daily_summary_extras(0.0)
        except Exception:
            extras = {}
        self.notifier.daily_summary(
            s["total_trades"], s["wins"], s["losses"], s["total_pnl"], 0.0,
            gross_pnl=s.get("gross_pnl"),
            total_fees=s.get("total_fees"),
            avg_win=s.get("avg_win"),
            avg_loss=s.get("avg_loss"),
            paper_trades=s.get("paper_trades"),
            live_trades=s.get("live_trades"),
            open_positions=s.get("open_positions"),
            **extras,
        )
        try:
            summary = self.order_mgr.compliance.export_summary()
            if summary:
                logger.info(
                    f"[Compliance] {summary.get('month')} — "
                    f"trades={summary.get('trades')} "
                    f"pnl={summary.get('total_pnl'):+.4f} USDT "
                    f"fees={summary.get('total_fees'):.4f} USDT")
        except Exception:
            pass
        self._run_learning()
        self._print_full_summary()
        if self.realtime_streams is not None:
            try:
                self.realtime_streams.close()
            except Exception as exc:
                logger.debug(f"[Realtime] shutdown failed: {exc}")
        logger.info("[Engine] Stopped.")

    def _daily_summary(self):
        s = self.tracker.summary()
        if DRY_RUN:
            balance = self.order_mgr.wallet.total_balance()
        else:
            # Daily summary shows user's actual wallet (free + locked margin),
            # NOT just free margin. Use equity extractor for an accurate picture.
            balance = 0.0
            for name, ex in self.active_exchanges.items():
                if name in _UNIFIED_EXCHANGES:
                    try:
                        bal = ex.fetch_balance("spot")
                        balance += self._extract_usdt_equity(bal, name)
                    except Exception:
                        pass
                else:
                    for mtype in ("spot", "futures"):
                        try:
                            bal = ex.fetch_balance(mtype)
                            balance += self._extract_usdt_equity(bal, name)
                        except Exception:
                            pass
        # Build rich extras for the daily email
        extras = self._build_daily_summary_extras(balance)
        self.notifier.daily_summary(
            s["total_trades"], s["wins"], s["losses"], s["total_pnl"], balance,
            gross_pnl=s.get("gross_pnl"),
            total_fees=s.get("total_fees"),
            avg_win=s.get("avg_win"),
            avg_loss=s.get("avg_loss"),
            paper_trades=s.get("paper_trades"),
            live_trades=s.get("live_trades"),
            open_positions=s.get("open_positions"),
            **extras,
        )

    def _build_daily_summary_extras(self, balance: float) -> dict:
        """Compute per-exchange, per-strategy, hour, best/worst, drawdown."""
        extras: dict = {}
        try:
            closed = list(self.tracker._closed)
        except Exception:
            closed = []

        # Per-exchange
        per_ex: dict = {}
        for p in closed:
            ex = getattr(p, "exchange", "?") or "?"
            d = per_ex.setdefault(ex, {"trades": 0, "wins": 0, "pnl": 0.0})
            d["trades"] += 1
            d["pnl"]    += float(p.pnl or 0)
            if (p.pnl or 0) > 0:
                d["wins"] += 1
        for ex, d in per_ex.items():
            d["win_rate"] = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
        if per_ex:
            extras["per_exchange"] = per_ex

        # Per-strategy
        per_strat: dict = {}
        for p in closed:
            s = getattr(p, "strategy", "?") or "?"
            d = per_strat.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
            d["trades"] += 1
            d["pnl"]    += float(p.pnl or 0)
            if (p.pnl or 0) > 0:
                d["wins"] += 1
        for s, d in per_strat.items():
            d["win_rate"] = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
        if per_strat:
            extras["per_strategy"] = per_strat

        # Best / worst
        if closed:
            best = max(closed, key=lambda p: (p.pnl or 0))
            worst = min(closed, key=lambda p: (p.pnl or 0))
            extras["best_trade"] = {
                "symbol": best.symbol, "side": best.side,
                "pnl": float(best.pnl or 0),
            }
            extras["worst_trade"] = {
                "symbol": worst.symbol, "side": worst.side,
                "pnl": float(worst.pnl or 0),
            }

        # Drawdown from peak equity (running)
        try:
            equity = 0.0
            peak = balance
            running = balance - sum(float(p.pnl or 0) for p in closed)
            peak = running
            for p in sorted(closed, key=lambda x: float(getattr(x, "close_time", 0) or 0)):
                running += float(p.pnl or 0)
                if running > peak:
                    peak = running
            equity = running
            if peak > 0:
                extras["peak_equity"] = peak
                extras["drawdown_pct"] = (equity - peak) / peak * 100
        except Exception:
            pass

        # Unrealized open PnL
        try:
            open_pnl = 0.0
            for p in self.tracker.get_open():
                ex = self.active_exchanges.get(getattr(p, "exchange", ""))
                if not ex:
                    continue
                try:
                    tkr = ex.fetch_ticker(p.symbol, p.market_type)
                    last = float(tkr.get("last", 0) or 0)
                    if last <= 0:
                        continue
                    if p.side == "buy":
                        open_pnl += (last - p.entry_price) * p.size
                    else:
                        open_pnl += (p.entry_price - last) * p.size
                except Exception:
                    pass
            extras["open_pnl"] = open_pnl
        except Exception:
            pass

        # Halt status
        try:
            if self.risk.is_halted:
                extras["halt_status"] = self.risk.halt_reason or "HALTED"
        except Exception:
            pass

        # Hour scores from knowledge model (now that hour_scores include net_pnl)
        try:
            from core.knowledge_model import KnowledgeModel
            km = KnowledgeModel()
            hs = km._model.get("hour_scores") if hasattr(km, "_model") else None
            if hs:
                extras["hour_scores"] = hs
        except Exception:
            pass

        return extras

    def _print_live_status(self):
        s        = self.tracker.summary()
        uptime_m = int((time.time() - self._start_time) / 60)
        mode     = "[DRY]" if DRY_RUN else "[LIVE]"
        halted   = f" [HALTED: {self.risk.halt_reason}]" if self.risk.is_halted else ""
        logger.debug(
            f"{mode}{halted} up={uptime_m}m scans={self._cycle} "
            f"pnl={s['total_pnl']:+.4f} fees={s['total_fees']:.4f} "
            f"open={s['open_positions']} W={s['wins']} L={s['losses']}")

    def _print_full_summary(self):
        s      = self.tracker.summary()
        uptime = (time.time() - self._start_time) / 3600
        wallet = self.order_mgr.wallet.total_balance() if DRY_RUN else 0
        table  = Table(title="Trading Bot — Final Summary", box=box.ROUNDED)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value",  style="green")
        c = "green" if s["total_pnl"] >= 0 else "red"
        table.add_row("Uptime",        f"{uptime:.1f} hours")
        table.add_row("Mode",          f"{'DRY RUN ($' + str(int(self.order_mgr.wallet.start_balance)) + ' start)' if DRY_RUN else 'LIVE'}")
        table.add_row("Trade Mode",    TRADING_MODE.upper())
        table.add_row("Exchanges",     str(list(self.active_exchanges.keys())))
        table.add_row("Scan Cycles",   str(self._cycle))
        table.add_row("Total Trades",  str(s["total_trades"]))
        table.add_row("Wins / Losses", f"{s['wins']} / {s['losses']}")
        table.add_row("Win Rate",      f"{s['win_rate']:.1f}%")
        table.add_row("Gross PnL",     f"{s['gross_pnl']:+.4f} USDT")
        table.add_row("Fees Paid",     f"-{s['total_fees']:.4f} USDT")
        table.add_row("Net PnL",       f"[{c}]{s['total_pnl']:+.4f}[/{c}] USDT")
        table.add_row("Avg Win",       f"{s['avg_win']:+.4f} USDT")
        table.add_row("Avg Loss",      f"{s['avg_loss']:+.4f} USDT")
        if DRY_RUN:
            table.add_row("Paper Wallet", f"{wallet:.4f} USDT")
        table.add_row("Open Pos",      str(s["open_positions"]))
        table.add_row("Daily PnL",     f"[{c}]{self.risk.daily_pnl:+.4f}[/{c}] USDT")
        if self.risk.is_halted:
            table.add_row("Halted", f"YES — {self.risk.halt_reason}")
        else:
            table.add_row("Halted", "No")
        table.add_row(
            "Blacklisted",
            str(list(self.order_mgr.blacklist.get_all().keys())) or "None")
        console.print(table)

