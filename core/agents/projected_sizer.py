"""ProjectedSizer — dual notional projection for shadow_decisions.

current = what the live risk_manager would size today
alt     = the candidate sizing config (default $200 fixed)
"""
from __future__ import annotations

from typing import Optional

from .base_agent import Proposal


def project(
    proposal: Proposal,
    *,
    free_balance_usdt: float,
    size_pct: float = 0.50,
    leverage: int = 2,
    alt_notional: float = 200.0,
    n_concurrent: int = 3,
) -> tuple[float, float]:
    """Returns (current_notional, alt_notional) in USDT."""
    safe_n = max(1, n_concurrent)
    current = max(0.0, size_pct * leverage * free_balance_usdt / safe_n)
    return current, alt_notional


def make_sizer(
    *,
    free_balance_provider,
    size_pct: float = 0.50,
    leverage: int = 2,
    alt_notional: float = 200.0,
    n_concurrent: int = 3,
):
    """Returns a callable(proposal) -> (cur, alt) for AgentCoordinator."""
    def _sizer(p: Proposal) -> tuple[float, float]:
        try:
            bal = float(free_balance_provider())
        except Exception:
            bal = 0.0
        return project(
            p, free_balance_usdt=bal, size_pct=size_pct,
            leverage=leverage, alt_notional=alt_notional,
            n_concurrent=n_concurrent,
        )
    return _sizer
