"""core/shadow_resolver.py — Phase 0b forward resolver (KEYSTONE).

Replays every PENDING shadow_decisions row on CLOSED candles to produce a
forward-RESOLVED, SL-first, after-cost outcome and writes it to
shadow_outcomes. This is the single source of truth a promotion gate may read;
shadow_decisions.sim_pnl stays a projected-at-TP diagnostic only.

SL-first wick logic mirrors core.labeler.triple_barrier (López de Prado AFML
Ch.3 conservative tie-break): when a single bar's [low, high] envelope contains
BOTH barriers, the stop fires first — intra-bar order is unobservable and
assuming TP-first inflates the empirical win rate.

Costs are modelled the same way as core.agents.execution_agent (taker fees per
side + directional slippage on entry and exit), extended to emit net PnL,
MFE/MAE and funding — not just a +1/-1/0 sign label.

PAPER/sim only. This module never touches an order path.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Sequence

# Cost defaults match ExecutionAgent (bps).
FEE_BPS_PER_SIDE = 6.0
OPEN_SLIP_BPS = 5.0
EXIT_SLIP_BPS = 5.0
SL_SLIP_BPS = 10.0  # fast-move stop fills are worse

# Legacy agents never set Proposal.horizon_bars, so their rows carry 0.
# horizon=0 used to bypass the censoring guard below, resolving those rows as
# premature, non-deterministic "time" exits at whatever bars happened to be
# closed when the runner fired (found 2026-07-07: 1,746 of 2,042 pending rows).
# A missing/zero horizon now maps to this documented default — applied in BOTH
# the runner's fetch cap and resolve_one, so they always agree.
DEFAULT_HORIZON_BARS = 24

# Dollar outcomes are meaningless when projected sizing rounds to ~$0 (a
# fully-deployed paper wallet sized 2,175 outcomes at ~$0.002 notional on
# 2026-07-05..07, making net_pnl/fees/mfe/mae micro-dollar noise). Below this
# floor the resolver falls back to the row's reference sizing
# (projected_notional_alt, normally $200), then to REFERENCE_NOTIONAL_USD, so
# dollar evidence stays meaningful and comparable across rows. r_multiple is
# size-invariant either way.
MIN_RESOLVE_NOTIONAL_USD = 1.0
REFERENCE_NOTIONAL_USD = 200.0


def resolve_one(
    row: dict,
    candles: Sequence,
    *,
    fee_bps_per_side: float = FEE_BPS_PER_SIDE,
    open_slip_bps: float = OPEN_SLIP_BPS,
    exit_slip_bps: float = EXIT_SLIP_BPS,
    sl_slip_bps: float = SL_SLIP_BPS,
    funding: float = 0.0,
) -> Optional[dict]:
    """Resolve a single shadow decision against forward CLOSED candles.

    `row` needs: side, entry_px, sl_px, tp_px, horizon_bars,
    projected_notional_current. `candles` are forward bars AFTER entry,
    already filtered to closed bars: [ts_ms, open, high, low, close, vol].

    Returns an outcome dict (exit_px/exit_reason/gross_pnl/net_pnl/fees/
    slippage/funding/mfe/mae/bars_held/r_multiple) or None if unresolvable.
    """
    try:
        side = str(row["side"])
        entry = float(row["entry_px"])
        sl = float(row["sl_px"])
        tp = float(row["tp_px"])
    except (KeyError, TypeError, ValueError):
        return None
    if entry <= 0 or not candles:
        return None

    horizon = int(row.get("horizon_bars") or 0)
    if horizon <= 0:
        horizon = DEFAULT_HORIZON_BARS
    notional = float(row.get("projected_notional_current") or 0.0)
    if notional < MIN_RESOLVE_NOTIONAL_USD:
        notional = float(row.get("projected_notional_alt") or 0.0)
    if notional < MIN_RESOLVE_NOTIONAL_USD:
        notional = REFERENCE_NOTIONAL_USD
    is_buy = side == "buy"

    open_slip = entry * open_slip_bps / 10_000.0
    entry_filled = entry + open_slip if is_buy else entry - open_slip
    if entry_filled <= 0:
        return None
    size = notional / entry_filled if notional > 0 else 0.0

    scan = list(candles)
    if horizon > 0:
        scan = scan[:horizon]
    if not scan:
        return None

    exit_reason = "time"
    exit_level: Optional[float] = None
    bars_held = 0
    mfe = 0.0  # max favorable price excursion (>= 0)
    mae = 0.0  # max adverse price excursion (<= 0)
    last_close = entry_filled

    for i, c in enumerate(scan, start=1):
        try:
            high = float(c[2])
            low = float(c[3])
            close = float(c[4])
        except (IndexError, TypeError, ValueError):
            continue
        bars_held = i
        last_close = close
        if is_buy:
            mfe = max(mfe, high - entry_filled)
            mae = min(mae, low - entry_filled)
            hit_sl = sl > 0 and low <= sl
            hit_tp = tp > 0 and high >= tp
        else:
            mfe = max(mfe, entry_filled - low)
            mae = min(mae, entry_filled - high)
            hit_sl = sl > 0 and high >= sl
            hit_tp = tp > 0 and low <= tp
        if hit_sl:  # SL-first conservative tie-break (same-bar both -> stop)
            exit_reason = "stop_loss"
            exit_level = sl
            break
        if hit_tp:
            exit_reason = "take_profit"
            exit_level = tp
            break

    if exit_level is None:
        # No barrier hit. If the full horizon of forward CLOSED bars hasn't accrued
        # yet, leave the row PENDING rather than resolving a censored time-exit — that
        # would inject a biased outcome into shadow_outcomes, the promotion-trusted table.
        if horizon > 0 and len(scan) < horizon:
            return None
        exit_level = last_close  # time barrier: mark out at last closed price

    # C10 (tpbot retrofit 2026-07-08): limit-TP counterfactual over the SAME
    # scan window — measurement only, the primary resolution above is
    # untouched. See limit_tp_counterfactual for the fill model.
    ltp_touched, ltp_filled, ltp_reason = limit_tp_counterfactual(
        scan, sl, tp, is_buy, horizon=horizon)

    slip_bps = sl_slip_bps if exit_reason == "stop_loss" else exit_slip_bps
    exit_slip = exit_level * slip_bps / 10_000.0
    exit_filled = exit_level - exit_slip if is_buy else exit_level + exit_slip

    move = (exit_filled - entry_filled) if is_buy else (entry_filled - exit_filled)
    gross = move * size
    fees = (entry_filled + exit_filled) * size * fee_bps_per_side / 10_000.0
    slippage_cost = (open_slip + exit_slip) * size
    net = gross - fees + funding
    risk = abs(entry_filled - sl) * size
    r_mult = net / risk if risk > 0 else 0.0

    return {
        "exit_px": exit_filled,
        "exit_reason": exit_reason,
        "gross_pnl": gross,
        "net_pnl": net,
        "fees": fees,
        "slippage": slippage_cost,
        "funding": funding,
        "mfe": mfe * size,
        "mae": mae * size,
        "bars_held": bars_held,
        "r_multiple": r_mult,
        "ltp_touched": ltp_touched,
        "ltp_filled": ltp_filled,
        "ltp_exit_reason": ltp_reason,
    }


# C10: trade-through fraction standing in for ">= 1 tick beyond the level" —
# the resolver has no per-symbol tick metadata, so ~1bp is the documented,
# conservative proxy. A REAL resting limit also needs queue position; this
# model grants the fill on any trade-through, i.e. it still slightly
# OVERSTATES maker-TP fill rates. Read touched-vs-filled with that in mind.
LIMIT_TP_TRADE_THROUGH_FRAC = 1e-4


def limit_tp_counterfactual(
    scan: list,
    sl: float,
    tp: float,
    is_buy: bool,
    through_frac: float = LIMIT_TP_TRADE_THROUGH_FRAC,
    horizon: int = 0,
) -> tuple:
    """Counterfactual (C10): the TP is a RESTING reduce-only LIMIT (maker)
    instead of the live market-trigger conditional.

    Whole-path simulation over the same closed-bar scan the primary
    resolver used: touch != fill — the limit fills only when price trades
    THROUGH the level by ``through_frac``; a touched-but-unfilled TP leaves
    the position open, so a later SL hit books stop_loss (the adverse case
    this metric exists to expose). Same-bar SL+fill resolves SL-first
    (the primary resolver's pessimistic AFML tie-break). Measurement only:
    never touches the live order path.

    Returns (touched 0/1, filled 0/1, exit_reason).
    """
    if not tp or tp <= 0:
        return 0, 0, "no_tp"
    thr = tp * (1.0 + through_frac) if is_buy else tp * (1.0 - through_frac)
    touched = 0
    for c in scan:
        try:
            high = float(c[2])
            low = float(c[3])
        except (IndexError, TypeError, ValueError):
            continue
        if is_buy:
            if high >= tp:
                touched = 1
            hit_sl = sl > 0 and low <= sl
            filled = high >= thr
        else:
            if low <= tp:
                touched = 1
            hit_sl = sl > 0 and high >= sl
            filled = low <= thr
        if hit_sl:  # SL-first pessimistic tie-break, same as the resolver
            return touched, 0, "stop_loss"
        if filled:
            return touched, 1, "take_profit_limit"
    # Review fix 2026-07-09 (MEDIUM): when the PRIMARY resolves on an early
    # barrier hit, the scan is truncated far short of the horizon — a
    # touched-but-unfilled limit that is still open at scan end has NOT
    # honestly timed out; with the full horizon it might have filled or hit
    # the SL (the adverse case this metric exists to expose). Label those
    # windows 'censored'; touched-vs-filled readers must EXCLUDE censored
    # rows or the adverse rate is systematically understated.
    if horizon and len(scan) < horizon:
        return touched, 0, "censored"
    return touched, 0, "time"


def _write_outcome(warehouse, proposal_id: str, outcome: dict, now: int) -> None:
    conn = warehouse._conn()
    # Warehouse connections run in autocommit (isolation_level=None), so the
    # INSERT and UPDATE would otherwise commit independently: a crash between
    # them leaves an outcome row whose decision is still PENDING, which gets
    # re-resolved (and possibly RELABELLED) on the next run. One transaction.
    conn.execute("BEGIN")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO shadow_outcomes "
            "(proposal_id, exit_px, exit_reason, gross_pnl, net_pnl, fees, slippage, "
            " funding, mfe, mae, bars_held, r_multiple, resolved_ts, label_status, "
            " ltp_touched, ltp_filled, ltp_exit_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESOLVED', ?, ?, ?)",
            (
                proposal_id, outcome["exit_px"], outcome["exit_reason"],
                outcome["gross_pnl"], outcome["net_pnl"], outcome["fees"],
                outcome["slippage"], outcome["funding"], outcome["mfe"],
                outcome["mae"], outcome["bars_held"], outcome["r_multiple"], now,
                # C10 counterfactual (None-safe for outcomes from older callers)
                outcome.get("ltp_touched"), outcome.get("ltp_filled"),
                outcome.get("ltp_exit_reason"),
            ),
        )
        conn.execute(
            "UPDATE shadow_decisions SET label_status='RESOLVED' WHERE proposal_id=?",
            (proposal_id,),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def resolve_pending(
    warehouse,
    fetch_candles: Callable[[dict], Sequence],
    *,
    now: Optional[int] = None,
    **cost_kwargs,
) -> dict:
    """Resolve every PENDING shadow_decisions row with non-null barriers.

    `fetch_candles(row_dict) -> candles` must return forward CLOSED candles
    after the decision's entry; callers are responsible for dropping the
    forming bar (e.g. via core.sim_execution.last_closed_bar) so the replay
    never repaints. Rows whose candles are unavailable are left PENDING.
    """
    now_ts = int(time.time()) if now is None else int(now)
    rows = warehouse.query(
        "SELECT * FROM shadow_decisions "
        "WHERE label_status='PENDING' AND entry_px IS NOT NULL "
        "ORDER BY ts ASC"  # oldest-first: each fetch group widens at most once
    )
    resolved = 0
    skipped = 0
    for row in rows:
        proposal_id = row.get("proposal_id")
        if not proposal_id:
            skipped += 1
            continue
        candles = fetch_candles(dict(row))
        if not candles:
            skipped += 1
            continue
        outcome = resolve_one(dict(row), candles, **cost_kwargs)
        if outcome is None:
            skipped += 1
            continue
        _write_outcome(warehouse, proposal_id, outcome, now_ts)
        resolved += 1
    return {"resolved": resolved, "skipped": skipped, "n_pending": len(rows)}
