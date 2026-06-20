"""Authoritative pre-trade risk guard — enforces the CLAUDE.md §2 hard limits.

This is a PURE function of plain numbers (no NautilusTrader import), so it is
unit-testable in isolation and is the single source of truth for the limits.
The NT strategy computes the inputs from its portfolio/cache and calls
``check_order`` before every ``submit_order``; a non-allowed result must block
the order and be logged as a SKIP.

CLAUDE.md §2 (the risk constitution):
  * Max 3% of capital risked on any single trade (risk = stop distance × notional).
  * Max 12% total open GROSS NOTIONAL exposure across ALL positions.
  * 8% stop-loss from entry is mandatory (a position with no stop is not allowed).
  * Max 2.5x leverage (TSMOM preservation uses 1x).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardLimits:
    """CLAUDE.md §2 values. Defaults match config.py / the constitution."""
    max_risk_per_trade_pct: float = 3.0
    max_gross_exposure_pct: float = 12.0
    max_stop_loss_pct: float = 8.0
    max_leverage: float = 2.5


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str = ""


def check_order(
    *,
    equity: float,
    order_notional: float,
    existing_gross_notional: float,
    sl_pct: float,
    leverage: float,
    limits: GuardLimits = GuardLimits(),
) -> GuardResult:
    """Return ALLOW/Deny for a proposed new order.

    Args:
        equity: current account equity (quote ccy).
        order_notional: gross notional of the NEW order (qty × price × leverage exposure).
        existing_gross_notional: sum of |notional| over ALL currently-open positions
            (PORTFOLIO-WIDE — not just this instrument).
        sl_pct: stop-loss distance from entry, in percent (must be > 0).
        leverage: leverage for this order.
    """
    if equity <= 0:
        return GuardResult(False, f"non-positive equity ({equity})")

    # Leverage cap (§2: max 2.5x).
    if leverage > limits.max_leverage + 1e-9:
        return GuardResult(False, f"leverage {leverage} > max {limits.max_leverage}")

    # Mandatory stop: a position with no stop, or a stop wider than the §2 disaster
    # stop, is not allowed (fail-closed).
    if sl_pct <= 0:
        return GuardResult(False, "missing stop-loss (sl_pct<=0)")
    if sl_pct > limits.max_stop_loss_pct + 1e-9:
        return GuardResult(False, f"stop {sl_pct}% > max {limits.max_stop_loss_pct}%")

    # 3% max risk per trade: stop distance × notional must not exceed 3% of equity.
    risk_amount = (sl_pct / 100.0) * order_notional
    max_risk = (limits.max_risk_per_trade_pct / 100.0) * equity
    if risk_amount > max_risk + 1e-9:
        return GuardResult(
            False,
            f"risk ${risk_amount:.2f} > {limits.max_risk_per_trade_pct}% of equity "
            f"(${max_risk:.2f})",
        )

    # 12% max portfolio-wide gross exposure.
    gross_after = existing_gross_notional + order_notional
    max_gross = (limits.max_gross_exposure_pct / 100.0) * equity
    if gross_after > max_gross + 1e-9:
        return GuardResult(
            False,
            f"gross exposure ${gross_after:.2f} > {limits.max_gross_exposure_pct}% of "
            f"equity (${max_gross:.2f})",
        )

    return GuardResult(True, "ok")
