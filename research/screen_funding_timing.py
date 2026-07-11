"""08a — Funding-settlement-window timing for F1 (edge-screener re-run).

Pre-registered in _workspace/strategy_pipeline/08a_screen_funding_timing.md
(frozen 2026-07-11; execution addendum frozen BEFORE this ran). Basis data:
scripts/backfill_premium_index.py -> data/premium_index/{venue}_{BASE}_15m.parquet
(exchange premium-index klines = premium FRACTIONS, the funding input itself).

Frozen design (see 08a; NOT re-registered here):
  * Baseline B = entry at -105 min; variants V1 -15m, V2 -30m, V3 -60m,
    V4 +15m post-settlement. n_trials = 4. Exit identical (21st settlement).
  * Event universe: binance+bybit settlements for the 15 F1 coins with
    rate(s0) > 0, trailing-7d mean > 0 (strictly pre-s0), expected edge over 21
    settlements >= max(15 bps, 3 x cost) via f1_net_expected_edge_bps.
  * Costs: binance 50 bps round trip (frozen), bybit 52 bps (config.FEE-derived).
    Cost delta between variants = 0 (identical legs); V4 additionally misses
    funding(s0) — charged exactly from the realized print.
  * PRIMARY metric = the FROZEN formula, basis term -(basis(t_V) - basis(t_B)).
    The execution addendum flags this sign as inconsistent with direct
    short-perp arithmetic; the sign-corrected series is reported as a clearly
    labeled DIAGNOSTIC. Verdict follows the frozen text; auditor adjudicates.
  * Gates: DSR>=0.10 (n_trials=4), PBO<=0.5, OOS-WR>=0.55 (5 folds, embargo +
    purge), MC on the timing-modified episode curve (P>0>=0.95, maxDD p95<=0.25),
    >=120 qualifying events per venue else INSUFFICIENT_DATA, NaN fails closed.
    NO_GO includes: sign instability across folds or venues, or best improvement
    <= the unmodeled fill-quality bound (the 5 bps leg-spread cap that cannot be
    verified historically).

Run: venv/Scripts/python.exe research/screen_funding_timing.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.funding_carry_lab import (  # noqa: E402
    F1_EXPANDED_UNIVERSE_2026_07_05,
    F1_MAX_LEG_SPREAD_BPS,
    f1_net_expected_edge_bps,
)
from research.screen_listing_short import (  # noqa: E402  (audited helpers)
    MAX_PBO,
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _pbo_across_horizons,
    load_funding_history,
)

# ── Frozen constants (08a) ───────────────────────────────────────────────────
PREMIUM_DIR = "data/premium_index"
VENUES = ("binance", "bybit")
BASES = tuple(s.split("/")[0] for s in F1_EXPANDED_UNIVERSE_2026_07_05)
HOLD_SETTLEMENTS = 21
COST_FRAC = {"binance": 0.0050,  # frozen 08a cost model
             "bybit": 0.0052}    # 2x10bps spot + 2x6bps perp + 4x5bps slip
MIN_EDGE_BPS = 15.0
COST_MULT = 3.0
BAR = 900  # 15m
MIN_ = 60
# entry-time offsets (seconds) relative to s0; premium bar CLOSING at t has open t-900
OFFSETS = {"B": -105 * MIN_, "V1": -15 * MIN_, "V2": -30 * MIN_,
           "V3": -60 * MIN_, "V4": +15 * MIN_}
VARIANTS = ("V1", "V2", "V3", "V4")
N_TRIALS = 4
MIN_EVENTS_PER_VENUE = 120
N_SPLITS = 5  # pre-registered ">= 5 folds"
TRAIL_SEC = 7 * 86400


# ── Pure functions (unit-tested) ────────────────────────────────────────────
def premium_close_at(prem: dict, t: int):
    """Premium close of the 15m bar CLOSING at t (open = t - 900)."""
    return prem.get(int(t) - BAR)


def trailing_mean(ts_arr: np.ndarray, rates: np.ndarray, i: int,
                  window_sec: int = TRAIL_SEC):
    """Mean of prints in [ts_i - window, ts_i) — strictly BEFORE s0. (mean, count)."""
    lo = int(np.searchsorted(ts_arr, ts_arr[i] - window_sec, side="left"))
    sl = rates[lo:i]
    if sl.size == 0:
        return None, 0
    return float(sl.mean()), int(sl.size)


def qualifies(rate_s0: float, trail_mean, cost_frac: float) -> bool:
    """Frozen F1-regime proxy: rate>0, 7d mean>0, expected edge >= max(15, 3xcost)."""
    if rate_s0 <= 0 or trail_mean is None or trail_mean <= 0:
        return False
    edge = f1_net_expected_edge_bps(
        funding_per_settlement=trail_mean,
        hold_settlements=HOLD_SETTLEMENTS,
        round_trip_cost_frac=cost_frac,
    )
    return edge >= max(MIN_EDGE_BPS, COST_MULT * cost_frac * 1e4)


def event_deltas(rate_s0: float, prems: dict) -> tuple[dict, dict]:
    """(frozen, corrected) per-variant Δ in FRACTIONS of notional.

    frozen:    basis term = -(prem[t_V] - prem[t_B])   (the pre-registered text)
    corrected: basis term = +(prem[t_V] - prem[t_B])   (direct short-perp
               arithmetic: PnL = basis(entry) - basis(exit), same exit)
    V4 additionally misses funding(s0): delta -rate_s0 in BOTH conventions."""
    frozen, corrected = {}, {}
    for v in VARIANTS:
        d_basis = prems[v] - prems["B"]
        f_delta = -rate_s0 if v == "V4" else 0.0
        frozen[v] = f_delta - d_basis
        corrected[v] = f_delta + d_basis
    return frozen, corrected


def oos_wr_5fold(returns: np.ndarray, entries: np.ndarray, exits: np.ndarray) -> float:
    """Pooled OOS win-rate, 5 anchored folds, embargo 1, time purge (frozen)."""
    from core.walk_forward import WalkForward

    n = int(returns.size)
    if n < 10:
        return float("nan")
    wf = WalkForward(n_splits=N_SPLITS, embargo_bars=1, anchored=True)
    wins = total = 0
    for tr, te in wf.split(n):
        if len(tr) == 0 or len(te) == 0:
            continue
        test_entry_min = int(entries[te].min())
        tr = tr[exits[tr] <= test_entry_min]
        if len(tr) == 0:
            continue
        wins += int((returns[te] > 0).sum())
        total += int(te.size)
    return float(wins / total) if total else float("nan")


def fold_sign_stability(returns: np.ndarray) -> dict:
    """Per-fold OOS mean signs for the frozen sign-stability NO_GO clause."""
    from core.walk_forward import WalkForward

    n = int(returns.size)
    if n < 10:
        return {"stable": False, "signs": []}
    wf = WalkForward(n_splits=N_SPLITS, embargo_bars=1, anchored=True)
    signs = []
    for _tr, te in wf.split(n):
        if len(te):
            signs.append(int(np.sign(returns[te].mean())))
    return {"stable": bool(signs and len(set(signs)) == 1 and signs[0] != 0),
            "signs": signs}


# ── Screen orchestration ────────────────────────────────────────────────────
def load_premium(venue: str, base: str) -> dict:
    path = os.path.join(PREMIUM_DIR, f"{venue}_{base}_15m.parquet")
    if not os.path.exists(path):
        return {}
    df = pd.read_parquet(path, columns=["ts", "close"])
    return dict(zip(df["ts"].astype("int64"), df["close"].astype(float)))


def collect_events() -> tuple[list[dict], dict]:
    """All qualifying, resolvable settlement events across venues/bases."""
    events, excl = [], {"not_qualifying": 0, "unresolved_tail": 0,
                        "no_premium_bar": 0, "no_trailing_prints": 0,
                        "missing_premium_file": [], "missing_funding_file": []}
    for venue in VENUES:
        cost = COST_FRAC[venue]
        for base in BASES:
            fser = load_funding_history(base, venue)
            if len(fser) == 0:
                excl["missing_funding_file"].append(f"{venue}_{base}")
                continue
            prem = load_premium(venue, base)
            if not prem:
                excl["missing_premium_file"].append(f"{venue}_{base}")
                continue
            ts_arr = np.asarray(fser.index, dtype="int64")
            rates = np.asarray(fser.values, dtype=float)
            for i in range(len(ts_arr) - HOLD_SETTLEMENTS + 1):
                s0 = int(ts_arr[i])
                m7, cnt = trailing_mean(ts_arr, rates, i)
                if cnt == 0:
                    excl["no_trailing_prints"] += 1
                    continue
                if not qualifies(float(rates[i]), m7, cost):
                    excl["not_qualifying"] += 1
                    continue
                j = i + HOLD_SETTLEMENTS - 1  # s20 = 21st captured settlement
                if j >= len(ts_arr):
                    excl["unresolved_tail"] += 1
                    continue
                t_x = int(ts_arr[j])
                prems = {k: premium_close_at(prem, s0 + off)
                         for k, off in OFFSETS.items()}
                prem_x = premium_close_at(prem, t_x)
                if any(v is None for v in prems.values()) or prem_x is None:
                    excl["no_premium_bar"] += 1
                    continue
                frozen, corrected = event_deltas(float(rates[i]), prems)
                base_ret = (float(rates[i:i + HOLD_SETTLEMENTS].sum())
                            + (prems["B"] - prem_x) - cost)
                events.append({"venue": venue, "base": base, "s0": s0, "t_x": t_x,
                               "frozen": frozen, "corrected": corrected,
                               "base_ret": base_ret})
    events.sort(key=lambda e: (e["s0"], e["venue"], e["base"]))
    return events, excl


def run_screen() -> dict:
    events, excl = collect_events()
    n_by_venue = {v: sum(1 for e in events if e["venue"] == v) for v in VENUES}
    evaluable_venues = [v for v in VENUES if n_by_venue[v] >= MIN_EVENTS_PER_VENUE]

    out = {
        "candidate": "08a — funding-settlement-window timing (F1 refinement)",
        "universe": {"bases": len(BASES), "venues": list(VENUES)},
        "cost_frac": COST_FRAC,
        "n_events_by_venue": n_by_venue,
        "min_events_per_venue": MIN_EVENTS_PER_VENUE,
        "exclusions": {k: (len(v) if isinstance(v, list) else v) for k, v in excl.items()},
        "exclusion_lists": {k: v for k, v in excl.items() if isinstance(v, list)},
        "n_trials_dsr": N_TRIALS,
    }
    if not evaluable_venues:
        out.update({"verdict": "INSUFFICIENT_DATA",
                    "reason": f"No venue reaches the pre-registered minimum of "
                              f"{MIN_EVENTS_PER_VENUE} qualifying settlement events "
                              f"(n={n_by_venue}).",
                    "gates": {}})
        return out

    pool = [e for e in events if e["venue"] in evaluable_venues]
    stats = {}
    for label in ("frozen", "corrected"):
        mat = np.array([[e[label][v] for v in VARIANTS] for e in pool], dtype=float)
        means_bps = {v: float(mat[:, k].mean() * 1e4) for k, v in enumerate(VARIANTS)}
        best = max(means_bps, key=means_bps.get)
        bi = VARIANTS.index(best)
        d = mat[:, bi]
        entries = np.array([e["s0"] for e in pool], dtype="int64")
        exits = np.array([e["t_x"] for e in pool], dtype="int64")
        venue_means = {v: float(np.mean([e[label][best] for e in pool
                                         if e["venue"] == v]) * 1e4)
                       for v in evaluable_venues}
        modified = np.array([e["base_ret"] + e[label][best] for e in pool], dtype=float)
        folds = fold_sign_stability(d)
        stats[label] = {
            "variant_mean_delta_bps": means_bps,
            "best_variant": best,
            "best_mean_delta_bps": means_bps[best],
            "dsr_prob": _dsr_prob(d, n_trials=N_TRIALS),
            "pbo_across_variants": _pbo_across_horizons(mat),
            "oos_wr_5fold": oos_wr_5fold(d, entries, exits),
            "fold_signs": folds,
            "venue_mean_delta_bps": venue_means,
            "venue_sign_stable": bool(len({np.sign(x) for x in venue_means.values()}) == 1
                                      and all(x != 0 for x in venue_means.values())),
            "monte_carlo_modified_curve": _monte_carlo(modified),
            "baseline_episode_mean_ret": float(np.mean([e["base_ret"] for e in pool])),
        }

    s = stats["frozen"]  # verdict follows the FROZEN text; corrected = diagnostic
    mc = s["monte_carlo_modified_curve"]
    checks = {
        "best_mean_delta>0": s["best_mean_delta_bps"] > 0,
        f"best_mean_delta>{F1_MAX_LEG_SPREAD_BPS}bps_unmodeled_fill_bound":
            s["best_mean_delta_bps"] > F1_MAX_LEG_SPREAD_BPS,
        "dsr>=0.10": bool(np.isfinite(s["dsr_prob"]) and s["dsr_prob"] >= MIN_DSR),
        "pbo<=0.5": bool(np.isfinite(s["pbo_across_variants"])
                         and s["pbo_across_variants"] <= MAX_PBO),
        "oos_wr>=0.55": bool(np.isfinite(s["oos_wr_5fold"])
                             and s["oos_wr_5fold"] >= MIN_OOS_WR),
        "fold_sign_stable": bool(s["fold_signs"]["stable"]),
        "venue_sign_stable": bool(s["venue_sign_stable"]
                                  and len(evaluable_venues) == len(VENUES)),
        "mc_P>0>=0.95": bool(mc.get("p_total_positive") is not None
                             and mc["p_total_positive"] >= 0.95),
        "mc_maxDD_p95<=0.25": bool(mc.get("max_drawdown_p95") is not None
                                   and mc["max_drawdown_p95"] <= 0.25),
    }
    verdict = "GO" if all(checks.values()) else "NO_GO"
    reason = ("All frozen gates pass on the frozen-sign primary metric."
              if verdict == "GO" else
              "Failed: " + ", ".join(k for k, v in checks.items() if not v))
    corrected_would_differ = None
    c = stats["corrected"]
    c_checks_pass = (c["best_mean_delta_bps"] > F1_MAX_LEG_SPREAD_BPS
                     and np.isfinite(c["dsr_prob"]) and c["dsr_prob"] >= MIN_DSR
                     and np.isfinite(c["pbo_across_variants"])
                     and c["pbo_across_variants"] <= MAX_PBO
                     and np.isfinite(c["oos_wr_5fold"])
                     and c["oos_wr_5fold"] >= MIN_OOS_WR)
    corrected_would_differ = bool(c_checks_pass != (verdict == "GO"))

    out.update({
        "evaluable_venues": evaluable_venues,
        "n_pooled_events": len(pool),
        "stats_frozen_sign_PRIMARY": stats["frozen"],
        "stats_corrected_sign_DIAGNOSTIC": stats["corrected"],
        "sign_convention_flag": {
            "note": "frozen basis-term sign contradicts direct short-perp arithmetic; "
                    "verdict follows frozen text, auditor adjudicates (08a addendum §5)",
            "corrected_would_change_verdict": corrected_would_differ,
        },
        "gates": checks,
        "verdict": verdict,
        "reason": reason,
    })
    return out


if __name__ == "__main__":
    print(json.dumps(run_screen(), indent=2, default=str))
