"""Multi-signal edge sweep — extends the funding pre-reg (2026-05-26).

Runs the remaining research surface through the SAME harness, with ONE honest
multiple-testing correction across the whole family (DSR deflated by total
n_trials; FDR across all p-values; shared PBO). All via ccxt in-code (no
per-instrument MCP calls): funding + OHLCV + OI for Binance/Bybit/Bitget.

Tests:
  A  cross-sectional funding (recap)         H1/H2/H3 x {8h,24h}
  B  time-series funding (per-symbol z-fade)  item 1
  C  multi-day carry (holds 1d/3d/7d)         item 2
  D  cross-venue funding dispersion           item 3
  E  open-interest x price                     item 5 (exploratory, ~30d)

No look-ahead: indexed by 8h boundary t; price=open at t; signal uses info <= t;
fwd_ret strictly future; carry uses funding realized in the hold window.
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
    cross_sectional_ic, dsr_for_returns, fdr_bh, ir, long_short_returns, sharpe_pvalue,
)
from core.stat_tests import sharpe  # noqa: E402

IR_MIN, DSR_PROMOTE, PBO_MAX, Q, SPLIT, FEE, SLIP, DAYS = 0.50, 0.90, 0.50, 0.20, 0.60, 0.0007, 0.0005, 70
CACHE = Path(__file__).resolve().parents[1] / "data" / "ohlcv_cache"


def universe():
    s = sorted({p.name.split("_")[0] for p in CACHE.glob("*_1h.parquet")})
    return [f"{x.replace('-', '/')}:USDT" for x in s]


def _funding_series(ex, sym, since):
    fr = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
    return pd.Series({pd.Timestamp(r["timestamp"], unit="ms", tz="UTC").floor("8h"):
                      float(r["fundingRate"]) for r in fr})


def fetch_all(syms):
    since = ccxt.binance().milliseconds() - DAYS * 24 * 3600 * 1000
    bn = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    by = ccxt.bybit({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    bg = ccxt.bitget({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    fund_bn, fund_by, fund_bg, price, oi = {}, {}, {}, {}, {}
    for sym in syms:
        try:
            f = _funding_series(bn, sym, since)
            oh = bn.fetch_ohlcv(sym, "8h", since=since, limit=1000)
        except Exception as e:
            print(f"  skip {sym}: {str(e)[:40]}"); continue
        if len(f) < 60 or len(oh) < 60:
            continue
        fund_bn[sym] = f[~f.index.duplicated()]
        price[sym] = pd.Series({pd.Timestamp(o[0], unit="ms", tz="UTC").floor("8h"): float(o[1])
                                for o in oh}).pipe(lambda s: s[~s.index.duplicated()])
        for ex2, store in ((by, fund_by), (bg, fund_bg)):
            try:
                s2 = _funding_series(ex2, sym, since)
                if len(s2) >= 60:
                    store[sym] = s2[~s2.index.duplicated()]
            except Exception:
                pass
        try:
            oh_oi = bn.fetch_open_interest_history(sym, "8h", since=ccxt.binance().milliseconds() - 28*24*3600*1000, limit=500)
            oi[sym] = pd.Series({pd.Timestamp(o["timestamp"], unit="ms", tz="UTC").floor("8h"):
                                 float(o["openInterestAmount"] or o.get("openInterestValue") or np.nan)
                                 for o in oh_oi}).pipe(lambda s: s[~s.index.duplicated()])
        except Exception:
            pass
        time.sleep(0.05)
    F = pd.DataFrame(fund_bn).sort_index()
    P = pd.DataFrame(price).sort_index()
    idx = F.index.intersection(P.index)
    return (F.loc[idx], P.loc[idx],
            pd.DataFrame(fund_by).reindex(idx) if fund_by else pd.DataFrame(),
            pd.DataFrame(fund_bg).reindex(idx) if fund_bg else pd.DataFrame(),
            pd.DataFrame(oi).reindex(idx) if oi else pd.DataFrame())


def fwd(P, h):
    return P.shift(-h) / P - 1.0


def split_idx(n, embargo):
    cut = int(n * SPLIT)
    return slice(0, cut - embargo), slice(cut, n)


def cost_per_bar(hold):
    return 2.0 * (FEE + SLIP) * 2.0 / max(1, hold)


def eval_ls(name, sig, sign, P, h, oos, ntr):
    fr = fwd(P, h).iloc[oos]
    ls = long_short_returns(sig.iloc[oos], fr, sign=sign, q=Q, min_width=10) - cost_per_bar(h)
    r = ls.dropna()
    if r.size < 4:
        return None
    half = r.size // 2
    return dict(name=name, h=h, n=int(r.size), sharpe=float(sharpe(r.to_numpy())),
                mean=float(r.mean()), pval=sharpe_pvalue(r), series=ls,
                halves=bool(r.iloc[:half].mean() > 0 and r.iloc[half:].mean() > 0))


def main():
    syms = universe()
    print(f"fetching {len(syms)} symbols x 3 venues (funding/price/OI)...")
    F, P, FBY, FBG, OI = fetch_all(syms)
    print(f"panel: {F.shape[0]} bars x {F.shape[1]} syms ({F.index[0]}->{F.index[-1]}) | "
          f"bybit {FBY.shape[1]} | bitget {FBG.shape[1]} | OI {OI.shape[1]}")
    if F.shape[1] < 10:
        print("INSUFFICIENT"); return

    ret1 = P / P.shift(1) - 1.0
    fz = (F - F.rolling(30, min_periods=10).mean()) / F.rolling(30, min_periods=10).std()
    rows, series, diagnostics = [], {}, []

    # ---- A: cross-sectional funding (recap) ----
    A = {"A_H1_revers": (-F, 1.0), "A_H2_mom": (F.diff(), 1.0), "A_H3_diverg": (F * ret1, 1.0)}
    # ---- D: cross-venue dispersion (binance - other) ----
    Dsig = {}
    if FBY.shape[1] >= 10:
        Dsig["D_disp_bybit"] = (-(F - FBY), 1.0)   # binance richer => fade (short)
    if FBG.shape[1] >= 10:
        Dsig["D_disp_bitget"] = (-(F - FBG), 1.0)
    # ---- E: OI x price (exploratory) ----
    Esig = {}
    if OI.shape[1] >= 10:
        Esig["E_oi_x_price"] = (np.sign(ret1) * (OI / OI.shift(1) - 1.0), 1.0)

    cs_signals = {**A, **Dsig, **Esig}
    horizons = [1, 3]

    # count trials BEFORE eval (cross-sec + TS + carry)
    n_trials = len(cs_signals) * len(horizons) + 1 + 3  # +TS portfolio +3 carry holds

    for h in horizons:
        is_sl, oos_sl = split_idx(F.shape[0], h)
        for name, (sig, sgn) in cs_signals.items():
            ic_is = cross_sectional_ic(sig.iloc[is_sl], fwd(P, h).iloc[is_sl])
            ir_is = sgn * ir(ic_is)
            res = eval_ls(name, sig, sgn, P, h, oos_sl, n_trials)
            if not res:
                continue
            res["ir_is"] = ir_is
            rows.append(res); series[f"{name}_{h}"] = res["series"]

    # ---- B: time-series funding (per-symbol z-fade) ----
    # diagnostic: per-symbol Spearman(fz, fwd8h); portfolio: pos=-sign(fz)*(|fz|>1)
    fr8 = fwd(P, 1)
    ts_ic = []
    for sym in F.columns:
        a, b = fz[sym], fr8[sym]
        m = a.notna() & b.notna()
        if m.sum() > 20:
            ts_ic.append(a[m].corr(b[m], method="spearman"))
    ts_ic = np.array([x for x in ts_ic if np.isfinite(x)])
    diagnostics.append(f"B time-series funding IC: mean={ts_ic.mean():+.3f} "
                       f"%pos={100*(ts_ic>0).mean():.0f}% t={ts_ic.mean()/ (ts_ic.std(ddof=1)/np.sqrt(ts_ic.size)):+.2f} (n_sym={ts_ic.size})")
    pos = -np.sign(fz) * (fz.abs() > 1.0)
    _, oos_sl = split_idx(F.shape[0], 1)
    ts_port = ((pos.iloc[oos_sl] * fr8.iloc[oos_sl]).mean(axis=1) - cost_per_bar(1)).dropna()
    if ts_port.size >= 4:
        half = ts_port.size // 2
        rows.append(dict(name="B_ts_zfade", h=1, n=int(ts_port.size),
                         sharpe=float(sharpe(ts_port.to_numpy())), mean=float(ts_port.mean()),
                         pval=sharpe_pvalue(ts_port), series=ts_port, ir_is=float("nan"),
                         halves=bool(ts_port.iloc[:half].mean() > 0 and ts_port.iloc[half:].mean() > 0)))
        series["B_ts_zfade_1"] = ts_port

    # ---- C: multi-day carry (long low-funding / short high, hold H, realized funding) ----
    for hold in (3, 9, 21):
        pr = fwd(P, hold)
        fcum = F.rolling(hold).sum().shift(-hold)  # funding settling within (t, t+hold]
        _, oos_sl = split_idx(F.shape[0], hold)
        Fi, pri, fci = F.iloc[oos_sl], pr.iloc[oos_sl], fcum.iloc[oos_sl]
        out = []
        for t in range(len(Fi)):
            fr_, pr_, fc_ = Fi.iloc[t].to_numpy(), pri.iloc[t].to_numpy(), fci.iloc[t].to_numpy()
            m = np.isfinite(fr_) & np.isfinite(pr_) & np.isfinite(fc_)
            if int(m.sum()) < 10:
                out.append(np.nan); continue
            idx = np.where(m)[0]; order = idx[np.argsort(fr_[idx])]
            k = max(1, int(round(int(m.sum()) * Q)))
            lo, sh = order[:k], order[-k:]
            out.append((pr_[lo].mean() - pr_[sh].mean()) + (fc_[sh].mean() - fc_[lo].mean()) - cost_per_bar(hold))
        r = pd.Series(out, index=Fi.index).dropna()
        if r.size >= 4:
            half = r.size // 2
            rows.append(dict(name=f"C_carry_{hold*8}h", h=hold, n=int(r.size),
                             sharpe=float(sharpe(r.to_numpy())), mean=float(r.mean()),
                             pval=sharpe_pvalue(r), series=r, ir_is=float("nan"),
                             halves=bool(r.iloc[:half].mean() > 0 and r.iloc[half:].mean() > 0)))
            series[f"C_carry_{hold*8}h_{hold}"] = r

    # ---- consolidated multiple-testing ----
    from core.stat_tests import pbo as _pbo
    mat = pd.DataFrame({k: v for k, v in series.items()}).sort_index().fillna(0.0)
    pbo_val = float(_pbo(mat.to_numpy(), n_partitions=8)) if mat.shape[0] >= 8 and mat.shape[1] >= 2 else 0.5
    for r in rows:
        r["dsr"] = dsr_for_returns(r["series"], n_trials=n_trials)
    fdr_flags = fdr_bh([r["pval"] for r in rows], q=0.05)

    print("\n" + "=" * 104)
    print(f"{'signal':16}{'h':>3}{'IR_is':>8}{'Sharpe':>9}{'mean':>11}{'DSR':>7}{'pval':>7}{'2half':>7}{'FDR':>5}")
    print("-" * 104)
    for r, fdr in zip(rows, fdr_flags):
        ir_s = f"{r['ir_is']:+.3f}" if np.isfinite(r["ir_is"]) else "  n/a"
        print(f"{r['name']:16}{r['h']:>3}{ir_s:>8}{r['sharpe']:>9.3f}{r['mean']:>11.5f}"
              f"{r['dsr']:>7.3f}{r['pval']:>7.3f}{'Y' if r['halves'] else 'n':>7}{'Y' if fdr else 'n':>5}")
    print("-" * 104)
    for d in diagnostics:
        print("  " + d)
    print(f"PBO(8)={pbo_val:.3f}  n_trials(DSR)={n_trials}  total_series={len(series)}")

    promoted = [f"{r['name']}@h{r['h']}" for r, fdr in zip(rows, fdr_flags)
                if (np.isfinite(r["ir_is"]) and r["ir_is"] >= IR_MIN or r["name"].startswith(("B_", "C_", "D_")))
                and r["dsr"] >= DSR_PROMOTE and pbo_val <= PBO_MAX and fdr and r["mean"] > 0 and r["halves"]]
    print("=" * 104)
    print(f"CANDIDATES: {promoted}" if promoted else
          "VERDICT: NO_EDGE — nothing cleared the frozen gates across the full sweep.")


if __name__ == "__main__":
    main()
