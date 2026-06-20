"""Stablecoin-supply edge screen (WS3b).

Question: does aggregate stablecoin-supply GROWTH (a market-wide liquidity-regime variable)
predict BTC forward returns? Tested through the time-series regime gate — |t| >= 3.5 on the
rank-correlation AND a label-shuffle null exceeded by >= 3.5 sigma AND sign-consistent in BOTH
halves (OOS stability). Honest prior (repo study): NO_EDGE / a slow overlay, not a tradeable edge.

Run: python scripts/run_stablecoin_edge_screen.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import pandas as pd  # noqa: E402

from core.alpha_zoo.ts_regime import timeseries_regime_gate  # noqa: E402
from core.data_feeds.stablecoin_supply_feed import (  # noqa: E402
    fetch_stablecoin_supply_records,
    parse_supply_series,
    supply_growth_signal,
)

WINDOWS = [14, 30, 60]  # supply-growth lookback (days)
HORIZONS = [7, 14, 30]  # BTC forward-return horizon (days)


def load_btc_daily_close() -> pd.Series:
    """BTC/USDT spot daily close (2018 -> now) via paginated ccxt; index = UTC date."""
    import ccxt

    ex = ccxt.binance({"enableRateLimit": True})
    rows: list = []
    since = ex.parse8601("2018-01-01T00:00:00Z")
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

    print("=== Stablecoin-supply edge screen (WS3b) ===")
    supply = parse_supply_series(fetch_stablecoin_supply_records())
    if supply.empty:
        print("[abort] no stablecoin supply data")
        return
    print(f"supply: {len(supply)} daily obs  {supply.index[0].date()} -> {supply.index[-1].date()}")
    btc = load_btc_daily_close()
    if btc.empty:
        print("[abort] no BTC daily close")
        return
    print(f"BTC:    {len(btc)} daily obs  {btc.index[0].date()} -> {btc.index[-1].date()}")

    n_trials = len(WINDOWS) * len(HORIZONS)
    print(
        f"n_trials (windows x horizons) = {n_trials}; "
        f"frozen gate: |t|>=3.5 AND shuffle-z>=3.5 AND both-half sign-consistent\n"
    )

    any_pass = False
    for w in WINDOWS:
        sig = supply_growth_signal(supply, window=w)
        for h in HORIZONS:
            fwd = btc.shift(-h) / btc - 1.0
            # step=h -> non-overlapping forward returns (overlap inflates t/null otherwise)
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
            "CANDIDATE(S) cleared the frozen gate. Treat as exploratory — extend walk-forward "
            "and re-screen OOS before ANY use; never deploy on a single in-sample pass."
        )
    else:
        print(
            "VERDICT: NO_EDGE — stablecoin-supply growth does not predict BTC forward returns "
            "past the frozen gate. Matches the repo-study prior (slow liquidity overlay, not a "
            "tradeable edge). Cost was ~2 modules + this script; the null stops cheaply."
        )


if __name__ == "__main__":
    main()
