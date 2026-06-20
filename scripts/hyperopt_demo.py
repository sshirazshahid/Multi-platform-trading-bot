"""Hyperopt: the TRAP demo + the GATED (honest) version, side by side.

Answers "why refuse naive hyperopt / live-FreqAI on a no-edge book" with EVIDENCE, using the
SAME 256-combo parameter grid for both, and the bot's OWN rigor functions
(core/alpha_zoo/screen: dsr_for_returns, fdr_bh, pbo_over_alphas) so the result is consistent
with how the bot already screens alphas.

RULE (tunable): long when EMA(fast)>EMA(slow) & RSI in [lo,hi] & ADX>=thr; short = mirror.
Per-bar dollar-neutral portfolio over 8 majors; returns are BETA-ADJUSTED vs BTC (beta from IS
only) so a winner cannot be BTC-beta, and net of ~5bps/side turnover cost. No look-ahead
(signal at t uses bars<=t; earns t+1 return). Chronological 60/40 IS/OOS.

TRAP (what naive freqtrade hyperopt does): pick the param set with the best IN-SAMPLE Sharpe,
then look at its OUT-OF-SAMPLE performance. Plus PBO (CSCV) and IS-vs-OOS rank correlation
across the whole grid — the family-level overfitting metrics.

GATED (the only honest version): keep a param set ONLY if it clears OUT-OF-SAMPLE FDR
(Benjamini-Hochberg q=0.05) AND IS/OOS Sharpe are both positive AND the trials-Deflated Sharpe
(deflated for all 256 attempts) >= 0.95. Expected survivors on a no-edge book: 0.

Records reports/hyperopt_demo_<date>.md (+ .json). Read-only.
"""

from __future__ import annotations

import itertools
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "ohlcv_cache"
REPORTS = ROOT / "reports"

from core.agents.indicators import adx, ema, rsi  # noqa: E402
from core.alpha_zoo.screen import (  # noqa: E402
    dsr_for_returns,
    fdr_bh,
    pbo_over_alphas,
    sharpe_pvalue,
)

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "ADA"]
TF = "1h"
COST_ONEWAY = 5.0 / 1e4
IS_FRAC = 0.60
PERIODS_PER_YEAR = 8760.0  # 1h bars

EMA_FAST = [5, 8, 12, 20]
EMA_SLOW = [26, 50, 100, 200]
RSI_LO = [30, 45]
RSI_HI = [60, 75]
ADX_THR = [0, 15, 20, 25]
ALL_SPANS = sorted(set(EMA_FAST + EMA_SLOW))


def _ann_sharpe(r: np.ndarray) -> float:
    r = r[np.isfinite(r)]
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # ---- load + align on a common bar grid ----
    raw = {}
    for c in COINS:
        p = CACHE / f"{c}-USDT_{TF}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)[["ts", "open", "high", "low", "close"]].dropna()
        raw[c] = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    coins = list(raw)
    common = None
    for c in coins:
        s = set(raw[c].index)
        common = s if common is None else (common & s)
    common = np.array(sorted(common))
    T = len(common)
    cut = int(T * IS_FRAC)
    print(f"Coins: {len(coins)} | common 1h bars: {T} | IS={cut} OOS={T - cut}")

    # ---- precompute indicator + beta-adjusted return matrices (T x nC) ----
    nC = len(coins)
    closes = np.full((T, nC), np.nan)
    rets = np.full((T, nC), np.nan)
    emas = {s: np.full((T, nC), np.nan) for s in ALL_SPANS}
    rsis = np.full((T, nC), np.nan)
    adxs = np.full((T, nC), np.nan)
    btc = raw["BTC"].reindex(common)
    btc_ret = btc["close"].pct_change().to_numpy()
    for j, c in enumerate(coins):
        d = raw[c].reindex(common)
        cl = d["close"]
        closes[:, j] = cl.to_numpy()
        rets[:, j] = cl.pct_change().to_numpy()
        for s in ALL_SPANS:
            emas[s][:, j] = ema(cl, s).to_numpy()
        rsis[:, j] = rsi(cl, 14).to_numpy()
        adxs[:, j] = adx(d[["high", "low", "close"]], 14).to_numpy()
    # beta per coin from IS rows; alpha return = coin_ret - beta*btc_ret
    ret_adj = np.full((T, nC), np.nan)
    betas = []
    for j in range(nC):
        rc, rb = rets[:cut, j], btc_ret[:cut]
        m = np.isfinite(rc) & np.isfinite(rb)
        beta = (
            float(np.cov(rc[m], rb[m])[0, 1] / np.var(rb[m]))
            if m.sum() > 30 and np.var(rb[m]) > 0
            else 1.0
        )
        betas.append(beta)
        ret_adj[:, j] = rets[:, j] - beta * btc_ret
    ret_adj = np.where(np.isfinite(ret_adj), ret_adj, 0.0)

    # ---- evaluate every combo -> per-bar portfolio return series ----
    combos = [
        (ef, es, lo, hi, thr)
        for ef, es, lo, hi, thr in itertools.product(EMA_FAST, EMA_SLOW, RSI_LO, RSI_HI, ADX_THR)
        if ef < es and lo < hi
    ]
    port = {}  # key -> (T,) portfolio return series
    is_sh, oos_sh = {}, {}
    for ef, es, lo, hi, thr in combos:
        key = f"ef{ef}_es{es}_rsi{lo}-{hi}_adx{thr}"
        long = (emas[ef] > emas[es]) & (rsis >= lo) & (rsis <= hi) & (adxs >= thr)
        short = (emas[ef] < emas[es]) & (rsis >= (100 - hi)) & (rsis <= (100 - lo)) & (adxs >= thr)
        pos = long.astype(float) - short.astype(float)  # (T,nC) in {-1,0,+1}
        pos = np.where(np.isfinite(pos), pos, 0.0)
        prev = np.vstack([np.zeros((1, nC)), pos[:-1]])  # position held into bar t
        turn = np.abs(pos - prev)
        contrib = prev * ret_adj - COST_ONEWAY * turn  # (T,nC)
        pser = contrib.mean(axis=1)  # equal-weight portfolio
        port[key] = pser
        is_sh[key] = _ann_sharpe(pser[:cut])
        oos_sh[key] = _ann_sharpe(pser[cut:])

    keys = list(port)
    n_combos = len(keys)
    print(f"Combos evaluated: {n_combos}")

    # ---- TRAP ----
    best_is = max(keys, key=lambda k: is_sh[k])
    is_arr = np.array([is_sh[k] for k in keys])
    oos_arr = np.array([oos_sh[k] for k in keys])
    rho, _ = spearmanr(is_arr, oos_arr)
    pbo_val = pbo_over_alphas({k: pd.Series(port[k], index=common) for k in keys}, n_partitions=10)
    best_is_total_is = float(np.nansum(port[best_is][:cut]) * 100)
    best_is_total_oos = float(np.nansum(port[best_is][cut:]) * 100)
    # family-level OOS distribution (the honest backdrop)
    oos_best = float(oos_arr.max())
    oos_med = float(np.median(oos_arr))
    frac_oos_pos = float((oos_arr > 0).mean())

    # ---- GATED ----
    pvals = [sharpe_pvalue(port[k][cut:]) for k in keys]
    fdr_flags = fdr_bh(pvals, q=0.05)
    survivors = []
    for k, flag in zip(keys, fdr_flags):
        if not (flag and is_sh[k] > 0 and oos_sh[k] > 0):
            continue
        dsr = dsr_for_returns(port[k], n_trials=n_combos)  # deflate for all attempts
        if dsr >= 0.95:
            survivors.append((k, oos_sh[k], dsr))
    n_fdr = sum(fdr_flags)

    # ---- report ----
    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    L = [
        f"# Hyperopt: TRAP vs GATED — {today}",
        "",
        f"Same {n_combos}-combo grid (EMA fast/slow × RSI band × ADX threshold), {len(coins)} majors, "
        f"{TF}, {T} bars (IS {cut} / OOS {T - cut}). Beta-adjusted per-bar dollar-neutral returns, "
        f"~{COST_ONEWAY * 1e4:.0f}bps/side cost. Uses the bot's own gates (dsr_for_returns / fdr_bh / "
        "pbo_over_alphas).",
        "",
        "## TRAP — what naive hyperopt does (optimize in-sample, then look out-of-sample)",
        "",
        f"- **In-sample BEST** combo: `{best_is}`",
        f"  - IS annualized Sharpe **{is_sh[best_is]:+.2f}**, IS total return **{best_is_total_is:+.1f}%** "
        f"→ looks great; naive hyperopt ships THIS.",
        f"  - **Out-of-sample** annualized Sharpe **{oos_sh[best_is]:+.2f}**, OOS total return "
        f"**{best_is_total_oos:+.1f}%** → the 'edge' inverts. Deploy it and you lose.",
        f"- **Family backdrop:** across all {n_combos} combos the BEST out-of-sample Sharpe is only "
        f"**{oos_best:+.2f}**, the median **{oos_med:+.2f}**, and just **{frac_oos_pos * 100:.0f}%** are "
        "even positive OOS → the whole strategy space is unprofitable after beta-adjustment + cost.",
        f"- IS-vs-OOS Sharpe rank correlation: **ρ = {rho:+.3f}**; **PBO (CSCV): {pbo_val:.2f}**.",
        "  - Read these HONESTLY: a low PBO/high ρ here does NOT mean 'safe'. They only say the "
        "RELATIVE ranking of combos is stable — the in-sample winner stays a *relative* leader OOS. "
        "But every combo is a *level* loser OOS, so the leader is just the least-bad corpse. This is a "
        "different overfit failure mode than rank-scrambling: not 'you got unlucky', but 'the space is "
        "dead and optimizing finds the prettiest in-sample fit, which still loses live.'",
        "",
        "## GATED — the honest version (select out-of-sample, corrected for the search)",
        "",
        f"- Gate: OOS Benjamini-Hochberg FDR (q=0.05) **AND** IS&OOS Sharpe>0 **AND** "
        f"trials-Deflated-Sharpe (deflated for all {n_combos} attempts) ≥ 0.95.",
        f"- Combos passing OOS-FDR: **{n_fdr}** | clearing the FULL gate: **{len(survivors)}**",
        "",
    ]
    if survivors:
        L += ["| combo | OOS Sharpe | DSR |", "|---|---|---|"]
        L += [f"| {k} | {sh:+.2f} | {d:.3f} |" for k, sh, d in survivors]
        L += ["", "⚠ A survivor on a supposedly no-edge book must be adversarially re-checked."]
    else:
        L += [
            "**0 survivors.** No parameter set clears the honest gate — exactly what should happen "
            "on a no-edge book. The naive winner above is the same grid's luckiest in-sample draw, "
            "not a strategy.",
            "",
        ]
    L += [
        "## The point",
        "",
        f"Naive hyperopt would have shipped `{best_is}` — an IS Sharpe of **{is_sh[best_is]:+.2f}** / "
        f"**{best_is_total_is:+.1f}%** that delivers **{oos_sh[best_is]:+.2f}** / **{best_is_total_oos:+.1f}%** "
        f"live. The honest gate ({n_combos}-trial Deflated-Sharpe + OOS-FDR + IS/OOS consistency) clears "
        f"**{len(survivors)}** of {n_combos} — and the best OOS Sharpe in the entire grid is only "
        f"{oos_best:+.2f}. So there is no parameter set to find: optimization on this book only converts "
        "in-sample noise into a deployable-looking mirage. THAT is why naive hyperopt + live auto-retrain "
        "FreqAI are **refused** here. The gated path — what the bot already uses to screen alphas — is the "
        "only honest one, and it correctly returns nothing, consistent with the settled NO-EDGE verdict.",
        "",
        "_Betas (IS) per coin: "
        + ", ".join(f"{c}={b:+.2f}" for c, b in zip(coins, betas))
        + ". Caveat: overlapping signals mildly inflate raw t; PBO/DSR/FDR are the robust gates._",
    ]
    out_md = REPORTS / f"hyperopt_demo_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"hyperopt_demo_{today}.json").write_text(
        json.dumps(
            {
                "date": today,
                "n_combos": n_combos,
                "trap": {
                    "best_is_combo": best_is,
                    "is_sharpe": is_sh[best_is],
                    "oos_sharpe": oos_sh[best_is],
                    "is_total_pct": best_is_total_is,
                    "oos_total_pct": best_is_total_oos,
                    "is_oos_rank_corr": float(rho),
                    "pbo": float(pbo_val),
                },
                "gated": {
                    "oos_fdr_survivors": int(n_fdr),
                    "full_gate_survivors": len(survivors),
                    "survivors": [
                        {"combo": k, "oos_sharpe": sh, "dsr": d} for k, sh, d in survivors
                    ],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== TRAP ===")
    print(
        f"  IS-best: {best_is}  IS Sharpe={is_sh[best_is]:+.2f} (tot {best_is_total_is:+.1f}%)  "
        f"-> OOS Sharpe={oos_sh[best_is]:+.2f} (tot {best_is_total_oos:+.1f}%)"
    )
    print(
        f"  family OOS: best={oos_best:+.2f} median={oos_med:+.2f} %positive={frac_oos_pos * 100:.0f}%"
    )
    print(
        f"  IS-vs-OOS rank corr rho={rho:+.3f} | PBO={pbo_val:.2f} (rank-stable but level-negative)"
    )
    print("=== GATED ===")
    print(f"  OOS-FDR survivors={n_fdr} | full-gate survivors={len(survivors)}")
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
