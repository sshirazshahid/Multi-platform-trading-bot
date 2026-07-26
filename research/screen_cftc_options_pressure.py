"""C1 — CFTC asset-manager options-pressure → BTC perp after-cost screen.

Pre-registered in:
  _workspace/strategy_pipeline/20_prereg_c1_cftc_options_pressure.md
  (sha256 recorded in companion .json BEFORE this script ran).

Expectation: NO_GO. Harvest: scripts/harvest_cftc_tff_btc.py → data/cftc_cot/.

Run:
  python research/screen_cftc_options_pressure.py
  python -m pytest tests/test_screen_cftc_options_pressure.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_listing_short import (  # noqa: E402
    MAX_PBO,
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _oos_wr_walk_forward,
    _pbo_across_horizons,
    funding_sum_in_window,
    load_funding_history,
    window_funding_covered,
)

ROOT = Path(__file__).resolve().parents[1]
CFTC_DIR = ROOT / "data" / "cftc_cot"
OHLCV_PATH = ROOT / "data" / "ohlcv_cache" / "BTC-USDT_1h.parquet"
PREREG_JSON = ROOT / "_workspace" / "strategy_pipeline" / "20_prereg_c1_cftc_options_pressure.json"
OUT_JSON = ROOT / "_workspace" / "strategy_pipeline" / "20_screen_c1_cftc_options_pressure.json"
OUT_MD = ROOT / "_workspace" / "strategy_pipeline" / "20_screen_c1_cftc_options_pressure.md"

CME_BTC_NAME = "BITCOIN - CHICAGO MERCANTILE EXCHANGE"
CME_BTC_CODE = "133741"
NY = ZoneInfo("America/New_York")
DAY = 24 * 3600
N_TRIALS = 6  # 3 horizons × 2 directions
MIN_N = 30
OOS_START = datetime(2025, 3, 5, tzinfo=timezone.utc)
ETF_SPLIT = datetime(2024, 1, 11, tzinfo=timezone.utc)
HORIZONS = {"H1": 7, "H2": 14, "H3": 21}
DIRECTIONS = ("long_on_pos", "short_on_pos")
VENUE_ORDER = ("binance", "bybit", "bitget")

# US federal holidays that can delay Friday COT releases (explicit prereg assumption).
_US_FEDERAL_HOLIDAYS = {
    # New Year's Day, MLK, Presidents, Memorial, Juneteenth, Independence,
    # Labor, Columbus/Indigenous, Veterans, Thanksgiving, Christmas — 2020-2026
    "2020-01-01", "2020-01-20", "2020-02-17", "2020-05-25", "2020-07-03",
    "2020-09-07", "2020-10-12", "2020-11-11", "2020-11-26", "2020-12-25",
    "2021-01-01", "2021-01-18", "2021-02-15", "2021-05-31", "2021-06-18",
    "2021-07-05", "2021-09-06", "2021-10-11", "2021-11-11", "2021-11-25",
    "2021-12-24",
    "2022-01-17", "2022-02-21", "2022-05-30", "2022-06-20", "2022-07-04",
    "2022-09-05", "2022-10-10", "2022-11-11", "2022-11-24", "2022-12-26",
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-05-29", "2023-06-19",
    "2023-07-04", "2023-09-04", "2023-10-09", "2023-11-10", "2023-11-23",
    "2023-12-25",
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-05-27", "2024-06-19",
    "2024-07-04", "2024-09-02", "2024-10-14", "2024-11-11", "2024-11-28",
    "2024-12-25",
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-05-26", "2025-06-19",
    "2025-07-04", "2025-09-01", "2025-10-13", "2025-11-11", "2025-11-27",
    "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26",
    "2026-12-25",
}


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────
def asset_mgr_net(long_pos, short_pos) -> float:
    return float(long_pos) - float(short_pos)


def options_only_net(combined_net: float, fut_only_net: float) -> float:
    return float(combined_net) - float(fut_only_net)


def residualize_against_lags(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """OLS residual of y on columns of X (with intercept). NaN rows dropped pairwise."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    resid = np.full_like(y, np.nan, dtype=float)
    if mask.sum() < X.shape[1] + 2:
        return resid
    Xm = np.column_stack([np.ones(mask.sum()), X[mask]])
    ym = y[mask]
    beta, *_ = np.linalg.lstsq(Xm, ym, rcond=None)
    resid[mask] = ym - Xm @ beta
    return resid


def next_business_day_ny(d: datetime) -> datetime:
    """Advance calendar day in NY until weekday and not a listed federal holiday."""
    cur = d
    while True:
        cur = cur + timedelta(days=1)
        if cur.weekday() >= 5:
            continue
        if cur.strftime("%Y-%m-%d") in _US_FEDERAL_HOLIDAYS:
            continue
        return cur


def release_ts_utc(report_date: datetime) -> int:
    """Friday 15:30 America/New_York after report Tuesday; holiday → next business day.

    report_date is the CFTC as-of Tuesday (date only, UTC noon sentinel OK).
    """
    # Find the Friday on/after the report Tuesday in NY.
    rd = report_date.astimezone(NY) if report_date.tzinfo else report_date.replace(tzinfo=NY)
    # Normalize to date at midnight NY
    day0 = datetime(rd.year, rd.month, rd.day, tzinfo=NY)
    # Tuesday=1; Friday = Tuesday + 3 days
    friday = day0 + timedelta(days=(4 - day0.weekday()) % 7)
    if friday.weekday() != 4:
        # If report isn't Tuesday, still take the next Friday on/after day0
        days_ahead = (4 - day0.weekday()) % 7
        friday = day0 + timedelta(days=days_ahead)
    if friday.strftime("%Y-%m-%d") in _US_FEDERAL_HOLIDAYS or friday.weekday() >= 5:
        friday = next_business_day_ny(friday - timedelta(days=1))
        # next_business_day advances from friday-1; if friday itself holiday, walk
        while friday.strftime("%Y-%m-%d") in _US_FEDERAL_HOLIDAYS or friday.weekday() >= 5:
            friday = next_business_day_ny(friday)
    release_local = friday.replace(hour=15, minute=30, second=0, microsecond=0)
    return int(release_local.astimezone(timezone.utc).timestamp())


def side_net_return(
    side: str,
    entry_price: float,
    exit_price: float,
    funding_sum_for_long: float,
    fee_per_side: float,
    slip_per_side: float,
) -> float:
    """After-cost return. funding_sum_for_long = sum(rates) in window (long pays +rate)."""
    roundtrip = 2.0 * (fee_per_side + slip_per_side)
    if side == "long":
        gross = (exit_price - entry_price) / entry_price
        funding = -float(funding_sum_for_long)
    else:
        gross = (entry_price - exit_price) / entry_price
        funding = float(funding_sum_for_long)
    return float(gross + funding - roundtrip)


def first_bar_at_or_after(df: pd.DataFrame, target_ts: int):
    sub = df[df["ts"] >= int(target_ts)]
    if len(sub) == 0:
        return None, None
    row = sub.iloc[0]
    return float(row["close"]), int(row["ts"])


def bar_near_ts(df: pd.DataFrame, target_ts: int, tol: int = 6 * 3600):
    if len(df) == 0:
        return None, None
    idx = int(np.searchsorted(df["ts"].values, target_ts))
    best = None
    for j in (idx - 1, idx):
        if 0 <= j < len(df):
            ts = int(df["ts"].iloc[j])
            d = abs(ts - target_ts)
            if best is None or d < best[0]:
                best = (d, ts, float(df["close"].iloc[j]))
    if best is None or best[0] > tol:
        return None, None
    return best[2], best[1]


# ── Data loaders ────────────────────────────────────────────────────────────
def _load_tff(path: Path) -> pd.DataFrame:
    rows = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(rows)
    df = df[df["cftc_contract_market_code"].astype(str) == CME_BTC_CODE].copy()
    df["report_date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"], utc=True)
    for c in ("asset_mgr_positions_long", "asset_mgr_positions_short"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["am_net"] = df["asset_mgr_positions_long"] - df["asset_mgr_positions_short"]
    return df.sort_values("report_date").drop_duplicates("report_date")


def build_options_signal(fut: pd.DataFrame, comb: pd.DataFrame) -> pd.DataFrame:
    m = fut[["report_date", "am_net"]].rename(columns={"am_net": "fut_net"}).merge(
        comb[["report_date", "am_net"]].rename(columns={"am_net": "comb_net"}),
        on="report_date",
        how="inner",
    )
    m["opt_net"] = m["comb_net"] - m["fut_net"]
    m["opt_delta"] = m["opt_net"].diff()
    return m


def tue_tue_returns(ohlcv: pd.DataFrame, report_dates: pd.Series) -> pd.Series:
    """BTC return from Tuesday open nearest report_date to next Tuesday (+7d)."""
    out = []
    idx = []
    for rd in report_dates:
        t0 = int(pd.Timestamp(rd).timestamp())
        # Prefer 00:00 UTC Tuesday bar; fall back to nearest within 6h
        p0, _ = bar_near_ts(ohlcv, t0, tol=12 * 3600)
        p1, _ = bar_near_ts(ohlcv, t0 + 7 * DAY, tol=12 * 3600)
        if p0 is None or p1 is None or p0 == 0:
            out.append(np.nan)
        else:
            out.append((p1 - p0) / p0)
        idx.append(pd.Timestamp(rd))
    return pd.Series(out, index=pd.DatetimeIndex(idx, tz="UTC"))


def build_residual_signal(sig: pd.DataFrame, ret_tt: pd.Series) -> pd.DataFrame:
    s = sig.set_index("report_date").copy()
    s["ret0"] = ret_tt.reindex(s.index)
    cols = ["ret0"]
    for lag in range(1, 9):
        col = f"ret_l{lag}"
        s[col] = s["ret0"].shift(lag)
        cols.append(col)
    X = s[cols].to_numpy()
    y = s["opt_delta"].to_numpy()
    s["resid"] = residualize_against_lags(y, X)
    s["raw_delta"] = s["opt_delta"]
    return s.reset_index()


def pick_funding(entry_ts: int, exit_ts: int):
    for venue in VENUE_ORDER:
        series = load_funding_history("BTC", venue=venue)
        if window_funding_covered(series, entry_ts, exit_ts):
            return venue, series
    return None, pd.Series(dtype=float)


def simulate_variant(
    signal_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    horizon_days: int,
    direction: str,
    fee: float,
    slip: float,
    score_col: str = "resid",
) -> list[dict]:
    trades = []
    for _, row in signal_df.iterrows():
        score = row.get(score_col)
        if score is None or not np.isfinite(score) or score == 0:
            continue
        rd = pd.Timestamp(row["report_date"]).to_pydatetime()
        if rd.tzinfo is None:
            rd = rd.replace(tzinfo=timezone.utc)
        rel = release_ts_utc(rd)
        entry_px, entry_ts = first_bar_at_or_after(ohlcv, rel)
        if entry_px is None:
            continue
        exit_target = int(pd.Timestamp(row["report_date"]).timestamp()) + horizon_days * DAY
        if entry_ts >= exit_target:
            continue  # untradeable
        exit_px, exit_ts = bar_near_ts(ohlcv, exit_target, tol=12 * 3600)
        if exit_px is None or exit_ts is None or exit_ts <= entry_ts:
            continue
        venue, fund = pick_funding(entry_ts, exit_ts)
        if venue is None:
            continue
        fsum = funding_sum_in_window(fund, entry_ts, exit_ts)
        # long_on_pos: positive score → long; short_on_pos flips the mapping
        if direction == "long_on_pos":
            side = "long" if score > 0 else "short"
        else:
            side = "short" if score > 0 else "long"
        net = side_net_return(side, entry_px, exit_px, fsum, fee, slip)
        trades.append(
            {
                "report_date": str(pd.Timestamp(row["report_date"]).date()),
                "release_ts": rel,
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "side": side,
                "score": float(score),
                "net_return": float(net),
                "venue": venue,
                "oos": pd.Timestamp(row["report_date"]) >= OOS_START,
                "post_etf": pd.Timestamp(row["report_date"]) >= ETF_SPLIT,
            }
        )
    return trades


def evaluate_trades(trades: list[dict], n_trials: int = N_TRIALS) -> dict:
    oos = [t for t in trades if t["oos"]]
    arr = np.array([t["net_return"] for t in oos], dtype=float)
    if arr.size < MIN_N:
        return {
            "n_oos": int(arr.size),
            "verdict": "INSUFFICIENT_DATA",
            "mean": float(arr.mean()) if arr.size else None,
            "wr": float((arr > 0).mean()) if arr.size else None,
        }
    pairs = [(t["entry_ts"], t["exit_ts"]) for t in oos]
    # chronological order
    order = np.argsort([t["entry_ts"] for t in oos])
    arr = arr[order]
    pairs = [pairs[i] for i in order]
    dsr = _dsr_prob(arr, n_trials)
    oos_wr = _oos_wr_walk_forward(arr, pairs)
    # PBO needs a matrix — single column padded with zeros for sibling variants
    # filled by caller for joint PBO; here per-variant uses 1-col (informational)
    matrix = arr.reshape(-1, 1)
    pbo = _pbo_across_horizons(matrix)
    mc = _monte_carlo(arr)
    gates = {
        "dsr": dsr,
        "dsr_pass": bool(np.isfinite(dsr) and dsr >= MIN_DSR),
        "pbo": pbo,
        "pbo_pass": bool(np.isfinite(pbo) and pbo <= MAX_PBO),
        "oos_wr": oos_wr,
        "oos_wr_pass": bool(np.isfinite(oos_wr) and oos_wr >= MIN_OOS_WR),
        "mc": mc,
        "mc_pass": bool(mc.get("passes")),
    }
    all_pass = all(
        gates[k]
        for k in ("dsr_pass", "pbo_pass", "oos_wr_pass", "mc_pass")
    )
    # NaN PBO on single column often fails — treat non-finite PBO as fail-closed
    if not np.isfinite(pbo):
        gates["pbo_pass"] = False
        all_pass = False
    return {
        "n_oos": int(arr.size),
        "mean": float(arr.mean()),
        "wr": float((arr > 0).mean()),
        "gates": gates,
        "verdict": "GO_CANDIDATE" if all_pass else "NO_GO",
    }


def run_screen() -> dict:
    import config

    if not PREREG_JSON.exists():
        raise RuntimeError("prereg JSON missing — refuse to screen without frozen hash")
    prereg = json.loads(PREREG_JSON.read_text(encoding="utf-8"))
    fee = float(config.FEE["binance_futures_taker"])
    slip = float(config.SLIPPAGE["pct_open"])

    fut = _load_tff(CFTC_DIR / "tff_fut_only_btc_raw.json")
    comb = _load_tff(CFTC_DIR / "tff_combined_btc_raw.json")
    sig = build_options_signal(fut, comb)
    ohlcv = pd.read_parquet(OHLCV_PATH)
    if "ts" not in ohlcv.columns:
        raise RuntimeError("BTC OHLCV missing ts column")
    ohlcv = ohlcv.sort_values("ts")
    ret_tt = tue_tue_returns(ohlcv, sig["report_date"])
    sig_r = build_residual_signal(sig, ret_tt)

    variants = {}
    all_returns_cols = []
    for hname, hdays in HORIZONS.items():
        for direction in DIRECTIONS:
            key = f"{hname}_{direction}"
            trades = simulate_variant(sig_r, ohlcv, hdays, direction, fee, slip, "resid")
            raw_trades = simulate_variant(sig_r, ohlcv, hdays, direction, fee, slip, "raw_delta")
            ev = evaluate_trades(trades)
            ev_raw = evaluate_trades(raw_trades)
            variants[key] = {
                "horizon_days": hdays,
                "direction": direction,
                "residual": ev,
                "raw_delta": ev_raw,
                "n_trades_all": len(trades),
                "delta_drift_kill": (
                    ev.get("verdict") == "NO_GO"
                    and ev_raw.get("verdict") == "GO_CANDIDATE"
                ),
            }
            oos_rets = [t["net_return"] for t in trades if t["oos"]]
            all_returns_cols.append((key, oos_rets))

    # Joint PBO across variants (aligned by truncating to min length chronologically
    # is imperfect; use column-stack of equal-length via pad NaN then drop — CSCV
    # needs complete matrix. Build from H2 long_on_pos length as reference.)
    lengths = [len(r) for _, r in all_returns_cols]
    min_len = min((L for L in lengths if L >= MIN_N), default=0)
    joint_pbo = float("nan")
    if min_len >= MIN_N:
        mat = np.column_stack([np.asarray(r[:min_len], dtype=float) for _, r in all_returns_cols])
        joint_pbo = _pbo_across_horizons(mat)

    # Horizon-mining check among residual variants
    means = {
        k: v["residual"].get("mean")
        for k, v in variants.items()
        if v["residual"].get("mean") is not None
    }
    go_keys = [k for k, v in variants.items() if v["residual"].get("verdict") == "GO_CANDIDATE"]
    horizon_mined = False
    if go_keys:
        for gk in go_keys:
            h = gk.split("_")[0]
            neighbors = [k for k in means if k.startswith(("H1", "H2", "H3")) and k != gk]
            # same direction neighbor
            direction = "_".join(gk.split("_")[1:])
            adj = [
                k
                for k in neighbors
                if k.endswith(direction) and means.get(k) is not None and means[gk] is not None
            ]
            same_sign = any(
                (means[k] > 0) == (means[gk] > 0) for k in adj
            )
            if not same_sign:
                horizon_mined = True

    any_delta_kill = any(v.get("delta_drift_kill") for v in variants.values())
    any_go = bool(go_keys) and not horizon_mined and not any_delta_kill

    # Overall verdict
    insuff = all(
        v["residual"].get("verdict") == "INSUFFICIENT_DATA" for v in variants.values()
    )
    if insuff:
        overall = "INSUFFICIENT_DATA"
    elif any_go:
        overall = "GO"
    else:
        overall = "NO_GO"

    result = {
        "candidate": "C1_cftc_options_pressure",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "prereg_sha256": prereg.get("prereg_sha256"),
        "harvest_manifest_sha256": prereg.get("harvest_manifest_sha256"),
        "n_trials": N_TRIALS,
        "n_signal_rows": int(len(sig_r)),
        "n_residual_finite": int(np.isfinite(sig_r["resid"]).sum()),
        "joint_pbo": joint_pbo,
        "horizon_mined": horizon_mined,
        "delta_drift_kill_any": any_delta_kill,
        "go_variant_keys": go_keys,
        "variants": variants,
        "verdict": overall,
        "expectation": "NO_GO",
        "cost_model": {
            "fee_per_side": fee,
            "slip_per_side": slip,
            "venues": list(VENUE_ORDER),
        },
        "harvest_cmd": "python scripts/harvest_cftc_tff_btc.py",
    }
    return result


def _write_outputs(result: dict) -> None:
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [
        "# 20 — Screen: C1 CFTC options-pressure",
        "",
        f"**Verdict:** `{result['verdict']}` (expectation NO_GO)",
        f"**Generated:** {result['generated_utc']}",
        f"**Prereg sha256:** `{result['prereg_sha256']}`",
        f"**Harvest manifest sha256:** `{result['harvest_manifest_sha256']}`",
        f"**n_trials:** {result['n_trials']}",
        f"**Joint PBO:** {result.get('joint_pbo')}",
        f"**Horizon-mined flag:** {result.get('horizon_mined')}",
        f"**Delta-drift kill:** {result.get('delta_drift_kill_any')}",
        "",
        "## Variants (residual signal)",
        "",
        "| Variant | n_OOS | mean | WR | DSR | OOS-WR | MC pass | Verdict |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for k, v in result["variants"].items():
        r = v["residual"]
        g = r.get("gates") or {}
        lines.append(
            f"| {k} | {r.get('n_oos')} | {r.get('mean')} | {r.get('wr')} | "
            f"{(g.get('dsr') if g else None)} | {(g.get('oos_wr') if g else None)} | "
            f"{(g.get('mc_pass') if g else None)} | {r.get('verdict')} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Raw Δ variants retained for delta-drift kill comparison only.",
            "- Costs: Binance futures taker + 5 bps slip/side + realized funding.",
            f"- Re-harvest: `{result['harvest_cmd']}`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    result = run_screen()
    _write_outputs(result)
    print(json.dumps({"verdict": result["verdict"], "go_keys": result["go_variant_keys"]}, indent=2))
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
