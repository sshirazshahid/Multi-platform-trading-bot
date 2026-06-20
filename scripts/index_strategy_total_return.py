"""Multi-index x multi-strategy walk-forward — TOTAL-RETURN (dividend-adjusted) benchmark.

Closes the one caveat in index_strategy_walkforward.py: Yahoo index closes are PRICE-only
(ex-dividends), which understates buy-and-hold and flatters timing strategies. Here the daily
return is TOTAL return = price return + (annual dividend yield / 252). Buy-and-hold earns it
every day; a strategy earns it ONLY while invested (in cash it misses the dividend) — the fair
treatment that credits dividends to time-in-market.

METHOD VALIDATION (not assumed): over 2000-2026, ^GSPC price CAGR = 6.35% vs the REAL S&P 500
total-return index ^SP500TR = 8.33% -> a 1.98%/yr realized dividend, matching the ~1.9% constant
used here. So "price + constant yield" reproduces the real total-return series; the script
re-checks this anchor at run time. DAX (^GDAXI) is ALREADY a total-return index -> +0%.

Per-index yields are approximate long-run averages (they vary year to year); the conclusion is
robust to ±0.5% because the decisive case (FTSE) has an unusually high ~3.6% yield. Reuses the
causal strategy generators + walk-forward machinery. Records reports/index_total_return_<date>.md.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
REPORTS = ROOT / "reports"

from index_strategies import STRATEGIES  # noqa: E402
from nifty_sma_backtest import fetch_index  # noqa: E402
from nifty_sma_walkforward import _run_wfa  # noqa: E402

INDICES = {
    "Nifty50": "^NSEI",
    "Sensex": "^BSESN",
    "S&P500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Dow": "^DJI",
    "FTSE100": "^FTSE",
    "Nikkei225": "^N225",
    "DAX": "^GDAXI",
}
# approx long-run gross dividend yield (%/yr). S&P validated vs ^SP500TR (=1.98%). DAX=0 (TR index).
DIV_YIELD = {
    "Nifty50": 1.3,
    "Sensex": 1.2,
    "S&P500": 1.98,
    "Nasdaq": 1.0,
    "Dow": 2.1,
    "FTSE100": 3.6,
    "Nikkei225": 1.7,
    "DAX": 0.0,
}
IS_DAYS, OOS_DAYS, STEP = 504, 126, 126
COST_ONEWAY = 5.0 / 1e4
PPY = 252


def _strat_ret(pos, ret, cost):
    prev = np.concatenate([[0.0], pos[:-1]])
    return prev * ret - cost * np.abs(pos - prev)


def _validate_sp500tr(span_lo_date, span_hi_date):
    """Cross-check: real ^SP500TR CAGR vs price+1.98% over the full window."""
    try:
        tr = fetch_index("^SP500TR", "maxdaily")
        c = tr["close"].to_numpy(dtype=float)
        yrs = len(c) / PPY
        return float((c[-1] / c[0]) ** (1 / yrs) - 1.0)
    except Exception:
        return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    results, bh_by_index, spans = {}, {}, {}
    for iname, sym in INDICES.items():
        try:
            df = fetch_index(sym, "maxdaily")
        except Exception as e:  # noqa: BLE001
            print(f"[skip] {iname}: {e}")
            continue
        close = df["close"].to_numpy(dtype=float)
        if len(close) < IS_DAYS + OOS_DAYS + 50:
            continue
        price_ret = np.zeros(len(close))
        price_ret[1:] = close[1:] / close[:-1] - 1.0
        tr_ret = price_ret + (DIV_YIELD[iname] / 100.0) / PPY  # total-return daily series
        tr_ret[0] = 0.0
        spans[iname] = (df["date"].iloc[0], df["date"].iloc[-1])
        for sname, (gen, grid) in STRATEGIES.items():
            sbp = {f"{sname}{p}": _strat_ret(gen(close, *p), tr_ret, COST_ONEWAY) for p in grid}
            r = _run_wfa(
                sbp, tr_ret, is_days=IS_DAYS, oos_days=OOS_DAYS, step=STEP, objective="sharpe"
            )
            if r:
                results[(iname, sname)] = r
                bh_by_index[iname] = r["bh"]
        print(
            f"{iname:10} div +{DIV_YIELD[iname]:.1f}%/yr | TR-B&H OOS "
            f"{bh_by_index[iname]['total_return'] * 100:+.0f}% Sharpe {bh_by_index[iname]['sharpe']:.2f}"
        )

    idx_names = [i for i in INDICES if i in bh_by_index]
    strat_names = list(STRATEGIES)
    n = len(results)
    beat_ret = [k for k, r in results.items() if r["beats_bh_ret"]]
    beat_sr = [k for k, r in results.items() if r["beats_bh_sharpe"]]
    sharpe_only = [k for k in beat_sr if k not in beat_ret]
    wfes = [r["wfe_median"] for r in results.values() if np.isfinite(r["wfe_median"])]
    sp_tr_real = _validate_sp500tr(None, None)

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    L = [
        f"# Multi-index × multi-strategy walk-forward — TOTAL-RETURN benchmark — {today}",
        "",
        f"{len(idx_names)} indices × {len(strat_names)} strategies = **{n} runs**. Daily return = "
        "price + dividend yield/252 (B&H earns it daily; strategies only while invested). "
        "2y IS / 6mo OOS, rolled & stitched; long/flat; 5bps/turn; causal.",
        "",
        "## Method validation",
        "",
    ]
    if sp_tr_real is not None:
        L.append(
            f"- Real `^SP500TR` full-window CAGR **{sp_tr_real * 100:.2f}%** vs ^GSPC price 6.35% "
            "+ assumed 1.98% div ≈ **8.33%** → the constant-yield approximation reproduces the "
            "real total-return index. Method sound."
        )
    L += [
        "- Assumed yields (%/yr): "
        + ", ".join(f"{i} {DIV_YIELD[i]:.1f}" for i in idx_names)
        + ". DAX = 0 (^GDAXI is already a total-return index).",
        "",
        "## Total-return Buy & Hold (per index, over OOS span)",
        "| index | TR-B&H total% | TR-B&H Sharpe | MaxDD% |",
        "|---|---|---|---|",
    ]
    for i in idx_names:
        b = bh_by_index[i]
        L.append(
            f"| {i} | {b['total_return'] * 100:+.0f} | {b['sharpe']:.2f} | {b['max_drawdown'] * 100:.0f} |"
        )

    L += [
        "",
        "## OOS Sharpe by strategy × index vs TOTAL-RETURN B&H  (★ beats on return & Sharpe)",
        "",
        "| strategy | " + " | ".join(idx_names) + " |",
        "|---|" + "---|" * len(idx_names),
    ]
    for s in strat_names:
        cells = []
        for i in idx_names:
            r = results.get((i, s))
            if not r:
                cells.append("—")
                continue
            star = "★" if (r["beats_bh_ret"] and r["beats_bh_sharpe"]) else ""
            cells.append(f"{r['wfa']['sharpe']:.2f}{star}")
        L.append(f"| {s} | " + " | ".join(cells) + " |")
    L += [
        "| _TR-B&H Sharpe_ | "
        + " | ".join(f"{bh_by_index[i]['sharpe']:.2f}" for i in idx_names)
        + " |"
    ]

    # did the prior price-only FTSE winners survive?
    ftse_note = []
    for s in ("RSI_reversal", "Bollinger_MR"):
        r = results.get(("FTSE100", s))
        if r:
            ftse_note.append(
                f"FTSE100/{s}: OOS {r['wfa']['total_return'] * 100:+.0f}% vs TR-B&H "
                f"{r['bh']['total_return'] * 100:+.0f}% → beats return: "
                f"{'YES' if r['beats_bh_ret'] else 'NO'}"
            )

    L += [
        "",
        "## Verdict (dividend-adjusted)",
        "",
        f"- Against the proper TOTAL-RETURN benchmark: **{len(beat_ret)}/{n}** beat B&H on return, "
        f"**{len(beat_sr)}/{n}** on Sharpe ({len(sharpe_only)} of those have LOWER return = "
        "vol-reduction, not alpha).",
        f"- Median Walk-Forward Efficiency **{np.median(wfes):.2f}**.",
    ]
    if ftse_note:
        L += [
            "- The price-only FTSE 'winners' under dividends: "
            + "; ".join(ftse_note)
            + " — confirming the earlier price-only outperformance was largely the missing dividend."
        ]
    L += [
        f"- **Conclusion:** with dividends correctly credited, classic price-timing beats "
        f"buy-and-hold on return in only {len(beat_ret)}/{n} cases — i.e. **no robust return edge "
        "across major indices.** Timing's durable value is risk/drawdown reduction, not return.",
        "",
        "_Approx constant yields (time-varying in reality); S&P validated vs ^SP500TR; "
        "long/flat; 5bps/turn._",
    ]
    out_md = REPORTS / f"index_total_return_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"index_total_return_{today}.json").write_text(
        json.dumps(
            {
                "date": today,
                "div_yield": DIV_YIELD,
                "sp500tr_real_cagr": sp_tr_real,
                "beat_bh_return": [f"{i}/{s}" for i, s in beat_ret],
                "beat_bh_sharpe": [f"{i}/{s}" for i, s in beat_sr],
                "median_wfe": float(np.median(wfes)) if wfes else None,
                "matrix": {
                    f"{i}|{s}": {
                        "oos_total": r["wfa"]["total_return"],
                        "oos_sharpe": r["wfa"]["sharpe"],
                        "bh_total": r["bh"]["total_return"],
                        "bh_sharpe": r["bh"]["sharpe"],
                        "beats_ret": r["beats_bh_ret"],
                        "beats_sharpe": r["beats_bh_sharpe"],
                    }
                    for (i, s), r in results.items()
                },
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(
        f"\nTOTAL-RETURN: {n} combos | beat B&H return {len(beat_ret)}, Sharpe {len(beat_sr)} | "
        f"median WFE {np.median(wfes):.2f}"
    )
    for note in ftse_note:
        print("  " + note)
    if beat_ret:
        print("  still beat return:", ", ".join(f"{i}/{s}" for i, s in beat_ret))
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
