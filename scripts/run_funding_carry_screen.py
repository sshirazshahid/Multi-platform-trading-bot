"""Funding/carry edge screen (Pine #4 port) — PRE-REGISTERED, frozen gates.

Variant grid (DECLARED BEFORE RESULTS; do not extend after seeing output):
    fc_thr    in {0.01, 0.03, 0.05}   (% per 8h, EMA-smoothed funding threshold)
    fc_smooth in {1, 8}               (EMA bars on the 1h-aligned funding series)
  -> 6 variants. Direction mapping FIXED to Pine #4 (carry-long on negative
  funding in an uptrend; fade-short on positive funding in a downtrend).
  No inversion sweep. rr fixed at 2.0 (Pine #4 default), atr_mult 1.5.

Gates (house frozen-gate standard, mirrors scripts/run_funding_edge_search.py):
  stage-1 IS (first 60% by time, embargo = max_hold before the split): FDR(BH,
  q=0.05) across the 6 variants' one-sided sharpe p-values on pooled per-trade
  net returns.
  stage-2 OOS (last 40%) on the best-IS-Sharpe variant: p < 0.05,
  DSR(n_trials=27) >= 0.90 (the house PROMOTION bar; 0.10 is the lenient
  inherited bar — too soft to promote on), both OOS halves positive mean.
  n_trials = 27 = 6 (this grid) + 7 + 14 (the two documented prior
  funding-family runs, 2026-05-26 prereg doc sections 9-10) — traceable
  family deflation.
  Costs are inside the engine harness (0.05%/side fee + 5-10 bps slippage);
  per-trade FUNDING ACCRUAL from actual settles crossed is added on top.

KNOWN LIMITATION (2026-06-10 adversarial review): pooled per-trade inference
treats concurrent trades across 31 correlated symbols as i.i.d., inflating
effective n severalfold — ANTI-conservative. A stage-2 pass from this script is
therefore NOT a promotion: re-test with per-bar portfolio aggregation
(carry_eval pattern, run_funding_edge_search.py:137-168) before believing it.
The 2026-06-10 run's NO_EDGE verdict survives this bias a fortiori (every
identified bias pushed TOWARD passing and all 6 variants still failed).

Data: spot 1h OHLCV from data/ohlcv_cache (deep-history majors, >=20k bars);
REAL Binance 8h funding history backfilled via quant_suite/funding_carry.py.
Spot price frame + perp funding is a documented approximation (no crypto perp
OHLCV cached offline); the funding leg uses actual settle timestamps.

Prior: time-series funding screened NO_EDGE on ~70d (best Sharpe 0.11,
pval 0.19, DSR 0.00). This is a RE-TEST at ~3y depth, not a fresh idea.

Run: venv\\Scripts\\python.exe scripts\\run_funding_carry_screen.py
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
from quant_suite import funding_carry as FC  # noqa: E402
from quant_suite.engine import BTParams, backtest, prep  # noqa: E402

OHLCV = ROOT / "data" / "ohlcv_cache"
REPORTS = ROOT / "reports"
MIN_BARS = 20_000  # deep-history majors only (~3y of 1h)
GRID = [(thr, sm) for thr in (0.01, 0.03, 0.05) for sm in (1, 8)]
N_TRIALS = 27  # 6 (this grid) + 7 + 14 (documented prior funding family)
SPLIT = 0.60  # chronological IS fraction
FDR_Q = 0.05
DSR_MIN = 0.90  # house PROMOTION bar (0.10 = lenient inherited bar)
EMBARGO_H = 73  # max_hold (72 bars) + 1: IS entries this close to the
# split realize outcomes inside OOS -> excluded from IS


def _ts(v) -> float:
    """UTC-explicit epoch seconds for a trade dt (numpy datetime64 -> tz-naive)."""
    t = pd.Timestamp(v)
    return (t.tz_localize("UTC") if t.tz is None else t).timestamp()


def discover_symbols() -> list[str]:
    bases = []
    for p in sorted(OHLCV.glob("*-USDT_1h.parquet")):
        if "USDTUSDT" in p.name:  # perp-form caches (analysis-only bases)
            continue
        try:
            n = len(pd.read_parquet(p, columns=["ts"]))
        except Exception:
            continue
        if n >= MIN_BARS:
            bases.append(p.name.replace("-USDT_1h.parquet", ""))
    return bases


def load_1h(base: str) -> pd.DataFrame:
    d = pd.read_parquet(OHLCV / f"{base}-USDT_1h.parquet")
    d = d.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    d["dt"] = pd.to_datetime(d["ts"], unit="s", utc=True)
    return d


def accrued_funding(trade: dict, fr: pd.DataFrame) -> float:
    """Funding realized while holding: -side * rate summed over settles crossed.

    Entry fills at the open of the bar AFTER the signal bar (engine harness), so
    the hold window starts at signal_dt + 1h (exclusive). SL/TP exits fill
    INTRABAR during the exit bar, so their window ends at that bar's OPEN
    (inclusive); only TIME exits (close of the exit bar) extend to its close."""
    t0 = pd.Timestamp(trade["dt"])
    if t0.tz is None:
        t0 = t0.tz_localize("UTC")
    start = (t0 + pd.Timedelta(hours=1)).timestamp()
    end_h = int(trade["bars"]) + (1 if trade.get("outcome") == "TIME" else 0)
    end = (t0 + pd.Timedelta(hours=end_h)).timestamp()
    win = fr[(fr["ts"] > start) & (fr["ts"] <= end)]
    if not len(win):
        return 0.0
    return float(-trade["side"] * win["rate"].sum())


def run() -> dict:
    import ccxt

    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    bases = discover_symbols()
    print(f"deep-history majors: {len(bases)} -> {bases}")

    per_variant: dict[tuple, list[dict]] = {g: [] for g in GRID}
    used, skipped = [], []
    for base in bases:
        d = load_1h(base)
        perp = f"{base}/USDT:USDT"
        try:
            fr = FC.load_or_backfill(ex, perp, since_ms=int(d["ts"].iloc[0]) * 1000)
        except Exception as e:
            skipped.append((base, str(e)[:80]))
            continue
        if len(fr) < 90:  # need at least ~1 month of settles
            skipped.append((base, f"funding rows={len(fr)}"))
            continue
        ff = FC.to_frame(fr)
        used.append(base)
        for thr, sm in GRID:
            p = BTParams(rr=2.0, fc_thr=thr, fc_smooth=sm)
            dd = prep(d, p=p, funding=ff)
            for t in backtest(dd, "carry", p):
                t["net"] = t["ret"] + accrued_funding(t, fr)
                t["symbol"] = base
                per_variant[(thr, sm)].append(t)
        print(
            f"  {base}: funding {len(fr)} settles, "
            f"trades/variant {[len(per_variant[g]) for g in GRID]}"
        )

    # chronological split boundary from the pooled union of trade times
    all_dts = sorted(_ts(t["dt"]) for g in GRID for t in per_variant[g])
    if not all_dts:
        return {"verdict": "NO_TRADES", "used": used, "skipped": skipped}
    t_split = all_dts[0] + SPLIT * (all_dts[-1] - all_dts[0])
    t_embargo = t_split - EMBARGO_H * 3600  # IS entries after this bleed into OOS

    rows = []
    for g in GRID:
        tr = sorted(per_variant[g], key=lambda t: _ts(t["dt"]))
        is_r = [t["net"] for t in tr if _ts(t["dt"]) <= t_embargo]
        oos_r = [t["net"] for t in tr if _ts(t["dt"]) > t_split]
        rows.append(
            {
                "thr": g[0],
                "smooth": g[1],
                "n_is": len(is_r),
                "n_oos": len(oos_r),
                "is_mean_pct": float(np.mean(is_r)) * 100 if is_r else None,
                "is_sharpe": float(sharpe(np.array(is_r))) if len(is_r) > 1 else None,
                "is_p": sharpe_pvalue(is_r) if len(is_r) > 1 else 1.0,
                "oos_mean_pct": float(np.mean(oos_r)) * 100 if oos_r else None,
                "oos_r": oos_r,
            }
        )

    flags = fdr_bh([r["is_p"] for r in rows], q=FDR_Q)
    for r, f in zip(rows, flags):
        r["fdr_pass"] = bool(f)

    cand = [r for r in rows if r["is_sharpe"] is not None]
    best = max(cand, key=lambda r: r["is_sharpe"]) if cand else None
    verdict, detail = "NO_EDGE", {}
    if best is not None:
        oos = np.array(best["oos_r"])
        h = len(oos) // 2
        detail = {
            "best_variant": {"thr": best["thr"], "smooth": best["smooth"]},
            "fdr_pass": best["fdr_pass"],
            "oos_n": int(oos.size),
            "oos_mean_pct": float(oos.mean() * 100) if oos.size else None,
            "oos_p": sharpe_pvalue(oos) if oos.size > 1 else 1.0,
            "oos_dsr": dsr_for_returns(oos, n_trials=N_TRIALS) if oos.size > 1 else 0.0,
            "oos_half1_mean": float(oos[:h].mean()) if h else None,
            "oos_half2_mean": float(oos[h:].mean()) if h else None,
        }
        promote = (
            best["fdr_pass"]
            and detail["oos_p"] < 0.05
            and detail["oos_dsr"] >= DSR_MIN
            and (detail["oos_half1_mean"] or 0) > 0
            and (detail["oos_half2_mean"] or 0) > 0
        )
        # deliberately NOT named "PROMOTE": pooled per-trade inference is
        # anti-conservative (see docstring) — a pass needs per-bar revalidation
        verdict = "STAGE2_PASS_NEEDS_REVALIDATION" if promote else "NO_EDGE"

    for r in rows:
        r.pop("oos_r", None)
    out = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "verdict": verdict,
        "grid": rows,
        "best": detail,
        "n_trials": N_TRIALS,
        "symbols_used": used,
        "skipped": skipped,
        "notes": "spot 1h price frame + real perp 8h funding accrual; engine "
        "costs (0.05%/side + 5-10bps slip) inside per-trade ret",
    }
    REPORTS.mkdir(exist_ok=True)
    stem = REPORTS / f"funding_carry_screen_{out['date']}"
    stem.with_suffix(".json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    md = [
        f"# Funding/carry screen (Pine #4 port) — {out['date']}",
        f"\n**VERDICT: {verdict}** (n_trials={N_TRIALS}, gates: FDR q={FDR_Q}, "
        f"OOS p<0.05, DSR>={DSR_MIN}, both OOS halves > 0)\n",
        "| thr %/8h | smooth | n IS | n OOS | IS mean % | IS Sharpe | IS p | FDR | OOS mean % |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        md.append(
            f"| {r['thr']} | {r['smooth']} | {r['n_is']} | {r['n_oos']} | "
            f"{r['is_mean_pct'] if r['is_mean_pct'] is None else round(r['is_mean_pct'], 4)} | "
            f"{r['is_sharpe'] if r['is_sharpe'] is None else round(r['is_sharpe'], 3)} | "
            f"{round(r['is_p'], 4)} | {'Y' if r['fdr_pass'] else 'n'} | "
            f"{r['oos_mean_pct'] if r['oos_mean_pct'] is None else round(r['oos_mean_pct'], 4)} |"
        )
    md.append(f"\nBest-IS variant detail: {json.dumps(detail, default=str)}")
    md.append(f"\nSymbols ({len(used)}): {', '.join(used)}")
    if skipped:
        md.append(f"\nSkipped: {skipped}")
    stem.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nVERDICT: {verdict}")
    print(f"report -> {stem}.md")
    return out


if __name__ == "__main__":
    run()
