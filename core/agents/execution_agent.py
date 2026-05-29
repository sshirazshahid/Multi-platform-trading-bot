"""ExecutionAgent — simulates fills and writes shadow_decisions rows.

NEVER calls OrderManager.open_position. Phase A is log-only.
"""
from __future__ import annotations

from typing import Optional

from .base_agent import Proposal


class ExecutionAgent:
    """Log-only execution simulator."""

    SLIPPAGE_BPS = 5.0   # taker open
    EXIT_SLIPPAGE_BPS = 5.0
    FEE_BPS_PER_SIDE = 6.0  # ~0.06% taker

    def __init__(self, warehouse=None):
        self.name = "ExecutionAgent"
        self._wh = warehouse

    def execute_shadow(
        self,
        proposal: Proposal,
        *,
        projected_notional_current: float,
        projected_notional_alt: float,
        model_version: str = "shadow_v1",
        outcome_price: Optional[float] = None,
    ) -> dict:
        """Project P&L assuming entry filled at proposal.entry + slippage,
        and a hypothetical close at outcome_price. If outcome_price is None,
        we project at TP (best case for the agent's reason).
        Writes one row to shadow_decisions and returns the row dict.
        """
        side = proposal.side
        slip_open = proposal.entry * self.SLIPPAGE_BPS / 10_000.0
        entry_filled = proposal.entry + slip_open if side == "buy" else proposal.entry - slip_open
        exit_target = outcome_price if outcome_price is not None else proposal.tp

        # Naive directional PnL on alt notional (the comparison candidate)
        size_alt = projected_notional_alt / entry_filled if entry_filled > 0 else 0.0
        if side == "buy":
            move = exit_target - entry_filled
        else:
            move = entry_filled - exit_target
        gross_pnl_alt = move * size_alt
        fees_alt = (entry_filled + exit_target) * size_alt * self.FEE_BPS_PER_SIDE / 10_000.0
        net_pnl_alt = gross_pnl_alt - fees_alt

        # Same calc for current notional (often smaller)
        size_cur = projected_notional_current / entry_filled if entry_filled > 0 else 0.0
        gross_pnl_cur = move * size_cur
        fees_cur = (entry_filled + exit_target) * size_cur * self.FEE_BPS_PER_SIDE / 10_000.0
        net_pnl_cur = gross_pnl_cur - fees_cur

        row = {
            "ts": proposal.ts,
            "model_version": model_version,
            "symbol": proposal.symbol,
            "side": side,
            "decision": "ALLOW",
            "p_win": proposal.confidence,
            "sim_pnl": net_pnl_cur,
            "sim_r_multiple": (net_pnl_cur / fees_cur) if fees_cur > 0 else 0.0,
            "agent_id": proposal.agent_id,
            "proposal_id": proposal.proposal_id,
            "proposed_at": proposal.ts,
            "vetoed_by": None,
            "veto_reason": None,
            "projected_notional_current": projected_notional_current,
            "projected_notional_alt": projected_notional_alt,
            "projected_pnl": net_pnl_alt,
            "projected_fee": fees_alt,
        }
        if self._wh is not None:
            self._write(row)
        return row

    def log_veto(self, proposal: Proposal, vetoed_by: str, reason: str) -> dict:
        row = {
            "ts": proposal.ts,
            "model_version": "shadow_v1",
            "symbol": proposal.symbol,
            "side": proposal.side,
            "decision": "VETO",
            "p_win": proposal.confidence,
            "sim_pnl": 0.0,
            "sim_r_multiple": 0.0,
            "agent_id": proposal.agent_id,
            "proposal_id": proposal.proposal_id,
            "proposed_at": proposal.ts,
            "vetoed_by": vetoed_by,
            "veto_reason": reason,
            "projected_notional_current": 0.0,
            "projected_notional_alt": 0.0,
            "projected_pnl": 0.0,
            "projected_fee": 0.0,
        }
        if self._wh is not None:
            self._write(row)
        return row

    def _write(self, row: dict):
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        sql = f"INSERT INTO shadow_decisions ({cols}) VALUES ({placeholders})"
        conn = self._wh._conn()
        conn.execute(sql, tuple(row.values()))
        conn.commit()
