"""TradingView dominance/aggregate regime screen — PRE-REGISTERED, frozen gates.

DATA (TradingView-only series, data/tv_cache via scripts/backfill_tv_cache.py;
probe 2026-06-11: anonymous websocket serves 2,600 daily bars, first bar
2019-04-29 for every CRYPTOCAP series):
  CRYPTOCAP:USDT.D / BTC.D / TOTAL / TOTAL3 1D + BINANCE:BTCUSDT 1D.
  Alt panel = deep-history majors (>= 20k 1h bars in data/ohlcv_cache — the
  funding-screen set) as BINANCE {BASE}USDT 1D closes, equal-weight mean daily
  return, >= 5 names required per day. WINDOW: 2020-01-01+ (pre-registered).

VARIANT GRID (8, DECLARED BEFORE RESULTS; do not extend after seeing output):
  v1 usdtd_trend    USDT.D EMA20<EMA50 -> long BTC, else short   (h=1d)
  v2 usdtd_z14      USDT.D 14d-ret z(60d): z<-1 long BTC / z>+1 short / flat
  v3 total_trend    TOTAL EMA20>EMA50 -> long BTC, else short
  v4 total_z14      TOTAL z: z>+1 long / z<-1 short / flat
  v5 btcd_rot7      BTC.D 7d mom<0 -> long alt-panel/short BTC (0.5 legs), else reverse
  v6 btcd_rot14     same, 14d
  v7 t3ratio_mom7   sign((TOTAL3/TOTAL) 7d mom) -> position in alt-panel
  v8 t3ratio_mom14  same, 14d
  Forward horizon h=1 day everywhere — no overlapping-returns inflation (the
  2026-06-05 stablecoin screen's non-overlap lesson does not bite at h=1).

PRICE-ONLY CONTROL TWINS (pre-registered): every variant is paired with the
IDENTICAL transform computed from BTC close alone — 1/BTC for the USDT.D
variants (the dominance denominator is ~BTC: mechanical coupling), BTC for the
TOTAL variants, BTC k-day momentum driving the same trade structure for v5-v8.
A variant that does not beat its control's OOS Sharpe is REDUNDANT
(price-derivable; the 443-alpha sweep already mined price) regardless of gates.

GATES (house frozen standard, per-bar PORTFOLIO returns — not pooled trades):
  chronological 60/40 IS/OOS, embargo 2 days (h+1) before the split;
  FDR(BH q=0.05) across the 8 IS one-sided sharpe p-values;
  best-IS variant must then pass OOS p<0.05, DSR(n_trials=17) >= 0.90,
  both OOS halves positive, AND beat its control twin OOS.
  n_trials = 17 = 8 (this grid) + 9 (stablecoin-FLOWS family, 9 cells,
  reports/stablecoin_edge_screen_2026-06-05.md — same liquidity-regime
  neighborhood). COSTS: 10 bps/side on position changes, inside the returns.

PRE-REGISTERED CAVEATS: CRYPTOCAP aggregates are TV-recalculated indices
(survivorship/recalc drift possible; point-in-time truth accrues forward via
scripts/harvest_tv.py); spot-index legs proxy perp execution; the alt panel is
SURVIVOR-ONLY (membership chosen by 2026 cache availability — bias direction
is PRO-PASS, so it cannot mask a real edge); intra-panel equal-weight
rebalancing turnover is uncharged (also pro-pass). n_trials inclusion rule:
this grid + the nearest pre-registered family on the same data-source
neighborhood; other NO_EDGE families (funding, ETF flows, OI) are different
data sources and excluded. The `redundant` column is a point-estimate Sharpe
comparison with no uncertainty quantification — at sub-gate magnitudes it is
noise; it is meaningful only as the final PROMOTION gate (binding after
DSR>=0.90, where a control matching by luck is a ~3-sigma event).

Run: venv\\Scripts\\python.exe scripts\\run_tv_regime_screen.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.alpha_zoo.screen import dsr_for_returns, fdr_bh, sharpe_pvalue  # noqa: E402
from core.stat_tests import sharpe  # noqa: E402

TV = ROOT / "data" / "tv_cache"
REPORTS = ROOT / "reports"
WINDOW_START = "2020-01-01"
MIN_PANEL = 5  # min alt names per day for the equal-weight panel
SPLIT = 0.60
EMBARGO_D = 2  # h+1: IS positions this close to the split realize in OOS
FDR_Q = 0.05
DSR_MIN = 0.90  # house PROMOTION bar
N_TRIALS = 17  # 8 (this grid) + 9 (stablecoin-flows family 2026-06-05)
COST_BPS = 10.0  # per side, charged on position changes

# kind: trend = EMA20/50 cross; z = k-day-return z-score band; rot = L/S alts-vs-BTC
# from k-day momentum; mom = alt-panel position from k-day momentum sign.
# asset: btc = position in BTC; ls = 0.5-leg alts-vs-BTC; alt = position in alt panel.
# control: inv_btc / btc = same transform on 1/BTC / BTC; btc_mom = BTC k-day
# momentum driving the same trade structure.
VARIANTS = [
    {
        "name": "usdtd_trend",
        "series": "USDT.D",
        "kind": "trend",
        "direction": -1,
        "k": None,
        "asset": "btc",
        "control": "inv_btc",
    },
    {
        "name": "usdtd_z14",
        "series": "USDT.D",
        "kind": "z",
        "direction": -1,
        "k": 14,
        "asset": "btc",
        "control": "inv_btc",
    },
    {
        "name": "total_trend",
        "series": "TOTAL",
        "kind": "trend",
        "direction": 1,
        "k": None,
        "asset": "btc",
        "control": "btc",
    },
    {
        "name": "total_z14",
        "series": "TOTAL",
        "kind": "z",
        "direction": 1,
        "k": 14,
        "asset": "btc",
        "control": "btc",
    },
    {
        "name": "btcd_rot7",
        "series": "BTC.D",
        "kind": "rot",
        "direction": -1,
        "k": 7,
        "asset": "ls",
        "control": "btc_mom",
    },
    {
        "name": "btcd_rot14",
        "series": "BTC.D",
        "kind": "rot",
        "direction": -1,
        "k": 14,
        "asset": "ls",
        "control": "btc_mom",
    },
    {
        "name": "t3ratio_mom7",
        "series": "TOTAL3/TOTAL",
        "kind": "mom",
        "direction": 1,
        "k": 7,
        "asset": "alt",
        "control": "btc_mom",
    },
    {
        "name": "t3ratio_mom14",
        "series": "TOTAL3/TOTAL",
        "kind": "mom",
        "direction": 1,
        "k": 14,
        "asset": "alt",
        "control": "btc_mom",
    },
]


# ---------------------------------------------------------------- signals
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sig_ema_trend(series: pd.Series, direction: int = 1, fast: int = 20, slow: int = 50):
    raw = np.where(_ema(series, fast) > _ema(series, slow), 1, -1)
    return pd.Series(raw * direction, index=series.index)


def sig_mom_z(
    series: pd.Series, k: int, direction: int = 1, lookback: int = 60, z_thr: float = 1.0
) -> pd.Series:
    r = series.pct_change(k)
    z = (r - r.rolling(lookback).mean()) / r.rolling(lookback).std()
    raw = np.where(z > z_thr, 1, np.where(z < -z_thr, -1, 0))
    return pd.Series(raw * direction, index=series.index)


def sig_mom_sign(series: pd.Series, k: int, direction: int = 1) -> pd.Series:
    m = np.sign(series.pct_change(k))
    return pd.Series(m * direction, index=series.index).fillna(0)


# ---------------------------------------------------------------- returns
def pos_returns(pos: pd.Series, ret: pd.Series, cost_bps: float = COST_BPS) -> pd.Series:
    """Per-bar pnl: position decided at t close earns the t+1 return; entering/
    exiting that position is charged in the same row it first earns."""
    held = pos.fillna(0.0).shift(1).fillna(0.0)
    turn = held.diff().abs().fillna(0.0)
    return held * ret - cost_bps / 1e4 * turn


def ls_returns(
    dir_sig: pd.Series, alt_ret: pd.Series, btc_ret: pd.Series, cost_bps: float = COST_BPS
) -> pd.Series:
    """Market-neutral 0.5-leg long/short: dir=+1 means long alts / short BTC.
    Turnover counts both legs (0.5 each => |d_dir| total)."""
    held = dir_sig.fillna(0.0).shift(1).fillna(0.0)
    turn = held.diff().abs().fillna(0.0)
    return 0.5 * held * (alt_ret - btc_ret) - cost_bps / 1e4 * turn


# ---------------------------------------------------------------- gates
def split_masks(dates, split_frac: float = SPLIT, embargo_d: int = EMBARGO_D):
    n = len(dates)
    oos_start = int(n * split_frac)
    is_mask = np.zeros(n, dtype=bool)
    is_mask[: max(0, oos_start - embargo_d)] = True
    oos_mask = np.zeros(n, dtype=bool)
    oos_mask[oos_start:] = True
    return pd.Series(is_mask), pd.Series(oos_mask)


def eval_variant(ret_is, ret_oos, n_trials: int = N_TRIALS) -> dict:
    is_r = np.asarray(ret_is, dtype=float)
    oos = np.asarray(ret_oos, dtype=float)
    is_r = is_r[~np.isnan(is_r)]
    oos = oos[~np.isnan(oos)]
    h = len(oos) // 2
    return {
        "n_is": int(is_r.size),
        "n_oos": int(oos.size),
        "is_mean": float(is_r.mean()) if is_r.size else None,
        "is_sharpe": float(sharpe(is_r)) if is_r.size > 1 else None,
        "is_p": sharpe_pvalue(is_r) if is_r.size > 1 else 1.0,
        "oos_mean": float(oos.mean()) if oos.size else None,
        "oos_sharpe": float(sharpe(oos)) if oos.size > 1 else None,
        "oos_p": sharpe_pvalue(oos) if oos.size > 1 else 1.0,
        "oos_dsr": dsr_for_returns(oos, n_trials=n_trials) if oos.size > 1 else 0.0,
        "oos_half1_mean": float(oos[:h].mean()) if h else None,
        "oos_half2_mean": float(oos[h:].mean()) if h else None,
    }


def is_redundant(variant_oos, control_oos) -> bool:
    """True when the price-only control twin does at least as well OOS."""
    v = np.asarray(variant_oos, dtype=float)
    c = np.asarray(control_oos, dtype=float)
    v = v[~np.isnan(v)]
    c = c[~np.isnan(c)]
    sv = float(sharpe(v)) if v.size > 1 else float("-inf")
    sc = float(sharpe(c)) if c.size > 1 else float("-inf")
    return sv <= sc


# ---------------------------------------------------------------- data
def _load_close(symbol: str) -> pd.Series:
    p = TV / f"{symbol.replace(':', '_')}_1D.parquet"
    d = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")
    idx = pd.to_datetime(d["ts"], unit="s", utc=True)
    return pd.Series(d["close"].values, index=idx, name=symbol)


def build_master() -> pd.DataFrame:
    core = {
        "usdtd": _load_close("CRYPTOCAP:USDT.D"),
        "btcd": _load_close("CRYPTOCAP:BTC.D"),
        "total": _load_close("CRYPTOCAP:TOTAL"),
        "total3": _load_close("CRYPTOCAP:TOTAL3"),
        "btc": _load_close("BINANCE:BTCUSDT"),
    }
    m = pd.DataFrame(core)
    alt_rets = {}
    for p in sorted(TV.glob("BINANCE_*USDT_1D.parquet")):
        base = p.name[len("BINANCE_") : -len("USDT_1D.parquet")]
        if base == "BTC":
            continue
        d = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")
        idx = pd.to_datetime(d["ts"], unit="s", utc=True)
        alt_rets[base] = pd.Series(d["close"].values, index=idx).pct_change()
    alts = pd.DataFrame(alt_rets)
    m["alt_ret"] = alts.mean(axis=1).where(alts.count(axis=1) >= MIN_PANEL)
    m = m[m.index >= pd.Timestamp(WINDOW_START, tz="UTC")]
    m["btc_ret"] = m["btc"].pct_change()
    m["t3ratio"] = m["total3"] / m["total"]
    return m.dropna(subset=["usdtd", "btcd", "total", "total3", "btc", "alt_ret", "btc_ret"])


# ---------------------------------------------------------------- harness
def _signal_for(spec: dict, m: pd.DataFrame, use_control: bool) -> pd.Series:
    if use_control:
        if spec["control"] == "inv_btc":
            series = 1.0 / m["btc"]
        elif spec["control"] == "btc":
            series = m["btc"]
        else:  # btc_mom: BTC k-day momentum drives the same trade structure
            return sig_mom_sign(m["btc"], spec["k"], spec["direction"])
    else:
        series = {
            "USDT.D": m["usdtd"],
            "BTC.D": m["btcd"],
            "TOTAL": m["total"],
            "TOTAL3/TOTAL": m["t3ratio"],
        }[spec["series"]]
    if spec["kind"] == "trend":
        return sig_ema_trend(series, spec["direction"])
    if spec["kind"] == "z":
        return sig_mom_z(series, spec["k"], spec["direction"])
    return sig_mom_sign(series, spec["k"], spec["direction"])  # rot / mom


def _pnl_for(spec: dict, sig: pd.Series, m: pd.DataFrame) -> pd.Series:
    if spec["asset"] == "btc":
        return pos_returns(sig, m["btc_ret"])
    if spec["asset"] == "ls":
        return ls_returns(sig, m["alt_ret"], m["btc_ret"])
    return pos_returns(sig, m["alt_ret"])  # alt


def run() -> dict:
    m = build_master()
    dates = m.index
    is_m, oos_m = split_masks(dates)
    print(
        f"master frame: {len(m)} days  {dates[0].date()} -> {dates[-1].date()}  "
        f"(IS {int(is_m.sum())} / embargo {EMBARGO_D} / OOS {int(oos_m.sum())})"
    )

    rows = []
    for spec in VARIANTS:
        pnl = _pnl_for(spec, _signal_for(spec, m, use_control=False), m)
        ctl = _pnl_for(spec, _signal_for(spec, m, use_control=True), m)
        ev = eval_variant(pnl[is_m.values], pnl[oos_m.values])
        ev_c = eval_variant(ctl[is_m.values], ctl[oos_m.values])
        ev["name"] = spec["name"]
        ev["control_oos_sharpe"] = ev_c["oos_sharpe"]
        ev["redundant"] = is_redundant(pnl[oos_m.values], ctl[oos_m.values])
        rows.append(ev)

    flags = fdr_bh([r["is_p"] for r in rows], q=FDR_Q)
    for r, f in zip(rows, flags):
        r["fdr_pass"] = bool(f)

    cand = [r for r in rows if r["is_sharpe"] is not None]
    best = max(cand, key=lambda r: r["is_sharpe"]) if cand else None
    verdict = "NO_EDGE"
    if best is not None:
        ok = (
            best["fdr_pass"]
            and best["oos_p"] < 0.05
            and best["oos_dsr"] >= DSR_MIN
            and (best["oos_half1_mean"] or 0) > 0
            and (best["oos_half2_mean"] or 0) > 0
            and not best["redundant"]
        )
        verdict = "PASS_FORWARD_VALIDATE" if ok else "NO_EDGE"

    out = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "verdict": verdict,
        "best": best["name"] if best else None,
        "grid": rows,
        "n_trials": N_TRIALS,
        "window": [str(dates[0].date()), str(dates[-1].date())],
        "n_days": int(len(m)),
        "gates": {
            "split": SPLIT,
            "embargo_d": EMBARGO_D,
            "fdr_q": FDR_Q,
            "dsr_min": DSR_MIN,
            "cost_bps_side": COST_BPS,
        },
        "notes": "per-bar portfolio returns; price-only control twins; spot-index "
        "legs proxy perp execution; CRYPTOCAP recalc drift caveat",
    }
    REPORTS.mkdir(exist_ok=True)
    stem = REPORTS / f"tv_regime_screen_{out['date']}"
    stem.with_suffix(".json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    md = [
        f"# TradingView dominance/aggregate regime screen — {out['date']}",
        f"\n**VERDICT: {verdict}**  (best-IS variant: {out['best']}; n_trials={N_TRIALS}; "
        f"gates: FDR q={FDR_Q}, OOS p<0.05, DSR>={DSR_MIN}, both OOS halves >0, "
        f"must beat price-only control)\n",
        f"Window {out['window'][0]} -> {out['window'][1]}, {out['n_days']} days, "
        f"costs {COST_BPS} bps/side.\n",
        "| variant | IS Sharpe | IS p | FDR | OOS Sharpe | OOS p | DSR | ctl OOS | redundant |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:

        def _f(x, nd=3):
            return "-" if x is None else round(x, nd)

        md.append(
            f"| {r['name']} | {_f(r['is_sharpe'])} | {_f(r['is_p'], 4)} | "
            f"{'Y' if r['fdr_pass'] else 'n'} | {_f(r['oos_sharpe'])} | {_f(r['oos_p'], 4)} | "
            f"{_f(r['oos_dsr'], 4)} | {_f(r['control_oos_sharpe'])} | "
            f"{'Y' if r['redundant'] else 'n'} |"
        )
    md.append(
        "\nPre-registered grid + caveats: see module docstring (scripts/run_tv_regime_screen.py)."
    )
    stem.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nVERDICT: {verdict}")
    print(f"report -> {stem}.md")
    return out


if __name__ == "__main__":
    run()
