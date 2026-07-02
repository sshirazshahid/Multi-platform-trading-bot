"""core/fill_reality.py — Phase C: FillRealityEngine adverse-outcome models.

The sim/cost/maker stack (``sim_execution`` + ``maker_fill_model`` + ``cost_model``)
already models slippage, wick SL/TP, funding and adverse-selected maker fills.
This module fills the three GENUINE gaps — the outcomes a paper account silently
skips but a live venue does not:

  1. ``order_rejection_reason`` — the exchange refuses the order (sub-min-notional,
     non-positive size, insufficient margin, bad price / tick).
  2. ``liquidation_buffer_breach`` — mark price has eaten the maintenance-margin
     buffer; the position is at/near forced liquidation regardless of the SL.
  3. ``failed_leg_outcome`` — in a multi-leg (hedge / cross-venue) order one leg
     does not fully fill, leaving residual UNHEDGED exposure.

Deterministic, pure functions only — no live calls, no order path.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

_EPS = 1e-12
LONG = {"buy", "long"}
SHORT = {"sell", "short"}


# --------------------------------------------------------------- rejection
def order_rejection_reason(
    *,
    size: float | None,
    price: float | None,
    min_notional: float = 5.0,
    available_margin: float | None = None,
    required_margin: float | None = None,
    tick_ok: bool = True,
) -> str | None:
    """Return the reason the venue would REJECT this order, or None if it clears.

    Ordered like a real matching-engine pre-trade check: size, price, notional,
    margin, then tick/price-filter.
    """
    if size is None or float(size) <= 0:
        return "nonpositive_size"
    if price is None or float(price) <= 0:
        return "invalid_price"
    notional = float(size) * float(price)
    if notional < float(min_notional):
        return f"min_notional:{notional:.4f}<{float(min_notional):.4f}"
    if (
        available_margin is not None
        and required_margin is not None
        and float(required_margin) > float(available_margin) + _EPS
    ):
        return "insufficient_margin"
    if not tick_ok:
        return "tick_size"
    return None


# ------------------------------------------------------------- liquidation
def liquidation_price(
    side: str, entry_px: float, leverage: float, *, mmr: float = 0.005
) -> float:
    """Approximate isolated-margin liquidation price for a perp position.

    long liq below entry, short liq above. ``mmr`` = maintenance-margin rate.
    """
    lev = float(leverage)
    if lev <= 0:
        raise ValueError("leverage must be > 0")
    e = float(entry_px)
    if str(side).lower() in LONG:
        return e * (1.0 - 1.0 / lev + float(mmr))
    return e * (1.0 + 1.0 / lev - float(mmr))


def liquidation_buffer_breach(
    side: str,
    entry_px: float,
    mark_px: float,
    leverage: float,
    *,
    mmr: float = 0.005,
    buffer_frac: float = 0.1,
) -> dict[str, Any]:
    """Has mark price crossed, or come within ``buffer_frac`` of, liquidation?

    ``buffer_frac`` is a fraction of the initial-margin distance (``1/leverage``).
    Returns ``{liq_px, crossed, near, breach, distance_frac}``.
    """
    liq = liquidation_price(side, entry_px, leverage, mmr=mmr)
    e = float(entry_px)
    m = float(mark_px)
    is_long = str(side).lower() in LONG
    crossed = m <= liq if is_long else m >= liq
    distance_frac = abs(m - liq) / e if e else 0.0
    threshold = float(buffer_frac) / float(leverage)
    near = crossed or distance_frac <= threshold
    return {
        "liq_px": liq,
        "crossed": crossed,
        "near": near,
        "breach": bool(near),
        "distance_frac": distance_frac,
    }


# ------------------------------------------------------------- failed leg
def failed_leg_outcome(legs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Detect partially/failed legs and the residual UNHEDGED quantity.

    Each leg dict: ``leg`` (id), ``target_qty``, ``filled_qty``. A leg is failed
    when ``filled_qty < target_qty``. For a 2-leg hedge the unhedged residual is
    the fill imbalance between the legs; single-leg residual is target-minus-fill.
    """
    failed = [
        dict_leg
        for dict_leg in legs
        if float(dict_leg.get("filled_qty", 0.0)) + _EPS
        < float(dict_leg.get("target_qty", 0.0))
    ]
    fills = [float(x.get("filled_qty", 0.0)) for x in legs]
    if len(fills) >= 2:
        unhedged = max(fills) - min(fills)
    elif legs:
        unhedged = float(legs[0].get("target_qty", 0.0)) - fills[0]
    else:
        unhedged = 0.0
    return {
        "any_failed": bool(failed),
        "failed_legs": [x.get("leg") for x in failed],
        "unhedged_qty": max(0.0, unhedged),
        "legging_risk": unhedged > _EPS,
    }
