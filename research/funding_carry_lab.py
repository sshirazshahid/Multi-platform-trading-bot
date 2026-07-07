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
import random
from dataclasses import dataclass, field

try:
    from research._stats import _percentiles, resample_blocks
except ImportError:  # run as a script (python research/funding_carry_lab.py)
    from _stats import _percentiles, resample_blocks

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
    borrow_rate_per_settlement=0.0,
    spot_exit_extra_taker_pct=0.0,
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
    # Spot leg is borrowed/financed while held (real cash-and-carry funds the spot
    # long); the spot exit often crosses a wider/illiquid book than the entry, so
    # model an ASYMMETRIC extra taker cost on the spot close only.
    spot_financing = spot_financing_cost(notional, borrow_rate_per_settlement, n)
    spot_exit_extra = notional * max(0.0, spot_exit_extra_taker_pct)
    total_costs = entry_cost + exit_cost + spot_financing + spot_exit_extra

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
            "spot_financing_cost": spot_financing,
            "spot_exit_extra_cost": spot_exit_extra,
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


# ── Monte-Carlo robustness (block bootstrap of the funding series) ────


def _bootstrap_series(series, n, block, rng) -> list:
    """Resample a value series to length n (moving-block bootstrap)."""
    return resample_blocks(series, n, block, rng)


def monte_carlo_carry(funding_rates, n_paths=1000, block=24, seed=0, **sim_kw) -> dict:
    """Block-bootstrap Monte-Carlo of the cash-and-carry over a funding series.

    Funding is persistent (regimes of positive/negative), so we resample blocks
    (default 24 settlements ~ 8 days) to keep that structure, run the carry on
    each synthetic path, and return percentile bands of annualized net yield (%)
    and the share of settlements that were positive. Shows how much the carry
    depends on the funding regime persisting. Deterministic given `seed`.
    """
    if not funding_rates:
        raise ValueError("funding_rates must be non-empty")
    if n_paths < 1:
        raise ValueError("n_paths must be >= 1")
    if block < 1:
        raise ValueError("block must be >= 1")
    rng = random.Random(seed)
    n = len(funding_rates)
    ann, netpct, pos = [], [], []
    for _ in range(n_paths):
        path = _bootstrap_series(funding_rates, n, block, rng)
        r = simulate_cash_and_carry(path, **sim_kw)
        ann.append(r.annualized_net_yield_pct)
        netpct.append(r.net_yield_pct)
        pos.append(r.pct_settlements_positive)
    return {
        "n_paths": n_paths,
        "block": block,
        "annualized_net_yield_pct": _percentiles(ann),
        "net_yield_pct": _percentiles(netpct),
        "pct_settlements_positive": _percentiles(pos),
    }


# ══════════════════════════════════════════════════════════════════════
# S1 (Phase 4) — delta-neutral carry LAB-SIM primitives.
# LAB-SIM ONLY. No real legs are ever placed. Verdict: see S1_VERDICT.
# ══════════════════════════════════════════════════════════════════════

# Final S1 verdict (recorded for the EvidenceRegistry / human review).
S1_VERDICT = "CONDITIONAL lab-GO / live-INFEASIBLE-AT-$420"

# ── Entry gate thresholds (microstructure quality required to OPEN) ───
MAX_ENTRY_BASIS_BPS = 25.0      # spot-vs-perp basis must be tight to enter
MIN_DEPTH_RATIO = 20.0          # top-of-book depth must be >= 20x our clip
MAX_ENTRY_SPREAD_BPS = 5.0      # combined leg spread cap

# ── Exit gate thresholds ──────────────────────────────────────────────
MAX_CONSEC_NEG_SETTLEMENTS = 2  # two consecutive PAY settlements -> exit
MAX_ADVERSE_BASIS_BPS = 50.0    # basis loss beyond this -> exit
MIN_MARGIN_BUFFER_X = 3.0       # equity/maintenance below 3x -> exit (liq risk)
DEFAULT_MAX_HEDGE_STALENESS_SEC = 300.0  # stale short-perp hedge leg -> exit
# Rev 5 exit additions (defined here so carry_exit_signal can default to them).
F1_MAX_NOTIONAL_MISMATCH_PCT = 1.0     # hedge legs drifted apart -> exit
F1_MAX_EXIT_SPREAD_BPS = 15.0          # book spread widened -> exit
F1_MAX_HOLD_SETTLEMENTS = 42           # max holding window (2x default cycle)


def carry_entry_gate(
    *,
    basis_bps: float,
    depth_ratio: float,
    spread_bps: float,
    max_basis_bps: float = MAX_ENTRY_BASIS_BPS,
    min_depth_ratio: float = MIN_DEPTH_RATIO,
    max_spread_bps: float = MAX_ENTRY_SPREAD_BPS,
):
    """Gate the OPEN of a carry position on book quality. Returns (ok, reason).

    Rejects on wide basis (entry slippage / mean-reversion risk), thin depth
    (can't size the two legs without moving the book), or wide spread.
    """
    if abs(basis_bps) > max_basis_bps:
        return False, f"basis {basis_bps:.1f}bps > {max_basis_bps:.0f}bps"
    if depth_ratio < min_depth_ratio:
        return False, f"depth {depth_ratio:.1f}x < {min_depth_ratio:.0f}x"
    if spread_bps > max_spread_bps:
        return False, f"spread {spread_bps:.1f}bps > {max_spread_bps:.0f}bps"
    return True, "ok"


@dataclass
class CarryPositionState:
    """Rolling state evaluated each settlement to decide whether to CLOSE."""
    consec_negative_settlements: int = 0
    adverse_basis_move_bps: float = 0.0  # adverse basis drift since entry
    margin_buffer_x: float = 10.0        # equity / maintenance margin (short leg)
    hedge_leg_age_sec: float = 0.0       # since last confirmed short-perp hedge
    # Rev 5 exit inputs (benign defaults keep pre-Rev-5 call sites unchanged).
    notional_mismatch_pct: float = 0.0   # |spot - perp| notional drift, % of leg
    spread_bps: float = 0.0              # current combined book spread
    net_edge_bps: float = 0.0            # re-checked net expected edge (bps)
    settlements_held: int = 0            # settlements since the pair was opened


def carry_exit_signal(
    state: CarryPositionState,
    *,
    max_consec_negative: int = MAX_CONSEC_NEG_SETTLEMENTS,
    max_adverse_basis_bps: float = MAX_ADVERSE_BASIS_BPS,
    min_margin_buffer_x: float = MIN_MARGIN_BUFFER_X,
    max_hedge_staleness_sec: float = DEFAULT_MAX_HEDGE_STALENESS_SEC,
    max_notional_mismatch_pct: float = F1_MAX_NOTIONAL_MISMATCH_PCT,
    max_exit_spread_bps: float = F1_MAX_EXIT_SPREAD_BPS,
    max_hold_settlements: int = F1_MAX_HOLD_SETTLEMENTS,
):
    """Evaluate the S1 + Rev 5 exit gates. Returns (should_exit, reason).

    Gates (any one trips an exit):
      1. two consecutive negative (PAY) funding settlements,
      2. adverse basis loss beyond the cap,
      3. margin buffer below the maintenance multiple (liquidation proximity),
      4. stale hedge leg (short-perp not confirmed recently -> unhedged drift),
      5. notional mismatch between the legs > 1% (hedge drift),          [Rev 5]
      6. book spread widened beyond 15bps (exit while it is still cheap), [Rev 5]
      7. re-checked net expected edge < 0 (carry no longer pays),         [Rev 5]
      8. max holding window reached (settlements).                        [Rev 5]
    """
    if state.consec_negative_settlements >= max_consec_negative:
        return True, f"two_consecutive_negative_settlements ({state.consec_negative_settlements})"
    if state.adverse_basis_move_bps > max_adverse_basis_bps:
        return True, f"basis_loss {state.adverse_basis_move_bps:.1f}bps > {max_adverse_basis_bps:.0f}"
    if state.margin_buffer_x < min_margin_buffer_x:
        return True, f"margin_buffer {state.margin_buffer_x:.2f}x < {min_margin_buffer_x:.1f}x"
    if state.hedge_leg_age_sec > max_hedge_staleness_sec:
        return True, f"stale_hedge_leg {state.hedge_leg_age_sec:.0f}s"
    if state.notional_mismatch_pct > max_notional_mismatch_pct:
        return True, (
            f"notional_mismatch {state.notional_mismatch_pct:.2f}% > "
            f"{max_notional_mismatch_pct:.1f}%"
        )
    if state.spread_bps > max_exit_spread_bps:
        return True, f"spread_widened {state.spread_bps:.1f}bps > {max_exit_spread_bps:.0f}bps"
    if state.net_edge_bps < 0:
        return True, f"net_edge_negative {state.net_edge_bps:+.1f}bps"
    if state.settlements_held >= max_hold_settlements:
        return True, f"max_hold_window {state.settlements_held} >= {max_hold_settlements}"
    return False, "hold"


def spot_financing_cost(notional, borrow_rate_per_settlement, n_settlements) -> float:
    """Cost of financing the borrowed/funded SPOT long leg over the hold.

    Real cash-and-carry funds the spot long (margin/borrow); accrues each
    settlement like funding, but PAID by us. Decimal rate, e.g. 0.00002/8h.
    """
    if n_settlements < 0:
        raise ValueError("n_settlements must be >= 0")
    return notional * max(0.0, borrow_rate_per_settlement) * n_settlements


# ── Funding-persistence pre-validation (does trailing APR predict carry?) ─

def _bootstrap_mean_ci_lb(values, n_boot=1000, alpha=0.05, seed=0) -> float:
    """Lower bound of the (1-alpha) bootstrap CI for the mean of `values`."""
    if not values:
        return float("-inf")
    rng = random.Random(seed)
    k = len(values)
    means = []
    for _ in range(n_boot):
        s = sum(values[rng.randrange(k)] for _ in range(k))
        means.append(s / k)
    means.sort()
    idx = int(alpha * n_boot)
    return means[min(idx, n_boot - 1)]


def funding_persistence_test(
    funding_rates,
    *,
    trailing_window: int = 21,
    forward_horizon: int = 21,
    fee_pct_per_side: float = DEFAULT_FEE_PCT_PER_SIDE,
    slippage_pct_per_side: float = DEFAULT_SLIPPAGE_PCT_PER_SIDE,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict:
    """PRE-VALIDATE whether trailing funding APR predicts forward realized NET
    carry after costs OUT-OF-SAMPLE, with a bootstrap CI lower bound above costs.

    Method (deterministic given seed):
      - At each t with `trailing_window` history and `forward_horizon` future,
        predictor = mean(trailing funding); target = forward NET carry %
        (simulate_cash_and_carry over the forward slice, net_yield_pct).
      - Time-split samples IS (first half) / OOS (second half). Threshold =
        median IS predictor. In OOS, take the cohort where predictor > threshold
        (the "carry looks good" regime) and bootstrap the CI-LB of its forward
        NET carry.
      - GO iff CI-LB > round-trip cost threshold (% of notional). Else NO_GO.
    """
    if not funding_rates:
        raise ValueError("funding_rates must be non-empty")
    if trailing_window < 1 or forward_horizon < 1:
        raise ValueError("windows must be >= 1")

    rates = list(funding_rates)
    cost_threshold_pct = round_trip_cost_pct(fee_pct_per_side, slippage_pct_per_side) * 100.0
    samples = []  # (t, predictor_apr, forward_net_pct)
    last = len(rates) - forward_horizon
    for t in range(trailing_window, last + 1):
        trailing = rates[t - trailing_window:t]
        fwd = rates[t:t + forward_horizon]
        if not fwd:
            continue
        pred = annualize_funding(sum(trailing) / len(trailing))
        r = simulate_cash_and_carry(
            fwd, notional=10_000.0,
            fee_pct_per_side=fee_pct_per_side, slippage_pct_per_side=slippage_pct_per_side)
        # Target = forward GROSS carry captured (% of notional); GO requires its
        # CI-LB to clear the round-trip cost threshold (i.e. net carry > 0 OOS).
        fwd_gross_pct = r.gross_funding_pnl / 10_000.0 * 100.0
        samples.append((t, pred, fwd_gross_pct))

    if len(samples) < 4:
        return {
            "verdict": "NO_GO", "reason": "insufficient_samples",
            "n_samples": len(samples), "n_oos_cohort": 0,
            "ci_lb_net_pct": float("-inf"), "cost_threshold_pct": cost_threshold_pct,
        }

    mid = len(samples) // 2
    is_preds = sorted(s[1] for s in samples[:mid])
    thr = is_preds[len(is_preds) // 2]  # median IS predictor
    oos = samples[mid:]
    cohort = [s[2] for s in oos if s[1] > thr]
    ci_lb = _bootstrap_mean_ci_lb(cohort, n_boot=n_boot, seed=seed) if cohort else float("-inf")
    verdict = "GO" if (cohort and ci_lb > cost_threshold_pct) else "NO_GO"
    return {
        "verdict": verdict,
        "n_samples": len(samples),
        "n_oos_cohort": len(cohort),
        "threshold_apr_pct": thr,
        "ci_lb_net_pct": ci_lb,
        "cost_threshold_pct": cost_threshold_pct,
        "cohort_mean_net_pct": (sum(cohort) / len(cohort)) if cohort else float("-inf"),
    }


# ── Two-leg notional-matched Position primitive + leg-aware risk model ─

# Hedged-leg family (mirror of tsmom_signal._TSMOM_FAMILY). A leg tagged with one
# of these strategies is part of a delta-neutral pair, so the DIRECTIONAL ATR-SL /
# 8%-guardian must NOT fire on it (the other leg offsets the price move).
_HEDGED_FAMILY = frozenset({"carry", "s1"})


@dataclass
class CarryLeg:
    symbol: str
    side: str          # "long" (spot) | "short" (perp)
    qty: float
    entry_px: float
    notional: float
    leverage: float
    strategy: str = "carry"  # tag -> is_hedged_leg() True


@dataclass
class TwoLegCarryPosition:
    symbol: str
    spot_leg: CarryLeg
    perp_leg: CarryLeg

    @classmethod
    def open(cls, *, symbol, notional, spot_px, perp_px, perp_leverage=1.0):
        """Open a notional-matched spot-long + perp-short delta-neutral pair (SIM)."""
        if notional <= 0 or spot_px <= 0 or perp_px <= 0:
            raise ValueError("notional and prices must be > 0")
        spot_qty = notional / spot_px
        perp_qty = notional / perp_px
        spot = CarryLeg(symbol, "long", spot_qty, spot_px, notional, 1.0, "carry")
        perp = CarryLeg(symbol, "short", perp_qty, perp_px, notional, perp_leverage, "carry")
        return cls(symbol=symbol, spot_leg=spot, perp_leg=perp)


def is_hedged_leg(pos) -> bool:
    """True if a leg/Position is tagged as part of a delta-neutral hedge.

    Mirror of tsmom_signal.is_tsmom_position: keys on the ``strategy`` tag so the
    risk policy follows how the leg was OPENED, not the current SIGNAL_SOURCE.
    """
    return str(getattr(pos, "strategy", "") or "").lower() in _HEDGED_FAMILY


def directional_stop_should_fire(pos, *, pnl_pct, atr_sl_hit, guardian_pct=8.0) -> bool:
    """Leg-aware directional stop. SUPPRESSED on a hedged leg.

    For a normal directional position the ATR-SL or the 8% guardian fires; for a
    hedged carry leg the offsetting leg cancels the price move, so neither fires
    (per-leg LIQUIDATION via ``per_leg_liquidation_price`` is the real rail).
    """
    if is_hedged_leg(pos):
        return False
    if atr_sl_hit:
        return True
    return pnl_pct <= -abs(guardian_pct)


def per_leg_liquidation_price(*, entry_px, side, leverage, maintenance_margin_pct=0.005):
    """Approximate isolated-margin liquidation price for one leg (SIM).

    Unleveraged spot long (leverage<=1) has no liquidation -> None. The short perp
    leg liquidates ABOVE entry as price rises and erodes margin.
    """
    if leverage <= 1.0 and side == "long":
        return None
    move = (1.0 / leverage) - maintenance_margin_pct
    if side == "short":
        return entry_px * (1.0 + move)
    return entry_px * (1.0 - move)


# ══════════════════════════════════════════════════════════════════════
# F1 (Phase D) — delta-neutral funding/basis carry StrategySpec primitives.
# Extends S1 with an explicit net-expected-edge entry gate fed by the
# MarketDataLedger fields, and a StrategySpec builder. LAB-SIM ONLY.
# ══════════════════════════════════════════════════════════════════════

F1_LIVE_FEASIBILITY = "INFEASIBLE-AT-$420"
F1_VERDICT = "lab-slice / live-INFEASIBLE-AT-$420"

# Entry-gate thresholds: require net expected edge >= max(15bps, 3x round-trip
# cost), plus depth / liquidation-buffer / funding-freshness / atomic-fill.
F1_MIN_EDGE_BPS = 15.0
F1_COST_MULT = 3.0
F1_MIN_DEPTH_RATIO = MIN_DEPTH_RATIO           # reuse S1 depth floor (20x)
F1_MIN_LIQ_BUFFER_X = MIN_MARGIN_BUFFER_X      # reuse S1 maintenance floor (3x)
F1_MAX_FUNDING_AGE_SEC = DEFAULT_MAX_HEDGE_STALENESS_SEC  # 300s funding freshness
# Pipeline acceptance gates (plan): cycles / stress / one-leg / concentration.
F1_MIN_CYCLES = 60
F1_MAX_PNL_CONCENTRATION_PCT = 60.0  # Rev 5: 40 -> 60, per symbol AND per venue
F1_STRESS_COST_MULT = 2.0
# Rev 5 entry additions: per-leg spread caps + funding-window timing.
F1_MAX_LEG_SPREAD_BPS = 5.0            # spot AND perp leg spread caps
F1_MIN_TIME_TO_FUNDING_MIN = 20.0      # too close to settlement -> skip
F1_MAX_TIME_TO_FUNDING_MIN = 180.0     # too far from settlement -> skip
# Rev 5 sizing caps (paper equity; LAB-SIM/PAPER only).
F1_MAX_SYMBOL_EQUITY_PCT = 5.0         # max 5% paper equity per symbol
F1_MAX_TOTAL_CARRY_PCT = 20.0          # max 20% total carry notional
F1_INITIAL_LEVERAGE_MAX = 1.0          # 1x on the initial entry
F1_LEVERAGE_MAX = 2.0                  # 2x hard cap thereafter
# Rev 5 universe: BTC/ETH on binance (same-venue hedge) unless explicitly opted in.
F1_DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT")
F1_DEFAULT_VENUES = ("binance",)
# 2026-07-05 owner-approved PRE-REGISTERED paper-universe expansion — see
# research/prereg_carry_universe_expansion_2026_07_05.md and the replay
# evidence in research/carry_universe_scan_2026_07_05.py (~7.8 qualifying
# entries/yr across the 15 vs 0.45 for BTC+ETH; 46/49 replay cycles won).
# The FULL named set is frozen before activation — including BTC and SUI,
# which showed 0 qualifying entries in 6.8y — precisely so later evaluation
# cannot be accused of post-hoc symbol selection. The Rev-5 latch above is
# UNCHANGED; consumers opt in explicitly (build_f1_spec requires
# allow_extended_universe=True for this set).
F1_EXPANDED_UNIVERSE_2026_07_05 = (
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "LINK/USDT",
    "TRX/USDT", "SUI/USDT", "ATOM/USDT", "DOGE/USDT", "ALGO/USDT",
    "ZEC/USDT", "ADA/USDT", "XRP/USDT", "LTC/USDT", "GRT/USDT",
)


def f1_net_expected_edge_bps(
    *, funding_per_settlement: float, hold_settlements: int, round_trip_cost_frac: float
) -> float:
    """Net expected carry edge over the hold, in basis points.

    gross = funding_per_settlement * hold_settlements (fraction of notional the
    short-perp leg collects); net = gross - round-trip cost. * 1e4 -> bps.
    """
    if hold_settlements <= 0:
        raise ValueError("hold_settlements must be > 0")
    gross_frac = float(funding_per_settlement) * int(hold_settlements)
    return (gross_frac - float(round_trip_cost_frac)) * 1e4


def f1_entry_gate(
    *,
    funding_per_settlement: float,
    hold_settlements: int,
    round_trip_cost_frac: float,
    depth_ratio: float,
    liq_buffer_x: float,
    funding_age_sec: float,
    both_legs_fillable: bool,
    min_edge_bps: float = F1_MIN_EDGE_BPS,
    cost_mult: float = F1_COST_MULT,
    min_depth_ratio: float = F1_MIN_DEPTH_RATIO,
    min_liq_buffer_x: float = F1_MIN_LIQ_BUFFER_X,
    max_funding_age_sec: float = F1_MAX_FUNDING_AGE_SEC,
    avg_funding_7d: float | None = None,
    perp_mark: float | None = None,
    spot_mid: float | None = None,
    spot_spread_bps: float = 0.0,
    perp_spread_bps: float = 0.0,
    time_to_next_funding_min: float | None = None,
    feeds_fresh: bool = True,
    max_leg_spread_bps: float = F1_MAX_LEG_SPREAD_BPS,
    min_time_to_funding_min: float = F1_MIN_TIME_TO_FUNDING_MIN,
    max_time_to_funding_min: float = F1_MAX_TIME_TO_FUNDING_MIN,
):
    """F1 OPEN gate. Returns (ok, reason, details).

    Passes only when ALL hold:
      * current funding rate > 0 (short-perp COLLECTS, never pays, at open),
      * 7d average funding > 0 when supplied (regime, not a one-print spike),
      * perp mark >= spot mid when supplied (contango; no negative basis entry),
      * spot AND perp leg spreads <= ``max_leg_spread_bps`` each,
      * time to next funding within [min, max] minutes when supplied,
      * data feeds fresh (``feeds_fresh``),
      * net expected edge (bps) >= max(``min_edge_bps``, ``cost_mult`` x cost),
      * top-of-book depth >= ``min_depth_ratio`` x our clip,
      * short-perp liquidation buffer >= ``min_liq_buffer_x`` maintenance,
      * funding quote fresher than ``max_funding_age_sec`` (freshness),
      * BOTH legs are atomically fillable now (no one-leg legging exposure).

    Rev 5 inputs default to pass-through values so pre-Rev-5 call sites keep
    their behavior; feeds that HAVE the data should always supply it.
    """
    cost_bps = float(round_trip_cost_frac) * 1e4
    edge_bps = f1_net_expected_edge_bps(
        funding_per_settlement=funding_per_settlement,
        hold_settlements=hold_settlements,
        round_trip_cost_frac=round_trip_cost_frac,
    )
    threshold = max(float(min_edge_bps), float(cost_mult) * cost_bps)
    det = {
        "net_edge_bps": edge_bps,
        "edge_threshold_bps": threshold,
        "cost_bps": cost_bps,
        "depth_ratio": float(depth_ratio),
        "liq_buffer_x": float(liq_buffer_x),
        "funding_age_sec": float(funding_age_sec),
        "both_legs_fillable": bool(both_legs_fillable),
        "avg_funding_7d": avg_funding_7d,
        "perp_mark": perp_mark,
        "spot_mid": spot_mid,
        "spot_spread_bps": float(spot_spread_bps),
        "perp_spread_bps": float(perp_spread_bps),
        "time_to_next_funding_min": time_to_next_funding_min,
        "feeds_fresh": bool(feeds_fresh),
    }
    if funding_per_settlement <= 0:
        return False, f"funding_rate {funding_per_settlement:+.6f} <= 0", det
    if avg_funding_7d is not None and avg_funding_7d <= 0:
        return False, f"avg_funding_7d {avg_funding_7d:+.6f} <= 0", det
    if perp_mark is not None and spot_mid is not None and perp_mark < spot_mid:
        return False, f"perp_mark {perp_mark:.4f} < spot_mid {spot_mid:.4f}", det
    if spot_spread_bps > max_leg_spread_bps:
        return False, f"spot_spread {spot_spread_bps:.1f}bps > {max_leg_spread_bps:.0f}bps", det
    if perp_spread_bps > max_leg_spread_bps:
        return False, f"perp_spread {perp_spread_bps:.1f}bps > {max_leg_spread_bps:.0f}bps", det
    if time_to_next_funding_min is not None and not (
        min_time_to_funding_min <= time_to_next_funding_min <= max_time_to_funding_min
    ):
        return False, (
            f"time_to_next_funding {time_to_next_funding_min:.0f}min outside "
            f"[{min_time_to_funding_min:.0f},{max_time_to_funding_min:.0f}]"
        ), det
    if not feeds_fresh:
        return False, "feeds_stale (data feeds not fresh)", det
    if edge_bps < threshold:
        return False, f"edge {edge_bps:.1f}bps < {threshold:.1f}bps", det
    if depth_ratio < min_depth_ratio:
        return False, f"depth {depth_ratio:.1f}x < {min_depth_ratio:.0f}x", det
    if liq_buffer_x < min_liq_buffer_x:
        return False, f"liquidation_buffer {liq_buffer_x:.2f}x < {min_liq_buffer_x:.1f}x", det
    if funding_age_sec > max_funding_age_sec:
        return False, f"funding_stale {funding_age_sec:.0f}s > {max_funding_age_sec:.0f}s", det
    if not both_legs_fillable:
        return False, "atomic_two_leg_fill unavailable (one-leg legging risk)", det
    return True, "ok", det


def pnl_concentration_ok(pnl_by_key: dict, *, max_pct: float = F1_MAX_PNL_CONCENTRATION_PCT):
    """Guard: no single venue/symbol contributes > ``max_pct`` of |PnL|.

    Returns (ok, worst_share_pct, worst_key). Share uses absolute PnL so a large
    single-name LOSS also trips the diversification guard. Empty/zero -> ok.
    """
    items = {k: abs(float(v)) for k, v in (pnl_by_key or {}).items()}
    total = sum(items.values())
    if total <= 0:
        return True, 0.0, None
    worst_key = max(items, key=items.get)
    worst_share = items[worst_key] / total * 100.0
    return worst_share <= float(max_pct), worst_share, worst_key


def f1_sizing_gate(
    *,
    paper_equity: float,
    symbol_notional: float,
    total_carry_notional: float,
    leverage: float,
    is_initial_entry: bool = True,
    adds_to_losing_position: bool = False,
    max_symbol_pct: float = F1_MAX_SYMBOL_EQUITY_PCT,
    max_total_pct: float = F1_MAX_TOTAL_CARRY_PCT,
):
    """Rev 5 F1 sizing caps (PAPER equity). Returns (ok, reason).

    Caps: <= 5% of paper equity per symbol, <= 20% total carry notional,
    leverage 1x on the initial entry / 2x hard max, and NO averaging down.
    """
    if paper_equity <= 0:
        return False, "paper_equity must be > 0"
    if adds_to_losing_position:
        return False, "averaging_down forbidden (no adds to a losing carry)"
    if symbol_notional > paper_equity * max_symbol_pct / 100.0:
        return False, (
            f"symbol notional {symbol_notional:.2f} > {max_symbol_pct:.0f}% of equity"
        )
    if total_carry_notional > paper_equity * max_total_pct / 100.0:
        return False, (
            f"total_carry notional {total_carry_notional:.2f} > {max_total_pct:.0f}% of equity"
        )
    lev_cap = F1_INITIAL_LEVERAGE_MAX if is_initial_entry else F1_LEVERAGE_MAX
    if leverage > lev_cap:
        return False, f"leverage {leverage:.2f}x > {lev_cap:.1f}x cap"
    return True, "ok"


def build_f1_spec(
    *,
    symbols=F1_DEFAULT_SYMBOLS,
    venues=F1_DEFAULT_VENUES,
    allow_extended_universe: bool = False,
):
    """Build the F1 delta-neutral carry ``StrategySpec`` (declarative; no orders).

    Rev 5 universe latch: BTC/ETH on binance (same-venue hedge) by default;
    SOL/BNB or additional venues require ``allow_extended_universe=True``.
    Returns a ``core.strategy_spec.StrategySpec`` (lazy import to avoid a cycle).
    """
    from core.strategy_spec import StrategySpec

    if not allow_extended_universe:
        bad_syms = [s for s in symbols if s not in F1_DEFAULT_SYMBOLS]
        bad_venues = [v for v in venues if v not in F1_DEFAULT_VENUES]
        if bad_syms or bad_venues:
            raise ValueError(
                f"extended universe {bad_syms + bad_venues} requires "
                "allow_extended_universe=True (Rev 5 latch)"
            )

    return StrategySpec(
        id="F1",
        family="carry",
        market_type="spot_perp_hedge",
        venues=list(venues),
        symbols=list(symbols),
        data_required=[
            "spot_bbo", "perp_mark", "basis", "funding_rate", "funding_interval",
            "book_depth", "fees",
        ],
        entry_rules={
            "type": "delta_neutral_funding_carry",
            "net_expected_edge_bps_floor": F1_MIN_EDGE_BPS,
            "cost_mult": F1_COST_MULT,
            "min_depth_ratio": F1_MIN_DEPTH_RATIO,
            "min_liq_buffer_x": F1_MIN_LIQ_BUFFER_X,
            "max_funding_age_sec": F1_MAX_FUNDING_AGE_SEC,
            "atomic_two_leg_fill": True,
            "funding_rate_positive": True,
            "avg_funding_7d_positive": True,
            "perp_mark_at_or_above_spot_mid": True,
            "max_leg_spread_bps": F1_MAX_LEG_SPREAD_BPS,
            "time_to_next_funding_min_window": [
                F1_MIN_TIME_TO_FUNDING_MIN, F1_MAX_TIME_TO_FUNDING_MIN,
            ],
            "feeds_fresh_required": True,
        },
        exit_rules={
            "max_consecutive_negative_settlements": MAX_CONSEC_NEG_SETTLEMENTS,
            "max_adverse_basis_bps": MAX_ADVERSE_BASIS_BPS,
            "min_margin_buffer_x": MIN_MARGIN_BUFFER_X,
            "max_hedge_staleness_sec": DEFAULT_MAX_HEDGE_STALENESS_SEC,
            "max_notional_mismatch_pct": F1_MAX_NOTIONAL_MISMATCH_PCT,
            "max_exit_spread_bps": F1_MAX_EXIT_SPREAD_BPS,
            "net_edge_recheck_floor_bps": 0.0,
            "max_hold_settlements": F1_MAX_HOLD_SETTLEMENTS,
        },
        sizing={
            "notional_matched": True,
            "delta_neutral": True,
            "max_symbol_equity_pct": F1_MAX_SYMBOL_EQUITY_PCT,
            "max_total_carry_pct": F1_MAX_TOTAL_CARRY_PCT,
            "initial_leverage_max": F1_INITIAL_LEVERAGE_MAX,
            "leverage_max": F1_LEVERAGE_MAX,
            "no_averaging_down": True,
        },
        risk_limits={
            "leg_aware_hedged": True,
            "suppress_directional_atr_sl_on_hedged_leg": True,
            "max_pnl_concentration_pct": F1_MAX_PNL_CONCENTRATION_PCT,
            "min_cycles": F1_MIN_CYCLES,
            "live_feasibility": F1_LIVE_FEASIBILITY,
        },
        validation_status="lab_sim",
        promotion_status="untested",
    )


def _fmt(r: CarryResult) -> str:
    return (
        f"  net={r.net_pnl:+10.2f} ({r.net_yield_pct:+.3f}% / "
        f"{r.annualized_net_yield_pct:+.2f}% ann)  "
        f"gross={r.gross_funding_pnl:+.2f} costs={r.total_costs:.2f}  "
        f"+settle={r.pct_settlements_positive:.0f}%  "
        f"ann.funding={r.annualized_gross_funding_pct:+.2f}%"
    )


if __name__ == "__main__":
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

    mc = monte_carlo_carry(fa, n_paths=500, block=24, seed=1)
    a = mc["annualized_net_yield_pct"]
    print(
        f"\nMonte-Carlo (A, 500 paths): annualized net yield "
        f"p5/p50/p95 = {a['p5']:+.2f}/{a['p50']:+.2f}/{a['p95']:+.2f}%"
    )
