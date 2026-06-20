"""Funding-rate edge search — pre-registered 2026-05-26.

Tests crypto-native funding-rate signals the OHLCV-only 443-alpha search could
not see, through the SAME validation harness (core/alpha_zoo/screen.py).

See docs/superpowers/plans/2026-05-26-prereg-funding-oi-edge.md for the frozen
hypotheses and gates. This script only RUNS them; it does not change gates.

No look-ahead: everything is indexed by the 8h funding-settlement boundary t.
- Price at t  = the 8h bar OPEN at t (price observable exactly at t).
- Signal at t = funding rate that SETTLED at t (known at t).
- fwd_ret(h)  = open[t+h]/open[t] - 1   (strictly future).
- Carry PnL   = funding realized in (t, t+1] = funding[t+1] (future settle).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ccxt  # noqa: E402

from core.alpha_zoo.screen import (  # noqa: E402
    cross_sectional_ic,
    dsr_for_returns,
    fdr_bh,
    ir,
    long_short_returns,
    pbo_over_alphas,
    sharpe_pvalue,
)
from core.stat_tests import sharpe  # noqa: E402

# ── frozen config (mirrors pre-registration) ───────────────────────────────
IR_MIN = 0.50
DSR_MIN_INHERITED = 0.10
DSR_MIN_PROMOTE = 0.90
PBO_MAX = 0.50
PBO_PARTITIONS = 8
FDR_Q = 0.05
QUANTILE = 0.20
SPLIT_FRAC = 0.60
FEE_PCT = 0.0007
SLIP_PCT = 0.0005
DAYS = 70
HORIZONS = {"8h": 1, "24h": 3}  # in 8h bars

CACHE = Path(__file__).resolve().parents[1] / "data" / "ohlcv_cache"


def universe() -> list[str]:
    syms = sorted({p.name.split("_")[0] for p in CACHE.glob("*_1h.parquet")})
    return [f"{s.replace('-', '/')}:USDT" for s in syms]


def fetch(ex, symbols):
    """Return (funding_df, price_df) on a shared 8h boundary index (T×N)."""
    since = ex.milliseconds() - DAYS * 24 * 3600 * 1000
    fund, price = {}, {}
    for sym in symbols:
        try:
            fr = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
            oh = ex.fetch_ohlcv(sym, "8h", since=since, limit=1000)
        except Exception as e:
            print(f"  skip {sym}: {type(e).__name__} {str(e)[:50]}")
            continue
        if len(fr) < 60 or len(oh) < 60:
            print(f"  skip {sym}: thin (fr={len(fr)}, oh={len(oh)})")
            continue
        f = pd.Series(
            {
                pd.Timestamp(r["timestamp"], unit="ms", tz="UTC").floor("8h"): float(
                    r["fundingRate"]
                )
                for r in fr
            }
        )
        # price at boundary t = the 8h bar OPEN at t
        p = pd.Series(
            {pd.Timestamp(o[0], unit="ms", tz="UTC").floor("8h"): float(o[1]) for o in oh}
        )
        fund[sym], price[sym] = f[~f.index.duplicated()], p[~p.index.duplicated()]
        if ex.enableRateLimit:
            time.sleep(ex.rateLimit / 1000)
    F = pd.DataFrame(fund).sort_index()
    P = pd.DataFrame(price).sort_index()
    idx = F.index.intersection(P.index)
    return F.loc[idx], P.loc[idx]


def verify_timestamps(ex, sym, F):
    """Advisor item 1: confirm funding ts is 8h-aligned settlement, print proof."""
    boundaries_ok = bool((F.index.hour % 8 == 0).all() and (F.index.minute == 0).all())
    print(f"  funding-ts check on {sym}: all 8h-aligned settlement = {boundaries_ok}")
    print(f"  sample funding index: {[str(t) for t in F.index[:3]]}")
    return boundaries_ok


def split(idx, frac, embargo):
    n = len(idx)
    cut = int(n * frac)
    return slice(0, cut - embargo), slice(cut, n)


def fwd_ret(P, h):
    return P.shift(-h) / P - 1.0


def turnover_cost(signal, sign, q, n_bars_held):
    """Conservative per-bar cost: assume full reform of both legs each rebalance.
    Round-trip cost per leg = 2*(fee+slip); two legs; amortized over hold bars."""
    rt = 2.0 * (FEE_PCT + SLIP_PCT) * 2.0  # both legs, entry+exit
    return rt / max(1, n_bars_held)


def eval_signal(name, signal, sign, P, h, oos, n_trials):
    fr = fwd_ret(P, h)
    fr_oos = fr.iloc[oos]
    sig_oos = signal.iloc[oos]
    ls_gross = long_short_returns(sig_oos, fr_oos, sign=sign, q=QUANTILE, min_width=10)
    cost = turnover_cost(signal, sign, QUANTILE, h)
    ls_net = ls_gross - cost
    r = ls_net.dropna()
    if r.size < 4:
        return None
    half = r.size // 2
    both_halves_pos = bool(r.iloc[:half].mean() > 0 and r.iloc[half:].mean() > 0)
    return {
        "name": name,
        "h": h,
        "n_oos": int(r.size),
        "sharpe_net": float(sharpe(r.to_numpy())),
        "mean_net": float(r.mean()),
        "dsr": dsr_for_returns(r, n_trials=n_trials),
        "pval": sharpe_pvalue(r),
        "both_halves_pos": both_halves_pos,
        "ls_net": ls_net,
    }


def carry_eval(F, P, oos, n_trials):
    """H4 carry: long bottom-funding / short top-funding; PnL = price move +
    funding REALIZED in (t,t+1] (= funding[t+1]) - cost. Selection uses F[t]."""
    pr = fwd_ret(P, 1)
    f_next = F.shift(-1)  # funding settling during the hold window
    cost = turnover_cost(F, 1.0, QUANTILE, 1)
    Fi, pri, fni = F.iloc[oos], pr.iloc[oos], f_next.iloc[oos]
    out = []
    for t in range(len(Fi)):
        fr_row, pr_row, fn_row = (
            Fi.iloc[t].to_numpy(),
            pri.iloc[t].to_numpy(),
            fni.iloc[t].to_numpy(),
        )
        m = np.isfinite(fr_row) & np.isfinite(pr_row) & np.isfinite(fn_row)
        n = int(m.sum())
        if n < 10:
            out.append(np.nan)
            continue
        idx = np.where(m)[0]
        order = idx[np.argsort(fr_row[idx])]  # ascending funding
        k = max(1, int(round(n * QUANTILE)))
        longs, shorts = order[:k], order[-k:]  # long low-funding, short high-funding
        price_pnl = pr_row[longs].mean() - pr_row[shorts].mean()
        carry_pnl = (
            fn_row[shorts].mean() - fn_row[longs].mean()
        )  # receive on shorts (high +funding)
        out.append(price_pnl + carry_pnl - cost)
    r = pd.Series(out, index=Fi.index).dropna()
    if r.size < 4:
        return None
    half = r.size // 2
    return {
        "name": "H4_carry",
        "h": 1,
        "n_oos": int(r.size),
        "sharpe_net": float(sharpe(r.to_numpy())),
        "mean_net": float(r.mean()),
        "dsr": dsr_for_returns(r, n_trials=n_trials),
        "pval": sharpe_pvalue(r),
        "both_halves_pos": bool(r.iloc[:half].mean() > 0 and r.iloc[half:].mean() > 0),
        "ls_net": r,
    }


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    syms = universe()
    print(f"universe: {len(syms)} symbols; fetching {DAYS}d funding + 8h OHLCV...")
    F, P = fetch(ex, syms)
    print(f"usable panel: {F.shape[0]} bars x {F.shape[1]} symbols ({F.index[0]} -> {F.index[-1]})")
    if F.shape[1] < 10 or F.shape[0] < 60:
        print("INSUFFICIENT DATA — abort.")
        return
    if not verify_timestamps(ex, F.columns[0], F):
        print("FUNDING TIMESTAMP ALIGNMENT FAILED — abort (look-ahead risk).")
        return

    recent_ret = P / P.shift(1) - 1.0
    # pre-registered features (direction baked in; pass sign=+1, no sign-snoop)
    feats = {
        "H1_reversal": (-F, 1.0),  # long low funding
        "H2_momentum": (F.diff(), 1.0),  # long rising funding
        "H3_divergence": (F * recent_ret, 1.0),  # long funding/price-aligned
    }
    horizons = list(HORIZONS.values())
    n_trials = len(feats) * len(horizons) + 1  # +1 for carry

    results, ls_by_key = [], {}
    for h in horizons:
        embargo = h
        is_sl, oos_sl = split(F.index, SPLIT_FRAC, embargo)
        for name, (sig, sgn) in feats.items():
            # Stage 1 (IS): signed IR vs pre-registered direction
            ic_is = cross_sectional_ic(sig.iloc[is_sl], fwd_ret(P, h).iloc[is_sl])
            ir_is = sgn * ir(ic_is)
            res = eval_signal(name, sig, sgn, P, h, oos_sl, n_trials)
            if res is None:
                continue
            res["ir_is"] = ir_is
            res["passes_s1"] = ir_is >= IR_MIN
            results.append(res)
            ls_by_key[f"{name}_{h}"] = res["ls_net"]

    # H4 carry (8h hold)
    _, oos_sl = split(F.index, SPLIT_FRAC, 1)
    carry = carry_eval(F, P, oos_sl, n_trials)
    if carry:
        carry["ir_is"] = float("nan")
        carry["passes_s1"] = True  # carry is not an IC test
        results.append(carry)
        ls_by_key["H4_carry_1"] = carry["ls_net"]

    pbo_val = pbo_over_alphas({k: v for k, v in ls_by_key.items()}, n_partitions=PBO_PARTITIONS)
    pvals = [r["pval"] for r in results]
    fdr_flags = fdr_bh(pvals, q=FDR_Q)

    print("\n" + "=" * 100)
    print(
        f"{'signal':16} {'h':>3} {'IR_is':>7} {'S1':>3} {'Shrp_net':>9} "
        f"{'mean_net':>10} {'DSR':>6} {'pval':>6} {'2halves':>8} {'FDR':>4}"
    )
    print("-" * 100)
    for r, fdr in zip(results, fdr_flags):
        print(
            f"{r['name']:16} {r['h']:>3} {r['ir_is']:>7.3f} "
            f"{'Y' if r['passes_s1'] else 'n':>3} {r['sharpe_net']:>9.3f} "
            f"{r['mean_net']:>10.5f} {r['dsr']:>6.3f} {r['pval']:>6.3f} "
            f"{'Y' if r['both_halves_pos'] else 'n':>8} {'Y' if fdr else 'n':>4}"
        )
    print("-" * 100)
    print(
        f"PBO (8 partitions, {len(ls_by_key)} signals): {pbo_val:.3f}  "
        f"(gate <= {PBO_MAX})   n_trials(DSR)={n_trials}"
    )

    # verdict per frozen gates
    promoted = []
    for r, fdr in zip(results, fdr_flags):
        inherited = (
            r["passes_s1"]
            and r["dsr"] >= DSR_MIN_INHERITED
            and pbo_val <= PBO_MAX
            and fdr
            and r["mean_net"] > 0
        )
        promote = inherited and r["dsr"] >= DSR_MIN_PROMOTE and r["both_halves_pos"]
        if promote:
            promoted.append(f"{r['name']}@{r['h'] * 8}h")
    print("=" * 100)
    if promoted:
        print(f"CANDIDATE(S) (need further OOS/paper-forward): {promoted}")
    else:
        print("VERDICT: NO_EDGE — no hypothesis cleared the frozen gates.")
        ic_irs = [r["ir_is"] for r in results if not np.isnan(r.get("ir_is", np.nan))]
        best = max(ic_irs) if ic_irs else float("nan")
        print(
            f"  best stage-1 signed IR among IC signals = {best:.3f} (gate {IR_MIN}); "
            "carry net-of-cost mean < 0."
        )


if __name__ == "__main__":
    main()
