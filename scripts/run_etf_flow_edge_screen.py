"""Spot-BTC-ETF net-flow edge screen (WS3b — the genuinely-new, non-price information source).

Question: does US spot-Bitcoin-ETF daily NET FLOW (institutional-demand quantity, orthogonal to
price/funding/OI) predict BTC forward returns? Tested through the SAME frozen time-series regime
gate as the stablecoin screen: |t| >= 3.5 on the rank-correlation AND a label-shuffle null
exceeded by >= 3.5 sigma AND sign-consistent in BOTH halves (OOS stability), on non-overlapping
forward returns.

CAUSALITY (leakage control): Farside publishes day-T flow AFTER the US close, i.e. only known at
~T+1 UTC -- AFTER BTC's day-T 00:00-UTC close. So the signal is lagged 1 calendar day before it is
aligned to returns: the flow window ending at day T-1 (known by BTC close T) predicts the return
from close T forward. Weekends/holidays = 0 net flow (no ETF creations/redemptions -- correct, not
imputation). Bigger windows are NOT tried beyond the sample (~618 obs since 2024-01-11).

HONEST PRIOR: expected NO_EDGE (standing source-agnostic result); even a real edge is capital-capped
at ~$1,300. This is a cheap falsification, not a deployment. GO only on the full frozen gate.

Run: python scripts/run_etf_flow_edge_screen.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.alpha_zoo.ts_regime import timeseries_regime_gate  # noqa: E402
from core.data_feeds.etf_flow_feed import (  # noqa: E402
    etf_flow_signal,
    fetch_etf_flow_html,
    parse_etf_flow_series,
)

WINDOWS = [5, 10, 20]  # ETF-flow lookback (trading days); fits the ~618-obs sample
HORIZONS = [3, 5, 10]  # BTC forward-return horizon (days)


def load_btc_daily_close() -> pd.Series:
    """BTC/USDT daily close, UTC-normalized index. Prefer the local cache parquet; fall back
    to paginated ccxt if the cache is missing or too short to cover the 2024+ ETF era."""
    p = ROOT / "data" / "ohlcv_cache" / "BTC-USDT_1d.parquet"
    if p.exists():
        df = pd.read_parquet(p)
        if {"ts", "close"}.issubset(df.columns) and len(df) > 400:
            idx = pd.to_datetime(df["ts"], unit="s", utc=True).dt.normalize()
            s = pd.Series(df["close"].astype(float).to_numpy(), index=idx)
            s = s[~s.index.duplicated(keep="last")].sort_index()
            if s.index.max() >= pd.Timestamp("2025-01-01", tz="UTC"):
                return s
    # fallback: ccxt
    import ccxt

    ex = ccxt.binance({"enableRateLimit": True})
    rows: list = []
    since = ex.parse8601("2023-06-01T00:00:00Z")
    while True:
        batch = ex.fetch_ohlcv("BTC/USDT", "1d", since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + 86_400_000
        if len(batch) < 1000:
            break
    idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True).normalize()
    s = pd.Series([float(r[4]) for r in rows], index=idx)
    return s[~s.index.duplicated(keep="last")].sort_index()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=== Spot-BTC-ETF net-flow edge screen (WS3b — new non-price source) ===")
    flows = parse_etf_flow_series(fetch_etf_flow_html())
    if flows.empty:
        print("[abort] no ETF-flow data parsed from Farside")
        return
    print(
        f"ETF flow: {len(flows)} daily obs  {flows.index[0].date()} -> {flows.index[-1].date()}  "
        f"(cum ${flows.sum():,.0f}M)"
    )
    btc = load_btc_daily_close()
    if btc.empty:
        print("[abort] no BTC daily close")
        return
    print(f"BTC:      {len(btc)} daily obs  {btc.index[0].date()} -> {btc.index[-1].date()}")

    # Daily flow on the BTC calendar: weekends/holidays = 0 net flow (correct), then a 1-day
    # causal lag (day-T flow only known after BTC's day-T close).
    era_start = flows.index.min()
    btc_era = btc[btc.index >= era_start]
    flow_daily = flows.reindex(btc_era.index).fillna(0.0)
    flow_known = flow_daily.shift(1)  # causal: known at next BTC close

    n_trials = len(WINDOWS) * len(HORIZONS)
    print(
        f"n_trials (windows x horizons) = {n_trials}; "
        f"frozen gate: |t|>=3.5 AND shuffle-z>=3.5 AND both-half sign-consistent"
    )
    print(f"overlap with BTC era: {len(btc_era)} days from {btc_era.index[0].date()}\n")

    any_pass = False
    for w in WINDOWS:
        sig = etf_flow_signal(flow_known, window=w)
        for h in HORIZONS:
            fwd = btc_era.shift(-h) / btc_era - 1.0
            r = timeseries_regime_gate(sig, fwd, n_shuffles=500, seed=0, step=h)
            verdict = "PASS" if r["passes"] else "no"
            print(
                f"win={w:2d}d h={h:2d}d | n={r['n']:4d} rho={r['rho']:+.3f} "
                f"t={r['t']:5.2f} z_null={r['z_vs_null']:5.2f} "
                f"h1={r['rho_first_half']:+.2f} h2={r['rho_second_half']:+.2f} "
                f"both={str(r['both_halves']):5s} -> {verdict}"
            )
            any_pass = any_pass or r["passes"]

    print()
    if any_pass:
        print(
            "CANDIDATE(S) cleared the frozen gate. Treat as EXPLORATORY only — small sample "
            "(~2.4y), single ETF era; extend walk-forward + re-screen OOS and confirm after-cost "
            "before ANY use. Never deploy on a single in-sample pass."
        )
    else:
        print(
            "VERDICT: NO_EDGE — spot-BTC-ETF net flow does not predict BTC forward returns past "
            "the frozen gate. The one genuinely-new non-price source tested cheaply; null stops "
            "here. (Flows are largely coincident with price, not leading, net of cost.)"
        )


if __name__ == "__main__":
    main()
