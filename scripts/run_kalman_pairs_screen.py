"""Kalman-filter pairs-trading edge screen (WS7-A, 2026-06-06).

User-requested strategy. Question: does a dynamic-hedge-ratio (Kalman) mean-reversion strategy on
cointegrated crypto pairs survive the SAME frozen statistical stack as every other edge claim,
AFTER realistic two-legged fees?

Method (look-ahead-free, program-wide multiple-testing control):
  1. Build a 4h log-close panel from data/ohlcv_cache for the liquid 4h universe.
  2. Split each pair 50/50 in time. IN-SAMPLE: Engle-Granger cointegration; keep pairs with p<0.05.
     (n_trials = ALL pairs tested, not just survivors — the honest selection count for the DSR.)
  3. OUT-OF-SAMPLE: causal Kalman spread-level mean-reversion returns, net of fees.
  4. Gate the OOS returns through the frozen stack: trials-deflated Sharpe (DSR>=0.10),
     Benjamini-Hochberg FDR (q=0.05) across candidates, and PBO over the candidate return matrix.

Honest prior (repo study + 4 months of NO_EDGE): crypto cointegration is unstable and breaks in the
regimes you'd trade, and the spread is thin vs fees on a small book -> expect NO_EDGE. The null stops
cheaply. Nothing here trades real capital; this is an offline screen on cached OHLCV.

Run: python scripts/run_kalman_pairs_screen.py [--timeframe 4h] [--fee 0.0005] [--max-bases 30]
"""
from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.alpha_zoo.kalman_pairs import cointegration_pvalue, pair_strategy_returns  # noqa: E402
from core.alpha_zoo.screen import dsr_for_returns, fdr_bh, pbo_over_alphas, sharpe_pvalue  # noqa: E402
from core.stat_tests import sharpe  # noqa: E402

CACHE = ROOT / "data" / "ohlcv_cache"
MIN_BARS = 15000         # require a multi-year history per base (1h: ~26k bars = ~3y)
MIN_OVERLAP = 8000       # require a decent aligned overlap per pair
COINT_P = 0.05           # in-sample cointegration threshold
DSR_BAR = 0.10           # frozen DSR promote/report bar


def load_panel(timeframe: str, max_bases: int) -> pd.DataFrame:
    """Aligned log-close panel (index=UTC time, cols=base) for the liquid `timeframe` universe."""
    series: dict[str, pd.Series] = {}
    for f in sorted(CACHE.glob(f"*_{timeframe}.parquet")):
        base = f.name.split("_")[0]
        if not base.isascii() or not base.replace("-", "").isalnum():
            continue  # skip junk/non-ascii tickers
        df = pd.read_parquet(f)
        if len(df) < MIN_BARS or "close" not in df.columns:
            continue
        idx = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
        s = pd.Series(np.log(df["close"].to_numpy(dtype=float)), index=idx)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series[base] = s
    panel = pd.DataFrame(series).sort_index()
    # keep the bases with the most history (bounds n_trials / runtime)
    keep = panel.count().sort_values(ascending=False).head(int(max_bases)).index
    return panel[keep]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--fee", type=float, default=0.0005, help="per-leg fee fraction (taker ~5bps)")
    ap.add_argument("--max-bases", type=int, default=30)
    args = ap.parse_args()

    print("=== Kalman-filter pairs-trading edge screen (WS7-A) ===")
    panel = load_panel(args.timeframe, args.max_bases)
    bases = list(panel.columns)
    if len(bases) < 2:
        print(f"[abort] only {len(bases)} base(s) @ {args.timeframe} cleared MIN_BARS={MIN_BARS}; "
              f"nothing to pair.")
        return
    print(f"universe: {len(bases)} bases @ {args.timeframe}  "
          f"{panel.index.min().date()} -> {panel.index.max().date()}")
    pairs = list(combinations(bases, 2))
    n_trials = len(pairs)
    print(f"n_trials (pairs tested) = {n_trials}; fee={args.fee:.4%}/leg; "
          f"frozen gate: in-sample coint p<{COINT_P} AND OOS DSR>={DSR_BAR} AND FDR(q=0.05); PBO reported\n")

    candidates = []  # (label, oos_returns, coint_p, oos_sharpe, p_value)
    for a, b in pairs:
        pair = panel[[a, b]].dropna()
        if len(pair) < MIN_OVERLAP:
            continue
        y = pair[a].to_numpy()
        x = pair[b].to_numpy()
        h = len(pair) // 2
        # in-sample cointegration pre-filter (uses ONLY the first half)
        try:
            p_in = cointegration_pvalue(y[:h], x[:h])
        except Exception:
            continue
        if p_in >= COINT_P:
            continue
        # OOS: run the causal Kalman over the full series, evaluate returns on the second half only
        r_full = pair_strategy_returns(y, x, fee=args.fee)
        r_oos = r_full[h:]
        if np.count_nonzero(np.isfinite(r_oos) & (r_oos != 0.0)) < 30:
            continue  # essentially never traded OOS
        candidates.append((f"{a}/{b}", r_oos, p_in, sharpe(r_oos), sharpe_pvalue(r_oos)))

    print(f"in-sample-cointegrated candidates that traded OOS: {len(candidates)}\n")
    if not candidates:
        print("VERDICT: NO_EDGE — no pair was even in-sample cointegrated + active OOS. Stops cheaply.")
        return

    pvals = [c[4] for c in candidates]
    fdr_flags = fdr_bh(pvals, q=0.05)
    returns_by = {c[0]: pd.Series(c[1]) for c in candidates}
    pbo_val = pbo_over_alphas(returns_by)

    passes = []
    candidates.sort(key=lambda c: c[3], reverse=True)
    print(f"{'pair':<14} {'coint_p':>8} {'oos_SR':>8} {'p_val':>7} {'DSR':>6} {'FDR':>4}  verdict")
    for label, r_oos, p_in, sr, pv in candidates:
        dsr = dsr_for_returns(r_oos, n_trials=n_trials)
        fdr_ok = fdr_flags[pvals.index(pv)]
        ok = bool(dsr >= DSR_BAR and fdr_ok)
        passes.append(ok)
        print(f"{label:<14} {p_in:>8.3f} {sr:>8.4f} {pv:>7.3f} {dsr:>6.2f} "
              f"{str(fdr_ok):>4}  {'PASS' if ok else 'no'}")

    print(f"\nPBO over {len(candidates)} candidate OOS return series = {pbo_val:.2f} "
          f"(>=0.5 => in-sample winner carries no OOS information)")
    if any(passes):
        print("\nCANDIDATE(S) cleared the frozen gate. Treat as EXPLORATORY only — extend walk-forward, "
              "re-screen on fresh OOS, and account for borrow/funding before ANY use. Never deploy on a "
              "single pass.")
    else:
        print("\nVERDICT: NO_EDGE — no cointegrated pair's Kalman mean-reversion survives the frozen "
              "stack (DSR + FDR) after fees. Matches the instrument-agnostic no-edge prior; the spread "
              "is too thin vs two-legged cost and cointegration is unstable out-of-sample. Cost was "
              "~1 module + this screen; the null stops cheaply.")


if __name__ == "__main__":
    main()
