"""research/screen_band_conditional.py — PRE-REGISTERED conditional-WR screen (13).

Question: do any measurable PRE-ENTRY conditions shift the accuracy-band
geometry's (f=0.35) after-cost win rate enough to constitute real entry edge
(WR >= 68% AND after-cost expectancy > 0, jointly)?

Pre-registration (frozen BEFORE any conditional outcome was computed):
`_workspace/strategy_pipeline/13_band_conditional_screen.md`. Six condition
families, buckets, sources, and gates are defined there and mirrored here —
any change requires re-registration.

Outcome machinery is IDENTICAL to the sweep that produced the 65.7% baseline
(`research/sim_accuracy_band.py`): production SL kept, TP swapped to
max(0.5%, 0.35 x SL%), resolved first-touch-wins via
`core.shadow_resolver.resolve_one` (6bps/side fees, 5/5/10bps slippage,
horizon censoring; censored rows excluded, never wins). Candle slices contain
only bars opened strictly AFTER entry (entry-anchored; no pre-entry bars).

Research path only. Never touches live decision code. Read-only warehouse.

Run:  venv/Scripts/python.exe research/screen_band_conditional.py
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import pandas as pd
from scipy.stats import binom as _binom

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.shadow_resolver import resolve_one  # noqa: E402
from core.stats_inference import wilson_interval  # noqa: E402
from research.sim_accuracy_band import fetch_all_candles, load_rows, swap_tp  # noqa: E402
from scripts.resolve_shadow_outcomes import _make_ccxt_fetcher  # noqa: E402
from utils.indicators import adx as _adx  # noqa: E402
from utils.indicators import atr as _atr  # noqa: E402
from utils.indicators import ema as _ema  # noqa: E402

# ---------------------------------------------------------------- frozen spec
FRAC = 0.35
MIN_TP_PCT = 0.5
P0 = 0.657  # frozen H0 baseline (the cited sweep)
BONFERRONI_M = 16  # 3+3+3+2+2+3 pre-registered gated buckets
REPLICATION_CUTOFF_TS = 1783651564  # ~the 8,878-entry sweep snapshot
GATE_WR = 0.68
GATE_N = 300
GATE_HALF_WR = 0.65
GATE_P = 0.05
GATE_FLOW = 0.10
F4_JOIN_TOL_S = 1800
F4_COVERAGE_FLOOR = 0.50
F4_MIN_WINDOW = 30  # implementation note: percentile needs a window
F2_MIN_WINDOW = 480  # 20d of 1h bars (pre-reg: <20d history uncovered)
DB = ROOT / "data" / "warehouse.sqlite"
WS = ROOT / "_workspace" / "strategy_pipeline"
MD_PATH = WS / "13_band_conditional_screen.md"
JSON_PATH = WS / "13_band_conditional_screen.json"
ROWS_CSV = WS / "13_band_conditional_rows.csv"
OUTCOME_CACHE = WS / "13_band_outcome_cache.json"
TF_SEC = {"1h": 3600, "4h": 14400}


# ------------------------------------------------------------- pure functions
def bucket_f1(ratio: float) -> str:
    if ratio < 0.7:
        return "<0.7"
    if ratio <= 1.3:
        return "0.7-1.3"
    return ">1.3"


def bucket_pctl3(pctl: float) -> str:
    if pctl < 25.0:
        return "<25th"
    if pctl <= 75.0:
        return "25-75th"
    return ">75th"


def bucket_f3(adx_val: float) -> str:
    if adx_val < 20.0:
        return "<20"
    if adx_val <= 30.0:
        return "20-30"
    return ">30"


def bucket_f4(pctl: float) -> str:
    return "<50th" if pctl < 50.0 else ">=50th"


def bucket_f6(gap_pct: float):
    """Gap < 0.15% is the pre-registered residual (NOT gated) -> None."""
    if gap_pct < 0.15:
        return None
    if gap_pct < 0.30:
        return "0.15-0.30%"
    if gap_pct <= 0.60:
        return "0.30-0.60%"
    return ">0.60%"


def last_closed_idx(opens_ms, entry_ts_s: int, tf_sec: int):
    """Index of the last bar CLOSED at entry (open + tf <= entry); None if none."""
    thr = entry_ts_s * 1000 - tf_sec * 1000
    i = bisect.bisect_right(opens_ms, thr) - 1
    return i if i >= 0 else None


def trailing_pctl(vals, x):
    """Mid-rank percentile of x within vals (ties get half weight)."""
    if not vals:
        return None
    below = sum(1 for v in vals if v < x)
    ties = sum(1 for v in vals if v == x)
    return 100.0 * (below + 0.5 * ties) / len(vals)


def binom_p_upper(wins: int, n: int, p0: float) -> float:
    """Exact one-sided P(X >= wins | n, p0)."""
    if n <= 0:
        return 1.0
    return float(_binom.sf(wins - 1, n, p0))


def bonferroni(p: float, m: int = BONFERRONI_M) -> float:
    return min(1.0, p * m)


def _wr(records) -> float:
    return (sum(1 for r in records if r["net_pnl"] > 0) / len(records)) if records else 0.0


def evaluate_gates(records, total_resolved: int) -> dict:
    """Apply the six frozen gates to one bucket. records: {ts, net_pnl, r_multiple}."""
    n = len(records)
    wins = sum(1 for r in records if r["net_pnl"] > 0)
    wr = wins / n if n else 0.0
    ci = wilson_interval(wins, n) if n else (0.0, 1.0)
    srt = sorted(records, key=lambda r: r["ts"])
    half = n // 2
    wr1, wr2 = _wr(srt[:half]), _wr(srt[half:])
    exp_usd = statistics.fmean([r["net_pnl"] for r in records]) if n else 0.0
    exp_r = statistics.fmean([r["r_multiple"] for r in records]) if n else 0.0
    p_raw = binom_p_upper(wins, n, P0)
    p_adj = bonferroni(p_raw)
    flow = (n / total_resolved) if total_resolved else 0.0
    gates = {
        "g1_wr_ge_68": {"ok": wr >= GATE_WR, "wr": round(wr, 4)},
        "g2_n_ge_300": {"ok": n >= GATE_N, "n": n},
        "g3_halves_ge_65": {
            "ok": (n >= 2 and wr1 >= GATE_HALF_WR and wr2 >= GATE_HALF_WR),
            "half1_wr": round(wr1, 4),
            "half2_wr": round(wr2, 4),
        },
        "g4_expectancy_pos": {
            "ok": exp_usd > 0,
            "exp_usd": round(exp_usd, 4),
            "exp_r": round(exp_r, 4),
        },
        "g5_bonferroni_p": {
            "ok": p_adj < GATE_P,
            "p_raw": p_raw,
            "p_adj": p_adj,
            "m": BONFERRONI_M,
        },
        "g6_flow_ge_10pct": {"ok": flow >= GATE_FLOW, "flow_share": round(flow, 4)},
    }
    gates["verdict_go"] = all(g["ok"] for k, g in gates.items() if k != "verdict_go")
    gates["wilson_ci"] = [round(ci[0], 4), round(ci[1], 4)]
    gates["wins"] = wins
    return gates


# ---------------------------------------------------------------- outcomes
def resolve_records(rows, candles):
    """Per-row f=0.35 outcome records (the sweep's exact machinery)."""
    out, censored = [], 0
    for r in rows:
        cs = candles.get(r["proposal_id"])
        if not cs:
            censored += 1
            continue
        try:
            entry, sl = float(r["entry_px"]), float(r["sl_px"])
        except (TypeError, ValueError):
            censored += 1
            continue
        side = str(r["side"])
        tp, _fb = swap_tp(entry, sl, side, FRAC, MIN_TP_PCT)
        mod = dict(r)
        mod["tp_px"] = tp
        outcome = resolve_one(mod, cs)
        if outcome is None:
            censored += 1
            continue
        out.append(
            {
                "proposal_id": r["proposal_id"],
                "ts": int(r["ts"]),
                "venue": str(r["venue"]),
                "symbol": str(r["symbol"]),
                "side": side,
                "net_pnl": float(outcome["net_pnl"]),
                "r_multiple": float(outcome["r_multiple"]),
            }
        )
    return out, censored


# ----------------------------------------------------------- history fetching
def fetch_history(fetch_ohlcv, venue, symbol, tf, since_ms, max_bars=25000):
    """Paginated closed-candle history from `since_ms` to now."""
    out, since = [], int(since_ms)
    while True:
        try:
            batch = list(fetch_ohlcv(venue, symbol, tf, since))
        except Exception:
            break
        batch = [c for c in batch if not out or c[0] > out[-1][0]]
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 5 or len(out) >= max_bars:
            break
        since = out[-1][0] + 1
    return out


def indicator_frame(candles):
    df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "c", "v"])
    return df


def compute_conditions(resolved, fetch_ohlcv, min_ts, progress=print):
    """Attach F1/F2/F3/F6 condition values to each resolved record (in place).
    Sources exactly as pre-registered; anchored to the last CLOSED bar."""
    pairs = sorted({(r["venue"], r["symbol"]) for r in resolved})
    since_1h = (min_ts - 31 * 86400) * 1000
    since_4h = (min_ts - 60 * 86400) * 1000

    # --- F1: BTC 1h ATR regime (binance)
    btc = fetch_history(fetch_ohlcv, "binance", "BTC/USDT:USDT", "1h", since_1h)
    bdf = indicator_frame(btc)
    b_atr = _atr(bdf["h"], bdf["l"], bdf["c"], 14)
    b_med = b_atr.rolling(720, min_periods=360).median()
    b_opens = bdf["ts"].tolist()
    progress(f"[cond] BTC 1h bars: {len(btc)}")

    hist_1h, hist_4h = {}, {}
    for i, (venue, symbol) in enumerate(pairs):
        h1 = fetch_history(fetch_ohlcv, venue, symbol, "1h", since_1h)
        h4 = fetch_history(fetch_ohlcv, venue, symbol, "4h", since_4h)
        d1, d4 = indicator_frame(h1), indicator_frame(h4)
        a1 = _atr(d1["h"], d1["l"], d1["c"], 14) if len(d1) else pd.Series(dtype=float)
        adx4 = _adx(d4["h"], d4["l"], d4["c"], 14)[0] if len(d4) else pd.Series(dtype=float)
        if len(d4):
            e20, e50 = _ema(d4["c"], 20), _ema(d4["c"], 50)
            gap4 = (e20 - e50).abs() / e50 * 100.0  # mirrors core/mcp_brain.py:2858
        else:
            gap4 = pd.Series(dtype=float)
        hist_1h[(venue, symbol)] = (d1["ts"].tolist(), a1)
        hist_4h[(venue, symbol)] = (d4["ts"].tolist(), adx4, gap4)
        if (i + 1) % 10 == 0:
            progress(f"[cond] history {i + 1}/{len(pairs)} pairs")

    for rec in resolved:
        ts = rec["ts"]
        # F1
        idx = last_closed_idx(b_opens, ts, 3600)
        rec["f1_ratio"] = None
        if idx is not None:
            a, m = b_atr.iloc[idx], b_med.iloc[idx]
            if pd.notna(a) and pd.notna(m) and m > 0:
                rec["f1_ratio"] = float(a / m)
        # F2
        opens1, a1 = hist_1h[(rec["venue"], rec["symbol"])]
        rec["f2_pctl"] = None
        idx = last_closed_idx(opens1, ts, 3600)
        if idx is not None:
            window = [float(v) for v in a1.iloc[max(0, idx - 719) : idx + 1] if pd.notna(v)]
            if len(window) >= F2_MIN_WINDOW and pd.notna(a1.iloc[idx]):
                rec["f2_pctl"] = trailing_pctl(window, float(a1.iloc[idx]))
        # F3 / F6
        opens4, adx4, gap4 = hist_4h[(rec["venue"], rec["symbol"])]
        rec["f3_adx"] = rec["f6_gap"] = None
        idx = last_closed_idx(opens4, ts, 14400)
        if idx is not None:
            if pd.notna(adx4.iloc[idx]):
                rec["f3_adx"] = float(adx4.iloc[idx])
            if pd.notna(gap4.iloc[idx]):
                rec["f6_gap"] = float(gap4.iloc[idx])


def compute_f4(resolved, db_path, min_ts):
    """F4 spread percentile from candidates.features_json.ob_spread_bps
    (nearest same-symbol row within +/-1800s; percentile vs the symbol's own
    trailing 30d of candidates spread values)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    per_sym: dict[str, list] = {}
    try:
        q = (
            "SELECT ts, symbol, features_json FROM candidates "
            "WHERE ts >= ? AND features_json LIKE '%ob_spread_bps%'"
        )
        for ts, sym, fj in conn.execute(q, (min_ts - 30 * 86400,)):
            try:
                v = json.loads(fj).get("ob_spread_bps")
            except (ValueError, TypeError):
                continue
            if v is None:
                continue
            per_sym.setdefault(sym.split(":")[0], []).append((int(ts), float(v)))
    finally:
        conn.close()
    for sym in per_sym:
        per_sym[sym].sort()

    fwd_joins = 0
    for rec in resolved:
        rec["f4_pctl"] = None
        arr = per_sym.get(rec["symbol"].split(":")[0])
        if not arr:
            continue
        ts = rec["ts"]
        tss = [t for t, _ in arr]
        i = bisect.bisect_left(tss, ts)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(arr) and abs(arr[j][0] - ts) <= F4_JOIN_TOL_S:
                if best is None or abs(arr[j][0] - ts) < abs(arr[best][0] - ts):
                    best = j
        if best is None:
            continue
        lo = bisect.bisect_left(tss, ts - 30 * 86400)
        hi = bisect.bisect_right(tss, ts)
        window = [v for _, v in arr[lo:hi]]
        if len(window) < F4_MIN_WINDOW:
            continue
        if arr[best][0] > ts:
            fwd_joins += 1
        rec["f4_pctl"] = trailing_pctl(window, arr[best][1])
    return fwd_joins


# -------------------------------------------------------------------- report
FAMILY_BUCKETS = {
    "f1_btc_atr_regime": ["<0.7", "0.7-1.3", ">1.3"],
    "f2_symbol_atr_pctl": ["<25th", "25-75th", ">75th"],
    "f3_adx_4h": ["<20", "20-30", ">30"],
    "f4_spread_pctl": ["<50th", ">=50th"],
    "f6_ema_gap_4h": ["0.15-0.30%", "0.30-0.60%", ">0.60%"],
}


def assign_buckets(rec):
    out = {}
    if rec.get("f1_ratio") is not None:
        out["f1_btc_atr_regime"] = bucket_f1(rec["f1_ratio"])
    if rec.get("f2_pctl") is not None:
        out["f2_symbol_atr_pctl"] = bucket_pctl3(rec["f2_pctl"])
    if rec.get("f3_adx") is not None:
        out["f3_adx_4h"] = bucket_f3(rec["f3_adx"])
    if rec.get("f4_pctl") is not None:
        out["f4_spread_pctl"] = bucket_f4(rec["f4_pctl"])
    if rec.get("f6_gap") is not None:
        out["f6_ema_gap_4h"] = bucket_f6(rec["f6_gap"])  # None => residual
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="cap rows (smoke test)")
    ap.add_argument("--max-group-fetches", type=int, default=200)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)

    rows = load_rows(DB)
    smoke = bool(args.limit)
    if args.limit:
        rows = rows[: args.limit]
    n_raw = len(rows)
    print(f"[13] loaded {n_raw} resolvable shadow rows")

    # Phase 1 — outcomes at f=0.35 (cached: the expensive network phase)
    if OUTCOME_CACHE.exists() and not args.no_cache and not args.limit:
        blob = json.loads(OUTCOME_CACHE.read_text(encoding="utf-8"))
        resolved, censored = blob["resolved"], blob["censored"]
        print(f"[13] outcome cache hit: {len(resolved)} resolved / {censored} censored")
    else:
        t0 = time.time()
        candles = fetch_all_candles(rows, args.max_group_fetches)
        print(f"[13] forward candles fetched in {time.time() - t0:.0f}s")
        resolved, censored = resolve_records(rows, candles)
        if not args.limit:
            OUTCOME_CACHE.write_text(
                json.dumps({"resolved": resolved, "censored": censored}), encoding="utf-8"
            )
        print(f"[13] resolved {len(resolved)} / censored {censored}")

    total_resolved = len(resolved)
    if total_resolved == 0:
        print("[13] nothing resolved — aborting without verdicts")
        return 1

    # Baselines (guard BEFORE conditional claims)
    base_all = evaluate_gates(resolved, total_resolved)
    repl = [r for r in resolved if r["ts"] <= REPLICATION_CUTOFF_TS]
    base_repl = evaluate_gates(repl, total_resolved) if repl else None
    repl_wr = _wr(repl) if repl else float("nan")
    guard_ok = repl and abs(repl_wr - P0) <= 0.02
    print(
        f"[13] baseline full={_wr(resolved):.4f} n={total_resolved} | "
        f"replication={repl_wr:.4f} n={len(repl)} | guard_ok={guard_ok}"
    )

    # Phase 2 — conditions
    min_ts = min(r["ts"] for r in resolved)
    fetch_ohlcv = _make_ccxt_fetcher()
    compute_conditions(resolved, fetch_ohlcv, min_ts)
    fwd_joins = compute_f4(resolved, DB, min_ts)

    # per-row CSV for the auditor
    fields = [
        "proposal_id",
        "ts",
        "venue",
        "symbol",
        "side",
        "net_pnl",
        "r_multiple",
        "f1_ratio",
        "f2_pctl",
        "f3_adx",
        "f4_pctl",
        "f6_gap",
    ]
    rows_csv = ROWS_CSV.with_suffix(".smoke.csv") if smoke else ROWS_CSV
    with rows_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(resolved)
    print(f"[13] wrote {rows_csv}")

    # Phase 3 — buckets, gates, verdicts
    span_days = max((max(r["ts"] for r in resolved) - min_ts) / 86400.0, 1e-9)
    by_bucket: dict[tuple, list] = {}
    residual_f6, covered = [], {f: 0 for f in FAMILY_BUCKETS}
    for rec in resolved:
        b = assign_buckets(rec)
        for fam, bucket in b.items():
            if fam == "f6_ema_gap_4h" and bucket is None:
                residual_f6.append(rec)
                covered[fam] += 1
                continue
            covered[fam] += 1
            by_bucket.setdefault((fam, bucket), []).append(rec)

    f4_coverage = covered["f4_spread_pctl"] / total_resolved
    f4_insufficient = f4_coverage < F4_COVERAGE_FLOOR

    verdicts = []
    for fam, buckets in FAMILY_BUCKETS.items():
        for bucket in buckets:
            recs = by_bucket.get((fam, bucket), [])
            if fam == "f4_spread_pctl" and f4_insufficient:
                verdicts.append(
                    {
                        "candidate": f"band_conditional_{fam}_{bucket}",
                        "family": fam,
                        "bucket": bucket,
                        "n": len(recs),
                        "verdict": "INSUFFICIENT_DATA",
                        "reason": (
                            f"coverage {f4_coverage:.1%} < 50% floor "
                            "(pre-registered); ob_spread_bps present on only a "
                            "subset of candidates and no precomputed spread "
                            "percentile exists in-span"
                        ),
                        "backfill": (
                            "persist spread percentile per candidate: re-enable "
                            "the FeatureVector writer (core/features.py -> "
                            "warehouse `features` table, dormant since "
                            "2026-06-14) or log ob_spread_bps on ALL candidates; "
                            "accrues forward only — historical book spreads "
                            "cannot be reconstructed"
                        ),
                    }
                )
                continue
            gates = evaluate_gates(recs, total_resolved) if recs else None
            verdicts.append(
                {
                    "candidate": f"band_conditional_{fam}_{bucket}",
                    "family": fam,
                    "bucket": bucket,
                    "n": len(recs),
                    "after_cost_metrics": None
                    if not recs
                    else {
                        "wr": gates["g1_wr_ge_68"]["wr"],
                        "wilson_ci": gates["wilson_ci"],
                        "exp_usd": gates["g4_expectancy_pos"]["exp_usd"],
                        "exp_r": gates["g4_expectancy_pos"]["exp_r"],
                        "entries_per_day": round(len(recs) / span_days, 1),
                    },
                    "gates": gates,
                    "verdict": (
                        "INSUFFICIENT_DATA"
                        if not recs
                        else ("GO" if (gates["verdict_go"] and guard_ok) else "NO_GO")
                    ),
                }
            )

    # F5 — fill type (data-availability verdict from trades)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        ft = dict(
            conn.execute(
                "SELECT COALESCE(fill_type,'NULL'), COUNT(*) FROM trades GROUP BY 1"
            ).fetchall()
        )
        n_mfe = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE mfe IS NOT NULL AND mae IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    for bucket in ("maker", "taker"):
        verdicts.append(
            {
                "candidate": f"band_conditional_f5_fill_type_{bucket}",
                "family": "f5_fill_type",
                "bucket": bucket,
                "n": ft.get("maker", 0) if bucket == "maker" else ft.get("taker_fallback", 0),
                "verdict": "INSUFFICIENT_DATA",
                "reason": (
                    f"trades.fill_type non-null on only "
                    f"{sum(v for k, v in ft.items() if k != 'NULL')} of "
                    f"{sum(ft.values())} trades; {n_mfe} trades have mfe/mae, so "
                    "the f=0.35 counterfactual band outcome is uncomputable"
                ),
                "backfill": (
                    "C7 MFE/MAE persistence (commit 2aff0f2, 2026-07-08) accrues "
                    "forward only; needs >=300 live/paper trades with fill_type "
                    "AND mfe/mae — no harvest command can backfill retroactively"
                ),
            }
        )

    json_path = JSON_PATH.with_suffix(".smoke.json") if smoke else JSON_PATH
    result = {
        "screen": "13_band_conditional",
        "generated_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "frac": FRAC,
        "p0_baseline": P0,
        "bonferroni_m": BONFERRONI_M,
        "n_raw": n_raw,
        "n_resolved": total_resolved,
        "n_censored": censored,
        "span_days": round(span_days, 2),
        "baseline_full": {
            "wr": round(_wr(resolved), 4),
            "n": total_resolved,
            "ci": base_all["wilson_ci"],
            "exp_usd": base_all["g4_expectancy_pos"]["exp_usd"],
            "exp_r": base_all["g4_expectancy_pos"]["exp_r"],
        },
        "baseline_replication_subset": None
        if not repl
        else {
            "cutoff_ts": REPLICATION_CUTOFF_TS,
            "wr": round(repl_wr, 4),
            "n": len(repl),
            "ci": base_repl["wilson_ci"],
            "deviation_pp": round(abs(repl_wr - P0) * 100, 2),
        },
        "replication_guard_ok": bool(guard_ok),
        "coverage": {f: covered[f] for f in FAMILY_BUCKETS},
        "f6_residual_below_0p15": len(residual_f6),
        "f4_forward_joins": fwd_joins,
        "verdicts": verdicts,
    }
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[13] wrote {json_path}")
    if smoke:
        print("[13] smoke run: skipping MD append and rows CSV overwrite")
        return 0

    # Append RESULTS to the pre-registration MD (nothing above the line changes)
    lines = [""]
    a = lines.append
    a(
        f"_Computed {result['generated_utc']} · research/screen_band_conditional.py · "
        f"n_raw={n_raw}, resolved={total_resolved}, censored={censored}, "
        f"span={span_days:.1f}d._"
    )
    a("")
    a("### Baseline recompute (guard)")
    a("")
    a(
        f"- Full sample: WR **{100 * _wr(resolved):.1f}%** "
        f"(CI {100 * base_all['wilson_ci'][0]:.1f}-{100 * base_all['wilson_ci'][1]:.1f}), "
        f"n={total_resolved}, exp={base_all['g4_expectancy_pos']['exp_r']:+.3f}R / "
        f"{base_all['g4_expectancy_pos']['exp_usd']:+.3f}$"
    )
    if repl:
        a(
            f"- Replication subset (ts<={REPLICATION_CUTOFF_TS}): WR "
            f"**{100 * repl_wr:.1f}%**, n={len(repl)}, deviation from 65.7% = "
            f"{abs(repl_wr - P0) * 100:.2f}pp -> guard "
            + ("**OK**" if guard_ok else "**FAILED — JOIN_SUSPECT, no GO possible**")
        )
    a("")
    a("### Per-bucket results")
    a("")
    a("| family | bucket | n | WR | CI | exp $ | exp R | e/day | p_adj | halves | verdict |")
    a("|---|---|---:|---:|:-:|---:|---:|---:|---:|:-:|---|")
    for v in verdicts:
        g = v.get("gates")
        m = v.get("after_cost_metrics")
        if not g or not m:
            a(
                f"| {v['family']} | {v['bucket']} | {v.get('n', 0)} | - | - | - | - | - | - "
                f"| - | {v['verdict']} |"
            )
            continue
        a(
            f"| {v['family']} | {v['bucket']} | {v['n']} | {100 * m['wr']:.1f}% | "
            f"{100 * m['wilson_ci'][0]:.1f}-{100 * m['wilson_ci'][1]:.1f} | "
            f"{m['exp_usd']:+.3f} | {m['exp_r']:+.3f} | {m['entries_per_day']} | "
            f"{g['g5_bonferroni_p']['p_adj']:.3g} | "
            f"{100 * g['g3_halves_ge_65']['half1_wr']:.0f}/"
            f"{100 * g['g3_halves_ge_65']['half2_wr']:.0f} | {v['verdict']} |"
        )
    a("")
    a(
        f"- Coverage: {result['coverage']} of {total_resolved} resolved; "
        f"F6 residual (<0.15% gap, not gated): {len(residual_f6)}; "
        f"F4 forward joins (candidate ts up to +30min after entry): {fwd_joins}."
    )
    a(
        "- Implementation notes (disclosed): F4 percentile needs >=30 trailing spread "
        "values; F2 needs >=480 ATR bars (20d). Neither is a gate change."
    )
    a("")
    md = MD_PATH.read_text(encoding="utf-8")
    MD_PATH.write_text(md.replace("_pending_", "\n".join(lines), 1), encoding="utf-8")
    print(f"[13] appended results to {MD_PATH}")

    for v in verdicts:
        print(f"[13] {v['candidate']}: {v['verdict']} (n={v.get('n', 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
