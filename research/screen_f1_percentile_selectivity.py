"""15b — Funding-percentile persistence selectivity for F1 (edge-screener).

Pre-registered in _workspace/strategy_pipeline/15b_screen_f1_selectivity.md
(frozen 2026-07-16 BEFORE this ran). NULL = the incumbent funding-observable
Rev-5 ``f1_entry_gate`` replayed with the audited f1_replay_historical
episode convention; variants overlay top-quartile percentile + persistence
entry selection and a trailing-median funding-decay exit.

Frozen design (see 15b; NOT re-registered here):
  * Universe: F1_EXPANDED_UNIVERSE_2026_07_05 (15 coins), binance + bybit,
    data/funding_history/{venue}_{BASE}.csv (verified current 2026-07-16).
  * V1 (PRIMARY): P75 of trailing 270 settlements (min 180), persistence 3;
    V2 (robustness): P75 of trailing 90 (min 60), persistence 2. Both add a
    decay exit: 2 consecutive prints below the trailing-90-settlement median.
    n_trials = 2 (Bonferroni m=2). No other configuration is evaluated.
  * Costs: binance 50 bps round trip, bybit 52 bps, charged once per episode;
    realized funding credited/charged per settlement; basis PnL 0 (shared).
  * PRIMARY endpoint: after-cost net carry per settlement-deployed.
  * Gates on V1: DSR>=0.10 (n_trials=2), PBO<=0.5 (grid CSCV over
    [incumbent,V1,V2]), OOS-WR>=0.55 (episodes, 5 anchored folds, embargo +
    purge), MC P>0>=0.95 & maxDD p95<=0.25; delta>0 pooled with Bonferroni
    bootstrap CI LB>0, ALL-5-fold and both-venue sign stability; harvest
    guard: V1 total >= 0.75x incumbent total and > 0. Floors: incumbent >= 60
    episodes pooled (else INSUFFICIENT_DATA), V1 >= 30 pooled (else NO_GO),
    >= 10/venue for the venue check. NaN fails closed.

Run: venv/Scripts/python.exe research/screen_f1_percentile_selectivity.py
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.funding_carry_lab import (  # noqa: E402
    F1_COST_MULT,
    F1_EXPANDED_UNIVERSE_2026_07_05,
    F1_MAX_HOLD_SETTLEMENTS,
    F1_MIN_EDGE_BPS,
    MAX_CONSEC_NEG_SETTLEMENTS,
    f1_net_expected_edge_bps,
    f1_net_funding_lower_bound_bps,
)
from research.screen_funding_timing import oos_wr_5fold  # noqa: E402
from research.screen_listing_short import (  # noqa: E402  (audited helpers)
    MAX_PBO,
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _pbo_across_horizons,
    load_funding_history,
)

# ── Frozen constants (15b) ───────────────────────────────────────────────────
VENUES = ("binance", "bybit")
BASES = tuple(s.split("/")[0] for s in F1_EXPANDED_UNIVERSE_2026_07_05)
COST_FRAC = {"binance": 0.0050, "bybit": 0.0052}   # frozen 08a lane cost model
TRAIL = 21                                          # 7d of 8h settlements
PLAN_HOLD = 21                                      # planning horizon (edge gate)
V1_CFG = {"name": "V1", "pct_window": 270, "pct_min": 180, "persistence": 3,
          "decay_window": 90, "decay_consec": 2}
V2_CFG = {"name": "V2", "pct_window": 90, "pct_min": 60, "persistence": 2,
          "decay_window": 90, "decay_consec": 2}
N_TRIALS = 2
MIN_INCUMBENT_EPISODES = 60
MIN_VARIANT_EPISODES = 30
MIN_VENUE_EPISODES = 10
HARVEST_GUARD_FRAC = 0.75
BOOT_N = 1000
BOOT_SEED = 7
BONF_PCTL = 2.5        # 95% one-sided, Bonferroni m=2 -> 2.5th percentile
N_FOLDS = 5
STALE_TOL_SEC = 3 * 86400
BACKFILL_CMD = "venv/Scripts/python.exe scripts/backfill_funding_history.py"


# ── Pure functions (unit-tested) ─────────────────────────────────────────────
def pct_threshold(rates: np.ndarray, i: int, *, window: int, min_prints: int,
                  q: float = 75.0):
    """P``q`` of the trailing ``window`` prints STRICTLY before index i.
    None (fail-closed) when fewer than ``min_prints`` are available."""
    w = rates[max(0, i - window):i]
    if w.size < min_prints:
        return None
    return float(np.percentile(w, q))


def _trailing_mean(rates: np.ndarray, i: int):
    if i < TRAIL:
        return None
    return float(rates[i - TRAIL:i].mean())


def incumbent_entry_ok(rates: np.ndarray, i: int, cost_frac: float) -> bool:
    """Funding-observable Rev-5 f1_entry_gate: current print > 0, trailing-21
    mean > 0, projected edge (min(current, trailing mean)) over 21 settlements
    >= max(15bps, 3x cost), and the frozen bootstrap 95% lower bound > 0."""
    if i < TRAIL:
        return False
    r = float(rates[i])
    if r <= 0:
        return False
    tm = _trailing_mean(rates, i)
    if tm is None or tm <= 0:
        return False
    edge = f1_net_expected_edge_bps(
        funding_per_settlement=min(r, tm),
        hold_settlements=PLAN_HOLD,
        round_trip_cost_frac=cost_frac,
    )
    if edge < max(F1_MIN_EDGE_BPS, F1_COST_MULT * cost_frac * 1e4):
        return False
    lb = f1_net_funding_lower_bound_bps(
        rates[i - TRAIL:i],
        hold_settlements=PLAN_HOLD,
        round_trip_cost_frac=cost_frac,
    )
    return lb > 0


def make_variant_entry_fn(cfg: dict):
    """Variant entry = percentile+persistence overlay AND the incumbent gate.
    Overlay checked first (cheap); logically a strict subset of the incumbent."""
    window, min_prints = int(cfg["pct_window"]), int(cfg["pct_min"])
    k = int(cfg["persistence"])

    def entry_ok(rates: np.ndarray, i: int, cost_frac: float) -> bool:
        if i - (k - 1) < 0:
            return False
        thr = pct_threshold(rates, i, window=window, min_prints=min_prints)
        if thr is None:
            return False
        if not all(float(rates[i - o]) >= thr for o in range(k)):
            return False
        return incumbent_entry_ok(rates, i, cost_frac)

    return entry_ok


def simulate(ts: np.ndarray, rates: np.ndarray, cost_frac: float, entry_ok,
             decay_cfg: dict | None = None) -> list[dict]:
    """Sequential episode replay (audited f1_replay_historical convention).

    Entry before settlement i (episode collects rates[i] first); accrues
    realized funding each held settlement; exits on 2 consecutive negatives,
    trailing-21 regime flip, max-hold (42), or — when ``decay_cfg`` is given —
    2 consecutive prints below the trailing decay-window median. One
    round-trip cost per episode. Episodes cut by the data end are flagged
    resolved=False. No overlap; scanning resumes after each exit.
    """
    episodes: list[dict] = []
    n = len(rates)
    i = TRAIL
    d_win = int(decay_cfg["decay_window"]) if decay_cfg else 0
    d_consec = int(decay_cfg["decay_consec"]) if decay_cfg else 0
    while i < n:
        if not entry_ok(rates, i, cost_frac):
            i += 1
            continue
        accrued, consec_neg, held, decay_cnt = 0.0, 0, 0, 0
        contribs: list[tuple[int, float]] = []
        exit_reason = "max_hold"
        j = i
        while j < n and held < F1_MAX_HOLD_SETTLEMENTS:
            r = float(rates[j])
            accrued += r
            contribs.append((int(ts[j]), r))
            consec_neg = consec_neg + 1 if r < 0 else 0
            held += 1
            trail = float(rates[j - TRAIL:j].mean())  # j >= i >= TRAIL always
            if consec_neg >= MAX_CONSEC_NEG_SETTLEMENTS:
                exit_reason = "consec_negative"
                break
            if trail <= 0:
                exit_reason = "regime_flip"
                break
            if decay_cfg is not None:
                med = float(np.median(rates[max(0, j - d_win):j]))
                decay_cnt = decay_cnt + 1 if r < med else 0
                if decay_cnt >= d_consec:
                    exit_reason = "funding_decay"
                    break
            j += 1
        if exit_reason == "max_hold" and held < F1_MAX_HOLD_SETTLEMENTS:
            exit_reason = "data_end"   # cut by the end of the series
        episodes.append({
            "entry_i": int(i),
            "entry_ts": int(ts[i]),
            "exit_ts": int(contribs[-1][0]),
            "held": int(held),
            "net": float(accrued - cost_frac),
            "cost": float(cost_frac),
            "exit_reason": exit_reason,
            "resolved": exit_reason != "data_end",
            "contribs": contribs,
        })
        # Inherited resume convention (matches the audited
        # scripts/f1_replay_historical.py replay, l.85; kept IDENTICAL on
        # purpose — comment only, 2026-07-18): after a break-exit j is the
        # exit settlement, so scanning resumes one print after it; after a
        # max-hold exit the inner loop leaves j one past the last held
        # settlement, so i = j + 1 additionally skips ONE post-max-hold print.
        # Pinned by tests/test_screen_f1_percentile_selectivity.py::
        # test_no_overlapping_episodes (entry_i == prev_entry_i + held + 1).
        i = j + 1
    return episodes


def efficiency(episodes: list[dict]) -> float:
    """PRIMARY endpoint: after-cost net carry per settlement-deployed (frac)."""
    held = sum(e["held"] for e in episodes)
    if held <= 0:
        return float("nan")
    return sum(e["net"] for e in episodes) / held


# ── Aggregation helpers ──────────────────────────────────────────────────────
def arm_stats(episodes: list[dict]) -> dict:
    n = len(episodes)
    if n == 0:
        return {"n": 0}
    nets = np.array([e["net"] for e in episodes], dtype=float)
    return {
        "n": n,
        "total_net_bps": float(nets.sum() * 1e4),
        "mean_bps_per_episode": float(nets.mean() * 1e4),
        "wr_per_episode": float((nets > 0).mean()),
        "mean_held_settlements": float(np.mean([e["held"] for e in episodes])),
        "eff_bps_per_settlement": float(efficiency(episodes) * 1e4),
        "exit_reasons": {r: sum(1 for e in episodes if e["exit_reason"] == r)
                         for r in sorted({e["exit_reason"] for e in episodes})},
    }


def bootstrap_delta_ci_lb(var_eps: list[dict], inc_eps: list[dict],
                          *, n_boot: int = BOOT_N, seed: int = BOOT_SEED,
                          pctl: float = BONF_PCTL) -> float:
    """Episode-level bootstrap of the primary-endpoint delta (variant − incumbent).
    Returns the Bonferroni-adjusted lower percentile of the delta distribution."""
    if not var_eps or not inc_eps:
        return float("nan")
    rng = np.random.default_rng(seed)
    nv, ni = len(var_eps), len(inc_eps)
    deltas = np.empty(n_boot)
    for b in range(n_boot):
        sv = [var_eps[k] for k in rng.integers(0, nv, nv)]
        si = [inc_eps[k] for k in rng.integers(0, ni, ni)]
        deltas[b] = efficiency(sv) - efficiency(si)
    return float(np.percentile(deltas, pctl))


def calendar_fold_deltas(inc_eps: list[dict], var_eps: list[dict],
                         n_folds: int = N_FOLDS) -> dict:
    """Primary-endpoint delta per equal-calendar-time fold (by entry ts).
    A fold where either arm has zero episodes is fail-closed (sign None)."""
    all_entries = [e["entry_ts"] for e in inc_eps] + [e["entry_ts"] for e in var_eps]
    if not all_entries:
        return {"deltas_bps": [], "signs": [], "all_positive": False}
    t0, t1 = min(all_entries), max(all_entries) + 1
    edges = np.linspace(t0, t1, n_folds + 1)
    deltas, signs = [], []
    for k in range(n_folds):
        lo, hi = edges[k], edges[k + 1]
        fi = [e for e in inc_eps if lo <= e["entry_ts"] < hi]
        fv = [e for e in var_eps if lo <= e["entry_ts"] < hi]
        if not fi or not fv:
            deltas.append(None)
            signs.append(None)
            continue
        d = efficiency(fv) - efficiency(fi)
        deltas.append(float(d * 1e4))
        signs.append(int(np.sign(d)))
    return {"deltas_bps": deltas, "signs": signs,
            "all_positive": bool(signs and all(s == 1 for s in signs))}


def grid_matrix(arms: list[list[dict]], all_ts: list[int]) -> np.ndarray:
    """(T, n_arms) per-settlement portfolio return matrix on the common grid.
    Each episode contributes funding at every held settlement and its
    round-trip cost at the entry settlement."""
    idx = {t: k for k, t in enumerate(all_ts)}
    out = np.zeros((len(all_ts), len(arms)), dtype=float)
    for a, eps in enumerate(arms):
        for ep in eps:
            first = True
            for t, r in ep["contribs"]:
                out[idx[t], a] += (r - ep["cost"]) if first else r
                first = False
    return out


# ── Screen orchestration ─────────────────────────────────────────────────────
def run_screen() -> dict:
    now = time.time()
    series: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    missing, stale, coverage = [], [], {}
    for venue in VENUES:
        for base in BASES:
            fser = load_funding_history(base, venue)
            if len(fser) == 0:
                missing.append(f"{venue}_{base}")
                continue
            ts = np.asarray(fser.index, dtype="int64")
            rates = np.asarray(fser.values, dtype=float)
            coverage[f"{venue}_{base}"] = {"n": int(len(rates)),
                                           "first": int(ts[0]), "last": int(ts[-1])}
            if now - float(ts[-1]) > STALE_TOL_SEC:
                stale.append(f"{venue}_{base}")
                continue
            series[(venue, base)] = (ts, rates)

    out: dict = {
        "candidate": "15b — funding-percentile persistence selectivity for F1",
        "hypothesis": ("percentile+persistence entry selection (+decay exit) beats "
                       "the incumbent Rev-5 f1_entry_gate after costs on net carry "
                       "per settlement-deployed, without gutting total harvest"),
        "prereg": "_workspace/strategy_pipeline/15b_screen_f1_selectivity.md",
        "universe": {"bases": list(BASES), "venues": list(VENUES)},
        "cost_frac": COST_FRAC,
        "n_trials_dsr": N_TRIALS,
        "data": {"missing_series": missing, "stale_series": stale,
                 "n_series_loaded": len(series)},
    }
    if missing or stale:
        out.update({
            "verdict": "INSUFFICIENT_DATA",
            "reason": (f"missing={missing} stale={stale}; run: {BACKFILL_CMD}"),
            "gates": {},
        })
        return out

    # ── Simulate all arms on every venue-symbol series ──────────────────────
    arms_cfg = [("incumbent", None), ("V1", V1_CFG), ("V2", V2_CFG)]
    episodes: dict[str, list[dict]] = {name: [] for name, _ in arms_cfg}
    stress: dict[str, list[dict]] = {name: [] for name, _ in arms_cfg}
    for (venue, base), (ts, rates) in sorted(series.items()):
        for name, cfg in arms_cfg:
            fn = incumbent_entry_ok if cfg is None else make_variant_entry_fn(cfg)
            for mult, sink in ((1.0, episodes), (2.0, stress)):
                eps = simulate(ts, rates, COST_FRAC[venue] * mult, fn,
                               decay_cfg=cfg)
                for e in eps:
                    e["venue"], e["base"] = venue, base
                sink[name].extend(eps)

    resolved = {k: sorted([e for e in v if e["resolved"]],
                          key=lambda e: (e["entry_ts"], e["venue"], e["base"]))
                for k, v in episodes.items()}
    unresolved_counts = {k: len(v) - len(resolved[k]) for k, v in episodes.items()}
    inc, v1, v2 = resolved["incumbent"], resolved["V1"], resolved["V2"]

    out["n"] = {k: len(v) for k, v in resolved.items()}
    out["unresolved_data_end_episodes"] = unresolved_counts
    out["after_cost_metrics"] = {k: arm_stats(v) for k, v in resolved.items()}
    out["after_cost_metrics_2x_cost_stress"] = {
        k: arm_stats([e for e in v if e["resolved"]]) for k, v in stress.items()}
    out["per_venue"] = {
        venue: {k: arm_stats([e for e in resolved[k] if e["venue"] == venue])
                for k in resolved}
        for venue in VENUES}

    # ── Sample floors (pre-registered short circuits) ────────────────────────
    if len(inc) < MIN_INCUMBENT_EPISODES:
        out.update({
            "verdict": "INSUFFICIENT_DATA",
            "reason": (f"incumbent episodes {len(inc)} < floor "
                       f"{MIN_INCUMBENT_EPISODES}: the window cannot express the "
                       f"null. Funding data is complete; more history would need "
                       f"a longer archive, not a re-run."),
            "gates": {},
        })
        return out
    if len(v1) < MIN_VARIANT_EPISODES:
        out.update({
            "verdict": "NO_GO",
            "reason": (f"V1 episodes {len(v1)} < floor {MIN_VARIANT_EPISODES} while "
                       f"the incumbent has {len(inc)}: selectivity too extreme to "
                       f"validate (pre-registered fail-closed NO_GO)."),
            "gates": {"v1_min_episodes": False},
        })
        return out

    # ── Gate battery on V1 + delta analysis ─────────────────────────────────
    v1_nets = np.array([e["net"] for e in v1], dtype=float)
    entries = np.array([e["entry_ts"] for e in v1], dtype="int64")
    exits = np.array([e["exit_ts"] for e in v1], dtype="int64")
    all_ts = sorted({int(t) for (ts, _r) in series.values() for t in ts})
    mat = grid_matrix([inc, v1, v2], all_ts)

    dsr = _dsr_prob(v1_nets, n_trials=N_TRIALS)
    pbo_v = _pbo_across_horizons(mat)
    oos_wr = oos_wr_5fold(v1_nets, entries, exits)
    mc = _monte_carlo(v1_nets)

    eff_inc, eff_v1 = efficiency(inc), efficiency(v1)
    delta_bps = (eff_v1 - eff_inc) * 1e4
    ci_lb = bootstrap_delta_ci_lb(v1, inc)
    folds = calendar_fold_deltas(inc, v1)
    venue_deltas = {}
    for venue in VENUES:
        iv = [e for e in inc if e["venue"] == venue]
        vv = [e for e in v1 if e["venue"] == venue]
        venue_deltas[venue] = {
            "n_v1": len(vv),
            "delta_bps": (float((efficiency(vv) - efficiency(iv)) * 1e4)
                          if vv and iv else None),
        }
    venue_ok = all(
        d["n_v1"] >= MIN_VENUE_EPISODES and d["delta_bps"] is not None
        and d["delta_bps"] > 0
        for d in venue_deltas.values())

    tot_inc = sum(e["net"] for e in inc)
    tot_v1 = sum(e["net"] for e in v1)
    harvest_ok = bool(tot_v1 > 0 and tot_v1 >= HARVEST_GUARD_FRAC * tot_inc)

    out["delta_analysis"] = {
        "primary_endpoint": "after-cost net carry bps per settlement-deployed",
        "incumbent_eff_bps": float(eff_inc * 1e4),
        "v1_eff_bps": float(eff_v1 * 1e4),
        "pooled_delta_bps": float(delta_bps),
        "bootstrap_delta_ci_lb_bps": (float(ci_lb * 1e4)
                                      if np.isfinite(ci_lb) else None),
        "calendar_folds": folds,
        "venue_deltas": venue_deltas,
        "total_net_bps": {"incumbent": float(tot_inc * 1e4),
                          "V1": float(tot_v1 * 1e4)},
        "v2_robustness": {
            "eff_bps": float(efficiency(v2) * 1e4) if v2 else None,
            "pooled_delta_bps": (float((efficiency(v2) - eff_inc) * 1e4)
                                 if v2 else None),
        },
    }
    checks = {
        "incumbent_min_episodes": len(inc) >= MIN_INCUMBENT_EPISODES,
        "v1_min_episodes": len(v1) >= MIN_VARIANT_EPISODES,
        "dsr>=0.10": bool(np.isfinite(dsr) and dsr >= MIN_DSR),
        "pbo<=0.5": bool(np.isfinite(pbo_v) and pbo_v <= MAX_PBO),
        "oos_wr>=0.55": bool(np.isfinite(oos_wr) and oos_wr >= MIN_OOS_WR),
        "mc_P>0>=0.95": bool(mc.get("p_total_positive") is not None
                             and mc["p_total_positive"] >= 0.95),
        "mc_maxDD_p95<=0.25": bool(mc.get("max_drawdown_p95") is not None
                                   and mc["max_drawdown_p95"] <= 0.25),
        "mc_min_trades": bool(mc["n_trades"] >= 30),
        "delta>0_pooled": bool(np.isfinite(delta_bps) and delta_bps > 0),
        "delta_ci_lb>0_bonferroni_m2": bool(np.isfinite(ci_lb) and ci_lb > 0),
        "fold_sign_stable_all5": bool(folds["all_positive"]),
        "venue_sign_stable": bool(venue_ok),
        "harvest_guard": harvest_ok,
    }
    out["gates"] = {
        "checks": checks,
        "dsr_prob": float(dsr) if np.isfinite(dsr) else None,
        "pbo": float(pbo_v) if np.isfinite(pbo_v) else None,
        "oos_wr_5fold": float(oos_wr) if np.isfinite(oos_wr) else None,
        "monte_carlo_v1_episodes": mc,
    }
    verdict = "GO" if all(checks.values()) else "NO_GO"
    out["verdict"] = verdict
    out["reason"] = ("All pre-registered gates pass on V1."
                     if verdict == "GO" else
                     "Failed: " + ", ".join(k for k, v in checks.items() if not v))
    return out


if __name__ == "__main__":
    result = run_screen()
    dest = os.path.join("_workspace", "strategy_pipeline",
                        "15b_screen_f1_selectivity.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))
    print(f"[15b] verdict json -> {dest}")
