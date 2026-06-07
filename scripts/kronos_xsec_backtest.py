"""Kronos CROSS-SECTIONAL ranking backtest — the paper's actual claimed strength.

The May/Jun runs falsified Kronos for SINGLE-ASSET directional scalping (IC ~0.05-0.08,
mean move < cost → negative after-cost EV; base 102M no better). This tests the DIFFERENT
use the paper is actually built for: at each rebalance, rank a universe of coins by Kronos's
forecast, go LONG the top-k / SHORT the bottom-k, dollar-neutral. The honest metric is
**cross-sectional RankIC** (does Kronos rank coins correctly?) and the **after-cost
long-short return** — benchmarked against a trivial trailing-momentum baseline (does the
foundation model add anything beyond "buy what went up?").

WHY THIS REMOVES THE BETA TRAP: a long-short book is market-neutral by construction, so
"everything pumped" cancels — the opposite problem from the candlestick screen (which
needed explicit beta-adjustment). The risk here instead is (a) RankIC inflated by
overlapping windows → we use NON-OVERLAPPING rebalances (step >= max horizon); (b) the
foundation model merely re-deriving price momentum → we run the momentum baseline head-to-head.

LEAKAGE CONTROL (identical to the prior probes): Kronos weights are Aug-2025 with an
undisclosed cutoff, so ALL data — context AND target — is restricted to bars >= 2025-09-01.
Forecast at bar t uses only bars <= t; the forward window (t, t+h] is the unseen target.

GATES (a GO must clear ALL):
  1. mean cross-sectional RankIC > 0 with |t| >= 3 over the non-overlapping rebalances;
  2. long-short NET (after-cost) mean return > 0 with a positive Sharpe;
  3. Kronos beats the trailing-momentum baseline on BOTH RankIC and net return.
Honest prior: NO_EDGE (consistent with every prior screen). The recorded result IS the
deliverable either way. Records reports/kronos_xsec_<date>.md (+ .json). Run with the
torch+CUDA interpreter (system Python312). Read-only on the bot.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "ohlcv_cache"
REPORTS = ROOT / "reports"
CUTOFF = pd.Timestamp("2025-09-01")

# 30 liquid coins (incl. all 11 the user named). Skipped automatically if data is thin.
UNIVERSE = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "ATOM", "ETC", "TRX", "SUI", "APT", "ARB", "FIL", "INJ",
    "AAVE", "HBAR", "FET", "JUP", "IOTA", "AXS", "ALGO", "NEAR", "OP", "ZEC",
]
LOOKBACK = 400
HORIZONS = (4, 12, 24)          # 4h / 12h / 24h on 1h bars
MAX_H = max(HORIZONS)
SAMPLES = 3                     # stochastic paths averaged per forecast
K = 6                           # long top-K, short bottom-K (~20% of 30)
MAX_DEC = 220                   # cap decision bars (≈ 6600 forecasts ≈ ~38 min)
COST_ONEWAY = 5.0 / 1e4         # 5 bps/side taker
# full-rebalance cost per period: long+short legs, open+close = 4x one-way on gross
COST_FULL = 4 * COST_ONEWAY     # ~20 bps/period  (taker, full turnover)
COST_LOW = 1.6 * COST_ONEWAY    # ~8 bps/period   (charitable: low turnover / maker-ish)


def _post_cutoff(sym: str) -> pd.DataFrame | None:
    p = CACHE / f"{sym}-USDT_1h.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p).reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["ts"], unit="s")
    df = df[df["dt"] >= CUTOFF].sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    return df if len(df) > LOOKBACK + MAX_H + 50 else None


def _tstat(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 and len(x) > 1 else 0.0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    from core.kronos_forecaster import get_kronos_forecaster

    # load universe
    data: dict[str, pd.DataFrame] = {}
    for s in UNIVERSE:
        df = _post_cutoff(s)
        if df is not None:
            df = df.set_index("ts")
            data[s] = df
    coins = list(data.keys())
    print(f"Universe: {len(coins)} coins with post-cutoff 1h data: {coins}")
    if len(coins) < 10:
        print("ERROR: universe too small")
        return 1

    # common hourly timestamp grid (intersection) for aligned cross-section
    common = None
    for s in coins:
        ts = set(data[s].index.tolist())
        common = ts if common is None else (common & ts)
    common = np.array(sorted(common))
    print(f"Common aligned timestamps: {len(common)}")

    # decision bars: need LOOKBACK history + MAX_H future for every coin; non-overlapping
    step = max(MAX_H, len(common) // MAX_DEC)
    dec_ts = []
    for j in range(LOOKBACK, len(common) - MAX_H, step):
        dec_ts.append(int(common[j]))
        if len(dec_ts) >= MAX_DEC:
            break
    print(f"Decision bars: {len(dec_ts)} (step={step}h, non-overlap @H={MAX_H})")
    assert pd.Timestamp(dec_ts[0], unit="s") >= CUTOFF, "LEAKAGE: decision bar < cutoff"

    fc = get_kronos_forecaster(lookback=LOOKBACK)
    t0 = time.time()
    fc.load()
    print(f"Kronos loaded in {time.time()-t0:.1f}s")

    # per-horizon accumulators of per-rebalance series
    ic_k = {h: [] for h in HORIZONS}      # kronos RankIC
    ic_m = {h: [] for h in HORIZONS}      # momentum RankIC
    ls_k = {h: [] for h in HORIZONS}      # kronos long-short gross spread
    ls_m = {h: [] for h in HORIZONS}      # momentum long-short gross spread
    n_inf = 0
    t_start = time.time()

    for bi, T in enumerate(dec_ts):
        # forecast every coin at this aligned bar
        exp = {h: {} for h in HORIZONS}
        mom = {h: {} for h in HORIZONS}
        fwd = {h: {} for h in HORIZONS}
        for s in coins:
            df = data[s]
            ctx = df.loc[:T]                      # rows with ts <= T (post-cutoff only)
            if len(ctx) < LOOKBACK:
                continue
            try:
                f = fc.forecast(ctx.reset_index()[["ts", "open", "high", "low", "close"]],
                                horizon=MAX_H, samples=SAMPLES)
            except Exception:
                continue
            n_inf += 1
            c0 = float(df["close"].loc[T])
            for h in HORIZONS:
                fut_ts = T + h * 3600
                past_ts = T - h * 3600
                if fut_ts in df.index and past_ts in df.index:
                    exp[h][s] = f.exp_ret_at(h)
                    fwd[h][s] = float(df["close"].loc[fut_ts]) / c0 - 1.0
                    mom[h][s] = c0 / float(df["close"].loc[past_ts]) - 1.0
        # cross-sectional stats per horizon (need enough coins this bar)
        for h in HORIZONS:
            ss = [s for s in coins if s in exp[h] and s in fwd[h] and s in mom[h]]
            if len(ss) < 2 * K + 2:
                continue
            ev = np.array([exp[h][s] for s in ss])
            mv = np.array([mom[h][s] for s in ss])
            rv = np.array([fwd[h][s] for s in ss])
            ick, _ = stats.spearmanr(ev, rv)
            icm, _ = stats.spearmanr(mv, rv)
            if not np.isnan(ick):
                ic_k[h].append(ick)
            if not np.isnan(icm):
                ic_m[h].append(icm)
            ek = np.argsort(ev)            # ascending
            em = np.argsort(mv)
            ls_k[h].append(float(rv[ek[-K:]].mean() - rv[ek[:K]].mean()))
            ls_m[h].append(float(rv[em[-K:]].mean() - rv[em[:K]].mean()))
        if (bi + 1) % 20 == 0:
            el = time.time() - t_start
            print(f"  {bi+1}/{len(dec_ts)} bars | {n_inf} inf | {el:.0f}s "
                  f"| ~{el/max(n_inf,1)*1000:.0f}ms/inf")

    # ---- aggregate + gates --------------------------------------------------
    rows = []
    for h in HORIZONS:
        ick = np.array(ic_k[h]); icm = np.array(ic_m[h])
        lk = np.array(ls_k[h]); lm = np.array(ls_m[h])
        pyear = 8760.0 / h
        def sharpe(x):
            x = np.asarray(x, float)
            return float(x.mean() / x.std(ddof=1) * np.sqrt(pyear)) if len(x) > 1 and x.std(ddof=1) > 0 else 0.0
        net_full = lk - COST_FULL
        net_low = lk - COST_LOW
        rows.append({
            "h": h, "n": int(len(lk)),
            "kronos_meanIC": float(ick.mean()) if len(ick) else 0.0,
            "kronos_IC_t": _tstat(ick) if len(ick) else 0.0,
            "mom_meanIC": float(icm.mean()) if len(icm) else 0.0,
            "mom_IC_t": _tstat(icm) if len(icm) else 0.0,
            "kronos_LS_gross_pct": float(lk.mean() * 100) if len(lk) else 0.0,
            "kronos_LS_t": _tstat(lk) if len(lk) else 0.0,
            "kronos_LS_net_full_pct": float(net_full.mean() * 100) if len(lk) else 0.0,
            "kronos_LS_net_low_pct": float(net_low.mean() * 100) if len(lk) else 0.0,
            "kronos_LS_sharpe_grossann": sharpe(lk),
            "kronos_LS_sharpe_net_full": sharpe(net_full),
            "mom_LS_gross_pct": float(lm.mean() * 100) if len(lm) else 0.0,
        })

    def is_go(r):
        return (r["kronos_meanIC"] > 0 and r["kronos_IC_t"] >= 3.0
                and r["kronos_LS_net_full_pct"] > 0 and r["kronos_LS_sharpe_net_full"] > 0
                and r["kronos_meanIC"] > r["mom_meanIC"]
                and r["kronos_LS_gross_pct"] > r["mom_LS_gross_pct"])
    gos = [r for r in rows if is_go(r)]

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    L = [f"# Kronos CROSS-SECTIONAL ranking backtest — {today}", ""]
    L += [
        "**Use tested:** the paper's actual strength — rank a universe by Kronos forecast, "
        "**long top-6 / short bottom-6, dollar-neutral.** (Single-asset directional scalping was "
        "already falsified May 29 + Jun 7.) Market-neutral by construction, so no beta trap; "
        "instead guarded with NON-OVERLAPPING rebalances and a trailing-momentum head-to-head.",
        "",
        f"**Setup:** {len(coins)} coins, 1h bars, leakage-safe (≥2025-09-01, context & target). "
        f"{len(dec_ts)} non-overlapping rebalances (step={step}h). Kronos-mini, samples={SAMPLES}, "
        f"{n_inf} forecasts. Horizons 4h/12h/24h. Cost: full-rebalance ~{COST_FULL*1e4:.0f}bps/period "
        f"(taker) and charitable ~{COST_LOW*1e4:.0f}bps/period.",
        "",
        f"## VERDICT: {'GO @ ' + ', '.join(str(r['h'])+'h' for r in gos) if gos else 'NO_EDGE (0 horizons clear the gate)'}",
        "",
        "**Gate:** mean RankIC>0 & |t|≥3; long-short net>0 & Sharpe>0; Kronos beats momentum on RankIC AND gross spread.",
        "",
        "| h | n | Kronos RankIC | IC t | Mom RankIC | Mom IC t | K LS gross% | LS t | K LS net%(full) | net%(low) | net Sharpe(ann) | Mom LS gross% |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        L.append("| {h} | {n} | {kronos_meanIC:+.4f} | {kronos_IC_t:+.2f} | {mom_meanIC:+.4f} | "
                 "{mom_IC_t:+.2f} | {kronos_LS_gross_pct:+.3f} | {kronos_LS_t:+.2f} | "
                 "{kronos_LS_net_full_pct:+.3f} | {kronos_LS_net_low_pct:+.3f} | "
                 "{kronos_LS_sharpe_net_full:+.2f} | {mom_LS_gross_pct:+.3f} |".format(**r))
    L += ["",
          "_RankIC = cross-sectional Spearman(forecast, realized) per rebalance, averaged. "
          "LS gross = mean(top-6 realized) − mean(bottom-6 realized) per period (before cost). "
          "Sharpe annualized at 8760/h periods/yr. Honest caveats: Kronos sampling is stochastic "
          "(sub-0.1% effects are noise); ~30-coin cross-section is a fraction of the paper's "
          "hundreds; even a positive result is NOT capturable at ~$1,300 (min-notional × many legs)._"]
    out_md = REPORTS / f"kronos_xsec_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"kronos_xsec_{today}.json").write_text(
        json.dumps({"date": today, "coins": coins, "n_rebalances": len(dec_ts),
                    "n_forecasts": n_inf, "rows": rows, "go_horizons": [r["h"] for r in gos]},
                   indent=2), encoding="utf-8")

    print("\n=== RESULT ===")
    for r in rows:
        print(f"h={r['h']:>2} n={r['n']:>3} | Kronos IC={r['kronos_meanIC']:+.4f}(t={r['kronos_IC_t']:+.2f}) "
              f"Mom IC={r['mom_meanIC']:+.4f} | LS gross={r['kronos_LS_gross_pct']:+.3f}% "
              f"net_full={r['kronos_LS_net_full_pct']:+.3f}% Sharpe={r['kronos_LS_sharpe_net_full']:+.2f} "
              f"| Mom LS={r['mom_LS_gross_pct']:+.3f}%")
    print(f"\nVERDICT: {'GO ' + str([r['h'] for r in gos]) if gos else 'NO_EDGE'}")
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
