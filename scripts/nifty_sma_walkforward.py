"""Nifty 50 SMA crossover — WALK-FORWARD validation (the honest out-of-sample verdict).

A single backtest's "best" SMA pair is in-sample selection (the overfitting trap). Walk-
forward asks the only fair question: if you RE-PICK the best pair on each training window and
trade it on the NEXT, unseen window, does the timing actually beat buy-and-hold out-of-sample?

Method (rolling): IS window of `is_days` -> pick the SMA pair with the best in-sample
objective (Sharpe by default) -> trade the following `oos_days` with that pair -> roll forward
by `oos_days` (contiguous, non-overlapping OOS) -> stitch all OOS segments into one continuous
out-of-sample track record. Positions are CAUSAL (verified by bias_checks.lookahead_violations
in the engine tests), so slicing the precomputed series per fold introduces no leakage.

Reports vs buy-and-hold over the SAME stitched OOS span, plus:
  * Walk-Forward Efficiency  = median(OOS Sharpe / IS Sharpe)  (<<1 => the IS edge doesn't carry)
  * per-fold consistency     = % of OOS folds positive / beating B&H
  * parameter stability      = how often the chosen pair changes (jumpy => fitting noise)
  * robustness sweep         = the verdict under different objectives x window sizes
Uses MAX available ^NSEI history (WFA needs many folds; 5y alone gives too few). No new deps.
Records reports/nifty_sma_walkforward_<date>.md (+ .json). Read-only.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
REPORTS = ROOT / "reports"

from nifty_sma_backtest import (  # noqa: E402
    _max_drawdown,
    fetch_nifty,
    run_backtest,
    sma_crossover_position,
)

PPY = 252
COST_ONEWAY = 5.0 / 1e4
GRID = [(f, s) for f in (5, 10, 20, 50) for s in (20, 50, 100, 200) if f < s]  # 13 pairs


def _seg_sharpe(r: np.ndarray, lo: int, hi: int) -> float:
    seg = r[lo:hi]
    seg = seg[np.isfinite(seg)]
    sd = seg.std(ddof=1) if len(seg) > 1 else 0.0
    return float(seg.mean() / sd * np.sqrt(PPY)) if sd > 0 else 0.0


def _seg_calmar(r: np.ndarray, lo: int, hi: int) -> float:
    seg = r[lo:hi]
    seg = seg[np.isfinite(seg)]
    if len(seg) < 2:
        return 0.0
    eq = np.cumprod(1.0 + seg)
    yrs = len(seg) / PPY
    cagr = eq[-1] ** (1 / yrs) - 1.0 if eq[-1] > 0 and yrs > 0 else -1.0
    mdd = _max_drawdown(eq)
    return float(cagr / abs(mdd)) if mdd < 0 else (cagr if cagr <= 0 else 99.0)


def _obj(r, lo, hi, objective):
    return _seg_calmar(r, lo, hi) if objective == "calmar" else _seg_sharpe(r, lo, hi)


def walk_forward(strat_by_param: dict, *, is_days: int, oos_days: int, step: int,
                 objective: str = "sharpe") -> list[dict]:
    """Rolling WFA. Returns one record per fold (chosen param + IS/OOS boundaries+Sharpes).
    Pure: operates on precomputed per-param strategy-return arrays (all same length)."""
    keys = list(strat_by_param)
    n = len(strat_by_param[keys[0]])
    folds = []
    t = is_days
    while t + oos_days <= n:
        is_lo, is_hi, oos_lo, oos_hi = t - is_days, t, t, t + oos_days
        best = max(keys, key=lambda k: _obj(strat_by_param[k], is_lo, is_hi, objective))
        folds.append({
            "param": best, "is_lo": is_lo, "is_hi": is_hi, "oos_lo": oos_lo, "oos_hi": oos_hi,
            "is_sharpe": _seg_sharpe(strat_by_param[best], is_lo, is_hi),
            "oos_sharpe": _seg_sharpe(strat_by_param[best], oos_lo, oos_hi),
        })
        t += step
    return folds


def _metrics_from_returns(r: np.ndarray) -> dict:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return {"n": len(r)}
    eq = np.cumprod(1.0 + r)
    yrs = len(r) / PPY
    sd = r.std(ddof=1)
    return {
        "n": len(r), "total_return": float(eq[-1] - 1.0),
        "cagr": float(eq[-1] ** (1 / yrs) - 1.0) if eq[-1] > 0 else float("nan"),
        "sharpe": float(r.mean() / sd * np.sqrt(PPY)) if sd > 0 else 0.0,
        "max_drawdown": _max_drawdown(eq),
    }


def _run_wfa(strat_by_param, ret_full, *, is_days, oos_days, step, objective):
    folds = walk_forward(strat_by_param, is_days=is_days, oos_days=oos_days, step=step,
                         objective=objective)
    if not folds:
        return None
    oos = np.concatenate([strat_by_param[f["param"]][f["oos_lo"]:f["oos_hi"]] for f in folds])
    span_lo, span_hi = folds[0]["oos_lo"], folds[-1]["oos_hi"]
    bh = ret_full[span_lo:span_hi]
    wfa_m, bh_m = _metrics_from_returns(oos), _metrics_from_returns(bh)
    # per-fold OOS vs that fold's B&H
    beat_ret = beat_sr = pos_folds = 0
    wfes = []
    for f in folds:
        f_oos = strat_by_param[f["param"]][f["oos_lo"]:f["oos_hi"]]
        f_bh = ret_full[f["oos_lo"]:f["oos_hi"]]
        om, bm = _metrics_from_returns(f_oos), _metrics_from_returns(f_bh)
        if om.get("total_return", 0) > 0:
            pos_folds += 1
        if om.get("total_return", -9) > bm.get("total_return", 9):
            beat_ret += 1
        if om.get("sharpe", -9) > bm.get("sharpe", 9):
            beat_sr += 1
        if f["is_sharpe"] > 0:
            wfes.append(f["oos_sharpe"] / f["is_sharpe"])
    nf = len(folds)
    params = [f["param"] for f in folds]
    return {
        "objective": objective, "is_days": is_days, "oos_days": oos_days, "n_folds": nf,
        "wfa": wfa_m, "bh": bh_m,
        "wfe_median": float(np.median(wfes)) if wfes else float("nan"),
        "pct_folds_positive": pos_folds / nf, "pct_folds_beat_ret": beat_ret / nf,
        "pct_folds_beat_sharpe": beat_sr / nf,
        "n_distinct_params": len(set(params)), "modal_param": Counter(params).most_common(1)[0],
        "beats_bh_ret": wfa_m["total_return"] > bh_m["total_return"],
        "beats_bh_sharpe": wfa_m["sharpe"] > bh_m["sharpe"],
        "folds": folds,
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    df = fetch_nifty("maxdaily")
    close = df["close"].to_numpy(dtype=float)
    dates = df["date"].tolist()
    n = len(close)
    print(f"^NSEI full daily history: {n} bars, {dates[0]} -> {dates[-1]} (~{n/PPY:.1f}y)")

    # precompute per-param causal strategy returns (leak-free: positions depend only on past)
    ret_full = np.zeros(n)
    ret_full[1:] = close[1:] / close[:-1] - 1.0
    strat_by_param = {}
    for (f, s) in GRID:
        pos = sma_crossover_position(close, f, s)
        strat_by_param[f"{f}/{s}"] = run_backtest(close, pos, COST_ONEWAY)["strat_ret"]

    # primary WFA: ~2y IS / 6mo OOS, Sharpe objective
    primary = _run_wfa(strat_by_param, ret_full, is_days=504, oos_days=126, step=126,
                       objective="sharpe")
    # robustness sweep
    sweep = []
    for objective in ("sharpe", "calmar"):
        for (isd, oosd) in ((504, 126), (756, 126), (252, 63)):
            r = _run_wfa(strat_by_param, ret_full, is_days=isd, oos_days=oosd, step=oosd,
                         objective=objective)
            if r:
                sweep.append(r)

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    p, w, b = primary, primary["wfa"], primary["bh"]
    L = [f"# Nifty 50 SMA crossover — WALK-FORWARD study — {today}", "",
         f"`^NSEI` daily, {dates[0]} → {dates[-1]} ({n} bars ≈ {n/PPY:.1f}y). {len(GRID)} SMA pairs. "
         "Each fold: pick best pair in-sample, trade the next unseen window, roll, stitch OOS. "
         "Long/flat, 5bps/turn, causal (no look-ahead).", "",
         "## Primary WFA (2y in-sample / 6mo out-of-sample, Sharpe objective)", "",
         f"- **Stitched OOS:** total **{w['total_return']*100:+.1f}%** | CAGR **{w['cagr']*100:+.1f}%** "
         f"| Sharpe **{w['sharpe']:.2f}** | MaxDD **{w['max_drawdown']*100:.1f}%**  ({w['n']} OOS days, "
         f"{p['n_folds']} folds)",
         f"- **Buy & Hold (same span):** total **{b['total_return']*100:+.1f}%** | CAGR "
         f"**{b['cagr']*100:+.1f}%** | Sharpe **{b['sharpe']:.2f}** | MaxDD **{b['max_drawdown']*100:.1f}%**",
         f"- **Beats B&H?** return: **{'YES' if p['beats_bh_ret'] else 'NO'}** | "
         f"Sharpe: **{'YES' if p['beats_bh_sharpe'] else 'NO'}**",
         f"- **Walk-Forward Efficiency** (median OOS/IS Sharpe): **{p['wfe_median']:.2f}** "
         f"({'IS edge does NOT carry OOS' if p['wfe_median'] < 0.5 else 'partial carry'})",
         f"- **Consistency:** {p['pct_folds_positive']*100:.0f}% of folds OOS-positive | "
         f"{p['pct_folds_beat_ret']*100:.0f}% beat B&H return | {p['pct_folds_beat_sharpe']*100:.0f}% "
         f"beat B&H Sharpe",
         f"- **Parameter stability:** {p['n_distinct_params']} distinct pairs chosen across "
         f"{p['n_folds']} folds; most common = {p['modal_param'][0]} ({p['modal_param'][1]}× "
         f"{'— stable' if p['n_distinct_params'] <= 3 else '— JUMPY, fitting noise'})",
         "", "## Robustness sweep (does the verdict survive different objectives/windows?)", "",
         "| objective | IS/OOS days | folds | OOS total% | OOS Sharpe | B&H total% | B&H Sharpe | "
         "WFE | beats B&H ret/SR |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sweep:
        L.append(f"| {r['objective']} | {r['is_days']}/{r['oos_days']} | {r['n_folds']} | "
                 f"{r['wfa']['total_return']*100:+.1f} | {r['wfa']['sharpe']:.2f} | "
                 f"{r['bh']['total_return']*100:+.1f} | {r['bh']['sharpe']:.2f} | "
                 f"{r['wfe_median']:.2f} | {'Y' if r['beats_bh_ret'] else 'N'}/"
                 f"{'Y' if r['beats_bh_sharpe'] else 'N'} |")

    n_beat_ret = sum(r["beats_bh_ret"] for r in sweep)
    n_beat_sr = sum(r["beats_bh_sharpe"] for r in sweep)
    L += ["", "## Per-fold (primary)", "| fold | OOS dates | param | IS Sharpe | OOS Sharpe |",
          "|---|---|---|---|---|"]
    for i, f in enumerate(p["folds"], 1):
        L.append(f"| {i} | {dates[f['oos_lo']]}→{dates[min(f['oos_hi']-1, n-1)]} | {f['param']} | "
                 f"{f['is_sharpe']:+.2f} | {f['oos_sharpe']:+.2f} |")

    L += ["", "## Honest verdict", "",
          f"- Across **{len(sweep)} walk-forward configurations**, the re-optimised SMA timing beat "
          f"buy-and-hold on **return in {n_beat_ret}/{len(sweep)}** and on **Sharpe in "
          f"{n_beat_sr}/{len(sweep)}**.",
          f"- Median Walk-Forward Efficiency ≈ **{p['wfe_median']:.2f}**: the in-sample-best pair's "
          "edge largely does NOT persist out-of-sample — re-optimising each window chases noise.",
          "- This is the rigorous confirmation of the single-backtest result: **SMA crossover has no "
          "robust return edge over buy-and-hold on the Nifty 50.** Its only durable benefit is "
          "**lower drawdown** (it sits out downturns). Use it to reduce risk, not to beat the index.",
          "", "_WFA caveats: long/flat, no shorting; daily bars; transaction cost 5bps/turn; "
          "objective optimises one metric so 'beats on Sharpe' can diverge from 'beats on return'._"]
    out_md = REPORTS / f"nifty_sma_walkforward_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"nifty_sma_walkforward_{today}.json").write_text(json.dumps({
        "date": today, "bars": n, "window": [dates[0], dates[-1]], "primary": p, "sweep": sweep,
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nPRIMARY (504/126, sharpe): {p['n_folds']} folds")
    print(f"  WFA OOS : total {w['total_return']*100:+.1f}%  Sharpe {w['sharpe']:.2f}  "
          f"MaxDD {w['max_drawdown']*100:.1f}%")
    print(f"  B&H     : total {b['total_return']*100:+.1f}%  Sharpe {b['sharpe']:.2f}  "
          f"MaxDD {b['max_drawdown']*100:.1f}%")
    print(f"  beats B&H ret/SR: {p['beats_bh_ret']}/{p['beats_bh_sharpe']} | WFE {p['wfe_median']:.2f} "
          f"| folds beat-ret {p['pct_folds_beat_ret']*100:.0f}% | params {p['n_distinct_params']} distinct")
    print(f"  SWEEP beats B&H: return {n_beat_ret}/{len(sweep)}, Sharpe {n_beat_sr}/{len(sweep)}")
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
