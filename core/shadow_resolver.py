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

# Timeframe -> seconds, for the aging guard below (mirrors the runner's parser;
# core stays decoupled from scripts/).
_TF_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _tf_seconds(tf: str) -> int:
    """Parse a ccxt-style timeframe ('15m', '1h', '1d') into seconds."""
    tf = str(tf).strip().lower()
    try:
        return int(tf[:-1]) * _TF_UNITS[tf[-1]]
    except (KeyError, ValueError, IndexError):
        return 3600  # conservative default: 1h


# UNRESOLVABLE aging (2026-07-07 backlog): a PENDING decision whose forward-candle
# window should have fully closed long ago but still can't be resolved (symbol
# delisted / data gap / candles that never return) was otherwise re-fetched on
# EVERY run forever. Once wall-clock age exceeds this multiple of the horizon
# window the missing bars are never coming, so the row is marked UNRESOLVABLE (a
# terminal label_status resolve_pending's PENDING-only SELECT then skips). 2x
# leaves a full extra horizon of slack past the point a live symbol would already
# have resolved. Rows deferred purely for the per-run fetch budget are NEVER aged
# out (they had no fetch attempt this run).
UNRESOLVABLE_HORIZON_MULT = 2.0


def _aged_out(row: dict, now_ts: int) -> bool:
    """True once a PENDING row is old enough that its forward window can never
    fill — the terminal-labeling trigger. Time-only; needs no schema change."""
    try:
        entry_ts = int(row.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    if entry_ts <= 0:
        return False
    horizon = int(row.get("horizon_bars") or 0)
    if horizon <= 0:
        horizon = DEFAULT_HORIZON_BARS
    window = horizon * _tf_seconds(str(row.get("timeframe") or ""))
    return window > 0 and (now_ts - entry_ts) >= window * UNRESOLVABLE_HORIZON_MULT

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
    funding_rate_sum: float = 0.0,
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
    # Realized funding (2026-07-07 backlog 1c). `funding_rate_sum` is the sum of
    # per-settlement funding RATES over the hold (e.g. the listing probe's
    # shadow_listing_probe.realized_funding_rate_sum); convert it to dollars on
    # the SAME resolved notional the gross uses. A SHORT (sell) RECEIVES funding
    # when the rate is positive; a LONG pays it. Rows WITHOUT a realized figure
    # keep funding at 0 — an explicit, documented OPTIMISTIC bound for 8h+ probe
    # horizons; we do NOT fabricate a funding estimate (project doctrine: never
    # guess funding). The explicit `funding` dollar kwarg still adds on top.
    funding_total = float(funding)
    if funding_rate_sum:
        side_sign = -1.0 if is_buy else 1.0
        funding_total += side_sign * float(funding_rate_sum) * notional
    net = gross - fees + funding_total
    risk = abs(entry_filled - sl) * size
    r_mult = net / risk if risk > 0 else 0.0

    return {
        "exit_px": exit_filled,
        "exit_reason": exit_reason,
        "gross_pnl": gross,
        "net_pnl": net,
        "fees": fees,
        "slippage": slippage_cost,
        "funding": funding_total,
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


def _mark_unresolvable(warehouse, proposal_id: str) -> None:
    """Flag a PENDING decision terminal (UNRESOLVABLE) — its forward candles
    never materialised (delisted/vanished symbol, data gap). resolve_pending's
    PENDING-only SELECT then stops re-fetching it. NO shadow_outcomes row is
    written (there is no honest after-cost outcome) and the decision row is
    preserved — never deleted. Same terminal label the warehouse migration
    already assigns to barrier-less legacy rows (warehouse.py)."""
    # Warehouse connections run in autocommit (isolation_level=None), so this
    # single UPDATE commits on its own.
    warehouse._conn().execute(
        "UPDATE shadow_decisions SET label_status='UNRESOLVABLE' WHERE proposal_id=?",
        (proposal_id,),
    )


def resolve_pending(
    warehouse,
    fetch_candles: Callable[[dict], Sequence],
    *,
    now: Optional[int] = None,
    funding_rate_provider: Optional[Callable[[dict], float]] = None,
    **cost_kwargs,
) -> dict:
    """Resolve every PENDING shadow_decisions row with non-null barriers.

    `fetch_candles(row_dict) -> candles` must return forward CLOSED candles
    after the decision's entry; callers are responsible for dropping the
    forming bar (e.g. via core.sim_execution.last_closed_bar) so the replay
    never repaints. Rows whose candles are unavailable are left PENDING — UNLESS
    they are aged past their horizon (delisted/vanished), which marks them
    UNRESOLVABLE so they stop being re-fetched forever (2026-07-07 backlog 1a).

    A `fetch_candles` that exposes a truthy ``budget_deferred`` attribute after a
    call signals its empty return was a per-run fetch-budget cap (not a missing
    symbol); such rows are never aged out — they simply retry next run.

    `funding_rate_provider(row_dict) -> float` (optional) supplies a realized
    per-settlement funding-rate SUM per row, folded into the resolved net
    (backlog 1c). Absent/None -> funding stays 0 (documented optimistic bound).
    """
    now_ts = int(time.time()) if now is None else int(now)
    rows = warehouse.query(
        "SELECT * FROM shadow_decisions "
        "WHERE label_status='PENDING' AND entry_px IS NOT NULL "
        "ORDER BY ts ASC"  # oldest-first: each fetch group widens at most once
    )
    resolved = 0
    skipped = 0
    unresolvable = 0
    for row in rows:
        proposal_id = row.get("proposal_id")
        if not proposal_id:
            skipped += 1
            continue
        candles = fetch_candles(dict(row))
        if not candles:
            # No candles this run: budget-deferred (retry) vs delisted/vanished
            # (age out). Never terminate a row the runner merely deferred.
            deferred = bool(getattr(fetch_candles, "budget_deferred", False))
            if not deferred and _aged_out(row, now_ts):
                _mark_unresolvable(warehouse, proposal_id)
                unresolvable += 1
            else:
                skipped += 1
            continue
        frs = 0.0
        if funding_rate_provider is not None:
            try:
                frs = float(funding_rate_provider(dict(row)) or 0.0)
            except Exception:
                frs = 0.0
        outcome = resolve_one(dict(row), candles, funding_rate_sum=frs, **cost_kwargs)
        if outcome is None:
            # Got candles but can't resolve (censored: horizon window not full, or
            # a permanently-bad row). Terminal only once aged past the horizon —
            # the bars that would resolve it are never arriving.
            if _aged_out(row, now_ts):
                _mark_unresolvable(warehouse, proposal_id)
                unresolvable += 1
            else:
                skipped += 1
            continue
        _write_outcome(warehouse, proposal_id, outcome, now_ts)
        resolved += 1
    return {"resolved": resolved, "skipped": skipped,
            "unresolvable": unresolvable, "n_pending": len(rows)}
