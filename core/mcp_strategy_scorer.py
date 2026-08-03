"""core/mcp_strategy_scorer.py — deterministic entry scorer (Phase B, PAPER-only).

Rebuilds mcp_brain's ALGORITHMIC layer as a thin, declarative wrapper that reads
approved ``StrategySpec``(s) + live market context and emits the standard
``analyze_portfolio`` action-dict WITHOUT any Claude/LLM value blended into the
entry score or confidence. The LLM is demoted to a research-only note logged to
``warehouse.claude_reviews`` (never read back into the entry decision).

It reuses the existing deterministic scoring in ``MCPBrain._algorithmic_portfolio``
/ ``_score_coin`` — the code that already "works without Claude" — so behavior is
identical to the algorithmic fallback path, minus the Claude blend entirely. The
action-dict contract and the SIGNAL_SOURCE seam are preserved: bot_engine routes
``SIGNAL_SOURCE=mcp`` and ``mcp_det`` both route here. ``mcp`` is retained as a
configuration compatibility alias; neither path grants an LLM order authority.
"""
from __future__ import annotations

import time
import uuid

from loguru import logger

from core.mcp_brain import ENTRY_COOLDOWN, MCPBrain

# F5 (2026-07-20 deep audit): loud empty-universe warning fires once per
# process, not once per construction, so restart loops cannot spam the log.
_EMPTY_UNIVERSE_WARNED = False
_EXECUTION_SPEC_IDS = frozenset({"MCP_DIRECTIONAL_PAPER"})


class MCPStrategyScorer:
    """Deterministic, LLM-free portfolio scorer over approved StrategySpec(s)."""

    def __init__(self, brain: MCPBrain | None = None, *, specs=None, warehouse=None):
        # Reuse the existing brain's data-fetch + deterministic scoring stack.
        self.brain = brain or MCPBrain()
        supplied_specs = specs if specs is not None else []
        self.spec_load_errors = tuple(getattr(supplied_specs, "errors", ()) or ())
        self.specs = list(supplied_specs)
        self.warehouse = warehouse
        self._last_trade_actions: list = []
        self._last_research_actions: list = []
        self._warn_if_universe_empty()

    def _warn_if_universe_empty(self) -> None:
        """F5 (2026-07-20 audit): loud one-time startup check for the
        silent-idle class. 2026-07-19 incident: the mcp/mcp_det lane produced
        zero entries for 8h because approved_paper_futures_routes() was empty
        and nothing said so. This scorer is only constructed on the
        mcp/mcp_det signal path, so construction IS the startup moment."""
        global _EMPTY_UNIVERSE_WARNED
        routes, reason = self._execution_routes()
        if routes:
            logger.info(
                f"[MCPScorer] startup universe check OK — {len(routes)} "
                "approved PAPER-futures base(s) route to execution")
            return
        if not _EMPTY_UNIVERSE_WARNED:
            _EMPTY_UNIVERSE_WARNED = True
            logger.warning(
                "[MCPScorer] STARTUP UNIVERSE EMPTY — "
                "approved_paper_futures_routes() returned NO routes "
                f"(reason: {reason or 'strategy_spec_no_active_paper_futures_universe'}). "
                "Every OPEN this scorer emits will be blocked and the "
                "mcp/mcp_det lane will sit SILENTLY IDLE (this class cost 8h "
                "on 2026-07-19). Check the StrategySpec catalog "
                "(promotion_status / market_type / venues) before trusting a "
                "quiet bot.")

    # ── universe ────────────────────────────────────────────────────────
    def _execution_routes(self) -> tuple[dict, str]:
        """Return approved PAPER-futures routes or a fail-closed reason."""
        if self.spec_load_errors:
            return {}, "strategy_spec_parse_error"
        try:
            from core.strategy_spec import approved_paper_futures_routes

            routes = approved_paper_futures_routes(
                self.specs,
                strategy_ids=_EXECUTION_SPEC_IDS,
            )
        except Exception as exc:
            logger.warning(f"[MCPScorer] invalid executable strategy spec: {exc}")
            return {}, "strategy_spec_invalid"
        if not routes:
            return {}, "strategy_spec_no_active_paper_futures_universe"
        return routes, ""

    def _filter_executable_opens(self, actions: list) -> list:
        """Keep research proposals broad, but return only approved OPEN routes."""
        routes, gate_reason = self._execution_routes()
        executable: list = []
        blocked_routes: list = []
        futures_types = {"futures", "future", "perpetual", "perp", "swap"}
        for action in actions:
            if not isinstance(action, dict) or str(action.get("type") or "").upper() != "OPEN":
                executable.append(action)
                continue
            symbol = str(action.get("symbol") or "").strip().upper()
            base = symbol.split("/", 1)[0].split(":", 1)[0]
            venue = str(action.get("exchange") or "").strip().lower()
            market_type = str(action.get("market_type") or "").strip().lower()
            allowed_venues = routes.get(base, frozenset())
            if (not gate_reason and market_type in futures_types
                    and venue in allowed_venues):
                executable.append(action)
            else:
                blocked_routes.append(f"{base or '?'}@{venue or '?'}")
        self._last_blocked_open_routes = blocked_routes
        if blocked_routes:
            reason = gate_reason or "strategy_spec_route_not_approved"
            detail = ", ".join(blocked_routes[:8])
            if len(blocked_routes) > 8:
                detail += f" +{len(blocked_routes) - 8} more"
            logger.warning(
                f"[MCPScorer] execution spec gate blocked {len(blocked_routes)} OPEN "
                f"action(s) [{detail}]: {reason}; research candidates remain available"
            )
        return executable

    # ── main entry point (action-dict contract) ─────────────────────────
    def analyze_portfolio(self, coins: list, open_positions: list,
                          exchange_balances: dict, risk_envelope: dict,
                          recent_trades: list, news_context: dict = None) -> list:
        """Deterministic entry scoring. Returns the standard OPEN/CLOSE action list.

        No Claude/LLM output can influence any field here — the CLI is never called
        on this path. Any advisory note is logged separately, after the decision.
        """
        b = self.brain
        if not getattr(b, "_enabled", True):
            return []

        now = time.time()
        if now - b._last_entry_run < ENTRY_COOLDOWN:
            return []
        b._last_entry_run = now

        # Score the full research universe so candidate/shadow evidence keeps
        # accumulating. Only the returned OPEN actions are execution-filtered.
        coins = list(coins)
        data = b._fetch_all_data(coins)
        if data.get("sources_ok", 0) < 2:
            logger.warning("[MCPScorer] < 2 data sources — no actions")
            return []
        if news_context:
            data["news_context"] = news_context

        exchange_indicators = b._fetch_exchange_indicators(coins)

        research_actions: list = []
        if risk_envelope.get("max_new_positions", 0) > 0:
            try:
                research_actions = b._algorithmic_portfolio(
                    coins, data, exchange_indicators,
                    open_positions, exchange_balances, risk_envelope) or []
            except Exception as e:
                logger.error(f"[MCPScorer] deterministic scoring failed: {e}")
                research_actions = []

        self._last_research_actions = list(research_actions)
        actions = self._filter_executable_opens(research_actions)

        # Provenance backstop: fresh decision_id + deterministic source tag.
        for a in actions:
            if isinstance(a, dict):
                if not a.get("decision_id"):
                    a["decision_id"] = str(uuid.uuid4())
                a["source"] = "algo_det"

        # LLM demoted to research-only: log a note, never blend it back in.
        self._log_research_note(research_actions)

        self._last_trade_actions = actions
        try:
            b._last_trade_actions = actions
            b._log_decisions({"actions": actions}, "portfolio")
            b._save_state()
        except Exception as e:
            logger.debug(f"[MCPScorer] state persist skipped: {e}")

        return actions

    def get_trade_actions(self) -> list:
        return list(self._last_trade_actions)

    # ── position monitor delegates to the deterministic brain path ──────
    def monitor_positions(self, positions: list) -> dict:
        return self.brain.monitor_positions(positions)

    # ── research-only advisory logging (never feeds the entry decision) ──
    def _log_research_note(self, actions: list) -> None:
        if self.warehouse is None or not actions:
            return
        try:
            symbols = [a.get("symbol") for a in actions if isinstance(a, dict)]
            self.warehouse.record_claude_review(
                ref_type="candidate",
                ref_id=None,
                task="research_note",
                decision="LOGGED",
                confidence=None,
                commentary=f"deterministic entry set (LLM advisory only): {symbols}",
            )
        except Exception as e:
            logger.debug(f"[MCPScorer] research-note log skipped: {e}")
