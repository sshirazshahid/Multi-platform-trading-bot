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
``SIGNAL_SOURCE=mcp_det`` here; the default ``mcp`` path (MCPBrain) is untouched.
"""
from __future__ import annotations

import time
import uuid

from loguru import logger

from core.mcp_brain import ENTRY_COOLDOWN, MCPBrain


class MCPStrategyScorer:
    """Deterministic, LLM-free portfolio scorer over approved StrategySpec(s)."""

    def __init__(self, brain: MCPBrain | None = None, *, specs=None, warehouse=None):
        # Reuse the existing brain's data-fetch + deterministic scoring stack.
        self.brain = brain or MCPBrain()
        self.specs = list(specs or [])
        self.warehouse = warehouse
        self._last_trade_actions: list = []

    # ── universe ────────────────────────────────────────────────────────
    def _restrict_universe(self, coins: list) -> list:
        """Restrict to approved-spec base symbols; pass through when none approved."""
        try:
            from core.strategy_spec import approved_symbols

            allowed = approved_symbols(self.specs)
        except Exception:
            allowed = set()
        if not allowed:
            return list(coins)
        return [c for c in coins if str(c).split("/")[0].upper() in allowed]

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

        coins = self._restrict_universe(coins)

        data = b._fetch_all_data(coins)
        if data.get("sources_ok", 0) < 2:
            logger.warning("[MCPScorer] < 2 data sources — no actions")
            return []
        if news_context:
            data["news_context"] = news_context

        exchange_indicators = b._fetch_exchange_indicators(coins)

        actions: list = []
        if risk_envelope.get("max_new_positions", 0) > 0:
            try:
                actions = b._algorithmic_portfolio(
                    coins, data, exchange_indicators,
                    open_positions, exchange_balances, risk_envelope) or []
            except Exception as e:
                logger.error(f"[MCPScorer] deterministic scoring failed: {e}")
                actions = []

        # Provenance backstop: fresh decision_id + deterministic source tag.
        for a in actions:
            if isinstance(a, dict):
                if not a.get("decision_id"):
                    a["decision_id"] = str(uuid.uuid4())
                a["source"] = "algo_det"

        # LLM demoted to research-only: log a note, never blend it back in.
        self._log_research_note(actions)

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
