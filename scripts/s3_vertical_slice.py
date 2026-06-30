"""Phase 1 — S3 TS-momentum ensemble vertical slice (PROVE THE PIPELINE).

Runs ONE single-symbol, single-venue, deterministic strategy end-to-end:

    register -> existing Binance daily candles -> sim (resolved outcomes)
    -> walk-forward OOS -> accept() verdict -> EvidenceRegistry record.

A correct NO_GO is success; the deliverable is pipeline correctness, NOT alpha.

Sim discipline (reuses scalp_replay conventions, daily cadence):
  * decision on CLOSED bar t (ensemble_momentum_state over closed closes only);
  * entry fills at bar t+1 OPEN;
  * exit is hold-to-flip — held until the ensemble flips non-long (exit next
    open) OR a wide disaster stop is wicked intrabar (SL-first, pessimistic);
  * after-cost net_pnl = price return - round_trip_cost - funding (longs pay).
No network: reads data/ohlcv_cache/<SYM>-USDT_1d.parquet only. PAPER/sim.

CLI:  venv/Scripts/python.exe scripts/s3_vertical_slice.py [SYMBOL] [VENUE]
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cost_model import round_trip_cost  # noqa: E402
from core.decision.promotion_loop import (  # noqa: E402
    ACTIVE_STRATEGIES_PATH,
    accept,
    register_evidence,
)
from core.tsmom_signal import DEFAULT_S3_LOOKBACKS  # noqa: E402
from core.walk_forward import WalkForward  # noqa: E402

CACHE = "data/ohlcv_cache"
# Wide disaster stop only (capital-preservation tsmom exit is the momentum flip,
# not a tight scalp bracket). 8% mirrors the tsmom OPEN action's sl_pct.
DEFAULT_SL_PCT = 0.08
# Assumed per-8h funding a long pays (3 settlements/day). Conservative small +ve.
DEFAULT_FUNDING_8H = 0.0001


def _load_daily(symbol: str, cache: str = CACHE) -> pd.DataFrame | None:
    p = os.path.join(cache, f"{symbol}-USDT_1d.parquet")
    if not os.path.exists(p):
        return None
    return pd.read_parquet(p).sort_values("ts").reset_index(drop=True)


def _is_long_array(closes: np.ndarray, lookbacks) -> np.ndarray:
    """Vectorized all-horizons-agree long mask, aligned with
    ``ensemble_momentum_state`` (positive AND enough history on every horizon)."""
    c = np.asarray(closes, dtype=float)
    n = c.size
    mats = []
    for lb in lookbacks:
        lb = int(lb)
        m = np.full(n, np.nan)
        if n > lb:
            m[lb:] = c[lb:] / c[:-lb] - 1.0
        mats.append(m)
    M = np.vstack(mats)
    all_pos = np.all(M > 0.0, axis=0)
    all_valid = ~np.any(np.isnan(M), axis=0)
    return all_pos & all_valid


def simulate_s3(
    df: pd.DataFrame,
    *,
    lookbacks=DEFAULT_S3_LOOKBACKS,
    sl_pct: float = DEFAULT_SL_PCT,
    venue: str = "binance",
    funding_8h: float = DEFAULT_FUNDING_8H,
    slip_mult: float = 1.0,
    tier_mult: float = 1.0,
) -> list[dict]:
    """Replay the long-only S3 ensemble over daily bars; return RESOLVED trades.

    Each trade dict carries an after-cost ``net_pnl`` and ``label_status=RESOLVED``
    (never a projected-at-TP value). Deterministic for fixed inputs.
    """
    closes = df["close"].to_numpy(float)
    lows = df["low"].to_numpy(float)  # long-only disaster stop checks the low only
    opens = df["open"].to_numpy(float)
    n = len(df)
    long_mask = _is_long_array(closes, lookbacks)
    rt = round_trip_cost(venue, slip_mult=slip_mult, tier_mult=tier_mult)

    trades: list[dict] = []
    start = max(int(x) for x in lookbacks) + 1
    i = start
    while i < n - 1:
        if not long_mask[i]:  # decision on closed bar i
            i += 1
            continue
        ei = i + 1  # fill next bar open
        fill = opens[ei]
        sl = fill * (1.0 - sl_pct)
        exit_idx = exit_px = None
        reason = None
        j = ei
        while j < n:
            if lows[j] <= sl:  # disaster stop, SL-first/pessimistic
                exit_idx, exit_px, reason = j, sl, "stop_loss"
                break
            if not long_mask[j]:  # ensemble flipped non-long on closed bar j
                if j + 1 < n:
                    exit_idx, exit_px, reason = j + 1, opens[j + 1], "momentum_flip"
                else:
                    exit_idx, exit_px, reason = j, closes[j], "eod"
                break
            j += 1
        if exit_idx is None:
            exit_idx, exit_px, reason = n - 1, closes[n - 1], "eod"

        bars_held = int(exit_idx - ei)
        gross = float(exit_px) / float(fill) - 1.0
        funding = funding_8h * 3.0 * bars_held  # long pays, 3 settlements/day
        net = gross - rt - funding
        trades.append({
            "entry_idx": int(ei),
            "exit_idx": int(exit_idx),
            "exit_reason": reason,
            "bars_held": bars_held,
            "gross_pnl": float(gross),
            "fees_slippage": float(rt),
            "funding": float(funding),
            "net_pnl": float(net),
            "label_status": "RESOLVED",
        })
        i = exit_idx + 1
    return trades


def run_s3_slice(
    *,
    symbol: str = "BTC",
    venue: str = "binance",
    lookbacks=DEFAULT_S3_LOOKBACKS,
    sl_pct: float = DEFAULT_SL_PCT,
    n_splits: int = 4,
    registry_path=ACTIVE_STRATEGIES_PATH,
    cache: str = CACHE,
) -> dict:
    """End-to-end register -> resolve -> accept loop (no manual steps)."""
    df = _load_daily(symbol, cache)
    if df is None or len(df) < max(int(x) for x in lookbacks) + 30:
        raise FileNotFoundError(
            f"insufficient daily cache for {symbol} at {cache}/{symbol}-USDT_1d.parquet"
        )

    trades = simulate_s3(df, lookbacks=lookbacks, sl_pct=sl_pct, venue=venue)
    rets = [t["net_pnl"] for t in trades]
    arr = np.asarray(rets, dtype=float)

    # walk-forward OOS folds over the resolved trade sequence
    fold_returns: list[list[float]] = []
    if arr.size >= n_splits + 1:
        for _train, test in WalkForward(n_splits=n_splits, embargo_bars=0).split(arr.size):
            fold_returns.append(arr[test].tolist())

    verdict = accept(
        returns=rets,
        strategy_class="swing",
        fold_returns=fold_returns,
        n_trials=1,
        trial_budget=1,
    )

    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    oos_metrics = {
        "n_trades": int(arr.size),
        "win_rate": float((arr > 0).mean()) if arr.size else 0.0,
        "ev": float(arr.mean()) if arr.size else 0.0,
        "sum_net": float(arr.sum()) if arr.size else 0.0,
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "accept": bool(verdict["accept"]),
    }

    promotion_status = "PROMOTED" if verdict["accept"] else "NO_EDGE"
    failure_reason = None
    if not verdict["accept"]:
        failed = [k for k, g in verdict["sub_gates"].items() if not g["pass"]]
        failure_reason = "failed sub-gates: " + ",".join(failed)

    evidence = register_evidence(
        "S3",
        hypothesis=(
            "Long-only 28/14/7d multi-horizon momentum ensemble (all horizons "
            "agree) preserves capital on a liquid major; held to momentum flip."
        ),
        rules={
            "type": "tsmom_ensemble",
            "lookbacks_days": list(int(x) for x in lookbacks),
            "long_only": True,
            "exit": "hold_to_flip_or_disaster_stop",
            "sl_pct": sl_pct,
            "timeframe": "1d",
            "symbol": symbol,
        },
        data_sources=[f"ohlcv_cache:{symbol}-USDT_1d", f"venue:{venue}"],
        data_depth=int(len(df)),
        cost_model={
            "round_trip_cost": round_trip_cost(venue),
            "funding_8h": DEFAULT_FUNDING_8H,
            "venue": venue,
        },
        cost_calibration_status="model_default",
        test_window={"bars": int(len(df)), "timeframe": "1d", "n_folds": len(fold_returns)},
        oos_metrics=oos_metrics,
        honest_n=int(arr.size),
        promotion_status=promotion_status,
        failure_reason=failure_reason,
        universe_construction_method="single_symbol_vertical_slice",
        path=registry_path,
    )

    return {
        "symbol": symbol,
        "venue": venue,
        "n_trades": int(arr.size),
        "trades": trades,
        "verdict": verdict,
        "evidence": evidence,
        "oos_metrics": oos_metrics,
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    symbol = argv[0] if len(argv) > 0 else "BTC"
    venue = argv[1] if len(argv) > 1 else "binance"
    rep = run_s3_slice(symbol=symbol, venue=venue)
    v = rep["verdict"]
    print("=" * 68)
    print(f"S3 vertical slice — {symbol} @ {venue}")
    print("=" * 68)
    print(f"resolved trades : {rep['n_trades']}")
    m = rep["oos_metrics"]
    print(f"WR={m['win_rate'] * 100:5.1f}%  EV={m['ev'] * 100:+.3f}%  "
          f"sum_net={m['sum_net'] * 100:+.1f}%")
    print(f"accept()        : {'GO' if v['accept'] else 'NO_GO'}")
    for name, g in v["sub_gates"].items():
        print(f"  {'PASS' if g['pass'] else 'fail'}  {name}")
    print(f"evidence status : {rep['evidence']['promotion_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
