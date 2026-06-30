"""Phase 0f — calibrated cost model: per-venue fees, slippage, breakeven, stress sweep.

Stops computing edge as ``small_number - unvalidated_flat_bp``. Three pieces:

  1. A per-venue maker/taker fee lookup sourced from ``config.FEE`` (the same
     constants the live order path uses), with a fee-tier stress multiplier.
  2. Round-trip cost = round-trip fee + open/close slippage (``config.SLIPPAGE``),
     each scalable by a stress multiplier so a backtest can price 1.5-2x slippage
     and a worse fee tier instead of one flat bp.
  3. ``calibrate_from_warehouse`` — realized fee RATE per notional from the
     warehouse's recorded broker fees (provenance documented inline), and
     ``stress_sweep`` — a pass/fail matrix per strategy across the cost grid.

Reconciliation of the two contested cost tools (``scripts/tp_accuracy_diagnosis``
~90% vs ``scripts/exec_cost_audit`` ~11%) is root-caused here: both read the SAME
``attribution`` cost numerator; the ~10x gap is purely a DENOMINATOR choice
(cost/|realized_pnl| vs cost/(|gross_alpha|+cost)). See ``reconcile_cost_shares``.

Deterministic, pure functions only — no live calls, no order path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

try:
    import config

    _FEE = config.FEE
    _SLIPPAGE = config.SLIPPAGE
except Exception:  # pragma: no cover - config always importable in this repo
    _FEE = {"futures_taker": 0.0005, "futures_maker": 0.0002}
    _SLIPPAGE = {"pct_open": 0.0005, "pct_close": 0.0005, "pct_stop_loss": 0.0010}

# Default scalp bracket geometry (mirrors scripts/scalp_replay_backtest.py).
DEFAULT_SL_PCT = 0.008
DEFAULT_TP_PCT = 0.013


# --------------------------------------------------------------------- fees
def fee_rate(
    venue: str,
    market_type: str = "futures",
    liquidity: str = "taker",
    *,
    tier_mult: float = 1.0,
) -> float:
    """Per-side fee rate for ``venue`` from ``config.FEE``, scaled by ``tier_mult``.

    Looks up ``{venue}_{market_type}_{liquidity}`` then falls back to the generic
    ``{market_type}_{liquidity}`` key. ``tier_mult`` stresses a worse fee tier
    (e.g. 2.0 = lost the VIP discount).
    """
    key = f"{venue}_{market_type}_{liquidity}"
    base = _FEE.get(key)
    if base is None:
        base = _FEE.get(f"{market_type}_{liquidity}")
    if base is None:
        raise KeyError(f"no fee rate for {key!r} or generic {market_type}_{liquidity}")
    return float(base) * float(tier_mult)


def round_trip_fee(
    venue: str,
    market_type: str = "futures",
    *,
    entry_liq: str = "taker",
    exit_liq: str = "taker",
    tier_mult: float = 1.0,
) -> float:
    """Entry + exit fee fraction (round trip)."""
    return fee_rate(venue, market_type, entry_liq, tier_mult=tier_mult) + fee_rate(
        venue, market_type, exit_liq, tier_mult=tier_mult
    )


def slippage_rt(slip_mult: float = 1.0) -> float:
    """Round-trip slippage (open + close) from ``config.SLIPPAGE``, scaled."""
    return (float(_SLIPPAGE["pct_open"]) + float(_SLIPPAGE["pct_close"])) * float(slip_mult)


def round_trip_cost(
    venue: str,
    *,
    market_type: str = "futures",
    entry_liq: str = "taker",
    exit_liq: str = "taker",
    tier_mult: float = 1.0,
    slip_mult: float = 1.0,
) -> float:
    """Total round-trip cost fraction = round-trip fee + round-trip slippage."""
    return round_trip_fee(
        venue, market_type, entry_liq=entry_liq, exit_liq=exit_liq, tier_mult=tier_mult
    ) + slippage_rt(slip_mult)


# ---------------------------------------------------------------- breakeven
def breakeven_wr(
    cost_rt: float, sl_pct: float = DEFAULT_SL_PCT, tp_pct: float = DEFAULT_TP_PCT
) -> float:
    """Win rate needed to break even on an SL/TP bracket given round-trip cost."""
    return (sl_pct + cost_rt) / (sl_pct + tp_pct)


# -------------------------------------------------------------- calibration
def calibrate_from_warehouse(
    db_path: str | Path, *, mode: str = "CONTROLLED_LIVE"
) -> dict:
    """Realized fee RATE (per notional) from the warehouse's recorded broker fees.

    PROVENANCE: ``trades.fee`` is the broker fee booked at close by the live order
    path (real round-trip taker/maker fees), NOT a model constant. We aggregate it
    over closed ``mode`` trades. ``fetch_my_trades`` ground truth (per-fill maker/
    taker) is the gold standard but is unavailable in this offline environment, so
    we calibrate against the warehouse-recorded fills and label the source. Realized
    spread/slippage is NOT recoverable from ``trades`` (needs historical mid), so the
    spread component stays modeled — flagged in ``provenance``.
    """
    db_path = str(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT entry_px, size, fee, realized_pnl FROM trades "
            "WHERE status='CLOSED' AND mode=?",
            (mode,),
        ).fetchall()
    finally:
        con.close()

    n = len(rows)
    notional = sum((r["entry_px"] or 0.0) * (r["size"] or 0.0) for r in rows)
    sum_fee = sum(r["fee"] or 0.0 for r in rows)
    realized_fee_rate = (sum_fee / notional) if notional else 0.0
    return {
        "n": n,
        "notional": notional,
        "sum_fee": sum_fee,
        "realized_fee_rate": realized_fee_rate,
        "provenance": (
            f"warehouse.sqlite trades.fee (broker fee booked at close), mode={mode}, "
            f"N={n}; fetch_my_trades per-fill ground truth UNAVAILABLE offline => "
            f"fee calibrated from recorded fills; realized spread/slippage stays modeled"
        ),
    }


# ------------------------------------------------------ tool reconciliation
def reconcile_cost_shares(
    cost_total: float, realized_pnl: float, gross_alpha: float
) -> dict:
    """Show the two cost SHARES from one cost numerator => root-cause the ~10x gap.

    ``scripts/tp_accuracy_diagnosis`` reports cost/|realized_pnl| (the "~90%"
    figure); ``scripts/exec_cost_audit`` reports cost/(|gross_alpha|+cost) (the
    "~11%" figure). Both share the SAME ``attribution`` cost numerator, so the
    disagreement is a DENOMINATOR artifact, not a measurement conflict.
    """
    denom_pnl = abs(realized_pnl) or float("nan")
    denom_alpha = abs(gross_alpha) + cost_total or float("nan")
    return {
        "numerator_cost": cost_total,
        "share_vs_pnl": cost_total / denom_pnl,
        "share_vs_alpha_plus_cost": cost_total / denom_alpha,
        "root_cause": (
            "Same cost numerator, different DENOMINATOR: tp_accuracy normalizes by "
            "|realized_pnl|, exec_cost_audit by (|gross_alpha|+cost). The ~10x gap is "
            "a normalization choice, not a measurement conflict."
        ),
    }


def cost_tools_reconciled(numer_a: float, numer_b: float, *, tol: float = 0.05) -> bool:
    """True if the two tools' cost NUMERATORS agree within relative ``tol``.

    The tools agree once normalized identically (both read ``attribution``); this
    checks the numerators are within tolerance.
    """
    denom = max(abs(numer_a), abs(numer_b)) or 1.0
    return abs(numer_a - numer_b) / denom <= tol


# ----------------------------------------------------------- stress sweep
def stress_sweep(
    strategies: list[dict],
    *,
    slippage_mults: tuple[float, ...] = (1.0, 1.5, 2.0),
    fee_tier_mults: tuple[float, ...] = (1.0, 2.0),
    market_type: str = "futures",
) -> dict:
    """Pass/fail matrix per strategy across a (slippage x fee-tier) cost grid.

    Each strategy dict: ``name``, ``venue``, ``wr`` (realized/assumed win rate),
    ``sl_pct``, ``tp_pct``, optional ``entry_liq``/``exit_liq``. A cell PASSES when
    the strategy's win rate exceeds the breakeven WR at that stressed cost.

    Returns ``{name: {(slip_mult, fee_tier_mult): {cost_rt, breakeven_wr, wr, pass}}}``.
    """
    out: dict = {}
    for s in strategies:
        name = s["name"]
        venue = s.get("venue", "bybit")
        wr = float(s["wr"])
        sl = float(s.get("sl_pct", DEFAULT_SL_PCT))
        tp = float(s.get("tp_pct", DEFAULT_TP_PCT))
        entry_liq = s.get("entry_liq", "taker")
        exit_liq = s.get("exit_liq", "taker")
        cells: dict = {}
        for slip in slippage_mults:
            for tier in fee_tier_mults:
                cost_rt = round_trip_cost(
                    venue,
                    market_type=market_type,
                    entry_liq=entry_liq,
                    exit_liq=exit_liq,
                    tier_mult=tier,
                    slip_mult=slip,
                )
                be = breakeven_wr(cost_rt, sl, tp)
                cells[(slip, tier)] = {
                    "cost_rt": cost_rt,
                    "breakeven_wr": be,
                    "wr": wr,
                    "pass": wr > be,
                }
        out[name] = cells
    return out
