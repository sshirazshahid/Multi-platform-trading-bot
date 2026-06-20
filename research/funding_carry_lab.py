"""funding_carry_lab.py — offline analytics for the DELTA-NEUTRAL cash-and-carry
funding trade (the highest-confidence futures edge from the 2026-06-20 research).

What this is (and is NOT)
=========================
This models the *market-neutral* carry trade: hold long spot + short the perpetual
of the same notional, so price moves cancel, and collect the perpetual funding
payment each settlement. Profit comes from funding, not from predicting direction.

It is NOT the directional funding *signal* (long/short based on funding sign) —
that already screened NO_EDGE in this repo (scripts/run_funding_carry_screen.py,
best Sharpe 0.11). Different trade entirely.

Like dca_rebalance_lab.py this is pure-Python, deterministic, and offline (feed a
plain list of per-settlement funding rates). Real funding history can be pulled
with quant_suite/funding_carry.py (needs ccxt/keys) and passed in here.

HONESTY / RISK (read before trusting any number):
- Funding flips negative -> you PAY instead of collect. % of settlements positive
  and the worst settlement are reported for this reason.
- Carry is capacity-constrained and competed down as capital crowds in.
- Real risks not in the PnL: basis/slippage at entry & exit, the short-perp leg's
  LIQUIDATION risk if margin is thin, exchange/counterparty risk, and funding-
  schedule changes. Size margin conservatively and treat this as low single-digit
  to mid-teens % annual at best, not free money.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Binance/Bybit/Bitget settle perpetual funding every 8h -> 3/day -> 1095/yr.
SETTLEMENTS_PER_YEAR = 1095
# Per-side taker cost defaults; the carry crosses 2 legs (spot + perp), entry+exit.
DEFAULT_FEE_PCT_PER_SIDE = 0.0006  # ~0.06% blended taker (spot+perp)
DEFAULT_SLIPPAGE_PCT_PER_SIDE = 0.0005  # 5 bps per leg


@dataclass
class CarryResult:
    label: str
    notional: float
    n_settlements: int
    gross_funding_pnl: float  # sum of funding collected/paid
    total_costs: float  # entry + exit, both legs
    net_pnl: float
    net_yield_pct: float  # net_pnl / notional, over the whole period
    annualized_net_yield_pct: float
    avg_funding_per_settlement: float
    annualized_gross_funding_pct: float
    pct_settlements_positive: float
    worst_settlement_rate: float
    break_even_settlements: float  # settlements of avg funding to cover round-trip
    equity_curve: list[float] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def annualize_funding(
    avg_rate_per_settlement: float, settlements_per_year: int = SETTLEMENTS_PER_YEAR
) -> float:
    """Convert an average per-settlement funding rate (decimal) to annual %."""
    return avg_rate_per_settlement * settlements_per_year * 100.0


def round_trip_cost_pct(
    fee_pct_per_side: float = DEFAULT_FEE_PCT_PER_SIDE,
    slippage_pct_per_side: float = DEFAULT_SLIPPAGE_PCT_PER_SIDE,
) -> float:
    """Total cost as a fraction of notional to OPEN and CLOSE both legs.

    Two legs (spot + perp) x two events (entry + exit) = 4 crossings.
    """
    return 4.0 * (fee_pct_per_side + slippage_pct_per_side)


def break_even_funding_per_settlement(
    n_settlements: int,
    fee_pct_per_side=DEFAULT_FEE_PCT_PER_SIDE,
    slippage_pct_per_side=DEFAULT_SLIPPAGE_PCT_PER_SIDE,
) -> float:
    """Average per-settlement funding rate needed to break even over n holds."""
    if n_settlements <= 0:
        raise ValueError("n_settlements must be > 0")
    return round_trip_cost_pct(fee_pct_per_side, slippage_pct_per_side) / n_settlements


def simulate_cash_and_carry(
    funding_rates,
    notional=10_000.0,
    fee_pct_per_side=DEFAULT_FEE_PCT_PER_SIDE,
    slippage_pct_per_side=DEFAULT_SLIPPAGE_PCT_PER_SIDE,
    settlements_per_year=SETTLEMENTS_PER_YEAR,
) -> CarryResult:
    """Simulate holding a delta-neutral long-spot/short-perp carry across the
    given per-settlement funding rates (decimal; +0.0001 = +0.01%/8h, longs pay
    shorts so the short-perp leg COLLECTS when the rate is positive).

    PnL = sum(rate * notional) over settlements  -  round-trip costs (both legs,
    entry+exit). Direction PnL is ~0 by construction (delta-neutral), so it is
    excluded; basis slippage at entry/exit is approximated inside the cost term.
    """
    if not funding_rates:
        raise ValueError("funding_rates must be non-empty")
    if notional <= 0:
        raise ValueError("notional must be > 0")

    n = len(funding_rates)
    entry_cost = notional * (fee_pct_per_side + slippage_pct_per_side) * 2.0  # 2 legs
    exit_cost = entry_cost
    total_costs = entry_cost + exit_cost

    equity = -entry_cost  # pay to open
    curve = []
    gross = 0.0
    positives = 0
    worst = math.inf
    for rate in funding_rates:
        pay = rate * notional  # +ve rate -> short perp receives
        gross += pay
        equity += pay
        curve.append(equity)
        if rate > 0:
            positives += 1
        if rate < worst:
            worst = rate
    equity -= exit_cost  # pay to close
    curve.append(equity)

    net_pnl = gross - total_costs
    avg_rate = sum(funding_rates) / n
    net_yield_pct = net_pnl / notional * 100.0
    years = n / settlements_per_year
    annualized_net = (net_yield_pct / years) if years > 0 else 0.0
    be = break_even_funding_per_settlement(n, fee_pct_per_side, slippage_pct_per_side)
    return CarryResult(
        label="cash_and_carry",
        notional=notional,
        n_settlements=n,
        gross_funding_pnl=gross,
        total_costs=total_costs,
        net_pnl=net_pnl,
        net_yield_pct=net_yield_pct,
        annualized_net_yield_pct=annualized_net,
        avg_funding_per_settlement=avg_rate,
        annualized_gross_funding_pct=annualize_funding(avg_rate, settlements_per_year),
        pct_settlements_positive=positives / n * 100.0,
        worst_settlement_rate=(worst if worst != math.inf else 0.0),
        break_even_settlements=(total_costs / notional) / avg_rate if avg_rate > 0 else math.inf,
        equity_curve=curve,
        extra={
            "break_even_funding_per_settlement": be,
            "round_trip_cost_pct": round_trip_cost_pct(fee_pct_per_side, slippage_pct_per_side)
            * 100.0,
            "avg_funding_clears_breakeven": avg_rate > be,
        },
    )


def summarize_funding(funding_rates, settlements_per_year=SETTLEMENTS_PER_YEAR) -> dict:
    """Quick descriptive stats of a funding series (no trade simulated)."""
    if not funding_rates:
        raise ValueError("funding_rates must be non-empty")
    n = len(funding_rates)
    avg = sum(funding_rates) / n
    pos = sum(1 for r in funding_rates if r > 0)
    return {
        "n": n,
        "avg_per_settlement": avg,
        "annualized_pct": annualize_funding(avg, settlements_per_year),
        "pct_positive": pos / n * 100.0,
        "max": max(funding_rates),
        "min": min(funding_rates),
    }


def _fmt(r: CarryResult) -> str:
    return (
        f"  net={r.net_pnl:+10.2f} ({r.net_yield_pct:+.3f}% / "
        f"{r.annualized_net_yield_pct:+.2f}% ann)  "
        f"gross={r.gross_funding_pnl:+.2f} costs={r.total_costs:.2f}  "
        f"+settle={r.pct_settlements_positive:.0f}%  "
        f"ann.funding={r.annualized_gross_funding_pct:+.2f}%"
    )


if __name__ == "__main__":
    import random

    random.seed(2)
    spy = SETTLEMENTS_PER_YEAR

    # Scenario A: healthy positive carry (~0.01%/8h avg => ~11%/yr gross).
    fa = [max(-0.0005, 0.0001 + random.gauss(0, 0.00005)) for _ in range(spy)]
    # Scenario B: thin/often-negative funding (bear/flat) => carry shouldn't clear.
    fb = [random.gauss(0.00002, 0.0002) for _ in range(spy)]

    print("Delta-neutral cash-and-carry, 1-year holds, $10k notional --------")
    print("A: healthy +funding")
    print(_fmt(simulate_cash_and_carry(fa)))
    print(f"   summary: {summarize_funding(fa)}")
    print("B: thin/noisy funding")
    print(_fmt(simulate_cash_and_carry(fb)))
    print(f"   summary: {summarize_funding(fb)}")
    print(
        f"\nRound-trip cost = {round_trip_cost_pct() * 100:.3f}% of notional; "
        f"break-even avg funding over 1yr = "
        f"{break_even_funding_per_settlement(spy) * 100:.5f}%/settlement"
    )
