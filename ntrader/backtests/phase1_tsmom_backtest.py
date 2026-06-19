"""Phase 1 harness: Strategy A (NT) and the Strategy B reference model, side by side.

Usage:
  python ntrader/backtests/phase1_tsmom_backtest.py --source synth   [--parity]
  python ntrader/backtests/phase1_tsmom_backtest.py --source parquet [--parity]

Strategy A = NautilusTrader port of the LIVE discrete signal (core/tsmom_signal.py),
bar-for-bar verified by ntrader/tests/test_tsmom_port_parity.py. Strategy B = the
research validation model (scripts/tsmom_validation_backtest.py: continuous
vol-weighted target weight, 5-day rebalance), recomputed here on the SAME bars for
aggregate context. A full NautilusTrader port of B is the documented next increment.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from ntrader.backtests._run import run_strategy_a  # noqa: E402
from ntrader.data_ingest.load_parquet_bars import load_frame  # noqa: E402
from ntrader.strategies.tsmom_reference import discrete_events  # noqa: E402

MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
LOOKBACK, HOLD, TARGET, MAXLEV, COST, ANN = 28, 5, 0.60, 1.0, 0.0015, 365.0


def _sharpe(r):
    r = r.dropna()
    return 0.0 if len(r) < 2 or r.std() == 0 else float(r.mean() / r.std() * np.sqrt(ANN))


def _cagr(eq):
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return 0.0
    yrs = len(eq) / ANN
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 else 0.0


def _maxdd(eq):
    peak = eq.cummax()
    return float(((eq - peak) / peak).min())


def strategy_b(df: pd.DataFrame) -> dict:
    """Port of scripts/tsmom_validation_backtest.py tsmom_positions/run_coin."""
    df = df.sort_values("ts")
    close = df["close"].reset_index(drop=True)
    mom = close > close.shift(LOOKBACK)
    realized = close.pct_change().rolling(20).std() * np.sqrt(ANN)
    volscale = (TARGET / realized).clip(upper=MAXLEV).fillna(0.0)
    raw = (mom.astype(float) * volscale).fillna(0.0)
    w = raw.copy()
    last = 0.0
    for i in range(len(w)):
        if i % HOLD == 0:
            last = raw.iloc[i]
        w.iloc[i] = last
    w = w.clip(lower=0.0, upper=MAXLEV)
    ret = close.pct_change().fillna(0.0)
    turn = w.diff().abs().fillna(w.abs())
    strat = w.shift(1).fillna(0.0) * ret - turn * COST
    eq = (1 + strat).cumprod()
    bh = (1 + ret).cumprod()
    return {
        "sharpe": round(_sharpe(strat), 3), "cagr": round(_cagr(eq) * 100, 1),
        "maxdd": round(_maxdd(eq) * 100, 1), "bh_sharpe": round(_sharpe(ret), 3),
        "bh_maxdd": round(_maxdd(bh) * 100, 1), "exposure": round(float((w > 0).mean()) * 100, 1),
        "beats_bh": bool(_sharpe(strat) > _sharpe(ret)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="synth", choices=["synth", "parquet"])
    ap.add_argument("--parity", action="store_true")
    a = ap.parse_args()
    print(f"=== Phase 1 harness (source={a.source}) ===")
    print("Strategy A  (NT port of the LIVE discrete signal; long-only, 8% stop, lev 1):")
    for b in MAJORS:
        try:
            r = run_strategy_a(base=b, source=a.source, enable_stop=True)
        except FileNotFoundError:
            print(f"  {b}: data missing for source={a.source}")
            continue
        opens = sum(1 for d in r["decision_bars"] if d[1] == "OPEN")
        ret = (r["final_balance"] / r["start_equity"] - 1) * 100 if r.get("start_equity") else float("nan")
        line = (f"  {b}: bars={r['n_bars']} opens={opens} fills={r['fills']} "
                f"stops={r['stop_orders']} ret={ret:+.2f}%")
        if a.parity:
            match = r["decision_bars"] == discrete_events(r["closes"], LOOKBACK)
            line += f" | parity={'100%' if match else 'MISMATCH'}"
        print(line)
    print("Strategy B  (research validation model: continuous vol-weight, 5d rebalance):")
    for b in MAJORS:
        try:
            df = load_frame(b, source=a.source)
        except FileNotFoundError:
            print(f"  {b}: data missing for source={a.source}")
            continue
        m = strategy_b(df)
        print(f"  {b}: sharpe={m['sharpe']:+.2f} (B&H {m['bh_sharpe']:+.2f}) "
              f"cagr={m['cagr']:+.1f}% dd={m['maxdd']:.1f}% (B&H {m['bh_maxdd']:.1f}%) "
              f"exp={m['exposure']:.0f}% beats_bh={m['beats_bh']}")


if __name__ == "__main__":
    main()
