"""Replay the deterministic MachineStrategyEngine on cached OHLCV.

This is the after-cost research gate for ``SIGNAL_SOURCE=machine``. It never
talks to an exchange and never places orders.

Backtest discipline:
* decision at bar t uses rows <= t only;
* entry fills at bar t+1 open with configured slippage;
* SL/TP are scanned from entry bar onward;
* if one candle touches both SL and TP, the stop wins;
* fees and slippage are subtracted from every trade.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine
from core.strategy_readiness import (
    DEFAULT_THRESHOLDS,
    StrategyReadinessThresholds,
    evaluate_records,
)


def load_ohlcv(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    df = df.copy()
    if "timestamp" in df.columns and "ts" not in df.columns:
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True).astype("int64") // 1_000_000_000
    required = ["ts", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")
    return df[required].sort_values("ts").reset_index(drop=True)


def symbol_from_path(path: str, timeframe: str) -> str:
    name = os.path.basename(path)
    for suffix in (f"_{timeframe}.parquet", f"_{timeframe}.csv", ".parquet", ".csv"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    base = name.split("-")[0].split("_")[0].upper()
    return f"{base}/USDT:USDT"


def ret_for(side: str, entry: float, exit_px: float) -> float:
    if side == "buy":
        return exit_px / entry - 1.0
    return entry / exit_px - 1.0


def bracket_outcome(
    df: pd.DataFrame,
    entry_idx: int,
    side: str,
    sl_pct: float,
    tp_pct: float,
    max_hold: int,
    *,
    fee_bps_side: float,
    entry_slip_bps: float,
    tp_slip_bps: float,
    stop_slip_bps: float,
) -> tuple[float, str, int]:
    open_px = float(df["open"].iloc[entry_idx])
    if side == "buy":
        entry = open_px * (1.0 + entry_slip_bps / 10_000.0)
        sl = entry * (1.0 - sl_pct)
        tp = entry * (1.0 + tp_pct)
    else:
        entry = open_px * (1.0 - entry_slip_bps / 10_000.0)
        sl = entry * (1.0 + sl_pct)
        tp = entry * (1.0 - tp_pct)

    end = min(entry_idx + max_hold, len(df) - 1)
    fee_rt = 2.0 * fee_bps_side / 10_000.0
    for j in range(entry_idx, end + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        hit_sl = lo <= sl if side == "buy" else hi >= sl
        hit_tp = hi >= tp if side == "buy" else lo <= tp
        if not (hit_sl or hit_tp):
            continue
        if hit_sl:
            exit_px = sl * (1.0 - stop_slip_bps / 10_000.0) if side == "buy" else sl * (
                1.0 + stop_slip_bps / 10_000.0
            )
            return ret_for(side, entry, exit_px) - fee_rt, "stop_loss", j - entry_idx
        exit_px = tp * (1.0 - tp_slip_bps / 10_000.0) if side == "buy" else tp * (
            1.0 + tp_slip_bps / 10_000.0
        )
        return ret_for(side, entry, exit_px) - fee_rt, "take_profit", j - entry_idx

    close_px = float(df["close"].iloc[end])
    exit_px = close_px * (1.0 - tp_slip_bps / 10_000.0) if side == "buy" else close_px * (
        1.0 + tp_slip_bps / 10_000.0
    )
    return ret_for(side, entry, exit_px) - fee_rt, "time_stop", end - entry_idx


def run_one(df: pd.DataFrame, symbol: str, engine: MachineStrategyEngine, args) -> list[dict]:
    trades: list[dict] = []
    next_free_idx = max(40, int(args.min_bars))
    max_i = len(df) - 2
    i = next_free_idx
    signals = engine.detector_frame(df) if not args.exact else None
    atr_pct = engine.atr_pct_series(df) if not args.exact else None
    while i <= max_i:
        if args.exact:
            window = df.iloc[: i + 1]
            decision = engine.score_frame(symbol, window, market_type=args.market_type)
            detectors = dict(decision.diagnostics.get("detectors") or {})
        else:
            assert signals is not None and atr_pct is not None
            detectors = {name: int(signals[name].iloc[i]) for name in signals.columns}
            decision = engine.decision_from_detector_values(
                symbol,
                detectors,
                atr_pct=float(atr_pct.iloc[i]),
                market_type=args.market_type,
            )
        if decision.action != "OPEN":
            i += int(args.step)
            continue
        ret, outcome, bars = bracket_outcome(
            df,
            i + 1,
            decision.side,
            decision.stop_loss_pct,
            decision.take_profit_pct,
            decision.time_stop_bars,
            fee_bps_side=args.fee_bps_side,
            entry_slip_bps=args.entry_slip_bps,
            tp_slip_bps=args.tp_slip_bps,
            stop_slip_bps=args.stop_slip_bps,
        )
        trades.append(
            {
                "symbol": symbol,
                "ts": int(df["ts"].iloc[i + 1]),
                "side": decision.side,
                "ret": ret,
                "outcome": outcome,
                "bars": bars,
                "score": decision.score,
                "reasons": list(decision.reasons),
                "detectors": {k: int(v) for k, v in detectors.items() if int(v) != 0},
                "detector_votes": {k: int(v) for k, v in detectors.items()},
            }
        )
        i = i + 1 + max(1, bars)
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    rets = np.array([float(t["ret"]) for t in trades], dtype=float)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    return {
        "n": int(len(rets)),
        "win_rate": float((rets > 0).mean()),
        "avg_ret": float(rets.mean()),
        "sum_ret": float(rets.sum()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else None,
        "median_score": float(np.median([t["score"] for t in trades])),
    }


def fold_summaries(trades: list[dict]) -> dict:
    if len(trades) < 30:
        return {}
    ts = np.array([t["ts"] for t in trades], dtype=float)
    q1, q2 = np.quantile(ts, [1 / 3, 2 / 3])
    folds = {"fold1": [], "fold2": [], "fold3": []}
    for t in trades:
        key = "fold1" if t["ts"] <= q1 else ("fold2" if t["ts"] <= q2 else "fold3")
        folds[key].append(t)
    return {k: summarize(v) for k, v in folds.items()}


def detector_summaries(trades: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {}
    for t in trades:
        for name, value in (t.get("detectors") or {}).items():
            side = "bull" if int(value) > 0 else "bear"
            buckets.setdefault(f"{name}:{side}", []).append(t)
            buckets.setdefault(name, []).append(t)
    return {k: summarize(v) for k, v in sorted(buckets.items())}


def files_for(args) -> list[str]:
    pats = [
        os.path.join(args.cache_dir, f"*_{args.timeframe}.parquet"),
        os.path.join(args.cache_dir, f"*_{args.timeframe}.csv"),
    ]
    files: list[str] = []
    for pat in pats:
        files.extend(glob.glob(pat))
    return sorted(files)[: args.max_files if args.max_files > 0 else None]


def _float_grid(text: str) -> list[float]:
    values = []
    for raw in str(text or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        values.append(float(raw))
    return values


def _readiness_for_trades(trades: list[dict], args, label: str) -> dict:
    thresholds = StrategyReadinessThresholds(
        min_trades=int(args.promotion_min_trades),
        min_profit_factor=float(args.promotion_min_profit_factor),
        min_avg_pnl=0.0,
        min_win_rate=DEFAULT_THRESHOLDS.min_win_rate,
        min_positive_folds=DEFAULT_THRESHOLDS.min_positive_folds,
        min_fold_trades=DEFAULT_THRESHOLDS.min_fold_trades,
        max_drawdown_to_gross_profit=DEFAULT_THRESHOLDS.max_drawdown_to_gross_profit,
        folds=DEFAULT_THRESHOLDS.folds,
        lookback_days=DEFAULT_THRESHOLDS.lookback_days,
    )
    records = [
        {
            "ts": t.get("ts"),
            "pnl": t.get("ret"),
            "strategy": "machine_replay",
            "detectors": t.get("detectors") or {},
        }
        for t in trades
    ]
    return evaluate_records(records, thresholds=thresholds, name=label)


def _detector_params(args) -> dict:
    return {
        "ema_rsi": {
            "fast_ema": int(args.ema_rsi_fast_ema),
            "slow_ema": int(args.ema_rsi_slow_ema),
            "trend_ema": int(args.ema_rsi_trend_ema),
            "rsi_period": int(args.ema_rsi_rsi_period),
            "rsi_signal_ema": int(args.ema_rsi_rsi_signal_ema),
            "trend_slope_bars": int(args.ema_rsi_trend_slope_bars),
            "min_trend_slope": float(args.ema_rsi_min_trend_slope),
            "min_ema_spread_pct": float(args.ema_rsi_min_ema_spread_pct),
            "max_ema_spread_pct": float(args.ema_rsi_max_ema_spread_pct),
            "long_rsi_floor": float(args.ema_rsi_long_rsi_floor),
            "long_rsi_ceiling": float(args.ema_rsi_long_rsi_ceiling),
            "short_rsi_floor": float(args.ema_rsi_short_rsi_floor),
            "short_rsi_ceiling": float(args.ema_rsi_short_rsi_ceiling),
            "hold_bars": int(args.ema_rsi_hold_bars),
        }
    }


def run_grid(args) -> dict:
    results = {}
    files = files_for(args)
    detector_params = _detector_params(args)
    for min_score in _float_grid(args.min_score_grid):
        for rr in _float_grid(args.rr_grid):
            for atr_stop_mult in _float_grid(args.atr_stop_mult_grid):
                label = f"score={min_score:g}|rr={rr:g}|atr={atr_stop_mult:g}"
                cfg = MachineStrategyConfig(
                    min_score=min_score,
                    rr=rr,
                    atr_stop_mult=atr_stop_mult,
                    min_stop_pct=args.min_stop_pct,
                    max_stop_pct=args.max_stop_pct,
                    scalp_time_stop_bars=args.time_stop_bars,
                    detector_params=detector_params,
                    confirmation_detectors=tuple(
                        s.strip()
                        for s in str(args.confirmation_detectors or "").split(",")
                        if s.strip()
                    ),
                    unconfirmed_min_score=float(args.unconfirmed_min_score),
                )
                engine = MachineStrategyEngine(cfg)
                all_trades: list[dict] = []
                for path in files:
                    try:
                        df = load_ohlcv(path)
                    except Exception as e:
                        print(f"skip {path}: {e}")
                        continue
                    if args.max_bars > 0:
                        df = df.tail(args.max_bars).reset_index(drop=True)
                    all_trades.extend(
                        run_one(df, symbol_from_path(path, args.timeframe), engine, args)
                    )
                results[label] = {
                    "params": {
                        "min_score": min_score,
                        "rr": rr,
                        "atr_stop_mult": atr_stop_mult,
                        "confirmation_detectors": [
                            s.strip()
                            for s in str(args.confirmation_detectors or "").split(",")
                            if s.strip()
                        ],
                        "unconfirmed_min_score": float(args.unconfirmed_min_score),
                    },
                    "summary": summarize(all_trades),
                    "folds": fold_summaries(all_trades),
                    "by_side": {
                        "buy": summarize([t for t in all_trades if t["side"] == "buy"]),
                        "sell": summarize([t for t in all_trades if t["side"] == "sell"]),
                    },
                    "by_detector": detector_summaries(all_trades),
                    "readiness": _readiness_for_trades(all_trades, args, label),
                    "sample_reasons": sorted(
                        {r for t in all_trades[:100] for r in t.get("reasons", [])}
                    )[:20],
                }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "exact_prefix" if args.exact else "fast_precomputed",
        "files": files,
        "results": results,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cache-dir", default="data/ohlcv_cache")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--market-type", default="futures", choices=["spot", "futures"])
    p.add_argument("--min-score-grid", default="0.25,0.34,0.45")
    p.add_argument("--rr", type=float, default=1.8)
    p.add_argument("--rr-grid", default="")
    p.add_argument("--atr-stop-mult", type=float, default=1.6)
    p.add_argument("--atr-stop-mult-grid", default="")
    p.add_argument("--min-stop-pct", type=float, default=0.0035)
    p.add_argument("--max-stop-pct", type=float, default=0.025)
    p.add_argument("--time-stop-bars", type=int, default=12)
    p.add_argument("--fee-bps-side", type=float, default=6.0)
    p.add_argument("--entry-slip-bps", type=float, default=5.0)
    p.add_argument("--tp-slip-bps", type=float, default=5.0)
    p.add_argument("--stop-slip-bps", type=float, default=10.0)
    p.add_argument("--min-bars", type=int, default=80)
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--max-files", type=int, default=0)
    p.add_argument("--max-bars", type=int, default=0)
    p.add_argument("--exact", action="store_true")
    p.add_argument("--promotion-min-trades", type=int, default=100)
    p.add_argument("--promotion-min-profit-factor", type=float, default=1.20)
    p.add_argument("--confirmation-detectors", default="ema_rsi")
    p.add_argument("--unconfirmed-min-score", type=float, default=0.40)
    p.add_argument("--ema-rsi-fast-ema", type=int, default=9)
    p.add_argument("--ema-rsi-slow-ema", type=int, default=21)
    p.add_argument("--ema-rsi-trend-ema", type=int, default=50)
    p.add_argument("--ema-rsi-rsi-period", type=int, default=14)
    p.add_argument("--ema-rsi-rsi-signal-ema", type=int, default=5)
    p.add_argument("--ema-rsi-trend-slope-bars", type=int, default=4)
    p.add_argument("--ema-rsi-min-trend-slope", type=float, default=0.00005)
    p.add_argument("--ema-rsi-min-ema-spread-pct", type=float, default=0.0002)
    p.add_argument("--ema-rsi-max-ema-spread-pct", type=float, default=0.035)
    p.add_argument("--ema-rsi-long-rsi-floor", type=float, default=45.0)
    p.add_argument("--ema-rsi-long-rsi-ceiling", type=float, default=70.0)
    p.add_argument("--ema-rsi-short-rsi-floor", type=float, default=30.0)
    p.add_argument("--ema-rsi-short-rsi-ceiling", type=float, default=55.0)
    p.add_argument("--ema-rsi-hold-bars", type=int, default=3)
    p.add_argument("--output", default="")
    args = p.parse_args()
    if not args.rr_grid:
        args.rr_grid = str(args.rr)
    if not args.atr_stop_mult_grid:
        args.atr_stop_mult_grid = str(args.atr_stop_mult)

    report = run_grid(args)
    for label, payload in report["results"].items():
        s = payload["summary"]
        print(
            f"{label} n={s.get('n', 0)} "
            f"WR={s.get('win_rate', 0.0) * 100:.1f}% "
            f"EV={s.get('avg_ret', 0.0) * 100:+.3f}% "
            f"sum={s.get('sum_ret', 0.0) * 100:+.1f}% "
            f"PF={s.get('profit_factor')} "
            f"gate={payload['readiness']['verdict']}"
        )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
