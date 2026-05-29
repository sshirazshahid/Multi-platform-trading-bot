"""Per-trade alpha attribution.

Decomposes every closed trade's realized PnL into:

  realized_pnl = alpha - spread - slippage - funding - fees

  alpha    : mid-to-mid PnL (pure market-direction edge)
  spread   : bid-ask crossing cost paid (positive = cost)
  slippage : sim-vs-mid divergence (paper trades only; 0 for live)
  funding  : perpetual funding paid over the hold (positive = cost)
  fees     : entry + exit trading fees

This is the diagnostic that gates the rest of the quant rebuild
(Phase 1 of the plan): if alpha is positive on average, signals have
real edge being eaten by costs/execution → ML rebuild justified. If
alpha is negative, no ML model on top of these signals will help.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Trade:
    """Inputs needed to attribute a closed trade.

    For live trades, leave `slippage_modeled` at 0 — the actual fill IS the
    truth; spread captures the entire mid-vs-fill divergence. For paper
    trades, set `slippage_modeled` to the slippage `SimExecutionModel`
    inserted on top of the mid (so you can see how much of the realized PnL
    came from the simulator vs the market).
    """
    side: str          # 'long' / 'buy' / 'short' / 'sell'
    size: float
    entry_mid: float
    entry_fill: float
    exit_mid: float
    exit_fill: float
    funding_paid: float = 0.0
    fees: float = 0.0
    slippage_modeled: float = 0.0


@dataclass
class AttributionRow:
    alpha: float
    spread: float
    slippage: float
    funding: float
    fees: float
    realized_pnl: float

    def as_dict(self) -> dict:
        return {
            "alpha": self.alpha,
            "spread": self.spread,
            "slippage": self.slippage,
            "funding": self.funding,
            "fees": self.fees,
            "realized_pnl": self.realized_pnl,
        }


def _side_sign(side: str) -> int:
    s = (side or "").strip().lower()
    if s in ("long", "buy", "+1", "1"):
        return 1
    if s in ("short", "sell", "-1"):
        return -1
    raise ValueError(f"unknown side: {side!r}")


def attribute(trade: Trade) -> AttributionRow:
    """Decompose `trade` into attribution components.

    Invariant (asserted by tests):
        realized_pnl == alpha - spread - slippage - funding - fees
    """
    sign = _side_sign(trade.side)
    sz = float(trade.size)

    # Pure direction: mid-to-mid PnL on `sz` units.
    alpha = (trade.exit_mid - trade.entry_mid) * sz * sign

    # Spread cost: signed so positive = paid (always non-negative for normal
    # market fills, but we don't clamp — a passive maker fill could be < 0
    # and that should appear as a *negative* spread "cost", i.e. a rebate).
    if sign == 1:
        # long: bought above mid (cost), sold below mid (cost)
        spread_per_unit = (trade.entry_fill - trade.entry_mid) \
                          + (trade.exit_mid - trade.exit_fill)
    else:
        # short: sold below mid (cost), bought above mid (cost)
        spread_per_unit = (trade.entry_mid - trade.entry_fill) \
                          + (trade.exit_fill - trade.exit_mid)
    spread = spread_per_unit * sz

    realized = (
        alpha
        - spread
        - trade.slippage_modeled
        - trade.funding_paid
        - trade.fees
    )

    return AttributionRow(
        alpha=alpha,
        spread=spread,
        slippage=trade.slippage_modeled,
        funding=trade.funding_paid,
        fees=trade.fees,
        realized_pnl=realized,
    )


def record(warehouse, trade_id: int, trade: Trade) -> AttributionRow:
    """Compute attribution and persist to `warehouse.attribution`.

    The warehouse's `record_attribution` is INSERT OR REPLACE keyed on
    `trade_id`, so re-running on a trade we've already attributed is safe
    (use case: a slippage snapshot becomes available later).
    """
    row = attribute(trade)
    warehouse.record_attribution(
        trade_id=int(trade_id),
        alpha=row.alpha,
        spread=row.spread,
        slippage=row.slippage,
        funding=row.funding,
        fees=row.fees,
        realized_pnl=row.realized_pnl,
        attributed_at=int(time.time()),
    )
    return row
