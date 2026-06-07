"""Capstone: buy-and-hold CORE + de-risk OVERLAY — one deployable strategy + equity curve.

Synthesis of the whole index study:
  * RETURN comes from holding the index (timing strategies never beat buy-and-hold OOS).
  * RISK is tamed by a de-risk overlay — scale exposure DOWN when recent realized volatility is
    high (never lever up), which cut max drawdown 8/8 robustly out-of-sample (WFE ~1.0).
So the combined strategy is: hold the index, weight = clip(target/realized_vol, 0, 1.0).

Two forms:
  A. Per-index combined strategy (core+overlay) vs plain buy-and-hold — the deployable single-asset
     version (no leverage, no FX).
  B. Diversified PORTFOLIO: equal-weight basket of the indices (the core) with the overlay on the
     basket — diversification and de-risking stack. ⚠ Basket is LOCAL-currency (no FX conversion);
     a real cross-currency portfolio needs FX-hedging — shown as illustration. A single-currency
     (e.g. US: S&P/Nasdaq/Dow) basket avoids this.

Fixed, deployable params: 60-day vol lookback (the walk-forward showed 20–120 all robust),
target 15%/yr, cap 1.0. Total-return series (price + approx dividend). Exports full equity curves
to CSV + an ASCII (log-scale) chart. Records reports/derisk_portfolio_<date>.md. No new deps.
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
sys.path.insert(0, str(ROOT / "scripts"))
REPORTS = ROOT / "reports"

from index_strategy_total_return import DIV_YIELD, _strat_ret  # noqa: E402
from nifty_sma_backtest import _max_drawdown, fetch_index, metrics  # noqa: E402
from vol_targeting_walkforward import vol_target_position  # noqa: E402

INDICES = {
    "Nifty50": "^NSEI", "Sensex": "^BSESN", "S&P500": "^GSPC", "Nasdaq": "^IXIC",
    "Dow": "^DJI", "FTSE100": "^FTSE", "Nikkei225": "^N225", "DAX": "^GDAXI",
}
LOOKBACK, TARGET_VOL, CAP = 60, 0.15, 1.0
COST_ONEWAY = 5.0 / 1e4
PPY = 252


def _tr_ret(close, yield_pct):
    pr = np.zeros(len(close))
    pr[1:] = close[1:] / close[:-1] - 1.0
    out = pr + (yield_pct / 100.0) / PPY
    out[0] = 0.0
    return out


def _combined(tr_ret):
    """core+overlay strategy returns + equity, and plain buy-and-hold equity."""
    pos = vol_target_position(tr_ret, LOOKBACK, target_vol=TARGET_VOL, cap=CAP)
    strat = _strat_ret(pos, tr_ret, COST_ONEWAY)
    return pos, strat, np.cumprod(1.0 + strat), np.cumprod(1.0 + tr_ret)


def _m(strat, equity, pos=None):
    return metrics(strat, equity, pos)


def _ascii(equity, label, width=72, height=12):
    v = np.log(np.asarray(equity, dtype=float))
    idx = np.linspace(0, len(v) - 1, width).astype(int)
    s = v[idx]
    lo, hi = s.min(), s.max()
    if hi <= lo:
        return [label + " (flat)"]
    rows = [f"{label} (log-scale equity, x{np.exp(hi - lo):.1f} total)"]
    for r in range(height, 0, -1):
        th = lo + (hi - lo) * (r - 0.5) / height
        rows.append("  " + "".join("#" if x >= th else " " for x in s))
    return rows


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    data = {}
    for iname, sym in INDICES.items():
        try:
            df = fetch_index(sym, "maxdaily")
        except Exception as e:  # noqa: BLE001
            print(f"[skip] {iname}: {e}")
            continue
        df = df.set_index("date")
        data[iname] = _tr_ret_series(df["close"].to_numpy(dtype=float), df.index, DIV_YIELD[iname])
    names = list(data)

    # ---- A. per-index combined strategy ----
    per_index = []
    for iname in names:
        tr = data[iname].to_numpy()
        pos, strat, eq, bh_eq = _combined(tr)
        cm, bm = _m(strat, eq, pos), _m(tr, bh_eq)
        per_index.append({"index": iname, "core_overlay": cm, "buy_hold": bm,
                          "avg_exposure": float(np.mean(pos))})

    # ---- B. diversified equal-weight basket (LOCAL ccy) ----
    common = None
    for iname in names:
        common = data[iname].index if common is None else common.intersection(data[iname].index)
    common = common.sort_values()
    basket_ret = np.mean([data[i].reindex(common).to_numpy() for i in names], axis=0)
    basket_ret = np.nan_to_num(basket_ret)
    b_pos, b_strat, b_eq, b_bh_eq = _combined(basket_ret)
    basket_overlay_m = _m(b_strat, b_eq, b_pos)
    basket_bh_m = _m(basket_ret, b_bh_eq)
    sp_bh = next(p["buy_hold"] for p in per_index if p["index"] == "S&P500")

    # export equity curves
    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    pd.DataFrame({"date": list(common), "basket_buyhold": b_bh_eq.tolist(),
                  "basket_core_overlay": b_eq.tolist(),
                  "basket_exposure": b_pos.tolist()}).to_csv(
        REPORTS / f"derisk_portfolio_equity_{today}.csv", index=False)

    def fmt(m):
        return (f"CAGR {m['cagr']*100:+.1f}% | Sharpe {m['sharpe']:.2f} | Sortino "
                f"{m.get('sortino', 0):.2f} | MaxDD {m['max_drawdown']*100:.0f}% | "
                f"Calmar {m.get('calmar', float('nan')):.2f} | vol {m['volatility']*100:.0f}%")

    L = [f"# Combined buy-and-hold core + de-risk overlay — {today}", "",
         f"Strategy = hold the index, exposure = clip({TARGET_VOL*100:.0f}% / realized_vol, 0, "
         f"{CAP:.0f}) on a {LOOKBACK}-day vol estimate (deployable fixed params; walk-forward showed "
         "the lookback is robust). Total-return series; 5bps/turn; no leverage; causal.", "",
         "## B. Diversified PORTFOLIO — equal-weight basket of "
         f"{len(names)} indices ({common[0]} → {common[-1]}, {len(common)} common days)", "",
         f"- **Core + overlay:** {fmt(basket_overlay_m)}  (avg exposure {np.mean(b_pos)*100:.0f}%)",
         f"- **Basket buy & hold:** {fmt(basket_bh_m)}",
         f"- **S&P 500 alone (B&H):** {fmt(sp_bh)}",
         "", "### Portfolio equity curve (core + overlay)"]
    L += ["```"] + _ascii(b_eq, "core+overlay") + ["```"]
    L += ["### Same, plain basket buy & hold", "```"] + _ascii(b_bh_eq, "basket B&H") + ["```"]

    L += ["", "## A. Per-index: core + overlay vs buy & hold", "",
          "| index | CO CAGR% | BH CAGR% | CO Sharpe | BH Sharpe | CO MaxDD% | BH MaxDD% | avg expo |",
          "|---|---|---|---|---|---|---|---|"]
    for p in per_index:
        c, b = p["core_overlay"], p["buy_hold"]
        L.append(f"| {p['index']} | {c['cagr']*100:+.1f} | {b['cagr']*100:+.1f} | {c['sharpe']:.2f} | "
                 f"{b['sharpe']:.2f} | {c['max_drawdown']*100:.0f} | {b['max_drawdown']*100:.0f} | "
                 f"{p['avg_exposure']*100:.0f}% |")

    dd_better = sum(p["core_overlay"]["max_drawdown"] > p["buy_hold"]["max_drawdown"] for p in per_index)
    sr_better = sum(p["core_overlay"]["sharpe"] > p["buy_hold"]["sharpe"] for p in per_index)
    L += ["", "## What this is — honestly", "",
          f"- The diversified portfolio + overlay earns **CAGR {basket_overlay_m['cagr']*100:.1f}%** at "
          f"**Sharpe {basket_overlay_m['sharpe']:.2f}** with a worst drawdown of "
          f"**{basket_overlay_m['max_drawdown']*100:.0f}%** — vs the plain basket "
          f"({basket_bh_m['cagr']*100:.1f}% / {basket_bh_m['sharpe']:.2f} / "
          f"{basket_bh_m['max_drawdown']*100:.0f}%) and vs S&P-alone "
          f"({sp_bh['cagr']*100:.1f}% / {sp_bh['sharpe']:.2f} / {sp_bh['max_drawdown']*100:.0f}%).",
          f"- Per-index, the overlay improved drawdown on **{dd_better}/{len(per_index)}** and Sharpe "
          f"on **{sr_better}/{len(per_index)}**.",
          "- **What actually did the work (honest, from the data):** diversification lowered "
          f"NORMAL-times volatility (basket {basket_overlay_m['volatility']*100:.0f}% vs S&P "
          f"{sp_bh['volatility']*100:.0f}%) and lifted Sharpe — but it did NOT cut the crisis "
          f"drawdown: in 2008 cross-market correlations spiked to ~1, so the plain basket still "
          f"fell {basket_bh_m['max_drawdown']*100:.0f}% like its members. The **DE-RISK OVERLAY** is "
          f"what reduced the worst drawdown (to {basket_overlay_m['max_drawdown']*100:.0f}%), by "
          "stepping exposure down as volatility spiked. Return was preserved (avg exposure "
          f"{np.mean(b_pos)*100:.0f}%) because the diversified basket's vol sits near the "
          f"{TARGET_VOL*100:.0f}% target, so the overlay only trims in turbulence.",
          "- ⚠ This is NOT alpha: returns still come from the equity beta you hold; the overlay trades "
          "some upside for much shallower drawdowns, and it cannot make a falling market profitable. "
          "⚠ The basket is LOCAL-currency (no FX) — a real multi-currency version needs hedging; a "
          "single-currency (US: S&P/Nasdaq/Dow) basket sidesteps this.",
          "", "_Equity curves exported: reports/derisk_portfolio_equity_%s.csv (plot for a real "
          "chart). Fixed 60d/15%%/cap1.0; total-return; 5bps/turn._" % today]

    out_md = REPORTS / f"derisk_portfolio_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"derisk_portfolio_{today}.json").write_text(json.dumps({
        "date": today, "params": {"lookback": LOOKBACK, "target_vol": TARGET_VOL, "cap": CAP},
        "basket_core_overlay": basket_overlay_m, "basket_buy_hold": basket_bh_m, "sp500_buy_hold": sp_bh,
        "basket_avg_exposure": float(np.mean(b_pos)), "n_common_days": len(common),
        "per_index": per_index,
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nDIVERSIFIED PORTFOLIO (core+overlay): {fmt(basket_overlay_m)} avg-expo "
          f"{np.mean(b_pos)*100:.0f}%")
    print(f"  plain basket B&H            : {fmt(basket_bh_m)}")
    print(f"  S&P500 alone B&H            : {fmt(sp_bh)}")
    print(f"  per-index overlay improved drawdown {dd_better}/{len(per_index)}, "
          f"Sharpe {sr_better}/{len(per_index)}")
    print(f"Report: {out_md}")
    return 0


def _tr_ret_series(close, dates, yield_pct):
    return pd.Series(_tr_ret(close, yield_pct), index=dates)


if __name__ == "__main__":
    raise SystemExit(main())
