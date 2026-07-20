"""Stage-1 descriptive screen: staked-ETH wrapper discount vs 4-leg cost floor.

Pre-registered in _workspace/strategy_pipeline/15d_screen_wrapper.md (written BEFORE
this script). Scout B candidate 3, strategy-evidence-pipeline run 2026-07-16.

STOP RULE (pre-registered): if the p95 discount magnitude (most favorable of the two
pre-registered deviation estimators, discount-side bars only) is below the most
strategy-favorable cost floor for BOTH wrappers, verdict = NO_GO on expectancy
grounds and NO Stage-2 backtest is built.

This is a DESCRIPTIVE pass: no entry/exit simulation, no parameter search.

Usage:
    venv\\Scripts\\python.exe research\\screen_wrapper_discount.py --selftest
    venv\\Scripts\\python.exe research\\screen_wrapper_discount.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import config  # noqa: E402  (FEE / SLIPPAGE are authoritative)

CACHE = os.path.join(_REPO, "data", "ohlcv_cache")
FUNDING_CSV = os.path.join(_REPO, "data", "funding_history", "binance_ETH.csv")
OUT_JSON = os.path.join(_REPO, "_workspace", "strategy_pipeline", "15d_screen_wrapper.json")

WRAPPERS = {
    "WBETH": os.path.join(CACHE, "WBETH-USDT_1h.parquet"),
    "STETH": os.path.join(CACHE, "STETH-USDT_1h.parquet"),
}
ETH_SPOT = os.path.join(CACHE, "ETH-USDT_qtrspot_1h.parquet")

HOURS_PER_YEAR = 365.25 * 24.0
TRAIL_WINDOW_H = 720  # 30d, pre-registered
EPISODE_THRESHOLDS_BPS = [10, 20, 30, 40, 50, 75, 100]  # descriptive bins only
DREF_THRESHOLD_BPS = 20.0  # pre-registered episode depth for D_ref
DREF_FALLBACK_H = 24.0  # pre-registered fallback if no >=20bps episodes


# ------------------------------------------------------------------ cost model


def fixed_cost_bps() -> float:
    """4-leg worst-case taker round trip from config.FEE + repo slippage convention."""
    spot = config.FEE["spot_taker"]
    fut = config.FEE["futures_taker"]
    slip = config.SLIPPAGE["pct_open"]  # 5bps; pct_close identical by convention
    slip_close = config.SLIPPAGE["pct_close"]
    total = (spot + slip) + (spot + slip_close) + (fut + slip) + (fut + slip_close)
    return round(total * 1e4, 4)


def funding_credit_bps(mean_8h_rate: float, hold_h: float) -> float:
    """Signed funding credit (bps) to the SHORT hedge over a hold of hold_h hours.

    Positive mean funding -> short receives -> positive credit (reduces floor).
    Negative mean funding -> short pays -> negative credit (raises floor).
    """
    return mean_8h_rate * 1e4 * (hold_h / 8.0)


def cost_floors(mean_8h_rate: float, d_ref_h: float) -> dict:
    fixed = fixed_cost_bps()
    credit = funding_credit_bps(mean_8h_rate, d_ref_h)
    floor_adj = fixed - credit  # signed: negative credit makes the floor WORSE
    return {
        "fixed_cost_bps": fixed,
        "funding_credit_bps_at_dref": round(credit, 4),
        "floor_nofunding_bps": fixed,
        "floor_funding_adj_bps": round(floor_adj, 4),
        "floor_min_bps": round(min(fixed, floor_adj), 4),
    }


# ------------------------------------------------------------------ deviation


def ols_residuals(ts_sec: np.ndarray, log_ratio: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimator A: OLS linear fit of log(ratio) on time. Returns (residuals, annual_drift)."""
    t_years = (ts_sec - ts_sec[0]) / (HOURS_PER_YEAR * 3600.0)
    slope, intercept = np.polyfit(t_years, log_ratio, 1)
    resid = log_ratio - (intercept + slope * t_years)
    return resid, float(slope)


def trailing_median_dev(
    log_ratio: pd.Series, annual_drift: float, window_h: int = TRAIL_WINDOW_H
) -> pd.Series:
    """Estimator B: causal trailing median, drift-corrected for the median's lag.

    A trailing window of W bars (inclusive of the current bar) centers on a point
    (W-1)/2 bars back, so that is the lag a linear drift must be corrected for.
    """
    med = log_ratio.rolling(window_h, min_periods=window_h).median()
    correction = annual_drift * ((window_h - 1) / 2.0) / HOURS_PER_YEAR
    return log_ratio - (med + correction)


def discount_stats(dev: np.ndarray) -> dict:
    """Percentiles of discount magnitude (bps) over discount-side bars (dev < 0)."""
    dev = dev[~np.isnan(dev)]
    mask = dev < 0
    if not mask.any():
        return {"n_bars": int(dev.size), "n_discount_bars": 0, "share_in_discount": 0.0,
                "p50_bps": 0.0, "p90_bps": 0.0, "p95_bps": 0.0, "p99_bps": 0.0,
                "max_bps": 0.0}
    mag = -dev[mask] * 1e4
    return {
        "n_bars": int(dev.size),
        "n_discount_bars": int(mask.sum()),
        "share_in_discount": round(float(mask.mean()), 4),
        "p50_bps": round(float(np.percentile(mag, 50)), 2),
        "p90_bps": round(float(np.percentile(mag, 90)), 2),
        "p95_bps": round(float(np.percentile(mag, 95)), 2),
        "p99_bps": round(float(np.percentile(mag, 99)), 2),
        "max_bps": round(float(mag.max()), 2),
    }


def find_episodes(dev: np.ndarray, threshold_bps: float) -> list[dict]:
    """Maximal consecutive runs with discount magnitude >= threshold_bps.

    NaNs (estimator warm-up) break runs. Returns [{start_idx, duration_h, max_depth_bps}].
    """
    thr = -threshold_bps / 1e4
    mask = np.where(np.isnan(dev), False, dev <= thr)
    episodes = []
    start = None
    # trailing False sentinel closes any open run in-loop
    for i, flag in enumerate(np.append(mask, False)):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            seg = dev[start:i]
            episodes.append({"start_idx": start, "duration_h": i - start,
                             "max_depth_bps": round(float(-seg.min() * 1e4), 2)})
            start = None
    return episodes


def episode_summary(dev: np.ndarray) -> dict:
    out = {}
    for thr in EPISODE_THRESHOLDS_BPS:
        eps = find_episodes(dev, float(thr))
        durs = [e["duration_h"] for e in eps]
        out[f">={thr}bps"] = {
            "n_episodes": len(eps),
            "median_duration_h": float(np.median(durs)) if durs else 0.0,
            "max_duration_h": int(max(durs)) if durs else 0,
            "max_depth_bps": max((e["max_depth_bps"] for e in eps), default=0.0),
        }
    return out


# ------------------------------------------------------------------ data


def load_parquet(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df["ts"].iloc[0] > 1e12:  # defensive: ms -> s
        df = df.assign(ts=df["ts"] // 1000)
    return df


def build_ratio_frame(wrapper_path: str, eth: pd.DataFrame) -> pd.DataFrame:
    """``eth`` is the pre-loaded ts/eth_close frame (loaded once in run_screen)."""
    w = load_parquet(wrapper_path)[["ts", "close", "volume"]].rename(
        columns={"close": "w_close", "volume": "w_vol"})
    df = w.merge(eth, on="ts", how="inner").sort_values("ts").reset_index(drop=True)
    df["log_ratio"] = np.log(df["w_close"] / df["eth_close"])
    df["quote_vol"] = df["w_close"] * df["w_vol"]  # base volume assumed -> USDT notional
    return df


def window_mean_funding(funding: pd.DataFrame, t0: int, t1: int) -> tuple[float, int]:
    """``funding`` is the pre-loaded funding CSV frame (read once in run_screen)."""
    f = funding[(funding["ts"] >= t0) & (funding["ts"] <= t1)]
    return float(f["funding_rate"].mean()), int(len(f))


# ------------------------------------------------------------------ screen


def screen_wrapper(name: str, wrapper_path: str, eth: pd.DataFrame,
                   funding: pd.DataFrame) -> dict:
    df = build_ratio_frame(wrapper_path, eth)
    ts = df["ts"].to_numpy(dtype=np.int64)
    logr = df["log_ratio"].to_numpy()

    dev_a, drift = ols_residuals(ts, logr)
    dev_b = trailing_median_dev(df["log_ratio"], drift).to_numpy()

    stats_a = discount_stats(dev_a)
    stats_b = discount_stats(dev_b)

    # Most strategy-favorable estimator per pre-registration
    if stats_b["p95_bps"] > stats_a["p95_bps"]:
        best_name, best_dev, best_stats = "B_trailing_median", dev_b, stats_b
    else:
        best_name, best_dev, best_stats = "A_ols", dev_a, stats_a

    eps20 = find_episodes(best_dev, DREF_THRESHOLD_BPS)
    d_ref = float(np.median([e["duration_h"] for e in eps20])) if eps20 else DREF_FALLBACK_H

    mean_f8, n_settle = window_mean_funding(funding, int(ts[0]), int(ts[-1]))
    floors = cost_floors(mean_f8, d_ref)

    # Liquidity at discount moments (>=20bps on best estimator) vs whole sample
    ep_mask = np.zeros(len(df), dtype=bool)
    for e in eps20:
        ep_mask[e["start_idx"]: e["start_idx"] + e["duration_h"]] = True
    liq = {
        "median_quote_vol_usdt_all": round(float(df["quote_vol"].median()), 0),
        "median_quote_vol_usdt_in_ge20bps_episodes": (
            round(float(df.loc[ep_mask, "quote_vol"].median()), 0) if ep_mask.any() else None
        ),
    }

    p95 = best_stats["p95_bps"]
    dead = bool(p95 < floors["floor_min_bps"])

    return {
        "wrapper": name,
        "n_matched_bars": int(len(df)),
        "window_utc": [
            str(pd.to_datetime(ts[0], unit="s")),
            str(pd.to_datetime(ts[-1], unit="s")),
        ],
        "annual_drift_log_ratio": round(drift, 6),
        "estimator_A_ols": stats_a,
        "estimator_B_trailing_median": stats_b,
        "best_estimator": best_name,
        "p95_disc_bps": p95,
        "episodes_best_estimator": episode_summary(best_dev),
        "d_ref_h": d_ref,
        "n_ge20bps_episodes": len(eps20),
        "mean_8h_funding_window": round(mean_f8, 8),
        "n_funding_settlements_in_window": n_settle,
        "cost_floors": floors,
        "liquidity": liq,
        "stop_rule_fired": dead,
    }


def run_screen() -> dict:
    eth = load_parquet(ETH_SPOT)[["ts", "close"]].rename(columns={"close": "eth_close"})
    funding = pd.read_csv(FUNDING_CSV)
    per_wrapper = [screen_wrapper(name, path, eth, funding)
                   for name, path in WRAPPERS.items()]
    all_dead = all(w["stop_rule_fired"] for w in per_wrapper)
    verdict = "NO_GO" if all_dead else "STAGE2_REQUIRED"
    result = {
        "candidate": "staked_eth_wrapper_discount",
        "hypothesis": (
            "Wrapper/ETH CEX-spot discounts vs slow fair trend exceed the 4-leg "
            "round-trip cost floor often enough to be harvestable (buy wrapper, "
            "ETH-perp hedge, exit at convergence)."
        ),
        "stage": "stage1_descriptive",
        "pre_registration": "_workspace/strategy_pipeline/15d_screen_wrapper.md",
        "n": {w["wrapper"]: w["n_matched_bars"] for w in per_wrapper},
        "after_cost_metrics": {
            w["wrapper"]: {
                "p95_disc_bps": w["p95_disc_bps"],
                "floor_min_bps": w["cost_floors"]["floor_min_bps"],
                "margin_bps": round(w["p95_disc_bps"] - w["cost_floors"]["floor_min_bps"], 2),
            }
            for w in per_wrapper
        },
        "gates": {
            "stage1_stop_rule": {
                "rule": "p95_disc_bps < floor_min_bps on BOTH wrappers => NO_GO, no backtest",
                "fired": all_dead,
            },
            "frozen_stage2_gates": "not reached" if all_dead else "pending",
        },
        "per_wrapper": per_wrapper,
        "verdict": verdict,
    }
    return result


# ------------------------------------------------------------------ selftest


def selftest() -> None:
    # 1. fixed cost from config: 2*(10+5) + 2*(5+5) = 50 bps
    assert abs(fixed_cost_bps() - 50.0) < 1e-9, fixed_cost_bps()

    # 2. funding credit sign + arithmetic: f8=1bp over 24h -> 3 settlements -> +3bps credit
    assert abs(funding_credit_bps(0.0001, 24.0) - 3.0) < 1e-9
    assert abs(funding_credit_bps(-0.0001, 24.0) + 3.0) < 1e-9
    fl = cost_floors(-0.0001, 24.0)  # negative funding must RAISE the adjusted floor
    assert fl["floor_funding_adj_bps"] == 53.0 and fl["floor_min_bps"] == 50.0, fl
    fl2 = cost_floors(0.0001, 24.0)  # positive funding credit lowers floor_min
    assert fl2["floor_min_bps"] == 47.0, fl2

    # 3. OLS estimator recovers an exact linear drift with ~zero residuals
    ts = np.arange(0, 3600 * 1000, 3600, dtype=np.int64)
    drift_true = 0.03  # +3%/yr
    line = 0.05 + drift_true * (ts - ts[0]) / (HOURS_PER_YEAR * 3600.0)
    resid, drift_hat = ols_residuals(ts, line)
    assert abs(drift_hat - drift_true) < 1e-9 and np.max(np.abs(resid)) < 1e-12

    # 4. trailing-median drift correction: exact-drift line -> deviation ~ 0
    dev_b = trailing_median_dev(pd.Series(line), drift_true, window_h=10)
    tail = dev_b.to_numpy()[10:]
    assert np.nanmax(np.abs(tail)) < 1e-6, np.nanmax(np.abs(tail))

    # 5. discount percentiles on a known array (discount side only)
    dev = np.array([0.001, -0.001, -0.002, -0.003, -0.004])  # mags 10,20,30,40 bps
    st = discount_stats(dev)
    assert st["n_discount_bars"] == 4 and st["p50_bps"] == 25.0 and st["max_bps"] == 40.0

    # 6. episode detection incl. trailing episode and NaN break
    dev = np.array([0.0, -0.0025, -0.0030, -0.0005, -0.0040, np.nan, -0.0050])
    eps = find_episodes(dev, 20.0)
    assert len(eps) == 3, eps
    assert eps[0] == {"start_idx": 1, "duration_h": 2, "max_depth_bps": 30.0}, eps[0]
    assert eps[1] == {"start_idx": 4, "duration_h": 1, "max_depth_bps": 40.0}, eps[1]
    assert eps[2] == {"start_idx": 6, "duration_h": 1, "max_depth_bps": 50.0}, eps[2]

    # 7. no-discount input degrades gracefully
    st0 = discount_stats(np.array([0.001, 0.002]))
    assert st0["n_discount_bars"] == 0 and st0["p95_bps"] == 0.0

    print("SELFTEST PASS (7 groups)")


# ------------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    result = run_screen()
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(json.dumps(result, indent=2))
    print(f"\nJSON verdict written: {OUT_JSON}")


if __name__ == "__main__":
    main()
