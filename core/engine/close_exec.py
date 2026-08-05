"""
core/engine/close_exec.py — BotEngine _CloseExecMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import _canonical_exit_reason


class _CloseExecMixin:
    def _execute_close(self, action: dict) -> bool:
        """Find and close a position by ID. Returns True if closed."""
        # Spec §4: OBSERVATION mode must not send any live orders
        from config import OPERATING_MODE as _close_mode
        if _close_mode == "OBSERVATION":
            logger.info("[Mode] OBSERVATION — close blocked (no live orders)")
            return False

        position_id = action.get("position_id", "")
        source = str(action.get("source", "claude") or "claude").lower()
        reason = action.get("reason", "claude_portfolio_close")
        # Canonicalize the LLM's free-text rationale to a clean machine label so
        # the warehouse exit_reason stays a usable GROUP BY key (audit H8). The
        # raw prose is preserved in the log line + exit_decision_id below.
        canon_reason = _canonical_exit_reason(reason, source=source)

        if not position_id:
            logger.warning("[Claude] CLOSE action missing position_id")
            return False

        # Find position by ID (full or prefix match)
        target = None
        for p in self.tracker.get_open():
            if p.id == position_id or p.id.startswith(position_id):
                target = p
                break

        if not target:
            logger.info(f"[Claude] Position {position_id[:8]} not found — may be closed already")
            return False

        # A4 (audit 2026-06-21): tsmom positions own their own exit (momentum
        # flip / disaster stop, Phase 2b). The SL/TP loop and the 30s monitor
        # already enforce this (order_manager.check_sl_tp / _run_mcp_position_
        # monitor); _execute_close was the one path missing it, so a discretionary
        # portfolio-cycle CLOSE could market-close a tsmom hold. ALWAYS-ON invariant.
        from core.tsmom_signal import (
            is_tsmom_action as _is_tsmom_action,
        )
        from core.tsmom_signal import (
            is_tsmom_position as _is_tsmom_pos,
        )
        if _is_tsmom_pos(target) and not _is_tsmom_action(action):
            logger.info(
                f"[Claude] portfolio CLOSE skipped — tsmom owns its exit: "
                f"{target.symbol} {target.id[:8]}")
            return False

        # Deep-breakout lane positions (2026-07-11) likewise own their exit
        # (2.2xATR SL / 3R TP / 126-bar max-hold via the lane tick + the §2 8%
        # guardian). A discretionary portfolio CLOSE would clip the researched
        # 3R geometry. The lane itself closes via order_manager.close_position
        # directly, never through this path. ALWAYS-ON invariant.
        from core.deep_breakout_lane import (
            is_deep_breakout_position as _is_db_pos,
        )
        if _is_db_pos(target):
            logger.info(
                f"[Claude] portfolio CLOSE skipped — deep_breakout owns its "
                f"exit: {target.symbol} {target.id[:8]}")
            return False

        try:
            from core.machine_signal import (
                is_machine_action as _is_machine_action,
            )
            from core.machine_signal import (
                is_machine_position as _is_machine_pos,
            )
        except Exception:
            _is_machine_action = lambda _action: False
            _is_machine_pos = lambda _pos: False
        if _is_machine_pos(target) and not _is_machine_action(action):
            logger.info(
                f"[Claude] portfolio CLOSE skipped - machine owns its exit: "
                f"{target.symbol} {target.id[:8]}")
            return False

        # Find exchange client
        exchange = None
        for ex_name, ex in self.active_exchanges.items():
            if ex_name == target.exchange.lower() or ex_name in target.exchange.lower():
                exchange = ex
                break

        if not exchange:
            logger.warning(f"[Claude] Exchange for {target.exchange} not connected")
            return False

        # A4 (audit 2026-06-21): flag-gated discretionary-close guard. When
        # enabled, refuse a Claude-sourced CLOSE of a NON-disaster position so
        # SL/TP/trailing own the exit (mirrors the 2026-04-24 monitor-CLOSE
        # suppression). A genuine catastrophic loss (net <= -8% price, spec §2
        # disaster stop) still passes (escape hatch). Default-OFF => unchanged.
        # Algo-fallback CLOSE (source != "claude") is exempt — it only fires on a
        # real loss-cut.
        from config import PORTFOLIO_DISCRETIONARY_CLOSE_GUARD_ENABLED as _pdcg
        if _pdcg and action.get("source") == "claude":
            try:
                _tk = exchange.fetch_ticker(target.symbol, target.market_type)
                _last = float(_tk.get("last") or _tk.get("close") or 0)
                if _last > 0:
                    _, _net_pct, _ = self.order_mgr._net_pnl_at_price(target, _last)
                    if _net_pct > -8.0:   # not a disaster -> let SL/TP own it
                        logger.info(
                            f"[Claude] portfolio CLOSE suppressed (discretionary, "
                            f"non-disaster net={_net_pct:+.2f}%) — SL/TP/trailing own "
                            f"exits (2026-04-24 monitor-policy parity): "
                            f"{target.symbol} {target.id[:8]}")
                        return False
            except Exception as _e:
                logger.debug(f"[Claude] discretionary-close guard skipped: {_e}")

        logger.info(
            f"[Claude] EXECUTING CLOSE: {target.symbol} {target.side} "
            f"on {target.exchange} | reason={canon_reason} | rationale={reason[:80]}")

        try:
            # Provenance: thread the CLOSE decision's id so the warehouse row
            # receives exit_decision_id via _finalize_close. The canonical
            # reason is what lands in exit_reason; the prose stays in the log.
            self.order_mgr.close_position(
                exchange, target, canon_reason,
                decision_id=action.get("decision_id"))
            return True
        except Exception as e:
            logger.error(f"[Claude] close_position failed: {e}")
            return False

