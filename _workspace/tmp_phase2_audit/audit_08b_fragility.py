"""Throwaway audit probe for 08b GO verdict (honesty-auditor, 2026-07-11).

1. Reproduces the W1/W2 accepted samples with the SAME frozen loop as
   research/screen_preunlock_short.py (imports its functions directly).
2. Recomputes gates on adversarial subsets: no-SUI, no-GUN, one-event-per-symbol,
   drop-top-3-winners, 2023-only vs 2025+ split.
3. DSR under harsher multiplicity (n_trials = 3 / 10 / 20).
4. End-to-end spot-check of ARB 2024-03-16 (entry/exit closes + funding sum).
Not pipeline code. Writes nothing; prints JSON.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.join(os.path.dirname(__file__), "..", ".."))

from research.screen_listing_short import (  # noqa: E402
    _dsr_prob,
    _monte_carlo,
    _oos_wr_walk_forward,
    apply_concurrency_cap,
    funding_sum_in_window,
    load_funding_history,
    short_net_return,
    window_funding_covered,
)
from research.screen_preunlock_short import (  # noqa: E402
    MAX_CONCURRENT,
    STAKE_FRAC,
    VENUE_ORDER,
    auc_score,
    first_bar_at_or_after,
    floor_day,
    funding_print_nearest,
    load_calendar,
    nearest_bar,
    rank_auc,
    window_bounds,
    RATIO_MIN,
    WIN_LO,
    WIN_HI,
    OHLCV_DIR,
)

import pandas as pd  # noqa: E402


def build_accepted(window: str):
    """Replicate the screen's per-window accepted sample exactly."""
    import config

    slip = (float(config.SLIPPAGE["pct_open"]) + float(config.SLIPPAGE["pct_close"])) / 2.0
    fees = {v: float(config.FEE[f"{v}_futures_taker"]) for v in VENUE_ORDER}
    docs = load_calendar()
    qualifying, price_dfs = [], {}
    for doc in docs:
        sym = doc["symbol"]
        path = os.path.join(OHLCV_DIR, f"{sym}-USDT_1h.parquet")
        if not os.path.exists(path):
            continue
        for e in doc.get("events") or []:
            if not e.get("tokens") or e.get("ratio") is None:
                continue
            if e["ratio"] < RATIO_MIN or not (WIN_LO <= e["ts"] < WIN_HI):
                continue
            if sym not in price_dfs:
                price_dfs[sym] = pd.read_parquet(path, columns=["ts", "close"])
            qualifying.append((sym, int(e["ts"]), float(e["ratio"])))
    fser_cache = {}

    def fser(sym, venue):
        key = (venue, sym)
        if key not in fser_cache:
            fser_cache[key] = load_funding_history(sym, venue)
        return fser_cache[key]

    cand = []
    for sym, T, ratio in qualifying:
        df = price_dfs[sym]
        ent_t, ex_t = window_bounds(T, window)
        if window == "W3":
            p0, a0 = nearest_bar(df, ent_t)
            p1, a1 = first_bar_at_or_after(df, ex_t)
        else:
            p0, a0 = first_bar_at_or_after(df, ent_t)
            p1, a1 = nearest_bar(df, ex_t)
        if not (p0 and p1 and p0 > 0) or a1 <= a0:
            continue
        venue = next((v for v in VENUE_ORDER
                      if window_funding_covered(fser(sym, v), a0, a1)), None)
        if venue is None:
            continue
        s = fser(sym, venue)
        fsum = funding_sum_in_window(s, a0, a1)
        net = short_net_return(p0, p1, fsum, fees[venue], slip)
        cand.append({"sym": sym, "T": T, "ratio": ratio, "entry_ts": a0, "exit_ts": a1,
                     "net": net, "fsum": fsum,
                     "score": auc_score(ratio, funding_print_nearest(s, a0)),
                     "p0": p0, "p1": p1, "venue": venue})
    ee = [(i, c["entry_ts"], c["exit_ts"]) for i, c in enumerate(cand)]
    acc_idx, capped_idx, _peak = apply_concurrency_cap(ee, MAX_CONCURRENT)
    return [cand[i] for i in acc_idx], [cand[i] for i in capped_idx]


def stats(rows, label):
    if len(rows) == 0:
        return {"label": label, "n": 0}
    rets = np.array([r["net"] for r in rows], dtype=float)
    acct = STAKE_FRAC * rets
    out = {
        "label": label, "n": int(rets.size),
        "mean_net": round(float(np.mean(rets)), 4),
        "win_rate": round(float(np.mean(rets > 0)), 3),
        "dsr_n3": round(_dsr_prob(acct, n_trials=3), 4) if rets.size >= 4 else None,
        "dsr_n10": round(_dsr_prob(acct, n_trials=10), 4) if rets.size >= 4 else None,
        "dsr_n20": round(_dsr_prob(acct, n_trials=20), 4) if rets.size >= 4 else None,
    }
    if rets.size >= 8:
        out["oos_wr"] = round(_oos_wr_walk_forward(
            acct, [(r["entry_ts"], r["exit_ts"]) for r in rows]), 3)
    if rets.size >= 4:
        mc = _monte_carlo(acct)
        out["mc_p_pos"] = round(mc["p_total_positive"], 3)
        out["mc_dd_p95"] = round(mc["max_drawdown_p95"], 4)
    out["auc"] = (round(rank_auc([r["score"] for r in rows], rets > 0), 3)
                  if rets.size >= 4 else None)
    return out


def main():
    report = {}
    for w in ("W1", "W2"):
        acc, capped = build_accepted(w)
        rep = {"reproduced_n_accepted": len(acc), "n_capped": len(capped),
               "capped_syms": [(c["sym"], c["T"]) for c in capped]}
        rep["full"] = stats(acc, f"{w} full")
        rep["no_SUI"] = stats([r for r in acc if r["sym"] != "SUI"], f"{w} no-SUI")
        rep["no_GUN"] = stats([r for r in acc if r["sym"] != "GUN"], f"{w} no-GUN")
        rep["no_SUI_no_GUN"] = stats(
            [r for r in acc if r["sym"] not in ("SUI", "GUN")], f"{w} no-SUI-GUN")
        # one event per symbol (first chronologically) — kills serial pseudo-replication
        seen, uniq = set(), []
        for r in sorted(acc, key=lambda r: r["entry_ts"]):
            if r["sym"] not in seen:
                seen.add(r["sym"])
                uniq.append(r)
        rep["one_per_symbol"] = stats(uniq, f"{w} one-per-symbol")
        # drop top-3 winners
        drop3 = sorted(acc, key=lambda r: r["net"], reverse=True)[3:]
        rep["drop_top3_winners"] = stats(drop3, f"{w} drop-top3")
        # regime split
        y23 = [r for r in acc if r["T"] < 1704067200]      # < 2024-01-01
        y25p = [r for r in acc if r["T"] >= 1735689600]    # >= 2025-01-01
        rep["events_2023"] = stats(y23, f"{w} 2023-only")
        rep["events_2025plus"] = stats(y25p, f"{w} 2025+")
        # symbol-clustered mean (equal weight per symbol)
        by_sym = {}
        for r in acc:
            by_sym.setdefault(r["sym"], []).append(r["net"])
        sym_means = np.array([np.mean(v) for v in by_sym.values()])
        rep["symbol_clustered"] = {
            "n_symbols": len(sym_means),
            "mean_of_symbol_means": round(float(np.mean(sym_means)), 4),
            "symbols_positive": int((sym_means > 0).sum()),
            "symbols_negative": int((sym_means <= 0).sum()),
        }
        report[w] = rep

    # spot-check ARB 2024-03-16 W1 end-to-end
    acc, _ = build_accepted("W1")
    arb = [r for r in acc if r["sym"] == "ARB"]
    if arb:
        r = arb[0]
        report["spotcheck_ARB_W1"] = {
            "entry_ts": r["entry_ts"], "exit_ts": r["exit_ts"],
            "p0": r["p0"], "p1": r["p1"],
            "gross_short": round((r["p0"] - r["p1"]) / r["p0"], 4),
            "funding_sum": round(r["fsum"], 4), "net": round(r["net"], 4),
            "venue": r["venue"],
            "hold_days": round((r["exit_ts"] - r["entry_ts"]) / 86400, 1),
        }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
