"""
core/engine/cycle.py — BotEngine _CycleMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403


class _CycleMixin:
    def _portfolio_cycle(self):
        """Single unified cycle: gather data -> deterministic signal -> execute."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
            logger.warning("[Portfolio] MCP Brain not available — skipping portfolio cycle")
            return

        # 2026-06-13: roll daily counters at cycle start, decoupled from the
        # entry path. can_trade() (the other rollover caller) is only reached
        # via _execute_open, so when entries are gated upstream for >1 day the
        # daily_pnl/trades_today counters froze at stale values and dashboards
        # read a stale "today". This keeps them fresh regardless of entries.
        if self.risk:
            try:
                self.risk.roll_day_if_needed()
            except Exception as e:
                logger.debug(f"[Risk] day-rollover check: {e}")

        # Refresh closed-loop mutations from latest post-mortems
        if self.auto_mutator:
            try:
                self.auto_mutator.refresh()
            except Exception as e:
                logger.debug(f"[Claude] AutoMutator refresh: {e}")

        self._log_balances()

        all_coins = self._collect_all_coins()
        open_positions = self._build_position_snapshot()
        risk_envelope = self._build_risk_envelope()
        recent_trades = self._get_recent_trades(20)

        # News/sentiment context removed (De-Emotion 2026-08-04).
        news_context = {}

        # Signal source switch: each source returns the same action-dict
        # contract, so execution/risk/warehouse plumbing below stays shared.
        from config import SIGNAL_SOURCE
        _sig_tag = {
            "tsmom": "TSMOM",
            "machine": "Machine",
            "s3": "S3",
            "mcp": "MCPDet",
            "mcp_det": "MCPDet",
            "none": "NoSignal",
        }.get(SIGNAL_SOURCE, SIGNAL_SOURCE)
        logger.info(
            f"[{_sig_tag}] Portfolio cycle: {len(all_coins)} coins, "
            f"{len(open_positions)} open positions, "
            f"balance=${risk_envelope.get('total_balance', 0):.0f}")

        if SIGNAL_SOURCE == "tsmom":
            _signal = self._tsmom_signal()
        elif SIGNAL_SOURCE == "s3":
            _signal = self._s3_signal()
        elif SIGNAL_SOURCE == "machine":
            _signal = self._machine_signal()
        elif SIGNAL_SOURCE in ("mcp", "mcp_det"):
            _signal = self._mcp_det_signal()
        elif SIGNAL_SOURCE == "none":
            _signal = self._none_signal()
        else:
            raise ValueError(
                f"SIGNAL_SOURCE={SIGNAL_SOURCE!r} is not a supported deterministic "
                "source (mcp/mcp_det/tsmom/machine/s3/none). LLM path removed."
            )
        actions = _signal.analyze_portfolio(
            coins=all_coins,
            open_positions=open_positions,
            exchange_balances=dict(self._balances),
            risk_envelope=risk_envelope,
            recent_trades=recent_trades,
            news_context=news_context,
        )

        # Deterministic-source observability: periodically log WHY it is quiet.
        if SIGNAL_SOURCE in ("tsmom", "machine", "s3"):
            import time as _t_w
            _watch_attr = f"_{SIGNAL_SOURCE}_watch_last"
            if _t_w.time() - getattr(self, _watch_attr, 0.0) >= 1800:
                try:
                    logger.info(f"[{_sig_tag}] watch: {_signal.watch_summary()}")
                except Exception:
                    pass
                setattr(self, _watch_attr, _t_w.time())

        if not actions:
            logger.info(f"[{_sig_tag}] No actions this cycle")
            self._cycle += 1
            return

        # Prefer dual-model FIT_BAND_PAPER bases among OPENs (2026-07-29).
        # Soft ranking only — does not invent edge; CLOSES stay first.
        try:
            from config import FIT_BAND_PAPER_BASES as _FIT_BASES
        except ImportError:
            _FIT_BASES = frozenset()
        if _FIT_BASES:
            def _base_of(_a: dict) -> str:
                return str(_a.get("symbol") or "").split("/")[0].split(":")[0].upper()

            _closes = [a for a in actions if a.get("type") == "CLOSE"]
            _opens = [a for a in actions if a.get("type") == "OPEN"]
            _other = [
                a for a in actions if a.get("type") not in ("OPEN", "CLOSE")
            ]
            _fit = [a for a in _opens if _base_of(a) in _FIT_BASES]
            _rest = [a for a in _opens if _base_of(a) not in _FIT_BASES]
            if _fit:
                actions = _closes + _fit + _rest + _other

        # Cap actions per cycle — provenance: the dropped tail is logged as
        # rejection rows so capped decisions never silently vanish (E-4).
        for _dropped in actions[MAX_ACTIONS_PER_CYCLE:]:
            self._log_rejection(_dropped, "cycle_cap", stage="cycle_cap")
        actions = actions[:MAX_ACTIONS_PER_CYCLE]

        executed = 0
        for action in actions:
            try:
                if action["type"] == "OPEN":
                    if self._execute_open(action):
                        executed += 1
                    else:
                        # Provenance: _execute_open stashes reject_reason at
                        # every exit; unset stash logs as "unspecified".
                        self._log_rejection(
                            action,
                            action.get("reject_reason", "unspecified"),
                            stage="execute_open")
                elif action["type"] == "CLOSE":
                    if self._execute_close(action):
                        executed += 1
                else:
                    action["reject_reason"] = "unknown_action_type"
                    self._log_rejection(
                        action, "unknown_action_type", stage="dispatch"
                    )
            except Exception as e:
                logger.error(f"[Claude] Action execution error: {e}")
                action["reject_reason"] = "action_execution_exception"
                self._log_terminal_decision(
                    action,
                    outcome="error",
                    reason="action_execution_exception",
                    stage="dispatch",
                )

        # The mcp compatibility alias is deterministic and has no implicit
        # fund-operation authority. Capital moves require a separate explicit
        # operator workflow and still pass the entry-policy latch.

        self._cycle += 1
        logger.info(
            f"[Claude] Cycle complete: {executed}/{len(actions)} actions executed")

