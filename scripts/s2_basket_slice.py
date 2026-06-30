"""Phase 2 — S2 cross-sectional long-short basket vertical slice (research/PAPER).

A ranked, vol-weighted, funding-adjusted market-neutral basket:

    align per-symbol daily candles -> point-in-time universe (delisted names
    kept until their terminal price bar) -> z-scored momentum cross-section ->
    long top-q / short bottom-q, vol-weighted -> decide at closed bar t, fill at
    t+1 OPEN, hold to next rebalance -> after-cost net basket return per period
    -> walk-forward OOS -> accept() verdict -> EvidenceRegistry S2 record.

A correct NO_GO is success; the deliverable is pipeline correctness (survivorship
control + honest fill convention + after-cost gate), NOT alpha.

SURVIVORSHIP (the #1 backtest inflator): the universe is reconstructed at every
rebalance from the prices known AT THAT BAR. A since-delisted perp is INCLUDED in
the folds spanning its life and realizes its terminal price path on exit; it is
not silently dropped. ``universe_construction_method`` records this.

FEATURE GATING: only deep-history-backtestable features (price momentum, funding)
are used. Forward-collect-required microstructure (OI-change, taker imbalance) is
listed in ``FORWARD_COLLECT_FEATURES`` and gated OUT until enough forward data
accrues — using it now would backfill a feature that did not exist historically.

Scope: BTC/ETH/SOL/BNB/XRP daily (the names with funding backfill + deep history);
breadth claims wait on ccxt funding-rate backfill. Live basket is INFEASIBLE at
$420 (min-notional on 2-leg+ market-neutral exposure exceeds the account), so this
is research/PAPER only. No network: reads data/ohlcv_cache/<SYM>-USDT_1d.parquet.

CLI:  venv/Scripts/python.exe scripts/s2_basket_slice.py
"""
from __future__ import annotations

import math
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
from core.walk_forward import WalkForward  # noqa: E402

CACHE = "data/ohlcv_cache"
DEFAULT_BASKET = ("BTC", "ETH", "SOL", "BNB", "XRP")
DEFAULT_LOOKBACK = 7          # cross-sectional momentum horizon (bars)
DEFAULT_VOL_LOOKBACK = 20     # realized-vol window for inverse-vol weighting
DEFAULT_FUNDING_8H = 0.0001   # conservative modeled funding drag per 8h settlement
# Microstructure features that require FORWARD collection (did not exist deep in
# history) — gated OUT of the backtest to avoid backfilling a non-existent signal.
FORWARD_COLLECT_FEATURES = ("oi_change", "taker_imbalance")
UNIVERSE_METHOD = "point_in_time_with_delisted_terminal_paths"
LIVE_FEASIBILITY = "INFEASIBLE-AT-$420"


def _load_daily(symbol: str, cache: str = CACHE) -> pd.DataFrame | None:
    p = os.path.join(cache, f"{symbol}-USDT_1d.parquet")
    if not os.path.exists(p):
        return None
    return pd.read_parquet(p).sort_values("ts").reset_index(drop=True)


def load_panel(symbols=DEFAULT_BASKET, cache: str = CACHE):
    """Align per-symbol daily candles on a common ts index.

    Returns (closes_df, opens_df) with rows = bars (sorted ts) and columns =
    symbols. Missing cells are NaN — which is exactly the point-in-time signal
    for "not yet listed" (leading NaN) or "delisted" (trailing NaN).
    """
    close_cols, open_cols = {}, {}
    for s in symbols:
        df = _load_daily(s, cache)
        if df is None:
            continue
        idx = pd.to_datetime(df["ts"], unit="s")
        close_cols[s] = pd.Series(df["close"].to_numpy(float), index=idx)
        open_cols[s] = pd.Series(df["open"].to_numpy(float), index=idx)
    if not close_cols:
        raise FileNotFoundError(f"no daily cache for any of {symbols} at {cache}")
    closes = pd.DataFrame(close_cols).sort_index().reset_index(drop=True)
    opens = pd.DataFrame(open_cols).sort_index().reset_index(drop=True)
    return closes, opens


def point_in_time_universe(closes: pd.DataFrame, t: int, *, lookback: int) -> list[str]:
    """Symbols tradeable AS OF bar ``t`` — survivorship-bias-free.

    A symbol qualifies iff it has a non-NaN close at ``t`` (listed & not yet
    delisted) AND a non-NaN close ``lookback`` bars earlier (enough history to
    score). Delisted names drop out automatically once their price path ends; no
    look-ahead resurrection.
    """
    if t < lookback or t >= len(closes):
        return []
    now = closes.iloc[t]
    past = closes.iloc[t - lookback]
    return sorted(
        s for s in closes.columns
        if not math.isnan(now[s]) and not math.isnan(past[s])
    )


def _zscore(values: np.ndarray) -> np.ndarray:
    sd = values.std(ddof=0)
    if sd <= 0 or not np.isfinite(sd):
        return np.zeros_like(values)
    return (values - values.mean()) / sd


def _realized_vol(closes: pd.DataFrame, sym: str, t: int, win: int) -> float:
    lo = max(0, t - win)
    seg = closes[sym].iloc[lo:t + 1].to_numpy(float)
    seg = seg[~np.isnan(seg)]
    if seg.size < 3:
        return float("nan")
    rets = np.diff(seg) / seg[:-1]
    v = float(np.std(rets, ddof=0))
    return v if v > 0 else float("nan")


def simulate_basket(
    closes: pd.DataFrame,
    opens: pd.DataFrame,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
    rebalance_every: int = 1,
    top_q: float = 0.2,
    bottom_q: float = 0.2,
    venue: str = "binance",
    funding_8h: float = DEFAULT_FUNDING_8H,
    slip_mult: float = 1.0,
    tier_mult: float = 1.0,
) -> list[dict]:
    """Replay the market-neutral basket; return one RESOLVED record per rebalance.

    Decision on CLOSED bar ``t`` (z-scored momentum cross-section), entry fill at
    bar ``t+1`` OPEN, held to the next rebalance bar (exit at that bar's t+1 open,
    or terminal price if a leg delists mid-hold). Each period's ``net_pnl`` is the
    after-cost, funding-adjusted, vol-weighted basket return. Deterministic.
    """
    n = len(closes)
    rt = round_trip_cost(venue, slip_mult=slip_mult, tier_mult=tier_mult)
    start = max(lookback, vol_lookback) + 1
    rebal = list(range(start, n - 1, max(1, int(rebalance_every))))
    periods: list[dict] = []

    for ri, t in enumerate(rebal):
        uni = point_in_time_universe(closes, t, lookback=lookback)
        if len(uni) < 2:
            continue
        # cross-sectional momentum feature (price; deep-history-backtestable)
        mom = np.array([
            closes.iloc[t][s] / closes.iloc[t - lookback][s] - 1.0 for s in uni
        ], dtype=float)
        z = _zscore(mom)
        order = np.argsort(z)  # ascending
        n_long = max(1, int(math.ceil(top_q * len(uni))))
        n_short = max(1, int(math.ceil(bottom_q * len(uni))))
        long_syms = [uni[i] for i in order[::-1][:n_long]]
        short_syms = [uni[i] for i in order[:n_short]]

        # inverse-vol weights, dollar-neutral (each side gross 0.5)
        def _side_weights(side_syms):
            inv = {}
            for s in side_syms:
                v = _realized_vol(closes, s, t, vol_lookback)
                inv[s] = (1.0 / v) if (v and np.isfinite(v)) else 1.0
            tot = sum(inv.values()) or 1.0
            return {s: 0.5 * inv[s] / tot for s in side_syms}

        lw = _side_weights(long_syms)
        sw = _side_weights(short_syms)

        ei = t + 1
        t_next = rebal[ri + 1] if ri + 1 < len(rebal) else (n - 1)
        xi = min(t_next + 1, n - 1)
        bars_held = max(1, xi - ei)
        funding_period = funding_8h * 3.0 * bars_held  # per-8h drag, conservative

        legs, net = [], 0.0
        for s, w, side in (
            [(s, lw[s], "long") for s in long_syms]
            + [(s, sw[s], "short") for s in short_syms]
        ):
            entry_px = opens.iloc[ei][s]
            if math.isnan(entry_px):
                continue
            # exit at scheduled bar open; if delisted mid-hold, use terminal close
            exit_px = opens.iloc[xi][s]
            if math.isnan(exit_px):
                path = closes[s].iloc[ei:xi + 1].to_numpy(float)
                path = path[~np.isnan(path)]
                if path.size == 0:
                    continue
                exit_px = float(path[-1])  # terminal price path of a delisted name
            sign = 1.0 if side == "long" else -1.0
            gross = sign * (float(exit_px) / float(entry_px) - 1.0)
            # longs pay funding, shorts receive; conservative net drag on exposure
            funding = -sign * funding_period if side == "long" else 0.0
            leg_net = w * gross - w * rt - (w * funding_period if side == "long" else 0.0)
            net += leg_net
            legs.append({
                "symbol": s,
                "side": side,
                "weight": float(w),
                "entry_idx": int(ei),
                "entry_px": float(entry_px),
                "exit_idx": int(xi),
                "exit_px": float(exit_px),
                "gross_pnl": float(gross),
                "funding": float(funding),
            })
        if not legs:
            continue
        periods.append({
            "decision_idx": int(t),
            "entry_idx": int(ei),
            "exit_idx": int(xi),
            "bars_held": int(bars_held),
            "n_legs": len(legs),
            "net_pnl": float(net),
            "fees_slippage": float(rt),
            "label_status": "RESOLVED",
            "legs": legs,
        })
    return periods


def run_s2_slice(
    *,
    symbols=DEFAULT_BASKET,
    venue: str = "binance",
    lookback: int = DEFAULT_LOOKBACK,
    rebalance_every: int = 1,
    top_q: float = 0.2,
    bottom_q: float = 0.2,
    n_splits: int = 4,
    registry_path=ACTIVE_STRATEGIES_PATH,
    cache: str = CACHE,
) -> dict:
    """End-to-end load -> simulate -> walk-forward -> accept -> register (S2)."""
    closes, opens = load_panel(symbols, cache)
    periods = simulate_basket(
        closes, opens, lookback=lookback, rebalance_every=rebalance_every,
        top_q=top_q, bottom_q=bottom_q, venue=venue,
    )
    rets = [p["net_pnl"] for p in periods]
    arr = np.asarray(rets, dtype=float)

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
        "n_periods": int(arr.size),
        "win_rate": float((arr > 0).mean()) if arr.size else 0.0,
        "ev": float(arr.mean()) if arr.size else 0.0,
        "sum_net": float(arr.sum()) if arr.size else 0.0,
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "accept": bool(verdict["accept"]),
        "live_feasibility": LIVE_FEASIBILITY,
    }

    promotion_status = "PROMOTED" if verdict["accept"] else "NO_EDGE"
    failure_reason = None
    if not verdict["accept"]:
        failed = [k for k, g in verdict["sub_gates"].items() if not g["pass"]]
        failure_reason = "failed sub-gates: " + ",".join(failed)

    universe_log = [
        {"decision_idx": p["decision_idx"], "symbols": [g["symbol"] for g in p["legs"]]}
        for p in periods
    ]

    evidence = register_evidence(
        "S2",
        hypothesis=(
            "Cross-sectional momentum (z-scored returns) long top-20% / short "
            "bottom-20%, inverse-vol weighted, funding-adjusted, 8h rebalance, "
            "is market-neutral capital-preserving on liquid majors."
        ),
        rules={
            "type": "cross_sectional_long_short_basket",
            "lookback_bars": lookback,
            "top_q": top_q,
            "bottom_q": bottom_q,
            "weighting": "inverse_vol_dollar_neutral",
            "rebalance_every_bars": rebalance_every,
            "timeframe": "1d",
            "symbols": list(symbols),
            "features_used": ["price_momentum_z", "funding"],
            "features_gated_out": list(FORWARD_COLLECT_FEATURES),
        },
        data_sources=[f"ohlcv_cache:{s}-USDT_1d" for s in symbols] + [f"venue:{venue}"],
        data_depth=int(len(closes)),
        cost_model={
            "round_trip_cost": round_trip_cost(venue),
            "funding_8h": DEFAULT_FUNDING_8H,
            "venue": venue,
        },
        cost_calibration_status="model_default",
        test_window={"bars": int(len(closes)), "timeframe": "1d", "n_folds": len(fold_returns)},
        oos_metrics=oos_metrics,
        honest_n=int(arr.size),
        promotion_status=promotion_status,
        failure_reason=failure_reason,
        universe_construction_method=UNIVERSE_METHOD,
        path=registry_path,
    )

    return {
        "symbols": list(symbols),
        "venue": venue,
        "n_periods": int(arr.size),
        "periods": periods,
        "universe_log": universe_log,
        "verdict": verdict,
        "evidence": evidence,
        "oos_metrics": oos_metrics,
        "live_feasibility": LIVE_FEASIBILITY,
    }


def main(argv: list[str] | None = None) -> int:
    rep = run_s2_slice()
    v = rep["verdict"]
    m = rep["oos_metrics"]
    print("=" * 68)
    print(f"S2 cross-sectional basket — {','.join(rep['symbols'])} @ {rep['venue']}")
    print("=" * 68)
    print(f"rebalance periods : {rep['n_periods']}")
    print(f"WR={m['win_rate'] * 100:5.1f}%  EV={m['ev'] * 100:+.3f}%  "
          f"sum_net={m['sum_net'] * 100:+.1f}%")
    print(f"accept()          : {'GO' if v['accept'] else 'NO_GO'}")
    for name, g in v["sub_gates"].items():
        print(f"  {'PASS' if g['pass'] else 'fail'}  {name}")
    print(f"evidence status   : {rep['evidence']['promotion_status']}")
    print(f"live feasibility  : {rep['live_feasibility']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
