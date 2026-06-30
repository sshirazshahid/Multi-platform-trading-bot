"""Phase 3 — S5 maker-aware execution sim (cost-reduction overlay, NOT alpha).

Replaces the dishonest "maker always fills" shortcut with a queue/through-trade
fill model: a resting limit fills ONLY if the next bar trades THROUGH it. A price
that runs away from the limit (the winning breakout) leaves the order UNFILLED —
no phantom fill. This is adversely selected by construction: limits fill on the
losers (price comes back to your resting buy) and miss the winners (price runs
away), so layering a maker entry LOWERS realized EV versus the always-fills
shortcut even though it saves the entry taker fee.

Consequences enforced here:
  * S5 is a COST-REDUCTION overlay only. It MUST NOT be promoted as standalone
    alpha (``refuse_as_standalone_alpha``), and ``simulate_maker_overlay`` refuses
    to run unless the base strategy already cleared the resolved-only ``accept()``
    gate (``base_accepted=True``).
  * Stop exits stay TAKER and pessimistic — only the ENTRY is re-priced maker.
  * The only REAL fill signal is the realized maker-fill RATE measured live in
    ``data/execution_stats.json`` (``realized_maker_fill_rate``); the modeled
    through-trade rate is a conservative simulation, never a promotion criterion.

Deterministic, pure functions only — no live calls, no order path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from core.cost_model import fee_rate

S5_ROLE = "cost-reduction-only execution overlay (entry maker re-pricing)"
STANDALONE_ALPHA_ALLOWED = False


def refuse_as_standalone_alpha() -> None:
    """Guard: S5 has no alpha of its own — adverse selection makes maker EV worse.

    Always raises; call sites that try to register S5 as an edge hit this.
    """
    raise ValueError(
        "S5 is a cost-reduction overlay, not standalone alpha: maker entries are "
        "adversely selected (fill on losers, miss winning breakouts) so they LOWER "
        "EV. Layer S5 only onto a strategy that already cleared accept()."
    )


def _bar_high_low(bar) -> tuple[float, float]:
    """Extract (high, low) from a dict {high,low} or a sequence [o,h,l,c,...]."""
    if isinstance(bar, Mapping):
        return float(bar["high"]), float(bar["low"])
    return float(bar[1]), float(bar[2])  # OHLCV ordering: [ts? no -> o,h,l]


def maker_fills(side: str, limit_px: float, bar) -> bool:
    """Did a resting maker limit at ``limit_px`` fill on ``bar`` (next bar)?

    Through-trade / queue model: the order fills ONLY if the bar trades strictly
    THROUGH the limit. A mere touch (low == limit for a buy) does NOT fill — you
    are assumed at the back of the queue, so price must clear the level. A price
    that runs away (buy limit below a bar that never trades down to it) stays
    UNFILLED — no phantom fill.
    """
    high, low = _bar_high_low(bar)
    if side == "buy":
        return low < float(limit_px)
    return high > float(limit_px)


def realized_maker_fill_rate(stats_path) -> Optional[float]:
    """Realized maker-fill RATE = ``limit_fills / total_orders`` from
    ``data/execution_stats.json`` (the smart_executor counters). This is the ONLY
    real fill signal. Returns None when the file is missing or has zero orders.
    """
    p = Path(stats_path)
    try:
        if not p.exists():
            return None
        stats = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    total = float(stats.get("total_orders") or 0)
    if total <= 0:
        return None
    return float(stats.get("limit_fills") or 0) / total


def simulate_maker_overlay(
    trades: Sequence[Mapping],
    *,
    venue: str,
    base_accepted: bool,
    market_type: str = "futures",
) -> dict:
    """Re-price a strategy's TAKER entries as MAKER limits and compare EV.

    Each trade dict carries: ``side`` (entry side), ``limit_px`` (resting limit),
    ``fill_bar`` (the next bar, dict/seq with high/low) and ``net_pnl_taker`` (the
    already-resolved after-cost net return if it had filled as taker).

    The maker overlay applies the entry taker->maker fee saving to every trade,
    then keeps ONLY the trades whose ``fill_bar`` traded THROUGH the limit. The
    ``always_fills`` shortcut keeps ALL trades (with the same saving) — the
    dishonest baseline. Because winners run away (unfilled) and losers come back
    (filled), ``ev_maker`` is typically WORSE than ``ev_always_fills``.

    Raises ValueError if ``base_accepted`` is False — S5 may only be layered onto
    a strategy that already cleared the resolved-only accept() gate.
    """
    if not base_accepted:
        raise ValueError(
            "simulate_maker_overlay refused: base strategy has not cleared the "
            "resolved-only accept() gate. S5 is cost-reduction only and may not "
            "manufacture an edge for an unproven signal."
        )

    # Entry-side taker->maker saving (per-notional fraction). Exit stays taker.
    saving = fee_rate(venue, market_type, "taker") - fee_rate(venue, market_type, "maker")

    all_net = []
    filled_net = []
    unfilled = []
    for t in trades:
        net_maker = float(t["net_pnl_taker"]) + saving
        all_net.append(net_maker)
        if maker_fills(str(t["side"]), float(t["limit_px"]), t["fill_bar"]):
            filled_net.append(net_maker)
        else:
            unfilled.append(t)

    n = len(all_net)
    n_filled = len(filled_net)
    ev_always = sum(all_net) / n if n else 0.0
    ev_maker = sum(filled_net) / n_filled if n_filled else 0.0
    return {
        "n_candidates": n,
        "n_filled": n_filled,
        "n_unfilled": len(unfilled),
        "fill_rate": (n_filled / n) if n else 0.0,
        "maker_fee_saving": float(saving),
        "ev_maker": float(ev_maker),
        "ev_always_fills": float(ev_always),
        "filled_net_pnls": filled_net,
        "role": S5_ROLE,
    }
