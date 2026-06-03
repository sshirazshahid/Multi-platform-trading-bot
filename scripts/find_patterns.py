"""Find + record repetitive CALENDAR / SEASONALITY patterns in liquid crypto prices.

Tests whether any recurring time window has a robust, after-cost, STATISTICALLY
SIGNIFICANT positive tendency:
  - hour-of-day  (intraday / "hourly")
  - day-of-week  ("weekly")
Across liquid majors (BTC ETH SOL BNB XRP).

METHODOLOGY (anti-data-mining — this is the whole point). Calendar effects are THE
classic overfitting trap. With ~24 hours x 7 weekdays x 5 assets (~155 windows) you
will ALWAYS find some that look profitable by chance. A window counts as "robust"
ONLY if ALL hold:
  1. beats ~6bps round-trip cost IN-SAMPLE (older 60%);
  2. beats cost OUT-OF-SAMPLE (newer 40%);
  3. the OOS mean is STATISTICALLY SIGNIFICANT (|t| >= 2, i.e. distinguishable from
     zero) — a positive average over a few dozen days is otherwise just noise;
  4. holds on >= 2 assets.
HONESTY CAVEATS recorded with the result: (a) the 5 majors are ~0.8 correlated, so
"holds on N assets" is closer to ONE crypto-beta observation than N independent ones;
(b) with ~155 windows tested, even |t|>=2 (p~0.05) would expect ~8 false positives —
a proper multiple-comparisons correction (Bonferroni) demands a far higher bar. So
even a "robust" hit here is a weak hypothesis, never a green light.
MONTHLY / longer is NOT tested: ~1-3y history gives only ~12-36 monthly observations
— far too few to estimate monthly means honestly.

Records reports/patterns_<date>.md. Read-only, gentle, re-runnable.
"""
from __future__ import annotations

import math
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.feature_store import load_ohlcv_window  # noqa: E402

ASSETS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
COST_RT = 6.0 / 1e4          # ~6 bps round-trip
MIN_OBS = 20                 # min occurrences per bucket per half
T_MIN = 2.0                  # OOS significance bar (|t| >= 2)
ROBUST_ASSETS = 2


def _returns(df):
    import numpy as np
    import pandas as pd
    df = df.sort_values("ts").copy()
    df["ret"] = np.log(df["close"]).diff()
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.dropna(subset=["ret"])


def _bucket_stats(df, bucket_attr):
    """{bucket: (n, mean, std)} for the in-sample and oos halves (chronological 60/40)."""
    cut = int(len(df) * 0.60)
    is_df, oos_df = df.iloc[:cut], df.iloc[cut:]

    def grp(d):
        out = {}
        g = getattr(d["dt"].dt, bucket_attr)
        for v in sorted(set(g)):
            r = d["ret"][g == v]
            if len(r) >= MIN_OBS:
                out[int(v)] = (len(r), float(r.mean()), float(r.std()))
        return out

    return grp(is_df), grp(oos_df)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import ccxt
    print("Connecting to Binance (public OHLCV)...")
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
    ex.load_markets()

    def _fetch(symbol, tf, since_ms, limit):
        return ex.fetch_ohlcv(symbol, tf, since=int(since_ms), limit=int(limit)) or []

    now = int(time.time())
    dims = [("Hour of day (UTC)", "1h", 365 * 24 * 3600, "hour"),
            ("Day of week (0=Mon)", "1d", 3 * 365 * 24 * 3600, "dayofweek")]

    coverage = []
    cand = []  # every evaluated (dim, asset, bucket): (dim, a, v, isn, ismean, on, omean, oos_t)
    for dim_name, tf, lookback, attr in dims:
        for a in ASSETS:
            df = load_ohlcv_window(f"{a}/USDT", tf, now - lookback, now, fetcher=_fetch)
            if len(df) < 200:
                coverage.append(f"{a} {tf}: only {len(df)} bars — skipped")
                continue
            is_b, oos_b = _bucket_stats(_returns(df), attr)
            for v, (isn, ismean, _isstd) in is_b.items():
                on, omean, ostd = oos_b.get(v, (0, 0.0, 0.0))
                oos_t = (omean / (ostd / math.sqrt(on))) if (on > 1 and ostd > 0) else 0.0
                cand.append((dim_name, a, v, isn, ismean, on, omean, oos_t))

    # Bonferroni-corrected OOS significance threshold over the ACTUAL window count (audit #3 —
    # the old fixed |t|>=2 had no multiple-comparisons control; the >=2-asset gate is NOT a
    # valid FWER control because the majors are ~0.8 correlated).
    k = len(cand)
    try:
        from scipy.stats import norm
        t_bar = float(norm.ppf(1 - (0.05 / max(k, 1)) / 2))
    except Exception:
        t_bar = 3.6 if k > 100 else (3.2 if k > 30 else 2.6)

    held_by_dim = {}
    detail = []        # (dim, asset, bucket, is_n, is_mean, oos_n, oos_mean, oos_t, holds)
    for dim_name, a, v, isn, ismean, on, omean, oos_t in cand:
        is_sweet = ismean > COST_RT
        holds = (is_sweet and on >= MIN_OBS and omean > COST_RT and oos_t >= t_bar)
        if is_sweet:
            detail.append((dim_name, a, v, isn, ismean, on, omean, oos_t, holds))
        if holds:
            held_by_dim.setdefault(dim_name, {}).setdefault(v, []).append(a)

    robust = [(d, v, assets) for d, buckets in held_by_dim.items()
              for v, assets in buckets.items() if len(assets) >= ROBUST_ASSETS]

    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Repetitive calendar / seasonality patterns ({now_s})", "",
         "_Anti-data-mining bar: a window counts ONLY if it beats ~6bps cost IN-SAMPLE, beats "
         "cost OUT-OF-SAMPLE, the OOS mean is statistically significant (|t|>=2), AND it holds "
         "on >=2 assets. Even then it is a weak hypothesis: the 5 majors are ~0.8 correlated "
         "(multi-asset != independent), and ~155 windows were tested (multiple comparisons)._", "",
         f"**Assets:** {', '.join(ASSETS)} | hour-of-day (1y hourly), day-of-week (3y daily). "
         "Monthly/longer NOT tested — too few observations to be honest.", "",
         f"**Tests:** {k} windows | Bonferroni OOS t-threshold ≈ {t_bar:.2f} (replaces the prior "
         "fixed |t|>=2, which had no multiple-comparisons control — audit #3).", "",
         f"## Verdict: {len(robust)} pattern(s) cleared the full bar "
         "(after-cost + OOS-significant + multi-asset)", ""]

    if robust:
        L.append("### Cleared the bar (still weak hypotheses — see caveats, do not bet the farm)")
        for d, v, assets in robust:
            L.append(f"- **{d} = {v}** — OOS-significant after cost on: {', '.join(assets)}")
        L += ["", "_Caveat: correlated assets + multiple comparisons mean even these need a "
              "Bonferroni-level bar and forward paper-tracking before being trusted._", ""]
    else:
        L.append("### None. No calendar window was after-cost + OOS-statistically-significant.")
        L.append("_The honest, expected result: crypto hour-of-day and day-of-week 'patterns' "
                 "do not survive an out-of-sample significance test. There is no recurring "
                 "hour/weekday you can reliably trade for profit here. (Monthly+ can't even be "
                 "tested with this much history.)_")
        L.append("")

    sig_fail = [d for d in detail if not d[8]]
    if sig_fail:
        L.append("### ⚠ Looked profitable in-sample, FAILED the OOS / significance bar (overfit)")
        L += ["| dimension | asset | bucket | IS n | IS mean% | OOS n | OOS mean% | OOS t |",
              "|---|---|---|---|---|---|---|---|"]
        for d, a, v, isn, ismean, on, omean, oos_t, _ in sorted(detail, key=lambda x: x[4], reverse=True)[:20]:
            L.append(f"| {d} | {a} | {v} | {isn} | {ismean * 100:+.3f}% | {on} | "
                     f"{omean * 100:+.3f}% | {oos_t:+.2f} |")
        L.append("")

    if coverage:
        L += ["### Data coverage notes"] + [f"- {c}" for c in coverage] + [""]

    L.append("---")
    L.append("_scripts/find_patterns.py — descriptive backward-looking seasonality test (after-cost, "
             "IS/OOS, |t|>=2, multi-asset), NOT a forward signal. Monthly+ intentionally untested. "
             "Re-run as history grows._")

    out = ROOT / "reports" / f"patterns_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"=== Seasonality scan ({now_s}) ===")
    print(f"VERDICT: {len(robust)} pattern(s) cleared after-cost + OOS-significant + multi-asset; "
          f"{len(sig_fail)} in-sample windows washed out.")
    for d, v, assets in robust:
        print(f"  CLEARED: {d}={v} on {', '.join(assets)}")
    print(f"\nFull record: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
