"""OI-divergence edge screen (H5) on >30d CoinDesk history — 2026-05-30.

Runs the ONE signal the 2026-05-26 funding/OI prereg DEFERRED for lack of data:
    H5 / E_oi_x_price = sign(Δprice) · ΔOI%        (info at bar t only)
through the SAME frozen harness + gates as scripts/run_edge_sweep.py.

Why now: B12 (OI-divergence bonus, +8) and V1 (OI-exhaustion veto) hold LIVE
entry authority in mcp_brain but were NEVER screened — the prereg used Binance
OI (≤28d) and dropped E (OI.shape[1] < 10). The CoinDesk OI-history endpoint
(fixed 2026-05-30: /open-interest/days, not /histoday) returns ~99 daily bars,
enough to finally evaluate H5. Owner directive 2026-05-30: "Screen OI-divergence
first" before any demote/keep decision on the live OI gates.

Frozen gates (DO NOT change after seeing results):
  Stage 1 (IS):  IR = mean(IC)/std(IC) >= 0.50          <- the bar 443 alphas failed
  Stage 2 (OOS): DSR (trials-deflated) >= 0.10 (report) / >= 0.90 (real promote)
                 PBO <= 0.50 ; OOS mean > 0 in BOTH halves (stability)
Daily cadence (~99 bars); horizons 1d & 3d. n_trials(DSR)=2 (E×2 horizons).
NOTE: ~99 daily obs is THIN — per the prereg, OI is exploratory/low-power.
Verdict is reported with that caveat.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import ccxt  # noqa: E402

from core.alpha_zoo.screen import (  # noqa: E402
    cross_sectional_ic,
    dsr_for_returns,
    ir,
    long_short_returns,
    sharpe_pvalue,
)
from core.data_feeds._coindesk_caller import call_coindesk_oi_ohlcv  # noqa: E402
from core.data_feeds.open_interest_feed import _coin_to_instrument  # noqa: E402
from core.stat_tests import pbo as _pbo  # noqa: E402
from core.stat_tests import sharpe  # noqa: E402

IR_MIN, DSR_REPORT, DSR_PROMOTE, PBO_MAX, Q, SPLIT, FEE, SLIP = (
    0.50,
    0.10,
    0.90,
    0.50,
    0.20,
    0.60,
    0.0007,
    0.0005,
)
MARKET = "binance"
BASES = [
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "ATOM",
    "ARB",
    "OP",
    "NEAR",
    "APT",
    "DOT",
    "LTC",
    "TRX",
    "UNI",
    "AAVE",
    "FIL",
    "INJ",
    "SUI",
    "TIA",
    "SEI",
    "RUNE",
    "FET",
    "WLD",
    "LDO",
]


def fetch_oi_daily(base: str, limit: int = 100) -> pd.Series:
    """Daily OI (CLOSE_SETTLEMENT = OI in base/contract units, price-independent)."""
    r = call_coindesk_oi_ohlcv(
        market=MARKET,
        instrument=_coin_to_instrument(base),
        frequency="days",
        aggregate=1,
        limit=limit,
    )
    data = (r or {}).get("Data", []) or []
    out = {}
    for b in data:
        ts = b.get("TIMESTAMP")
        v = b.get("CLOSE_SETTLEMENT")
        if ts and v:
            out[pd.Timestamp(ts, unit="s", tz="UTC").floor("D")] = float(v)
    s = pd.Series(out).sort_index()
    return s[~s.index.duplicated()]


def fetch_price_daily(ex, base: str, limit: int = 120) -> pd.Series:
    oh = ex.fetch_ohlcv(f"{base}/USDT:USDT", "1d", limit=limit)
    s = pd.Series({pd.Timestamp(o[0], unit="ms", tz="UTC").floor("D"): float(o[4]) for o in oh})
    return s[~s.index.duplicated()].sort_index()


def fwd(P, h):
    return P.shift(-h) / P - 1.0


def cost_per_bar(hold):
    return 2.0 * (FEE + SLIP) * 2.0 / max(1, hold)


def main():
    bn = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    oi, price = {}, {}
    for base in BASES:
        try:
            o = fetch_oi_daily(base)
            p = fetch_price_daily(bn, base)
        except Exception as e:
            print(f"  skip {base}: {str(e)[:50]}")
            continue
        if len(o) >= 40 and len(p) >= 40:
            oi[base], price[base] = o, p
        time.sleep(0.05)

    OI = pd.DataFrame(oi).sort_index()
    P = pd.DataFrame(price).sort_index()
    idx = OI.index.intersection(P.index)
    OI, P = OI.loc[idx], P.loc[idx]
    print(
        f"panel: {OI.shape[0]} daily bars x {OI.shape[1]} symbols "
        f"({idx[0].date()} -> {idx[-1].date()})"
    )
    if OI.shape[1] < 10 or OI.shape[0] < 40:
        print("INSUFFICIENT DATA (need >=10 symbols, >=40 aligned bars) — verdict: UNRESOLVED")
        return

    ret1 = P / P.shift(1) - 1.0
    doi = OI / OI.shift(1) - 1.0
    # H5 / E: sign(Δprice) · ΔOI%  (both legs known at bar t -> no look-ahead)
    E = np.sign(ret1) * doi

    horizons = [1, 3]
    n_trials = len(horizons)  # E at h=1, h=3
    rows, series = [], {}
    n = OI.shape[0]
    cut = int(n * SPLIT)
    for h in horizons:
        is_sl, oos_sl = slice(0, cut - h), slice(cut, n)
        ic_is = cross_sectional_ic(E.iloc[is_sl], fwd(P, h).iloc[is_sl])
        ir_is = ir(ic_is)
        fr = fwd(P, h).iloc[oos_sl]
        ls = (
            long_short_returns(E.iloc[oos_sl], fr, sign=1.0, q=Q, min_width=8) - cost_per_bar(h)
        ).dropna()
        if ls.size < 4:
            rows.append((h, ir_is, None, None, None, None))
            continue
        half = ls.size // 2
        halves = bool(ls.iloc[:half].mean() > 0 and ls.iloc[half:].mean() > 0)
        rows.append(
            (h, ir_is, float(sharpe(ls.to_numpy())), float(ls.mean()), sharpe_pvalue(ls), halves)
        )
        series[f"E_h{h}"] = ls

    dsr = {k: dsr_for_returns(v, n_trials=n_trials) for k, v in series.items()}
    mat = pd.DataFrame(series).sort_index().fillna(0.0)
    pbo_val = (
        float(_pbo(mat.to_numpy(), n_partitions=8))
        if mat.shape[0] >= 8 and mat.shape[1] >= 2
        else float("nan")
    )

    print("\n" + "=" * 88)
    print("H5 / E_oi_x_price = sign(dPrice) * dOI%   (daily, CoinDesk OI ~99d)")
    print(
        f"{'h(d)':>5}{'IR_is':>9}{'Sharpe_oos':>12}{'mean_oos':>11}{'DSR':>8}{'pval':>7}{'2half':>7}"
    )
    print("-" * 88)
    passed_s1 = []
    for h, ir_is, shp, mean, pval, halves in rows:
        dk = f"E_h{h}"
        dsr_v = dsr.get(dk, float("nan"))
        shp_s = f"{shp:.3f}" if shp is not None else "n/a"
        mean_s = f"{mean:+.5f}" if mean is not None else "n/a"
        pval_s = f"{pval:.3f}" if pval is not None else "n/a"
        half_s = ("Y" if halves else "n") if halves is not None else "-"
        print(f"{h:>5}{ir_is:>+9.3f}{shp_s:>12}{mean_s:>11}{dsr_v:>8.3f}{pval_s:>7}{half_s:>7}")
        if abs(ir_is) >= IR_MIN:
            passed_s1.append((h, ir_is))
    print("-" * 88)
    print(f"PBO(8)={pbo_val:.3f}  n_trials(DSR)={n_trials}  IS bars={cut}  OOS bars={n - cut}")
    print("=" * 88)

    # Verdict per frozen gates
    promoted = []
    for h, ir_is, shp, mean, pval, halves in rows:
        if shp is None:
            continue
        dsr_v = dsr.get(f"E_h{h}", 0.0)
        if (
            abs(ir_is) >= IR_MIN
            and dsr_v >= DSR_PROMOTE
            and (np.isnan(pbo_val) or pbo_val <= PBO_MAX)
            and mean > 0
            and halves
        ):
            promoted.append(f"E@h{h}d (IR={ir_is:+.2f}, DSR={dsr_v:.2f})")

    if promoted:
        print(f"CANDIDATES (cleared frozen gates): {promoted}")
        print("=> OI-divergence shows screenable edge — keep B12/V1 live; extend walk-forward.")
    elif not passed_s1:
        print("VERDICT: NO_EDGE — OI-divergence FAILED stage-1 IR>=0.50 (the cost-free bar).")
        print("=> B12 bonus + V1 veto hold LIVE authority on an UNVALIDATED signal.")
        print("   Honest options: demote B12/V1 to logged-only, or accept they add noise.")
    else:
        print("VERDICT: INSUFFICIENT — passed stage-1 IR but not stage-2 DSR/PBO/halves.")
        print("   (Thin ~99-bar daily sample; treat as exploratory, not a clear keep.)")


if __name__ == "__main__":
    main()
