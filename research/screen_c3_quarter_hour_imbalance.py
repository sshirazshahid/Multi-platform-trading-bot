"""C3 — Quarter-hour opening imbalance → 4–12h after-cost screen (pilot).

Pre-registered in:
  _workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.md

Expectation: NO_GO. Harvest: scripts/harvest_binance_aggtrades_qh.py

Run:
  python research/screen_c3_quarter_hour_imbalance.py
  python -m pytest tests/test_screen_c3_quarter_hour_imbalance.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from research.screen_listing_short import (  # noqa: E402
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _pbo_across_horizons,
    load_funding_history,
)

ROOT = Path(__file__).resolve().parents[1]
HARVEST_DIR = ROOT / "data" / "aggtrades_qh"
PREREG_JSON = ROOT / "_workspace" / "strategy_pipeline" / "22_prereg_c3_quarter_hour_imbalance.json"
OUT_JSON = ROOT / "_workspace" / "strategy_pipeline" / "22_screen_c3_quarter_hour_imbalance.json"
OUT_MD = ROOT / "_workspace" / "strategy_pipeline" / "22_screen_c3_quarter_hour_imbalance.md"

SYMBOLS = ("BTCUSDT", "ETHUSDT")
BASE_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
HORIZONS = {"H4": 4, "H8": 8, "H12": 12}
DIRECTIONS = ("aligned", "contrarian")
N_TRIALS = 6
MIN_N = 30
MIN_RESID_HISTORY = 200
EXPANSION_BPS = 0.0020  # 20 bps
OOS_FRAC = 0.5
SLIP = 0.0005


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────
def boundary_ms(ts_ms: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    qm = (dt.minute // 15) * 15
    b = dt.replace(minute=qm, second=0, microsecond=0)
    return int(b.timestamp() * 1000)


def trade_side(direction: str, residual: float) -> str | None:
    if not np.isfinite(residual) or residual == 0:
        return None
    sign = 1.0 if residual > 0 else -1.0
    if direction == "aligned":
        return "long" if sign > 0 else "short"
    return "short" if sign > 0 else "long"


def side_net_return(
    side: str,
    entry_price: float,
    exit_price: float,
    funding_sum: float,
    fee_per_side: float,
    slip_per_side: float,
) -> float:
    gross = (exit_price - entry_price) / entry_price
    if side == "short":
        gross = -gross
    rt_cost = 2 * (fee_per_side + slip_per_side)
    if side == "long":
        funding_charge = funding_sum
    else:
        funding_charge = -funding_sum
    return float(gross - rt_cost - funding_charge)


def expanding_residuals(
    raw: np.ndarray,
    controls: np.ndarray,
    min_history: int = MIN_RESID_HISTORY,
) -> np.ndarray:
    """Expanding OLS residual; first min_history rows use fit on all available."""
    n = len(raw)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        end = i + 1
        start = 0 if end < min_history else max(0, end - min_history * 4)
        # expanding: use all prior rows up to i for fit when i >= min_history
        if end >= min_history:
            start = 0
        y = raw[start:end]
        X = controls[start:end]
        mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        if mask.sum() < X.shape[1] + 3:
            continue
        beta_y = y[mask]
        beta_X = X[mask]
        Xm = np.column_stack([np.ones(len(beta_y)), beta_X])
        beta, *_ = np.linalg.lstsq(Xm, beta_y, rcond=None)
        xi = np.concatenate([[1.0], controls[i]])
        if np.all(np.isfinite(xi)) and np.isfinite(raw[i]):
            out[i] = raw[i] - float(xi @ beta)
    return out


def _load_ohlcv(base: str) -> pd.DataFrame:
    path = ROOT / "data" / "ohlcv_cache" / f"{base}-USDT_1h.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_parquet(path)
    if "ts" not in df.columns:
        raise ValueError(f"{path}: missing ts")
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def _build_controls(ohlcv: pd.DataFrame, boundary_ms_list: np.ndarray) -> pd.DataFrame:
    ts_arr = ohlcv["ts"].to_numpy(dtype=np.int64)
    closes = ohlcv["close"].to_numpy(dtype=float)
    vols = ohlcv["volume"].to_numpy(dtype=float)
    rows = []
    for b_ms in boundary_ms_list:
        b_sec = b_ms // 1000
        hour_open = b_sec - (b_sec % 3600)
        idx = int(np.searchsorted(ts_arr, hour_open, side="right") - 1)
        if idx < 5:
            rows.append({"boundary_ms": b_ms, "ret_1h": np.nan, "ret_4h": np.nan, "log_vol_1h": np.nan})
            continue
        c0 = closes[idx]
        c1 = closes[idx - 1]
        c4 = closes[idx - 4]
        if min(c0, c1, c4) <= 0:
            rows.append({"boundary_ms": b_ms, "ret_1h": np.nan, "ret_4h": np.nan, "log_vol_1h": np.nan})
            continue
        rows.append(
            {
                "boundary_ms": b_ms,
                "ret_1h": float(np.log(c0 / c1)),
                "ret_4h": float(np.log(c0 / c4)),
                "log_vol_1h": float(np.log1p(vols[idx])),
            }
        )
    return pd.DataFrame(rows)


def _exit_close(ohlcv: pd.DataFrame, entry_sec: int, horizon_h: int) -> tuple[float, int] | tuple[None, None]:
    exit_target = entry_sec + horizon_h * 3600
    ts_arr = ohlcv["ts"].to_numpy(dtype=np.int64)
    idx = int(np.searchsorted(ts_arr, exit_target, side="left"))
    if idx >= len(ts_arr):
        return None, None
    return float(ohlcv["close"].iloc[idx]), int(ts_arr[idx])


def _eval_variant(rets: np.ndarray, is_mask: np.ndarray, oos_mask: np.ndarray) -> dict:
    oos = rets[oos_mask]
    oos = oos[np.isfinite(oos)]
    n_oos = int(oos.size)
    if n_oos < MIN_N:
        return {"n_oos": n_oos, "verdict": "INSUFFICIENT_DATA"}
    mean = float(np.mean(oos))
    wr = float(np.mean(oos > 0))
    dsr = _dsr_prob(oos, N_TRIALS)
    mc = _monte_carlo(oos)
    gates = {
        "dsr": dsr,
        "oos_wr": wr,
        "mc_pass": mc["passes"],
        "p_total_positive": mc["p_total_positive"],
        "max_drawdown_p95": mc["max_drawdown_p95"],
    }
    pass_all = (
        np.isfinite(dsr)
        and dsr >= MIN_DSR
        and wr >= MIN_OOS_WR
        and mc["passes"]
        and mean >= EXPANSION_BPS
    )
    return {
        "n_oos": n_oos,
        "n_is": int(np.isfinite(rets[is_mask]).sum()),
        "mean": mean,
        "mean_bps": mean * 10_000,
        "wr": wr,
        "gates": gates,
        "verdict": "GO_CANDIDATE" if pass_all else "NO_GO",
    }


def run_screen() -> dict:
    prereg = json.loads(PREREG_JSON.read_text(encoding="utf-8"))
    fee = float(config.FEE["binance_futures_taker"])

    all_trades: list[dict] = []
    for sym in SYMBOLS:
        ev_path = HARVEST_DIR / f"{sym}_qh_events.parquet"
        if not ev_path.exists():
            raise FileNotFoundError(f"missing harvest: {ev_path} — run harvest script first")
        ev = pd.read_parquet(ev_path)
        base = BASE_MAP[sym]
        ohlcv = _load_ohlcv(base)
        ctrl = _build_controls(ohlcv, ev["boundary_ms"].to_numpy())
        merged = ev.merge(ctrl, on="boundary_ms").sort_values("boundary_ms")
        raw = merged["imbalance_dollar"].to_numpy(dtype=float)
        X = merged[["ret_1h", "ret_4h", "log_vol_1h"]].to_numpy(dtype=float)
        resid = expanding_residuals(raw, X)

        funding = load_funding_history("binance", base)
        last_exit_by_h: dict[str, int] = {h: 0 for h in HORIZONS}
        merged = merged.reset_index(drop=True)
        resid_s = pd.Series(resid, index=merged.index)
        for idx, row in merged.iterrows():
            r = float(resid_s.loc[idx])
            b_ms = int(row["boundary_ms"])
            entry_sec = b_ms // 1000 + 10
            entry_px = float(row["last_price"])
            if entry_px <= 0 or not np.isfinite(r):
                continue
            for hname, hh in HORIZONS.items():
                if entry_sec < last_exit_by_h[hname]:
                    continue
                exit_px, exit_sec = _exit_close(ohlcv, entry_sec, hh)
                if exit_px is None or exit_sec is None:
                    continue
                last_exit_by_h[hname] = exit_sec
                fsum = 0.0
                if funding is not None and not funding.empty:
                    t0 = pd.Timestamp(entry_sec, unit="s", tz="UTC")
                    t1 = pd.Timestamp(exit_sec, unit="s", tz="UTC")
                    window = funding[(funding["ts"] > t0) & (funding["ts"] <= t1)]
                    fsum = float(window["rate"].sum()) if not window.empty else 0.0
                for direction in DIRECTIONS:
                    side = trade_side(direction, r)
                    if side is None:
                        continue
                    nr = side_net_return(side, entry_px, exit_px, fsum, fee, SLIP)
                    all_trades.append(
                        {
                            "symbol": sym,
                            "boundary_ms": b_ms,
                            "horizon": hname,
                            "direction": direction,
                            "signal": "residual",
                            "side": side,
                            "net_return": nr,
                            "entry_sec": entry_sec,
                            "exit_sec": exit_sec,
                        }
                    )

        # raw-signal trades for delta-drift kill (aligned, per horizon non-overlap)
        last_exit_by_h = {h: 0 for h in HORIZONS}
        for idx, row in merged.iterrows():
            raw_sig = float(row["imbalance_dollar"])
            b_ms = int(row["boundary_ms"])
            entry_sec = b_ms // 1000 + 10
            entry_px = float(row["last_price"])
            if entry_px <= 0 or not np.isfinite(raw_sig):
                continue
            for hname, hh in HORIZONS.items():
                if entry_sec < last_exit_by_h[hname]:
                    continue
                exit_px, exit_sec = _exit_close(ohlcv, entry_sec, hh)
                if exit_px is None or exit_sec is None:
                    continue
                last_exit_by_h[hname] = exit_sec
                fsum = 0.0
                side = trade_side("aligned", raw_sig)
                if side is None:
                    continue
                nr = side_net_return(side, entry_px, exit_px, fsum, fee, SLIP)
                all_trades.append(
                    {
                        "symbol": sym,
                        "boundary_ms": b_ms,
                        "horizon": hname,
                        "direction": "aligned",
                        "signal": "raw",
                        "side": side,
                        "net_return": nr,
                        "entry_sec": entry_sec,
                        "exit_sec": exit_sec,
                    }
                )

    trades = pd.DataFrame(all_trades)
    if trades.empty:
        return {"verdict": "INSUFFICIENT_DATA", "reason": "no trades built"}

    # time split on boundary_ms
    boundaries = np.sort(trades["boundary_ms"].unique())
    cut = boundaries[int(len(boundaries) * (1 - OOS_FRAC))]
    is_mask_fn = lambda s: s["boundary_ms"] < cut
    oos_mask_fn = lambda s: s["boundary_ms"] >= cut

    variants: dict = {}
    all_returns_cols: list[tuple[str, list[float]]] = []
    for hname in HORIZONS:
        for direction in DIRECTIONS:
            key = f"{hname}_{direction}"
            sub = trades[(trades["horizon"] == hname) & (trades["direction"] == direction) & (trades["signal"] == "residual")]
            rets = sub["net_return"].to_numpy(dtype=float)
            is_m = is_mask_fn(sub).to_numpy()
            oos_m = oos_mask_fn(sub).to_numpy()
            variants[key] = {"residual": _eval_variant(rets, is_m, oos_m)}

            raw_key = f"{hname}_{direction}_raw"
            sub_raw = trades[
                (trades["horizon"] == hname)
                & (trades["direction"] == direction)
                & (trades["signal"] == "raw")
            ]
            if not sub_raw.empty:
                rr = sub_raw["net_return"].to_numpy(dtype=float)
                variants[raw_key] = {
                    "raw": _eval_variant(
                        rr,
                        is_mask_fn(sub_raw).to_numpy(),
                        oos_mask_fn(sub_raw).to_numpy(),
                    )
                }
            if direction == "aligned" and variants[key]["residual"].get("n_oos", 0) >= MIN_N:
                all_returns_cols.append((key, rets[oos_m].tolist()))

    # delta-drift kill
    for hname in HORIZONS:
        for direction in DIRECTIONS:
            k = f"{hname}_{direction}"
            rk = f"{hname}_{direction}_raw"
            if rk not in variants:
                continue
            res_v = variants[k]["residual"].get("verdict")
            raw_v = variants[rk]["raw"].get("verdict")
            variants[k]["delta_drift_kill"] = raw_v == "GO_CANDIDATE" and res_v != "GO_CANDIDATE"

    joint_pbo = float("nan")
    if all_returns_cols:
        min_len = min(len(r) for _, r in all_returns_cols if len(r) >= MIN_N)
        if min_len >= MIN_N:
            mat = np.column_stack([np.asarray(r[:min_len], dtype=float) for _, r in all_returns_cols])
            joint_pbo = _pbo_across_horizons(mat)

    go_keys = [
        k
        for k, v in variants.items()
        if k.count("_") == 1 and v.get("residual", {}).get("verdict") == "GO_CANDIDATE"
    ]
    any_delta_kill = any(v.get("delta_drift_kill") for k, v in variants.items() if k.count("_") == 1)
    best_mean_bps = max(
        (
            v["residual"].get("mean_bps", float("-inf"))
            for k, v in variants.items()
            if k.endswith("_aligned") and k.count("_") == 1
        ),
        default=float("nan"),
    )

    insuff = all(
        v["residual"].get("verdict") == "INSUFFICIENT_DATA"
        for k, v in variants.items()
        if k.count("_") == 1
    )
    if insuff:
        overall = "INSUFFICIENT_DATA"
    elif go_keys and not any_delta_kill:
        overall = "GO"
    else:
        overall = "NO_GO"

    return {
        "candidate": "C3_quarter_hour_imbalance",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "prereg_sha256": prereg.get("prereg_sha256"),
        "harvest_manifest_sha256": prereg.get("harvest_manifest_sha256"),
        "n_trials": N_TRIALS,
        "n_trade_rows": int(len(trades)),
        "oos_cut_boundary_ms": int(cut),
        "joint_pbo": joint_pbo,
        "best_aligned_mean_bps_oos": best_mean_bps,
        "expansion_bar_bps": EXPANSION_BPS * 10_000,
        "go_variant_keys": go_keys,
        "delta_drift_kill_any": any_delta_kill,
        "variants": variants,
        "verdict": overall,
        "expectation": "NO_GO",
        "harvest_cmd": "python scripts/harvest_binance_aggtrades_qh.py",
    }


def _write_outputs(result: dict) -> None:
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [
        "# 22 — Screen: C3 quarter-hour opening imbalance (pilot)",
        "",
        f"**Verdict:** `{result['verdict']}` (expectation NO_GO)",
        f"**Generated:** {result['generated_utc']}",
        f"**Prereg sha256:** `{result.get('prereg_sha256')}`",
        f"**Harvest manifest sha256:** `{result.get('harvest_manifest_sha256')}`",
        f"**Best aligned OOS mean (bps):** {result.get('best_aligned_mean_bps_oos')}",
        f"**Expansion bar (bps):** {result.get('expansion_bar_bps')}",
        f"**Joint PBO:** {result.get('joint_pbo')}",
        f"**Delta-drift kill:** {result.get('delta_drift_kill_any')}",
        "",
        "## Residual variants",
        "",
        "| Variant | n_OOS | mean (bps) | WR | DSR | MC pass | Verdict |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for k, v in sorted(result["variants"].items()):
        if not k.count("_") == 1:
            continue
        r = v["residual"]
        g = r.get("gates") or {}
        lines.append(
            f"| {k} | {r.get('n_oos')} | {r.get('mean_bps')} | {r.get('wr')} | "
            f"{g.get('dsr')} | {g.get('mc_pass')} | {r.get('verdict')} |"
        )
    lines.extend(["", f"- Re-harvest: `{result['harvest_cmd']}`", ""])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    result = run_screen()
    _write_outputs(result)
    print(json.dumps({"verdict": result["verdict"], "best_bps": result.get("best_aligned_mean_bps_oos")}, indent=2))
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
