"""Indicator bias audit — look-ahead + recursive/repaint, on the bot's REAL indicators.

The freqtrade `lookahead-analysis` + `recursive-analysis` capability, wired onto this bot's
actual indicator functions (core/agents/indicators.py: ema/rsi/atr/adx) via the extended
core/alpha_zoo/bias_checks.py detectors. Two questions, both pure truth-exposure (cannot
manufacture edge):

  1. LOOK-AHEAD: does any indicator's value at a past bar change when future bars are
     removed? (If yes → the backtest peeked → every backtest verdict is suspect.)
  2. RECURSIVE/REPAINT: how much does an indicator's final-bar value drift with the amount
     of warmup history? (Quantifies the backtest-vs-live gap + the warmup each needs.)

A clean result (0 look-ahead, small drift past a known warmup) is itself valuable: it means
the NO-EDGE findings are real, not a look-ahead artifact, and tells the live loop how many
startup candles each indicator needs. Records reports/indicator_bias_audit_<date>.md.
Read-only.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "ohlcv_cache"
REPORTS = ROOT / "reports"

from core.agents.indicators import adx, atr, ema, rsi  # noqa: E402
from core.alpha_zoo.bias_checks import lookahead_violations, recursive_instability  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "DOGE", "ZEC"]
TF = "1h"
N_BARS = 1200  # recent window per coin
CHECK_LAST = 40  # look-ahead: recompute on prefixes for the last N bars (bounds O(n^2))


def _load(coin: str) -> pd.DataFrame | None:
    p = CACHE / f"{coin}-USDT_{TF}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)[["open", "high", "low", "close"]].dropna()
    df = df.tail(N_BARS).reset_index(drop=True)
    return df if len(df) > 200 else None


def _indicators():
    """name -> (compute_fn(series)->series, needs_dataframe)."""
    return {
        "ema20": (lambda s: ema(s, 20), False),
        "ema50": (lambda s: ema(s, 50), False),
        "rsi14": (lambda s: rsi(s, 14), False),
        "atr14": (lambda df: atr(df, 14), True),
        "adx14": (lambda df: adx(df, 14), True),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    rows = []
    for coin in COINS:
        df = _load(coin)
        if df is None:
            rows.append({"coin": coin, "skip": True})
            continue
        close = df["close"]
        ohlc = df[["high", "low", "close"]]
        n = len(df)
        check_idx = range(n - CHECK_LAST, n)
        for name, (fn, needs_df) in _indicators().items():
            series = ohlc if needs_df else close
            la = lookahead_violations(fn, series, check_indices=check_idx)
            rec = recursive_instability(fn, series)
            rows.append(
                {
                    "coin": coin,
                    "indicator": name,
                    "lookahead_violations": len(la),
                    "rec_max_rel_drift": rec["max_rel_drift"],
                    "rec_min_stable_len": rec["min_stable_len"],
                }
            )

    audited = [r for r in rows if not r.get("skip")]
    any_la = sum(r["lookahead_violations"] for r in audited)
    # per-indicator rollups
    per_ind = {}
    for r in audited:
        d = per_ind.setdefault(r["indicator"], {"la": 0, "max_drift": 0.0, "warmup": 0})
        d["la"] += r["lookahead_violations"]
        if np.isfinite(r["rec_max_rel_drift"]):
            d["max_drift"] = max(d["max_drift"], r["rec_max_rel_drift"])
        if r["rec_min_stable_len"]:
            d["warmup"] = max(d["warmup"], r["rec_min_stable_len"])

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    L = [
        f"# Indicator bias audit — {today}",
        "",
        f"Bot's real indicators (`core/agents/indicators.py`) on {len([r for r in rows if r.get('skip')]) and ''}"
        f"{', '.join(c for c in COINS)} {TF}, last {N_BARS} bars. "
        "Detectors: `bias_checks.lookahead_violations` (causal recompute) + `recursive_instability` "
        "(warmup/repaint drift).",
        "",
        f"## Verdict: {'LOOK-AHEAD DETECTED — investigate' if any_la else 'CLEAN — no look-ahead; indicators are causal'}",
        "",
        f"- Total look-ahead violations across all indicators/coins (last {CHECK_LAST} bars each): **{any_la}**",
        "- Recursive drift below quantifies the backtest-vs-live gap at the final bar and the warmup each indicator needs.",
        "",
        "## Per-indicator rollup",
        "| indicator | look-ahead viol. | max recursive rel-drift | recommended warmup (bars) |",
        "|---|---|---|---|",
    ]
    for name in _indicators():
        d = per_ind.get(name, {"la": 0, "max_drift": float("nan"), "warmup": 0})
        warm = d["warmup"] or "—"
        L.append(f"| {name} | {d['la']} | {d['max_drift']:.2e} | {warm} |")

    L += [
        "",
        "## Per coin × indicator",
        "| coin | indicator | LA viol. | rec rel-drift | min stable len |",
        "|---|---|---|---|---|",
    ]
    for r in audited:
        L.append(
            f"| {r['coin']} | {r['indicator']} | {r['lookahead_violations']} | "
            f"{r['rec_max_rel_drift']:.2e} | {r['rec_min_stable_len'] or '—'} |"
        )
    skipped = [r["coin"] for r in rows if r.get("skip")]
    if skipped:
        L += ["", f"_Skipped (insufficient data): {', '.join(skipped)}_"]
    L += [
        "",
        "_EMA/RSI/ATR/ADX use `ewm(adjust=False)` (recursive): final-bar value converges as "
        "warmup accrues, so a small non-zero drift is expected and the 'recommended warmup' is the "
        "honest number of startup candles to load before trusting them. Zero look-ahead confirms the "
        "decision features are causal — the NO-EDGE findings are not a peeking artifact._",
    ]

    out_md = REPORTS / f"indicator_bias_audit_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"indicator_bias_audit_{today}.json").write_text(
        json.dumps(
            {
                "date": today,
                "total_lookahead_violations": int(any_la),
                "per_indicator": per_ind,
                "rows": audited,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(
        f"Look-ahead violations (total): {any_la}  -> "
        f"{'INVESTIGATE' if any_la else 'CLEAN (causal)'}"
    )
    for name in _indicators():
        d = per_ind.get(name, {})
        print(
            f"  {name:7} LA={d.get('la', 0)}  max_rel_drift={d.get('max_drift', float('nan')):.2e}  "
            f"warmup~{d.get('warmup', 0) or '—'}"
        )
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
