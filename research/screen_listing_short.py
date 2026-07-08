"""Candidate B — post-listing perp-short after-cost screen (edge-screener).

Pre-registered in _workspace/strategy_pipeline/02b_screener_listing_short.md.
Honest, local-data-only screen. Charges ALL costs: config.FEE + config.SLIPPAGE
per leg, and realized funding to the short leg from local funding history ONLY.
Listings without funding coverage are EXCLUDED and COUNTED — never guessed.

Listing date = idiosyncratic first-candle timestamp (FMZQuant method) with the
backfill-cluster caveat enforced (first-ts shared by >=3 symbols = cache backfill
artifact, excluded).

Run: venv/Scripts/python.exe research/screen_listing_short.py
Emits JSON diagnostics to stdout; no files written by default.
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
WIN_LO = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
WIN_HI = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
MIN_POST_DAYS = 30
CLUSTER_MIN_SHARED = 3
HORIZONS_D = (7, 30, 90)  # one pre-registered family; n_trials = 3
DAY = 24 * 3600
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


# ── Screen orchestration ────────────────────────────────────────────────────
def _base(sym: str) -> str:
    return sym.split("-")[0]


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


def run_screen() -> dict:
    import config

    fee = float(config.FEE["futures_taker"])
    slip_open = float(config.SLIPPAGE["pct_open"])
    slip_close = float(config.SLIPPAGE["pct_close"])
    slip = (slip_open + slip_close) / 2.0  # 5bps each side; per-side value

    firstlast = scan_first_last()
    firsts = {s: v[0] for s, v in firstlast.items()}
    lasts = {s: v[1] for s, v in firstlast.items()}
    clusters = find_backfill_clusters(firsts)

    # eligible genuine listings
    genuine = {
        s: firsts[s]
        for s in firsts
        if firsts[s] not in clusters
        and WIN_LO <= firsts[s] <= WIN_HI
        and (lasts[s] - firsts[s]) >= MIN_POST_DAYS * DAY
    }

    fc_set, derivs_set = funding_coverage_sets()

    def has_funding(sym):
        return _base(sym) in fc_set or _base(sym) in derivs_set

    covered = {s: ts for s, ts in genuine.items() if has_funding(s)}
    uncovered = {s: ts for s, ts in genuine.items() if not has_funding(s)}

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
        for cs, cdf in control_df.items():
            p0, _ = price_at(cdf, entry_ts)
            p1, _ = price_at(cdf, exit_ts)
            if p0 and p1 and p0 > 0:
                rets.append((p1 - p0) / p0)
        return float(np.mean(rets)) if rets else None

    # DIAGNOSTIC ONLY: fees-only short paths (funding UNCHARGED — not gate-eligible).
    # Computed over the genuine listings to characterize the pre-funding price path.
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
            # tolerance: exit bar must be within +12h of target (else data gap)
            if a1 - exit_ts > 12 * 3600:
                continue
            listing_ret = (p1 - p0) / p0
            snet = short_net_return(p0, p1, 0.0, fee, slip)  # funding=0 → UNCHARGED
            cr = control_return(a0, a1)
            short_nets.append(snet)
            listing_prets.append(listing_ret)
            if cr is not None:
                ctrl_rets.append(cr)
                underperf.append(cr - listing_ret)  # >0 = listing worse than market
        arr = np.array(short_nets, dtype=float)
        diag[f"{H}d"] = {
            "n": int(arr.size),
            "short_net_mean_feesonly": float(np.mean(arr)) if arr.size else None,
            "short_net_median_feesonly": float(np.median(arr)) if arr.size else None,
            "short_win_rate_feesonly": float(np.mean(arr > 0)) if arr.size else None,
            "listing_price_ret_mean": float(np.mean(listing_prets)) if listing_prets else None,
            "listing_price_ret_median": float(np.median(listing_prets)) if listing_prets else None,
            "control_ret_mean": float(np.mean(ctrl_rets)) if ctrl_rets else None,
            "underperf_mean(ctrl-listing)": float(np.mean(underperf)) if underperf else None,
            "underperf_median(ctrl-listing)": float(np.median(underperf)) if underperf else None,
            "listing_underperforms_ctrl_rate": (
                float(np.mean(np.array(underperf) > 0)) if underperf else None
            ),
        }
        # diagnostic DSR (fees-only, funding-uncharged) via the repo's own gate math
        if arr.size >= 4:
            try:
                from core.promotion_gate import _kurt, _skew
                from core.stat_tests import deflated_sharpe, sharpe

                sr = sharpe(arr)
                dsr_prob = deflated_sharpe(
                    sr_observed=sr,
                    n_trials=len(HORIZONS_D),
                    n_obs=int(arr.size),
                    skew=float(_skew(arr)),
                    kurt=float(_kurt(arr)),
                    sr_var=1.0 / max(2, int(arr.size)),
                )
                diag[f"{H}d"]["diag_sharpe_feesonly"] = float(sr)
                diag[f"{H}d"]["diag_dsr_prob_feesonly"] = float(dsr_prob)
            except Exception as e:
                diag[f"{H}d"]["diag_dsr_error"] = str(e)

    return {
        "costs": {"fee_per_side": fee, "slip_per_side": slip,
                  "roundtrip_cost": 2 * (fee + slip)},
        "universe": {
            "total_1h_symbols_with_data": len(firsts),
            "backfill_cluster_timestamps": len(clusters),
            "excluded_backfill_cluster_symbols": sum(
                1 for s in firsts if firsts[s] in clusters
            ),
            "genuine_listings_in_window_30d": len(genuine),
            "cluster_ts_iso": sorted(
                datetime.fromtimestamp(ts, timezone.utc).isoformat()
                + f" (n={Counter(firsts.values())[ts]})"
                for ts in clusters
            ),
        },
        "funding_coverage": {
            "funding_cache_coins": len(fc_set),
            "derivs_symbols": len(derivs_set),
            "genuine_listings_WITH_funding_coverage": len(covered),
            "genuine_listings_WITHOUT_funding_coverage_EXCLUDED": len(uncovered),
            "covered_symbols": sorted(covered.keys()),
        },
        "control_basket_size": len(control_syms),
        "diagnostic_feesonly_funding_UNCHARGED": diag,
    }


if __name__ == "__main__":
    print(json.dumps(run_screen(), indent=2, default=str))
