"""TradFi macro -> crypto regime screen (TradingView global data) — PRE-REGISTERED.

QUESTION: do traditional-market series (dollar index, equity vol, equities,
yields, gold) — a data family this program has never screened AS CRYPTO INPUTS
(the 2026-06-07 index study traded the indices themselves) — predict BTC
next-day returns? Uses TradingView's global market data via quant_suite/
tv_client.py (the "Global Market Data" capability), daily, 2020-01-01+.

DATA (data/tv_cache, scripts/backfill_tv_cache.py --symbols ... 1D):
  DXY:  TVC:DXY   (fallback ICEUS:DXY)      dollar index
  VIX:  TVC:VIX   (fallback CBOE:VIX)       equity implied vol
  SPX:  SP:SPX    (fallback TVC:SPX)        US equities
  US10Y:TVC:US10Y                            10y yield
  GOLD: TVC:GOLD  (fallback OANDA:XAUUSD)   gold spot
  BTC leg: BINANCE:BTCUSDT 1D (existing cache).
  Fallback order DECLARED BEFORE the pull; first cache file present wins.

CAUSALITY (load-bearing, pre-registered): tradfi daily bars are stamped by
SESSION date, and several of these sessions (ICE DXY, treasury futures) cross
00:00 UTC — a bar labeled day t can finalize AFTER crypto's day-t close, so
same-date use would be look-ahead. Macro series therefore enter via
align_macro(): date-normalized, ffilled onto the 7d/week crypto calendar
(weekends/holidays carry the last close), then LAGGED ONE crypto day. With
pos_returns()'s own decision shift, macro info dated t affects positions
earning t+2 at the earliest. Conservative by construction.

VARIANT GRID (7, DECLARED BEFORE RESULTS; do not extend after seeing output):
  v1 dxy_trend    EMA20<EMA50 on DXY -> long BTC, else short (rising $ = risk-off)
  v2 vix_trend    EMA20<EMA50 on VIX -> long BTC, else short
  v3 spx_trend    EMA20>EMA50 on SPX -> long BTC, else short (risk-on spillover)
  v4 us10y_trend  EMA20<EMA50 on US10Y -> long BTC, else short (easing = risk-on)
  v5 gold_trend   EMA20>EMA50 on GOLD -> long BTC, else short (store-of-value bid)
  v6 dxy_z14      14d-ret z(60d) on DXY: z<-1 long BTC / z>+1 short / flat
  v7 vix_z14      same on VIX
  All h=1 day, position in BTC only.

PRICE-ONLY CONTROL TWINS (pre-registered): the identical transform on BTC
close itself, direction +1 (plain BTC trend/momentum) — a macro variant that
cannot beat the BTC-price baseline OOS is REDUNDANT (correlation in disguise)
regardless of gates.

GATES: identical frozen harness as scripts/run_tv_regime_screen.py (reviewed
2026-06-11 by 3-agent adversarial pass) — 60/40 chronological IS/OOS, embargo
2d, FDR(BH q=0.05) on per-bar IS sharpe p-values, best-IS variant needs OOS
p<0.05, DSR(n_trials) >= 0.90, both OOS halves positive, beats control.
n_trials = 15 = 7 (this grid) + 8 (the 2026-06-10 dominance regime grid: same
harness/structure neighborhood, different source). Costs 10 bps/side.

HONEST PRIOR: LOW. BTC-SPX correlation is unstable and well-known; risk-on/off
overlays are heavily mined by the market; the structurally-identical dominance
family just screened NO_EDGE. This screen exists because tradfi is a genuinely
NEW data source for this program, not because a pass is expected.

Run: venv\\Scripts\\python.exe scripts\\run_tv_macro_screen.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TV = ROOT / "data" / "tv_cache"
REPORTS = ROOT / "reports"
WINDOW_START = "2020-01-01"
N_TRIALS = 15  # 7 (this grid) + 8 (dominance regime grid 2026-06-10)

# (key, [symbol, fallback...], direction, label)
SERIES = [
    ("dxy", ["TVC:DXY", "ICEUS:DXY"], -1),
    ("vix", ["TVC:VIX", "CBOE:VIX"], -1),
    ("spx", ["SP:SPX", "TVC:SPX"], 1),
    ("us10y", ["TVC:US10Y"], -1),
    ("gold", ["TVC:GOLD", "OANDA:XAUUSD"], 1),
]
VARIANTS = [
    {"name": "dxy_trend", "series": "dxy", "kind": "trend", "direction": -1, "k": None},
    {"name": "vix_trend", "series": "vix", "kind": "trend", "direction": -1, "k": None},
    {"name": "spx_trend", "series": "spx", "kind": "trend", "direction": 1, "k": None},
    {"name": "us10y_trend", "series": "us10y", "kind": "trend", "direction": -1, "k": None},
    {"name": "gold_trend", "series": "gold", "kind": "trend", "direction": 1, "k": None},
    {"name": "dxy_z14", "series": "dxy", "kind": "z", "direction": -1, "k": 14},
    {"name": "vix_z14", "series": "vix", "kind": "z", "direction": -1, "k": 14},
]


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "tv_regime_screen", ROOT / "scripts" / "run_tv_regime_screen.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _load_harness()  # sig_ema_trend, sig_mom_z, pos_returns, split_masks, eval_variant, ...


def align_macro(macro: pd.Series, crypto_idx: pd.DatetimeIndex) -> pd.Series:
    """Macro daily closes (trading-day calendar) -> crypto 7d/week calendar.

    Date-normalized, ffilled (weekends/holidays carry the last close), then
    lagged ONE crypto day: tradfi sessions can cross 00:00 UTC, so a bar
    labeled day t may finalize after crypto's day-t close — same-date use
    would be look-ahead."""
    m = macro.copy()
    m.index = pd.to_datetime(m.index, utc=True).normalize()
    m = m[~m.index.duplicated(keep="last")].sort_index()
    out = m.reindex(crypto_idx.normalize(), method="ffill")
    out.index = crypto_idx
    return out.shift(1)


def _load_close_first(symbols: list[str]) -> tuple[pd.Series, str] | None:
    for sym in symbols:
        p = TV / f"{sym.replace(':', '_')}_1D.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")
        idx = pd.to_datetime(d["ts"], unit="s", utc=True)
        return pd.Series(d["close"].values, index=idx, name=sym), sym
    return None


def build_master() -> tuple[pd.DataFrame, dict]:
    btc, _ = _load_close_first(["BINANCE:BTCUSDT"])
    m = pd.DataFrame({"btc": btc})
    m = m[m.index >= pd.Timestamp(WINDOW_START, tz="UTC")]
    used = {}
    for key, syms, _d in SERIES:
        got = _load_close_first(syms)
        if got is None:
            raise FileNotFoundError(f"no tv_cache file for {key} (tried {syms})")
        ser, sym = got
        used[key] = sym
        m[key] = align_macro(ser, m.index)
    m["btc_ret"] = m["btc"].pct_change()
    return m.dropna(), used


def run() -> dict:
    m, used = build_master()
    dates = m.index
    is_m, oos_m = H.split_masks(dates)
    print(
        f"master frame: {len(m)} days  {dates[0].date()} -> {dates[-1].date()}  "
        f"(IS {int(is_m.sum())} / embargo {H.EMBARGO_D} / OOS {int(oos_m.sum())})  series={used}"
    )

    rows = []
    for spec in VARIANTS:
        s = m[spec["series"]]
        if spec["kind"] == "trend":
            sig = H.sig_ema_trend(s, spec["direction"])
            ctl_sig = H.sig_ema_trend(m["btc"], 1)
        else:
            sig = H.sig_mom_z(s, spec["k"], spec["direction"])
            ctl_sig = H.sig_mom_z(m["btc"], spec["k"], 1)
        pnl = H.pos_returns(sig, m["btc_ret"])
        ctl = H.pos_returns(ctl_sig, m["btc_ret"])
        ev = H.eval_variant(pnl[is_m.values], pnl[oos_m.values], n_trials=N_TRIALS)
        ev_c = H.eval_variant(ctl[is_m.values], ctl[oos_m.values], n_trials=N_TRIALS)
        ev["name"] = spec["name"]
        ev["control_oos_sharpe"] = ev_c["oos_sharpe"]
        ev["redundant"] = H.is_redundant(pnl[oos_m.values], ctl[oos_m.values])
        rows.append(ev)

    from core.alpha_zoo.screen import fdr_bh

    flags = fdr_bh([r["is_p"] for r in rows], q=H.FDR_Q)
    for r, f in zip(rows, flags):
        r["fdr_pass"] = bool(f)

    cand = [r for r in rows if r["is_sharpe"] is not None]
    best = max(cand, key=lambda r: r["is_sharpe"]) if cand else None
    verdict = "NO_EDGE"
    if best is not None:
        ok = (
            best["fdr_pass"]
            and best["oos_p"] < 0.05
            and best["oos_dsr"] >= H.DSR_MIN
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
        "series_used": used,
        "window": [str(dates[0].date()), str(dates[-1].date())],
        "n_days": int(len(m)),
        "gates": {
            "split": H.SPLIT,
            "embargo_d": H.EMBARGO_D,
            "fdr_q": H.FDR_Q,
            "dsr_min": H.DSR_MIN,
            "cost_bps_side": H.COST_BPS,
        },
        "notes": "macro series ffilled to crypto calendar + 1-day lag (session-stamp "
        "causality); BTC-only positions; controls = same transform on BTC close",
    }
    REPORTS.mkdir(exist_ok=True)
    stem = REPORTS / f"tv_macro_screen_{out['date']}"
    stem.with_suffix(".json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    md = [
        f"# TradFi macro -> crypto regime screen (TradingView global data) — {out['date']}",
        f"\n**VERDICT: {verdict}**  (best-IS variant: {out['best']}; n_trials={N_TRIALS}; "
        f"gates: FDR q={H.FDR_Q}, OOS p<0.05, DSR>={H.DSR_MIN}, both OOS halves >0, "
        f"must beat BTC-price control)\n",
        f"Window {out['window'][0]} -> {out['window'][1]}, {out['n_days']} days, "
        f"costs {H.COST_BPS} bps/side. Series: {used}\n",
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
    md.append("\nPre-registered grid, causality lag + caveats: see module docstring.")
    stem.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nVERDICT: {verdict}")
    print(f"report -> {stem}.md")
    return out


if __name__ == "__main__":
    run()
