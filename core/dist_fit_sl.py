"""Distribution-fitted SL/TP (Phase 2 / Task B).

Replaces the fixed `ATR × 1.8 / 4.5` SL/TP block in risk_manager.get_sl_tp
and the duplicate `max(1.5%, min(3.5%, atr_1h_pct * 1.5))` in
mcp_brain._score_coin. Fits stop loss to the realized MAE distribution per
(symbol, regime) cell.

Inputs
------
symbol      : ccxt symbol (e.g. "BTC/USDT:USDT")
side        : 'buy' / 'sell' / 'long' / 'short'
atr_pct     : 1h ATR as a fraction (e.g. 0.012 for 1.2%)
regime      : optional regime tag — when warehouse rows don't yet carry a
              regime column we silently aggregate across regimes.

Outputs
-------
(sl_pct, tp_pct) — both fractions of entry, e.g. (0.018, 0.045).

Sample-size policy
------------------
n < MIN_FIT_TRADES (default 30) for the (symbol[, regime]) cell → fall back
to the historical ATR×1.8 / ATR×4.5 formula. The caller decides whether to
log the fallback.

MAE / MFE proxy
---------------
Existing warehouse `trades` rows do NOT store intra-trade extremes. We
approximate:

  MAE (loser) ≈ |entry_px - exit_px| / entry_px   when exit_reason == 'stop_loss'
                                                  (the price reached at least
                                                  the stop level)
  MAE (other loss) ≈ realized loss %              when realized_pnl < 0
  MFE (winner) ≈ |exit_px - entry_px| / entry_px  when realized_pnl > 0
                                                  (price reached at least the
                                                  exit level)

C7 (tpbot retrofit 2026-07-08): that future change landed — the 10s monitor
now persists real intra-trade extremes onto trades.mfe/mae and
_row_mae_mfe PREFERS them; the proxies above remain only as the fallback
for legacy/NULL rows (and for wicks between 10s polls, which sampling
still misses).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from loguru import logger

MIN_FIT_TRADES = 30        # per (symbol[, regime]) cell — below this, fallback
LOOKBACK_DAYS = 90         # rolling fit window
SL_QUANTILE = 0.85         # SL covers 85% of realized MAEs
SL_FLOOR_ATR_MULT = 1.0    # never tighter than 1.0× ATR
SL_CEIL_ATR_MULT = 4.0     # never wider than 4.0× ATR
R_TARGET_MIN = 1.5         # tp/sl ratio clamps
R_TARGET_MAX = 3.0
R_FALLBACK = 2.0           # used when winners absent in cell
ATR_FALLBACK_SL_MULT = 1.8
ATR_FALLBACK_TP_MULT = 4.5


@dataclass
class FitResult:
    sl_pct: float
    tp_pct: float
    source: str           # 'fitted' | 'fallback'
    n: int                # how many trades fed the fit (0 on fallback)
    cell: str             # 'BTC/USDT:USDT|trend' or 'BTC/USDT:USDT|*'
    r_target: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.sl_pct, self.tp_pct)


def _normalize_side(side: str) -> str:
    s = (side or "").strip().lower()
    if s in ("buy", "long", "+1", "1"):
        return "buy"
    if s in ("sell", "short", "-1"):
        return "sell"
    raise ValueError(f"unknown side: {side!r}")


def _quantile(xs: list[float], q: float) -> float:
    """Linear-interpolation quantile. Avoids a numpy dep here."""
    if not xs:
        return 0.0
    arr = sorted(xs)
    if q <= 0:
        return arr[0]
    if q >= 1:
        return arr[-1]
    pos = q * (len(arr) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(arr) - 1)
    frac = pos - lo
    return arr[lo] * (1 - frac) + arr[hi] * frac


def _row_mae_mfe(row: dict) -> tuple[float, float]:
    """Per-row (MAE, MFE) in fractional units of entry price.

    C7 (tpbot retrofit 2026-07-08): prefers the REAL intra-trade extremes
    now persisted on trades rows (positive fractions, written by the 10s
    monitor) — the upgrade this module's docstring always called for.
    Legacy/NULL rows fall back to the documented exit-price proxies, so
    the fit degrades gracefully and improves as real rows accumulate.
    """
    try:
        r_mae = row.get("mae")
        r_mfe = row.get("mfe")
        if r_mae is not None and r_mfe is not None:
            r_mae_f = float(r_mae)
            r_mfe_f = float(r_mfe)
            if r_mae_f > 0.0 or r_mfe_f > 0.0:
                return max(r_mae_f, 0.0), max(r_mfe_f, 0.0)
    except (TypeError, ValueError):
        pass  # malformed extremes -> proxy fallback
    entry = float(row.get("entry_px") or 0.0)
    exit_ = float(row.get("exit_px") or 0.0)
    pnl = float(row.get("realized_pnl") or 0.0)
    if entry <= 0 or exit_ <= 0:
        return 0.0, 0.0
    move = abs(exit_ - entry) / entry
    if pnl > 0:
        return 0.0, move
    # Loser: MAE is at least the round-trip excursion
    return move, 0.0


class DistFitSL:
    """Fits SL/TP from a rolling warehouse window. Stateless across calls;
    the warehouse is the source of truth and we re-query each compute()."""

    def __init__(self, warehouse=None):
        self._wh = warehouse  # if None, lazy-load on first compute

    def _warehouse(self):
        if self._wh is None:
            from core.warehouse import get_warehouse
            self._wh = get_warehouse()
        return self._wh

    def _fetch_rows(self, symbol: str, since_ts: float) -> list[dict]:
        try:
            # C7: mfe/mae are the REAL intra-trade extremes when the 10s
            # monitor observed the trade (NULL on legacy rows -> proxies).
            return self._warehouse().query(
                "SELECT entry_px, exit_px, realized_pnl, exit_reason, "
                "mfe, mae "
                "FROM trades WHERE status='CLOSED' AND symbol=? "
                "AND ts_entry >= ? "
                "AND entry_px > 0 AND exit_px > 0",
                (symbol, float(since_ts)),
            )
        except Exception as e:
            logger.debug(f"[DistFitSL] warehouse query failed for {symbol}: {e}")
            return []

    def compute(
        self,
        symbol: str,
        side: str,
        atr_pct: float,
        *,
        regime: str | None = None,
        now_ts: float | None = None,
    ) -> FitResult:
        """Return (sl_pct, tp_pct) as fractions of entry.

        Falls back to ATR×1.8/4.5 when the cell has < MIN_FIT_TRADES rows.
        """
        _normalize_side(side)  # validates; result not yet used downstream
        atr_pct = max(float(atr_pct or 0.0), 0.0)
        cell_tag = f"{symbol}|{regime or '*'}"

        if atr_pct <= 0:
            # Without an ATR baseline we can't even clamp. Fall back to a
            # mid-range default (1.8%) — caller's responsibility to widen.
            return FitResult(
                sl_pct=0.018, tp_pct=0.045, source="fallback",
                n=0, cell=cell_tag, r_target=R_FALLBACK,
            )

        atr_floor = atr_pct * SL_FLOOR_ATR_MULT
        atr_ceil = atr_pct * SL_CEIL_ATR_MULT
        fallback_sl = atr_pct * ATR_FALLBACK_SL_MULT
        fallback_tp = atr_pct * ATR_FALLBACK_TP_MULT

        now = float(now_ts or time.time())
        since = now - LOOKBACK_DAYS * 86400.0
        rows = self._fetch_rows(symbol, since)

        if len(rows) < MIN_FIT_TRADES:
            return FitResult(
                sl_pct=fallback_sl, tp_pct=fallback_tp, source="fallback",
                n=len(rows), cell=cell_tag, r_target=R_FALLBACK,
            )

        maes: list[float] = []
        mfes: list[float] = []
        wins = 0
        for r in rows:
            mae, mfe = _row_mae_mfe(r)
            if mae > 0:
                maes.append(mae)
            if mfe > 0:
                mfes.append(mfe)
                wins += 1

        if len(maes) < MIN_FIT_TRADES // 2:
            # Not enough adverse data to fit the tail
            return FitResult(
                sl_pct=fallback_sl, tp_pct=fallback_tp, source="fallback",
                n=len(rows), cell=cell_tag, r_target=R_FALLBACK,
            )

        sl_pct = _quantile(maes, SL_QUANTILE)
        sl_pct = max(atr_floor, min(atr_ceil, sl_pct))

        # R_target: pick the multiple that maximises expectancy
        # E = wr·R·sl − (1−wr)·sl  →  argmax over R in [R_MIN, R_MAX]
        # Since E is monotonic in R given wr > 0, we just pick R = mean MFE/SL
        # then clamp. When MFE is sparse, fall back to R=2.0.
        if mfes:
            mean_mfe = sum(mfes) / len(mfes)
            r_target = mean_mfe / sl_pct if sl_pct > 0 else R_FALLBACK
        else:
            r_target = R_FALLBACK
        r_target = max(R_TARGET_MIN, min(R_TARGET_MAX, r_target))

        # Sanity gate: if win rate looks negative-edge, do not amplify TP.
        wr = wins / max(len(rows), 1)
        if wr < 0.30:
            r_target = R_TARGET_MIN  # keep TP modest until edge returns

        tp_pct = sl_pct * r_target

        return FitResult(
            sl_pct=sl_pct, tp_pct=tp_pct, source="fitted",
            n=len(rows), cell=cell_tag, r_target=r_target,
        )


# --------------------------------------------------------------------------
# Phase 4.1: pure-function API.
#
# The class above is the warehouse-coupled instance API consumed by today's
# risk_manager / mcp_brain callers. The function below is the new pure-
# function entry point per the Phase 4.1 spec — caller passes in the MAE/MFE
# arrays directly and gets back a (sl_pct, tp_pct) tuple. Caller wiring is
# a sibling task; this module ships the pure-function library only.
#
# Spec deviation: the spec asked for `DistFitSL.compute(...)` as a
# @staticmethod, but `DistFitSL.compute` already exists as an instance
# method with a different signature consumed by production code. We expose
# the new API as a module-level function so both can coexist without
# breaking callers.
# --------------------------------------------------------------------------


def compute_sl_tp(
    mae_history,
    mfe_history,
    atr_pct: float,
    win_rate: float,
    *,
    sl_quantile: float = 0.85,
    atr_floor: float = 1.0,
    atr_ceiling: float = 4.0,
    min_sample: int = 30,
    rr_buffer: float = 1.2,
    rr_floor: float = 1.0,
) -> tuple[float, float]:
    """Distribution-fitted (sl_pct, tp_pct) from MAE/MFE arrays.

    Replaces the fixed `ATR × 1.8 / 4.5` block — anchors SL to the realized
    MAE distribution and scales TP off the realized win rate so expectancy
    stays positive even when WR drifts.

    Parameters
    ----------
    mae_history : array-like
        Per-trade max adverse excursion as a positive fraction of price
        (e.g. 0.012 = 1.2%).
    mfe_history : array-like
        Per-trade max favorable excursion, same units.
    atr_pct : float
        Current ATR as fraction of price (e.g. 0.012 = 1.2%). Used to
        clamp the fitted SL inside [atr_floor*atr_pct, atr_ceiling*atr_pct]
        and to drive the legacy fallback.
    win_rate : float
        Historical WR for this symbol/regime in [0, 1].
    sl_quantile : float
        Fraction of MAE distribution the SL should cover.
    atr_floor, atr_ceiling : float
        SL clamp band, expressed as multiples of ATR.
    min_sample : int
        Below this MAE sample size we revert to the legacy ATR×1.8 / ATR×4.5
        fallback so the function is safe to drop in for the existing block.
    rr_buffer : float
        Multiplier on the breakeven R needed by the realized WR. >1 keeps
        expected value positive against slippage/fees.
    rr_floor : float
        Minimum tp/sl ratio. Guards against a too-tight TP when WR is
        very high.

    Returns
    -------
    (sl_pct, tp_pct) : tuple[float, float]
        Both fractions of entry price.
    """
    mae_arr = np.asarray(mae_history, dtype=float).ravel()
    np.asarray(mfe_history, dtype=float).ravel()
    atr_pct = float(atr_pct or 0.0)

    fallback_sl = atr_pct * ATR_FALLBACK_SL_MULT
    fallback_tp = atr_pct * ATR_FALLBACK_TP_MULT

    # Sample-size fallback — too few trades to fit the tail.
    if mae_arr.size < int(min_sample):
        return (fallback_sl, fallback_tp)

    # Degenerate distribution fallback — all zeros / non-positive quantile.
    # Sample size alone won't catch a vector of zeros, so test the quantile.
    sl_raw = float(np.quantile(mae_arr, float(sl_quantile)))
    if sl_raw <= 0.0 or not np.isfinite(sl_raw):
        return (fallback_sl, fallback_tp)

    # Clamp into [atr_floor*ATR, atr_ceiling*ATR].
    if atr_pct > 0:
        sl_pct = float(np.clip(sl_raw, atr_floor * atr_pct, atr_ceiling * atr_pct))
    else:
        sl_pct = sl_raw

    # TP: scale R-target from realized WR so expectancy ≥ 0.
    #   breakeven R = (1 - wr) / wr
    #   target R    = max(rr_floor, breakeven * rr_buffer)
    wr = float(np.clip(win_rate, 1e-6, 1.0 - 1e-6))
    breakeven_r = (1.0 - wr) / wr
    target_r = max(float(rr_floor), breakeven_r * float(rr_buffer))
    tp_pct = sl_pct * target_r

    return (sl_pct, tp_pct)
