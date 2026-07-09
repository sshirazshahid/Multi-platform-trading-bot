"""Candidate B — post-listing perp-short after-cost screen (edge-screener).

Pre-registered in _workspace/strategy_pipeline/02b_screener_listing_short.md
(rev2 re-run: _workspace/strategy_pipeline/02b_rev2_screener_listing_short.md).
Honest, local-data-only screen. Charges ALL costs: config.FEE + config.SLIPPAGE
per leg, and realized funding to the short leg from local funding history ONLY.
Listings without funding coverage are EXCLUDED and COUNTED — never guessed.

Listing date = idiosyncratic first-candle timestamp (FMZQuant method) with the
backfill-cluster caveat enforced (first-ts shared by >=3 symbols = cache backfill
artifact, excluded).

Funding source (rev2): prefers the harvested realized history in
``data/funding_history/binance_{BASE}.csv`` (schema: ts, funding_rate, venue,
symbol) written by scripts/backfill_funding_history.py. Falls back to the
coverage-only sets (funding_cache/derivs) when that store is absent, so the
pre-backfill behaviour and the existing pure-function tests are unchanged.

Run: venv/Scripts/python.exe research/screen_listing_short.py
Emits JSON diagnostics + verdict to stdout; no files written by default.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# allow `import config` / `core.*` when run as a script from research/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Pre-registered constants ────────────────────────────────────────────────
OHLCV_DIR = "data/ohlcv_cache"
FUNDING_CACHE_DIR = "data/funding_cache"
DERIVS_PATH = "data/derivs_history.jsonl"
FUNDING_HISTORY_DIR = "data/funding_history"  # rev2 harvested realized funding
WIN_LO = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
WIN_HI = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
MIN_POST_DAYS = 30
CLUSTER_MIN_SHARED = 3
HORIZONS_D = (7, 30, 90)  # one pre-registered family; n_trials = 3
DAY = 24 * 3600
SETTLE = 8 * 3600  # nominal funding interval used only for window-coverage tolerance
# Frozen gate thresholds (core/promotion_gate.py, core/decision/monte_carlo.py).
MIN_DSR = 0.10
MAX_PBO = 0.5
MIN_OOS_WR = 0.55
MC_MIN_TRADES = 30  # core/decision/monte_carlo.MonteCarloResult.passes floor
# Pre-registered evaluability floor: below MC's own min_trades a funding-charged
# horizon cannot run the capital-preservation gate -> not evaluable.
MIN_EVALUABLE_N = MC_MIN_TRADES
# authoritative costs pulled from config at runtime in run_screen()


# ── Pure functions (unit-tested) ────────────────────────────────────────────
def find_backfill_clusters(first_ts_by_symbol: dict, min_shared: int = CLUSTER_MIN_SHARED) -> set:
    """First-candle timestamps shared by >= min_shared symbols = cache backfill
    artifacts (a bulk backfill truncates many pre-existing coins to one start hour)."""
    cnt = Counter(first_ts_by_symbol.values())
    return {ts for ts, c in cnt.items() if c >= min_shared}


def funding_pnl_short(rates) -> float:
    """Funding PnL (as a return on notional) accruing to a SHORT over held
    settlements. Short RECEIVES positive funding, PAYS negative funding → +sum."""
    rates = list(rates)
    if not rates:
        return 0.0
    return float(np.sum(rates))


def short_net_return(
    entry_price: float,
    exit_price: float,
    funding_sum: float,
    fee_per_side: float,
    slip_per_side: float,
) -> float:
    """After-cost return of a short: price gain on decline + funding − round-trip cost."""
    gross_short = (entry_price - exit_price) / entry_price
    roundtrip_cost = 2.0 * (fee_per_side + slip_per_side)
    return float(gross_short + funding_sum - roundtrip_cost)


def price_at(df: pd.DataFrame, target_ts: int):
    """Close of the first bar with ts >= target_ts. (None, None) if past end."""
    sub = df[df["ts"] >= target_ts]
    if len(sub) == 0:
        return (None, None)
    row = sub.iloc[0]
    return (float(row["close"]), int(row["ts"]))


def funding_sum_in_window(series: pd.Series, entry_ts: int, exit_ts: int) -> float:
    """Sum of realized funding rates whose settlement ts is in [entry_ts, exit_ts).

    This is the funding PnL accruing to the short over the hold (short receives
    positive, pays negative — the pre-declared killer cost)."""
    if series is None or len(series) == 0:
        return 0.0
    mask = (series.index >= int(entry_ts)) & (series.index < int(exit_ts))
    return float(series[mask].sum())


def window_funding_covered(series: pd.Series, entry_ts: int, exit_ts: int) -> bool:
    """True iff realized funding brackets the whole holding window (no end-gaps).

    Requires the series to start at/before entry (+one settlement tolerance) and
    to run to at/after exit (−one settlement tolerance), and to hold ≥1 point in
    the window. A listing whose funding history does not span its window is
    EXCLUDED (counted) — never charged a guessed number."""
    if series is None or len(series) == 0:
        return False
    lo = int(series.index.min())
    hi = int(series.index.max())
    in_win = ((series.index >= int(entry_ts)) & (series.index < int(exit_ts))).sum()
    return bool(lo <= entry_ts + SETTLE and hi >= exit_ts - SETTLE and in_win >= 1)


# ── Data loading ────────────────────────────────────────────────────────────
def _base(sym: str) -> str:
    return sym.split("-")[0]


def load_funding_history(base: str, venue: str = "binance") -> pd.Series:
    """Realized funding series (index = settlement ts seconds, value = rate) from
    data/funding_history/{venue}_{base}.csv. Empty Series when absent."""
    path = os.path.join(FUNDING_HISTORY_DIR, f"{venue}_{base}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    d = pd.read_csv(path)
    if "ts" not in d.columns or "funding_rate" not in d.columns or len(d) == 0:
        return pd.Series(dtype=float)
    d = d.drop_duplicates("ts").sort_values("ts")
    return pd.Series(d["funding_rate"].astype(float).values, index=d["ts"].astype("int64").values)


def funding_history_bases() -> set:
    """Bases with a realized funding-history CSV on disk (any venue)."""
    if not os.path.isdir(FUNDING_HISTORY_DIR):
        return set()
    out = set()
    for fn in os.listdir(FUNDING_HISTORY_DIR):
        if fn.endswith(".csv") and "_" in fn:
            out.add(fn.split("_", 1)[1].rsplit(".", 1)[0])
    return out


def scan_first_last() -> dict:
    """symbol -> (first_ts, last_ts, path) for every *_1h.parquet with data."""
    out = {}
    for f in sorted(glob.glob(os.path.join(OHLCV_DIR, "*_1h.parquet"))):
        sym = os.path.basename(f).replace("_1h.parquet", "")
        df = pd.read_parquet(f, columns=["ts"])
        if len(df) == 0:
            continue
        out[sym] = (int(df["ts"].iloc[0]), int(df["ts"].iloc[-1]), f)
    return out


def funding_coverage_sets() -> tuple[set, set]:
    fc = {os.path.basename(x).replace("-USDTUSDT_8h.parquet", "")
          for x in glob.glob(os.path.join(FUNDING_CACHE_DIR, "*.parquet"))}
    derivs = set()
    if os.path.exists(DERIVS_PATH):
        with open(DERIVS_PATH) as fh:
            for line in fh:
                try:
                    derivs.add(json.loads(line)["symbol"])
                except Exception:
                    pass
    return fc, derivs


def compute_genuine_listings() -> tuple[dict, dict]:
    """(firstlast, genuine) where genuine maps symbol -> first_ts for idiosyncratic
    listings in the pre-registered window with >= MIN_POST_DAYS of data.

    Shared by run_screen and by scripts/backfill_funding_history (so the backfill
    fetches exactly the screen's listing universe rather than a hardcoded copy)."""
    firstlast = scan_first_last()
    firsts = {s: v[0] for s, v in firstlast.items()}
    lasts = {s: v[1] for s, v in firstlast.items()}
    clusters = find_backfill_clusters(firsts)
    genuine = {
        s: firsts[s]
        for s in firsts
        if firsts[s] not in clusters
        and WIN_LO <= firsts[s] <= WIN_HI
        and (lasts[s] - firsts[s]) >= MIN_POST_DAYS * DAY
    }
    return firstlast, genuine


def genuine_listing_bases() -> list[str]:
    """Sorted unique base tickers of the genuine listing universe."""
    _, genuine = compute_genuine_listings()
    return sorted({_base(s) for s in genuine})


# ── Gate battery on the funding-charged sample ──────────────────────────────
def _dsr_prob(arr: np.ndarray, n_trials: int) -> float:
    from core.promotion_gate import _kurt, _skew
    from core.stat_tests import deflated_sharpe, sharpe

    n = int(arr.size)
    if n < 4:
        return float("nan")
    return float(
        deflated_sharpe(
            sr_observed=sharpe(arr),
            n_trials=int(n_trials),
            n_obs=n,
            skew=float(_skew(arr)),
            kurt=float(_kurt(arr)),
            sr_var=1.0 / max(2, n),
        )
    )


def _pbo_across_horizons(matrix: np.ndarray) -> float:
    """PBO via CSCV across the horizon-variant columns. NaN when too few rows."""
    from core.stat_tests import pbo

    T = matrix.shape[0]
    parts = 16 if T >= 16 else (T - (T % 2))
    if parts < 4:
        return float("nan")
    try:
        return float(pbo(matrix, n_partitions=parts))
    except Exception:
        return float("nan")


def _oos_wr_walk_forward(returns_by_date: np.ndarray, exit_entry_pairs) -> float:
    """Pooled out-of-sample win-rate over listing-date-ordered walk-forward folds
    with embargo + a time-based purge (drop train listings whose hold window
    overlaps the test fold's earliest entry). NaN when too few points."""
    from core.walk_forward import WalkForward

    n = int(returns_by_date.size)
    if n < 8:
        return float("nan")
    entries = np.array([e for (e, _x) in exit_entry_pairs], dtype="int64")
    exits = np.array([x for (_e, x) in exit_entry_pairs], dtype="int64")
    wf = WalkForward(n_splits=4, embargo_bars=1, anchored=True)
    wins, total = 0, 0
    for tr, te in wf.split(n):
        if len(tr) == 0 or len(te) == 0:
            continue
        test_entry_min = int(entries[te].min())
        # purge: train listings still open when the test fold begins leak forward.
        tr = tr[exits[tr] <= test_entry_min]
        if len(tr) == 0:
            continue
        wins += int((returns_by_date[te] > 0).sum())
        total += int(te.size)
    return float(wins / total) if total else float("nan")


def _monte_carlo(arr: np.ndarray) -> dict:
    from core.decision.monte_carlo import monte_carlo_trade_sequence

    mc = monte_carlo_trade_sequence(arr)
    return {
        "n_trades": mc.n_trades,
        "p_total_positive": mc.p_total_positive,
        "max_drawdown_p95": mc.max_drawdown_p95,
        "passes": bool(mc.passes(min_p_positive=0.95, max_dd_p95=0.25, min_trades=MC_MIN_TRADES)),
    }


# ── Screen orchestration ────────────────────────────────────────────────────
def run_screen() -> dict:
    import config

    fee = float(config.FEE["futures_taker"])
    slip_open = float(config.SLIPPAGE["pct_open"])
    slip_close = float(config.SLIPPAGE["pct_close"])
    slip = (slip_open + slip_close) / 2.0  # 5bps each side; per-side value

    firstlast, genuine = compute_genuine_listings()
    firsts = {s: v[0] for s, v in firstlast.items()}
    clusters = find_backfill_clusters(firsts)

    fc_set, derivs_set = funding_coverage_sets()
    hist_bases = funding_history_bases()

    def has_coverage_flag(sym):
        b = _base(sym)
        return b in fc_set or b in derivs_set or b in hist_bases

    covered_flag = {s: ts for s, ts in genuine.items() if has_coverage_flag(s)}
    uncovered_flag = {s: ts for s, ts in genuine.items() if not has_coverage_flag(s)}

    # control basket: liquid majors (funding_cache coins) that have _1h OHLCV
    control_syms = []
    control_df = {}
    for coin in fc_set:
        p = os.path.join(OHLCV_DIR, f"{coin}-USDT_1h.parquet")
        if os.path.exists(p):
            control_syms.append(f"{coin}-USDT")
            control_df[f"{coin}-USDT"] = pd.read_parquet(p, columns=["ts", "close"])

    def control_return(entry_ts, exit_ts):
        rets = []
        for _cs, cdf in control_df.items():
            p0, _ = price_at(cdf, entry_ts)
            p1, _ = price_at(cdf, exit_ts)
            if p0 and p1 and p0 > 0:
                rets.append((p1 - p0) / p0)
        return float(np.mean(rets)) if rets else None

    # ── DIAGNOSTIC (funding UNCHARGED) — kept for continuity; NOT gate-eligible ──
    diag = {}
    for H in HORIZONS_D:
        short_nets, listing_prets, ctrl_rets, underperf = [], [], [], []
        for sym, first_ts in genuine.items():
            df = pd.read_parquet(firstlast[sym][2], columns=["ts", "close"])
            entry_ts = first_ts + DAY
            exit_ts = entry_ts + H * DAY
            p0, a0 = price_at(df, entry_ts)
            p1, a1 = price_at(df, exit_ts)
            if not (p0 and p1 and p0 > 0):
                continue
            if a1 - exit_ts > 12 * 3600:
                continue
            listing_ret = (p1 - p0) / p0
            snet = short_net_return(p0, p1, 0.0, fee, slip)  # funding=0 → UNCHARGED
            cr = control_return(a0, a1)
            short_nets.append(snet)
            listing_prets.append(listing_ret)
            if cr is not None:
                ctrl_rets.append(cr)
                underperf.append(cr - listing_ret)
        arr = np.array(short_nets, dtype=float)
        diag[f"{H}d"] = {
            "n": int(arr.size),
            "short_net_mean_feesonly": float(np.mean(arr)) if arr.size else None,
            "short_win_rate_feesonly": float(np.mean(arr > 0)) if arr.size else None,
            "underperf_mean(ctrl-listing)": float(np.mean(underperf)) if underperf else None,
        }

    # ── FUNDING-CHARGED gate-eligible sample (rev2) ──────────────────────────
    charged = {}
    covered_used_bases = set()
    for H in HORIZONS_D:
        rows = []  # (sym, first_ts, entry_ts, exit_ts, short_net, listing_ret, ctrl_ret, funding_sum)
        excl_no_price = excl_no_funding = 0
        for sym, first_ts in genuine.items():
            base = _base(sym)
            fser = load_funding_history(base, "binance")
            df = pd.read_parquet(firstlast[sym][2], columns=["ts", "close"])
            entry_ts = first_ts + DAY
            exit_ts = entry_ts + H * DAY
            p0, a0 = price_at(df, entry_ts)
            p1, a1 = price_at(df, exit_ts)
            if not (p0 and p1 and p0 > 0) or (a1 - exit_ts > 12 * 3600):
                excl_no_price += 1
                continue
            if not window_funding_covered(fser, a0, a1):
                excl_no_funding += 1
                continue
            fsum = funding_sum_in_window(fser, a0, a1)  # short receives +, pays −
            snet = short_net_return(p0, p1, fsum, fee, slip)
            cr = control_return(a0, a1)
            rows.append((sym, first_ts, a0, a1, snet, (p1 - p0) / p0, cr, fsum))
            covered_used_bases.add(base)

        rets = np.array([r[4] for r in rows], dtype=float)
        fsums = np.array([r[7] for r in rows], dtype=float)
        underperf = np.array(
            [r[6] - r[5] for r in rows if r[6] is not None], dtype=float
        )
        n = int(rets.size)
        entry = {
            "n_funding_charged": n,
            "excluded_no_price_window": excl_no_price,
            "excluded_no_funding_window": excl_no_funding,
        }
        if n:
            entry.update({
                "short_net_mean": float(np.mean(rets)),
                "short_net_median": float(np.median(rets)),
                "win_rate": float(np.mean(rets > 0)),
                "funding_sum_mean": float(np.mean(fsums)),
                "funding_sum_median": float(np.median(fsums)),
                "funding_negative_rate": float(np.mean(fsums < 0)),
                "underperf_vs_ctrl_mean": float(np.mean(underperf)) if underperf.size else None,
                "beats_control": bool(underperf.size and np.mean(underperf) > 0),
                "dsr_prob": _dsr_prob(rets, n_trials=len(HORIZONS_D)),
                "monte_carlo": _monte_carlo(rets),
                "oos_wr_walk_forward": _oos_wr_walk_forward(
                    rets, [(r[2], r[3]) for r in rows]
                ),
                "_rows": rows,  # internal; popped before serialization
            })
        charged[f"{H}d"] = entry

    # PBO across horizons on the common covered listings
    common = None
    horizon_ret_by_sym = {}
    for H in HORIZONS_D:
        rows = charged[f"{H}d"].get("_rows", [])
        m = {r[0]: r[4] for r in rows}
        horizon_ret_by_sym[H] = m
        s = set(m)
        common = s if common is None else (common & s)
    common = sorted(common or [])
    if len(common) >= 4:
        matrix = np.array(
            [[horizon_ret_by_sym[H][sym] for H in HORIZONS_D] for sym in common], dtype=float
        )
        pbo_value = _pbo_across_horizons(matrix)
    else:
        pbo_value = float("nan")

    for H in HORIZONS_D:
        charged[f"{H}d"].pop("_rows", None)

    verdict, reason, gate_detail = _decide(charged, pbo_value)

    return {
        "candidate": "B — post-listing perp short (rev2, funding-charged)",
        "costs": {"fee_per_side": fee, "slip_per_side": slip,
                  "roundtrip_cost": 2 * (fee + slip)},
        "universe": {
            "total_1h_symbols_with_data": len(firsts),
            "backfill_cluster_timestamps": len(clusters),
            "excluded_backfill_cluster_symbols": sum(1 for s in firsts if firsts[s] in clusters),
            "genuine_listings_in_window_30d": len(genuine),
        },
        "funding_coverage": {
            "funding_cache_coins": len(fc_set),
            "derivs_symbols": len(derivs_set),
            "funding_history_bases": len(hist_bases),
            "genuine_listings_flagged_covered": len(covered_flag),
            "genuine_listings_flagged_uncovered_EXCLUDED": len(uncovered_flag),
            "funding_history_covered_bases_used": sorted(covered_used_bases),
        },
        "control_basket_size": len(control_syms),
        "diagnostic_feesonly_funding_UNCHARGED": diag,
        "funding_charged": charged,
        "pbo_across_horizons": pbo_value,
        "gate_detail": gate_detail,
        "verdict": verdict,
        "reason": reason,
        "thresholds": {"MIN_DSR": MIN_DSR, "MAX_PBO": MAX_PBO, "MIN_OOS_WR": MIN_OOS_WR,
                       "MC_MIN_TRADES": MC_MIN_TRADES, "MIN_EVALUABLE_N": MIN_EVALUABLE_N},
    }


def _decide(charged: dict, pbo_value: float) -> tuple[str, str, dict]:
    """Apply the frozen pre-registered decision tree to the funding-charged sample.

    GO   : some horizon has ALL frozen gates evaluable AND passing AND beats control.
    NO_GO: an evaluable horizon shows funding-charged mean<=0 OR WR<MIN_OOS_WR OR
           DSR<MIN_DSR OR PBO>MAX_PBO OR fails to beat control.
    INSUFFICIENT_DATA: no horizon reaches the frozen MC min_trades floor (gates
           not evaluable) — funding coverage still excludes too much.
    NaN on any required gate fails closed."""
    horizons = list(charged.keys())
    ns = {h: charged[h]["n_funding_charged"] for h in horizons}
    max_n = max(ns.values()) if ns else 0
    if max_n == 0:
        return ("INSUFFICIENT_DATA",
                "Funding-charged sample n=0 across all horizons — realized funding still "
                "does not cover any genuine listing's window.", {"n_by_horizon": ns})

    evaluable = {h: ns[h] >= MIN_EVALUABLE_N for h in horizons}
    detail = {"n_by_horizon": ns, "evaluable_by_horizon": evaluable, "pbo_across_horizons": pbo_value}

    # If no horizon is evaluable under the frozen MC floor, we cannot run the
    # capital-preservation gate -> INSUFFICIENT_DATA (not a merit NO_GO).
    if not any(evaluable.values()):
        return ("INSUFFICIENT_DATA",
                f"Funding-charged coverage below the frozen MC min_trades floor "
                f"({MIN_EVALUABLE_N}) on every horizon (n={ns}); DSR/PBO/OOS-WR/Monte-Carlo "
                f"not evaluable -> fail closed.", detail)

    go_reasons, nogo_reasons = [], []
    for h in horizons:
        if not evaluable[h]:
            continue
        c = charged[h]
        dsr = c.get("dsr_prob")
        wr = c.get("win_rate")
        oos_wr = c.get("oos_wr_walk_forward")
        mc = c.get("monte_carlo", {})
        mean = c.get("short_net_mean")
        beats = c.get("beats_control")
        checks = {
            "mean>0": (mean is not None and mean > 0),
            "wr>=MIN_OOS_WR": (wr is not None and wr >= MIN_OOS_WR),
            "oos_wr>=MIN_OOS_WR": (oos_wr is not None and np.isfinite(oos_wr) and oos_wr >= MIN_OOS_WR),
            "dsr>=MIN_DSR": (dsr is not None and np.isfinite(dsr) and dsr >= MIN_DSR),
            "pbo<=MAX_PBO": (np.isfinite(pbo_value) and pbo_value <= MAX_PBO),
            "mc_passes": bool(mc.get("passes")),
            "beats_control": bool(beats),
        }
        detail[h] = checks
        if all(checks.values()):
            go_reasons.append(h)
        else:
            failed = [k for k, v in checks.items() if not v]
            nogo_reasons.append(f"{h}: failed {failed}")

    if go_reasons:
        return ("GO", f"All frozen gates pass on funding-charged sample at {go_reasons}.", detail)
    return ("NO_GO",
            "No horizon clears all frozen gates on the funding-charged sample. "
            + " | ".join(nogo_reasons), detail)


if __name__ == "__main__":
    out = run_screen()
    print(json.dumps(out, indent=2, default=str))
