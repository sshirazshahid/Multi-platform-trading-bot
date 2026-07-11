"""08b — Pre-unlock short, capital-scaled (edge-screener re-run, calendar unblocked).

Pre-registered in _workspace/strategy_pipeline/08b_screen_preunlock_short.md
(frozen 2026-07-11; execution addendum frozen BEFORE this ran). Audit 08d C2-a
unblocked the calendar via the keyless DefiLlama datasets bucket
(scripts/backfill_unlock_calendar.py -> data/unlock_calendar/{SYMBOL}.json).

Frozen design (NOT re-registered here — see 08b):
  * Windows: W1 entry (T floor-day)-28d -> exit nearest-T bar; W2 -14d -> T;
    W3 entry nearest-T -> exit first bar >= T+3d. n_trials = 3.
  * Events: merged cliff unlocks 2023-06 -> 2026-06, event tokens exclude
    insiders + noncirculating, ratio = tokens / documented circ(T) >= 0.10.
  * Prices: local 1h closes (spot-as-perp proxy, stated divergence).
  * Costs: venue futures taker (config.FEE) + 5 bps slip/side; realized funding
    charged settlement-aligned to the SHORT (venue preference binance>bybit>bitget,
    first with full window coverage). No coverage -> EXCLUDED and counted.
  * Sizing: 3% stake, 12% cap (4 concurrent), unlevered; account curve with
    concurrent MTM; maxDD gate = MC p95 <= 0.25 AND realized MTM maxDD <= 0.25.
  * AUC score (frozen pre-outcome): tanh(ratio/0.20) + 10 x funding print nearest
    entry. Gates: DSR>=0.10 (n_trials=3), PBO<=0.5, OOS-WR>=0.55, AUC>=0.60,
    MC P(total>0)>=0.95, min n=30 per variant else INSUFFICIENT_DATA.

Run: venv/Scripts/python.exe research/screen_preunlock_short.py
Emits JSON diagnostics + verdict to stdout; writes nothing by default.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_listing_short import (  # noqa: E402  (audited helpers, reused)
    MAX_PBO,
    MC_MIN_TRADES,
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _oos_wr_walk_forward,
    _pbo_across_horizons,
    apply_concurrency_cap,
    funding_sum_in_window,
    load_funding_history,
    short_net_return,
    window_funding_covered,
)

# ── Frozen constants (08b) ───────────────────────────────────────────────────
CALENDAR_DIR = "data/unlock_calendar"
OHLCV_DIR = "data/ohlcv_cache"
WIN_LO = int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp())
WIN_HI = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
RATIO_MIN = 0.10
DAY = 24 * 3600
WINDOWS = ("W1", "W2", "W3")
STAKE_FRAC = 0.03
MAX_CONCURRENT = 4  # 12% / 3%
N_TRIALS = 3
MIN_N = 30
BAR_TOL = 12 * 3600
VENUE_ORDER = ("binance", "bybit", "bitget")
MIN_AUC = 0.60
AUC_RATIO_SCALE = 0.20  # frozen pre-outcome (08b execution addendum §6)
AUC_FUNDING_W = 10.0


# ── Pure functions (unit-tested) ────────────────────────────────────────────
def floor_day(ts: int) -> int:
    """ts floored to 00:00 UTC."""
    return int(ts // DAY * DAY)


def nearest_bar(df: pd.DataFrame, target_ts: int, tol: int = BAR_TOL):
    """(close, ts) of the bar whose OPEN ts is nearest target_ts within tol."""
    if df is None or len(df) == 0:
        return (None, None)
    idx = int(np.searchsorted(df["ts"].values, target_ts))
    best = None
    for j in (idx - 1, idx):
        if 0 <= j < len(df):
            ts = int(df["ts"].iloc[j])
            d = abs(ts - target_ts)
            if best is None or d < best[0]:
                best = (d, ts, float(df["close"].iloc[j]))
    if best is None or best[0] > tol:
        return (None, None)
    return (best[2], best[1])


def first_bar_at_or_after(df: pd.DataFrame, target_ts: int, tol: int = BAR_TOL):
    """(close, ts) of first bar with open ts >= target_ts, within tol."""
    if df is None or len(df) == 0:
        return (None, None)
    idx = int(np.searchsorted(df["ts"].values, target_ts))
    if idx >= len(df):
        return (None, None)
    ts = int(df["ts"].iloc[idx])
    if ts - target_ts > tol:
        return (None, None)
    return (float(df["close"].iloc[idx]), ts)


def window_bounds(T: int, window: str) -> tuple[int, int]:
    """Frozen entry/exit TARGET timestamps for a window variant."""
    if window == "W1":
        return floor_day(T) - 28 * DAY, T
    if window == "W2":
        return floor_day(T) - 14 * DAY, T
    if window == "W3":
        return T, T + 3 * DAY
    raise ValueError(window)


def auc_score(ratio: float, funding_entry: float) -> float:
    """Frozen pre-outcome discriminating score (08b addendum §6)."""
    return float(np.tanh(ratio / AUC_RATIO_SCALE) + AUC_FUNDING_W * funding_entry)


def rank_auc(scores, labels) -> float:
    """Mann-Whitney AUC of scores against binary labels. NaN if one class."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_s = s[order]
    i = 0
    r = np.arange(1, len(s) + 1, dtype=float)
    while i < len(s):  # average ranks for ties
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        r[i:j + 1] = (i + j + 2) / 2.0
        i = j + 1
    ranks[order] = r
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def max_drawdown(curve: np.ndarray) -> float:
    """Max peak-to-trough drawdown (fraction of peak) of an equity curve."""
    if curve.size == 0:
        return 0.0
    peak = np.maximum.accumulate(curve)
    return float(np.max((peak - curve) / peak))


def funding_print_nearest(series: pd.Series, ts: int) -> float:
    """Realized funding print nearest ts (frozen AUC input); 0.0 if empty."""
    if series is None or len(series) == 0:
        return 0.0
    idx = np.asarray(series.index, dtype="int64")
    return float(series.iloc[int(np.argmin(np.abs(idx - ts)))])


def build_mtm_curve(positions: list[dict], price_dfs: dict) -> np.ndarray:
    """Realized CONCURRENT-MTM daily account equity curve (start = 1.0).

    positions: [{sym, entry_ts, exit_ts, entry_px, net_ret, fee, slip, fser}].
    Marks at 00:00 UTC days; open positions marked with the full round-trip cost
    charged from day one (conservative, stated in 08b addendum §7)."""
    if not positions:
        return np.array([1.0])
    lo = floor_day(min(p["entry_ts"] for p in positions))
    hi = floor_day(max(p["exit_ts"] for p in positions)) + DAY
    days = np.arange(lo, hi + DAY, DAY, dtype="int64")
    eq = np.full(days.shape, 1.0)
    for p in positions:
        df = price_dfs[p["sym"]]
        for k, d in enumerate(days):
            if d < p["entry_ts"]:
                continue
            if d >= p["exit_ts"]:
                eq[k] += STAKE_FRAC * p["net_ret"]
                continue
            px, _ = nearest_bar(df, int(d))
            if px is None or p["entry_px"] <= 0:
                continue
            fsum = funding_sum_in_window(p["fser"], p["entry_ts"], int(d))
            eq[k] += STAKE_FRAC * short_net_return(
                p["entry_px"], px, fsum, p["fee"], p["slip"])
    return eq


# ── Screen orchestration ────────────────────────────────────────────────────
def load_calendar() -> list[dict]:
    docs = []
    for f in sorted(glob.glob(os.path.join(CALENDAR_DIR, "*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                docs.append(json.load(fh))
        except Exception:  # noqa: BLE001 - count, never guess
            continue
    return docs


def run_screen() -> dict:
    import config

    slip = (float(config.SLIPPAGE["pct_open"]) + float(config.SLIPPAGE["pct_close"])) / 2.0
    fees = {v: float(config.FEE[f"{v}_futures_taker"]) for v in VENUE_ORDER}

    docs = load_calendar()
    excl = {"zero_tokens_or_no_ratio": 0, "ratio_below_10pct": 0, "no_ohlcv_file": 0,
            "no_price_window": 0, "no_funding_coverage": 0}
    qualifying = []  # (sym, T, ratio)
    price_dfs: dict[str, pd.DataFrame] = {}
    fser_cache: dict[tuple, pd.Series] = {}

    for doc in docs:
        sym = doc["symbol"]
        path = os.path.join(OHLCV_DIR, f"{sym}-USDT_1h.parquet")
        have_px = os.path.exists(path)
        for e in doc.get("events") or []:
            if not e.get("tokens") or e.get("ratio") is None:
                excl["zero_tokens_or_no_ratio"] += 1
                continue
            if e["ratio"] < RATIO_MIN:
                excl["ratio_below_10pct"] += 1
                continue
            if not (WIN_LO <= e["ts"] < WIN_HI):
                continue
            if not have_px:
                excl["no_ohlcv_file"] += 1
                continue
            if sym not in price_dfs:
                price_dfs[sym] = pd.read_parquet(path, columns=["ts", "close"])
            qualifying.append((sym, int(e["ts"]), float(e["ratio"])))

    def fser(sym, venue):
        key = (venue, sym)
        if key not in fser_cache:
            fser_cache[key] = load_funding_history(sym, venue)
        return fser_cache[key]

    per_window: dict[str, dict] = {}
    ret_by_event: dict[str, dict] = {w: {} for w in WINDOWS}
    for w in WINDOWS:
        cand = []  # dict per event
        e_no_price = e_no_funding = 0
        for sym, T, ratio in qualifying:
            df = price_dfs[sym]
            ent_t, ex_t = window_bounds(T, w)
            if w == "W3":
                p0, a0 = nearest_bar(df, ent_t)
                p1, a1 = first_bar_at_or_after(df, ex_t)
            else:
                p0, a0 = first_bar_at_or_after(df, ent_t)
                p1, a1 = nearest_bar(df, ex_t)
            if not (p0 and p1 and p0 > 0) or a1 <= a0:
                e_no_price += 1
                continue
            venue = next((v for v in VENUE_ORDER
                          if window_funding_covered(fser(sym, v), a0, a1)), None)
            if venue is None:
                e_no_funding += 1
                continue
            s = fser(sym, venue)
            fsum = funding_sum_in_window(s, a0, a1)
            net = short_net_return(p0, p1, fsum, fees[venue], slip)
            cand.append({"sym": sym, "T": T, "ratio": ratio, "entry_ts": a0,
                         "exit_ts": a1, "entry_px": p0, "net_ret": net,
                         "fsum": fsum, "venue": venue, "fee": fees[venue],
                         "slip": slip, "fser": s,
                         "score": auc_score(ratio, funding_print_nearest(s, a0))})
        ee = [(i, c["entry_ts"], c["exit_ts"]) for i, c in enumerate(cand)]
        acc_idx, capped_idx, peak = apply_concurrency_cap(ee, MAX_CONCURRENT)
        accepted = [cand[i] for i in acc_idx]
        rets = np.array([a["net_ret"] for a in accepted], dtype=float)
        acct = STAKE_FRAC * rets
        for a in accepted:
            ret_by_event[w][(a["sym"], a["T"])] = a["net_ret"]
        n = int(rets.size)
        entry = {"n_candidates": len(cand), "n_accepted": n,
                 "n_capped_out": len(capped_idx), "peak_concurrent": peak,
                 "excluded_no_price_window": e_no_price,
                 "excluded_no_funding_coverage": e_no_funding}
        if n:
            mtm = build_mtm_curve(accepted, price_dfs)
            fsums = np.array([a["fsum"] for a in accepted], dtype=float)
            entry.update({
                "raw_net_mean": float(np.mean(rets)),
                "raw_net_median": float(np.median(rets)),
                "acct_total_return": float(np.sum(acct)),
                "win_rate": float(np.mean(rets > 0)),
                "funding_sum_mean": float(np.mean(fsums)),
                "funding_negative_rate": float(np.mean(fsums < 0)),
                "dsr_prob": _dsr_prob(acct, n_trials=N_TRIALS),
                "monte_carlo": _monte_carlo(acct),
                "oos_wr_walk_forward": _oos_wr_walk_forward(
                    acct, [(a["entry_ts"], a["exit_ts"]) for a in accepted]),
                "auc_frozen_score": rank_auc([a["score"] for a in accepted], rets > 0),
                "realized_mtm_maxdd": max_drawdown(mtm),
                "venue_mix": {v: sum(1 for a in accepted if a["venue"] == v)
                              for v in VENUE_ORDER},
            })
        per_window[w] = entry

    # PBO across the 3 frozen window variants on common events
    common = set(ret_by_event["W1"]) & set(ret_by_event["W2"]) & set(ret_by_event["W3"])
    common = sorted(common)
    if len(common) >= 4:
        matrix = np.array([[ret_by_event[w][k] for w in WINDOWS] for k in common],
                          dtype=float)
        pbo_value = _pbo_across_horizons(matrix)
    else:
        pbo_value = float("nan")

    verdict, reason, detail = _decide(per_window, pbo_value)
    return {
        "candidate": "08b — pre-unlock short, capital-scaled (calendar re-run)",
        "costs": {"fees_per_side": fees, "slip_per_side": slip},
        "universe": {
            "calendar_symbols": len(docs),
            "qualifying_events_ratio>=10pct": len(qualifying),
            "exclusions": excl,
        },
        "sizing": {"stake_frac": STAKE_FRAC, "max_concurrent": MAX_CONCURRENT},
        "n_trials_dsr": N_TRIALS,
        "windows": per_window,
        "pbo_across_windows": pbo_value,
        "gate_detail": detail,
        "verdict": verdict,
        "reason": reason,
        "thresholds": {"MIN_DSR": MIN_DSR, "MAX_PBO": MAX_PBO, "MIN_OOS_WR": MIN_OOS_WR,
                       "MIN_AUC": MIN_AUC, "MC_MIN_TRADES": MC_MIN_TRADES,
                       "MIN_N": MIN_N, "maxDD<=": 0.25, "MC_P>0>=": 0.95},
    }


def _decide(per_window: dict, pbo_value: float) -> tuple[str, str, dict]:
    """Frozen 08b decision tree. NaN fails closed."""
    ns = {w: per_window[w].get("n_accepted", 0) for w in WINDOWS}
    detail = {"n_by_window": ns, "pbo_across_windows": pbo_value}
    if not any(n >= MIN_N for n in ns.values()):
        return ("INSUFFICIENT_DATA",
                f"No window variant reaches the frozen minimum n={MIN_N} qualifying "
                f"resolved events (n={ns}).", detail)
    go, nogo = [], []
    for w in WINDOWS:
        c = per_window[w]
        if ns[w] < MIN_N:
            detail[w] = {"evaluable": False}
            continue
        mc = c.get("monte_carlo", {})
        oos = c.get("oos_wr_walk_forward")
        dsr = c.get("dsr_prob")
        auc = c.get("auc_frozen_score")
        checks = {
            "mean>0": bool(c.get("raw_net_mean") is not None and c["raw_net_mean"] > 0),
            "wr>=0.55": bool(c.get("win_rate") is not None and c["win_rate"] >= MIN_OOS_WR),
            "oos_wr>=0.55": bool(oos is not None and np.isfinite(oos) and oos >= MIN_OOS_WR),
            "dsr>=0.10": bool(dsr is not None and np.isfinite(dsr) and dsr >= MIN_DSR),
            "pbo<=0.5": bool(np.isfinite(pbo_value) and pbo_value <= MAX_PBO),
            "auc>=0.60": bool(auc is not None and np.isfinite(auc) and auc >= MIN_AUC),
            "mc_P>0>=0.95": bool(mc.get("p_total_positive") is not None
                                 and mc["p_total_positive"] >= 0.95),
            "mc_maxDD_p95<=0.25": bool(mc.get("max_drawdown_p95") is not None
                                       and mc["max_drawdown_p95"] <= 0.25),
            "realized_mtm_maxDD<=0.25": bool(c.get("realized_mtm_maxdd") is not None
                                             and c["realized_mtm_maxdd"] <= 0.25),
        }
        detail[w] = checks
        (go if all(checks.values()) else nogo).append(
            w if all(checks.values())
            else f"{w}: failed {[k for k, v in checks.items() if not v]}")
    if go:
        return ("GO", f"All frozen gates pass at {go}.", detail)
    return ("NO_GO", "No window variant clears all frozen gates. " + " | ".join(nogo),
            detail)


if __name__ == "__main__":
    print(json.dumps(run_screen(), indent=2, default=str))
