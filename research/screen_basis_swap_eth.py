"""08c — Quarterly-futures basis leg-swap, ETH variant (edge-screener run).

Pre-registered in _workspace/strategy_pipeline/08c_screen_basis_swap.md — the
feasibility half (frozen 2026-07-11) plus the ETH SCREEN PRE-REGISTRATION
(frozen 2026-07-11 BEFORE scripts/backfill_quarterly_basis.py pulled any data).
BTC is INFEASIBLE-AT-CURRENT-CAPITAL and is NOT screened.

Frozen design (see 08c; NOT re-registered here):
  * Decision points: 1st of month 00:00 UTC, 2021-03 -> 2026-06, current-quarter
    contract >= 14 days to expiry. Hold = entry -> expiry (last Friday of
    Mar/Jun/Sep/Dec 08:00 UTC). Entries within a quarter share an expiry
    (overlap stated; walk-forward purge; MC limitation stated).
  * PRIMARY sample: events where APR_basis(entry) > APR_funding(entry)
    (trailing 21 realized binance prints strictly before entry x 3 x 365).
  * Δ = [basis_frac(entry) - basis_frac(exit) - 0.0045]
      - [Σ realized funding in [entry, expiry) - 0.0050].
    cost_qtr = 45 bps (spot RT 20 + qtr entry taker 5 + settlement fee 5 +
    3 crossings x 5 slip); perp arm = F1's frozen 50 bps, one round trip.
  * Gates: DSR>=0.10 (n_trials=1), PBO<=0.5 (2-col basis/perp matrix),
    OOS-WR>=0.55 (4 folds, embargo+purge), MC P>0>=0.95 & maxDD p95<=0.25,
    min_trades=30; conditional n<30 -> INSUFFICIENT_DATA (small-N binds first,
    exactly as pre-registered). NaN fails closed.

Run: venv/Scripts/python.exe research/screen_basis_swap_eth.py
"""
from __future__ import annotations

import calendar
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_listing_short import (  # noqa: E402  (audited helpers)
    MAX_PBO,
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _oos_wr_walk_forward,
    _pbo_across_horizons,
    load_funding_history,
)

# ── Frozen constants (08c ETH registration) ──────────────────────────────────
QTR_PATH = "data/ohlcv_cache/ETH-USDT_qtrcont_1h.parquet"
SPOT_PATH = "data/ohlcv_cache/ETH-USDT_qtrspot_1h.parquet"
COST_QTR = 0.0045
COST_PERP = 0.0050
MIN_DTE_DAYS = 14.0
MIN_N = 30
N_TRIALS = 1
TRAIL_PRINTS = 21
DAY = 86400
HOUR = 3600
DEC_LO = (2021, 3)
DEC_HI = (2026, 6)  # inclusive


# ── Pure functions (unit-tested) ────────────────────────────────────────────
def last_friday_expiry(year: int, month: int) -> int:
    """Binance quarterly expiry: last Friday of the month, 08:00 UTC (unix s)."""
    last_dom = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last_dom, 8, 0, tzinfo=timezone.utc)
    offset = (d.weekday() - 4) % 7  # weekday(): Mon=0 ... Fri=4
    return int(d.timestamp()) - offset * DAY


def expiry_schedule(y0: int = 2021, y1: int = 2026) -> list[int]:
    return [last_friday_expiry(y, m) for y in range(y0, y1 + 1)
            for m in (3, 6, 9, 12)]


def next_expiry(entry_ts: int, schedule: list[int]):
    for e in schedule:
        if e > entry_ts:
            return e
    return None


def decision_points() -> list[int]:
    """1st of month 00:00 UTC from DEC_LO to DEC_HI inclusive."""
    out = []
    y, m = DEC_LO
    while (y, m) <= DEC_HI:
        out.append(int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp()))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def apr_basis(basis_frac: float, dte_days: float) -> float:
    return basis_frac * 365.0 / dte_days


def apr_funding(trail_mean_per_settlement: float) -> float:
    return trail_mean_per_settlement * 3.0 * 365.0


def trailing_mean_before(ts_arr: np.ndarray, rates: np.ndarray, entry_ts: int,
                         k: int = TRAIL_PRINTS):
    """Mean of the last k prints STRICTLY before entry_ts; None if none."""
    hi = int(np.searchsorted(ts_arr, entry_ts, side="left"))
    lo = max(0, hi - k)
    sl = rates[lo:hi]
    return float(sl.mean()) if sl.size else None


# ── Screen ───────────────────────────────────────────────────────────────────
def _close_map(path: str) -> dict:
    df = pd.read_parquet(path, columns=["ts", "close"])
    return dict(zip(df["ts"].astype("int64"), df["close"].astype(float)))


def _bar_at_or_after(cm: dict, keys: np.ndarray, ts: int, tol: int = 12 * HOUR):
    i = int(np.searchsorted(keys, ts))
    if i >= len(keys) or keys[i] - ts > tol:
        return None
    return int(keys[i])


def _last_bar_before(keys: np.ndarray, ts: int, tol: int = 12 * HOUR):
    i = int(np.searchsorted(keys, ts, side="left")) - 1
    if i < 0 or ts - keys[i] > tol:
        return None
    return int(keys[i])


def run_screen() -> dict:
    for p in (QTR_PATH, SPOT_PATH):
        if not os.path.exists(p):
            return {"candidate": "08c — ETH quarterly basis leg-swap",
                    "verdict": "INSUFFICIENT_DATA",
                    "reason": f"missing {p} (run scripts/backfill_quarterly_basis.py)"}
    qtr = _close_map(QTR_PATH)
    spot = _close_map(SPOT_PATH)
    common = np.array(sorted(set(qtr) & set(spot)), dtype="int64")
    fser = load_funding_history("ETH", "binance")
    fts = np.asarray(fser.index, dtype="int64")
    frates = np.asarray(fser.values, dtype=float)

    sched = expiry_schedule()
    rows, excl = [], {"no_entry_bar": 0, "dte_below_14d": 0, "no_exit_bar": 0,
                      "no_trailing_funding": 0, "unresolved": 0}
    now_ts = int(common[-1]) if len(common) else 0
    for dp in decision_points():
        ets = _bar_at_or_after(qtr, common, dp)
        if ets is None:
            excl["no_entry_bar"] += 1
            continue
        exp = next_expiry(ets, sched)
        if exp is None or exp > now_ts:
            excl["unresolved"] += 1
            continue
        dte = (exp - ets) / DAY
        if dte < MIN_DTE_DAYS:
            excl["dte_below_14d"] += 1
            continue
        xts = _last_bar_before(common, exp)
        if xts is None or xts <= ets:
            excl["no_exit_bar"] += 1
            continue
        m21 = trailing_mean_before(fts, frates, ets)
        if m21 is None:
            excl["no_trailing_funding"] += 1
            continue
        b_in = (qtr[ets] - spot[ets]) / spot[ets]
        b_out = (qtr[xts] - spot[xts]) / spot[xts]
        lo = int(np.searchsorted(fts, ets, side="left"))
        hi = int(np.searchsorted(fts, exp, side="left"))
        fsum = float(frates[lo:hi].sum())
        basis_net = b_in - b_out - COST_QTR
        perp_net = fsum - COST_PERP
        rows.append({
            "entry_ts": ets, "exit_ts": xts, "expiry": exp, "dte_days": dte,
            "basis_frac_entry": b_in, "basis_frac_exit": b_out,
            "apr_basis": apr_basis(b_in, dte), "apr_funding": apr_funding(m21),
            "conditional": apr_basis(b_in, dte) > apr_funding(m21),
            "basis_net": basis_net, "perp_net": perp_net,
            "delta": basis_net - perp_net,
        })

    cond = [r for r in rows if r["conditional"]]
    out = {
        "candidate": "08c — ETH quarterly basis leg-swap (conditional leg-swap vs perp)",
        "costs": {"cost_qtr_frac": COST_QTR, "cost_perp_frac": COST_PERP},
        "n_decision_points": len(decision_points()),
        "n_resolved_events": len(rows),
        "n_conditional": len(cond),
        "exclusions": excl,
        "n_trials_dsr": N_TRIALS,
        "unconditional_diagnostic": {
            "delta_mean": float(np.mean([r["delta"] for r in rows])) if rows else None,
            "delta_win_rate": float(np.mean([r["delta"] > 0 for r in rows])) if rows else None,
            "apr_basis_mean": float(np.mean([r["apr_basis"] for r in rows])) if rows else None,
            "apr_funding_mean": float(np.mean([r["apr_funding"] for r in rows])) if rows else None,
        },
    }
    if len(cond) < MIN_N:
        out.update({
            "verdict": "INSUFFICIENT_DATA",
            "reason": f"Conditional sample n={len(cond)} < frozen minimum {MIN_N} "
                      f"(small-N binds before the gates, exactly as pre-registered). "
                      f"Resolved events total={len(rows)}.",
            "gates": {},
        })
        return out

    d = np.array([r["delta"] for r in cond], dtype=float)
    entries = [(r["entry_ts"], r["exit_ts"]) for r in cond]
    mat = np.array([[r["basis_net"], r["perp_net"]] for r in cond], dtype=float)
    dsr = _dsr_prob(d, n_trials=N_TRIALS)
    pbo = _pbo_across_horizons(mat)
    oos = _oos_wr_walk_forward(d, entries)
    mc = _monte_carlo(d)
    checks = {
        "mean_delta>0": bool(np.mean(d) > 0),
        "wr>=0.55": bool(np.mean(d > 0) >= MIN_OOS_WR),
        "oos_wr>=0.55": bool(np.isfinite(oos) and oos >= MIN_OOS_WR),
        "dsr>=0.10": bool(np.isfinite(dsr) and dsr >= MIN_DSR),
        "pbo<=0.5": bool(np.isfinite(pbo) and pbo <= MAX_PBO),
        "mc_P>0>=0.95": bool(mc.get("p_total_positive") is not None
                             and mc["p_total_positive"] >= 0.95),
        "mc_maxDD_p95<=0.25": bool(mc.get("max_drawdown_p95") is not None
                                   and mc["max_drawdown_p95"] <= 0.25),
    }
    out.update({
        "conditional_stats": {
            "delta_mean": float(np.mean(d)), "delta_median": float(np.median(d)),
            "win_rate": float(np.mean(d > 0)), "dsr_prob": dsr,
            "pbo_basis_vs_perp": pbo, "oos_wr_walk_forward": oos,
            "monte_carlo": mc,
        },
        "gates": checks,
        "verdict": "GO" if all(checks.values()) else "NO_GO",
        "reason": ("All frozen gates pass." if all(checks.values()) else
                   "Failed: " + ", ".join(k for k, v in checks.items() if not v)),
    })
    return out


if __name__ == "__main__":
    print(json.dumps(run_screen(), indent=2, default=str))
