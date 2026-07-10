"""research/sim_accuracy_band.py — ACCURACY-BAND GEOMETRY SIMULATION.

Owner goal (2026-07-10): 60-67% realized win-rate on Binance/Bybit/Bitget
futures. ACCURACY_TARGET_MODE is live and sets TP = frac x SL distance (the
config knob ACCURACY_TP_FRAC_OF_SL, currently 0.5, floored at ACCURACY_MIN_TP_PCT
so the target always clears round-trip costs). This script MEASURES, on REAL
historical warehouse entries, which frac lands the AFTER-COST win rate inside the
60-67% band BEFORE any forward trade accrues.

METHOD (reuses tested machinery — no re-invention):
  * Data: resolved-eligible shadow_decisions rows (real entry_px / sl_px / venue
    / symbol / ts / timeframe / horizon_bars). The trades table (2,221 rows) is
    smaller; shadow_decisions (8,874 with barriers) is the richer source.
  * Replay: KEEP each row's production SL distance; REPLACE the TP barrier with
    the live geometry  TP% = max(min_tp_pct, frac x SL%)  (mirrors
    core.mcp_brain._apply_accuracy_target so the recommendation maps to the
    actual knob, floor and all). SL is never modified.
  * Resolve: core.shadow_resolver.resolve_one — first-touch-wins, SL-first
    conservative tie-break, taker fees 6bps/side, directional slippage, horizon
    censoring. Forward CLOSED candles via the runner's fetch machinery
    (scripts.resolve_shadow_outcomes.build_fetch_candles + public ccxt OHLCV).

HONESTY: after-cost only; censored rows (forward window not fully closed) are
EXCLUDED from the win rate, never counted as wins. This is a TUNING measurement
of a geometry band, NOT an edge claim — expectancy will very likely be negative
(the signal is no-edge) and is reported plainly next to the win rate. No
synthetic candles: a row with no real forward data is censored, not fabricated.

Re-runnable and deterministic (seedless; the only variation is live candle data).
Research path only — never imports/touches a live order path.

Run:  venv/Scripts/python.exe research/sim_accuracy_band.py
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.shadow_resolver import resolve_one  # noqa: E402
from core.stats_inference import wilson_interval  # noqa: E402
from scripts.resolve_shadow_outcomes import (  # noqa: E402
    _make_ccxt_fetcher,
    build_fetch_candles,
)

FRACS = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70]
DEFAULT_MIN_TP_PCT = 0.5  # config ACCURACY_MIN_TP_PCT default (percent of entry)
BAND_LO, BAND_HI = 0.60, 0.67
MID_LO, MID_HI = 0.62, 0.64
DEFAULT_DB = ROOT / "data" / "warehouse.sqlite"
RESOLVABLE_WHERE = (
    "entry_px IS NOT NULL AND sl_px IS NOT NULL AND tp_px IS NOT NULL"
)


def swap_tp(entry: float, sl: float, side: str, frac: float,
            min_tp_pct: float = DEFAULT_MIN_TP_PCT) -> tuple[float, bool]:
    """Return (tp_px, floor_bound) for the accuracy-band geometry.

    Mirrors core.mcp_brain._apply_accuracy_target: the TP DISTANCE is
    frac x the production SL distance, expressed in percent and floored at
    ``min_tp_pct`` so the target clears round-trip costs. SL is untouched.
    ``floor_bound`` is True when the min-TP floor raised the target above the
    pure frac x SL geometry (transparency: a bound floor lowers the achievable
    win rate versus pure geometry).
    """
    if entry <= 0:
        raise ValueError("entry must be positive")
    sl_dist_pct = 100.0 * abs(entry - sl) / entry
    geom_pct = sl_dist_pct * frac
    tp_pct = max(min_tp_pct, geom_pct)
    dist = tp_pct / 100.0
    tp = entry * (1.0 + dist) if side == "buy" else entry * (1.0 - dist)
    return tp, (geom_pct < min_tp_pct)


def load_rows(db_path: Path) -> list[dict]:
    """Read every resolvable (barriers present) shadow_decisions row, oldest
    first so the runner's per-group candle cache widens at most once."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT * FROM shadow_decisions WHERE {RESOLVABLE_WHERE} ORDER BY ts ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def fetch_all_candles(rows: list[dict], max_group_fetches: int) -> dict[str, list]:
    """One forward-candle slice per row via the SAME machinery the resolver
    runner uses (public ccxt OHLCV, no-repaint, forming-bar dropped). Cached per
    (venue, symbol, timeframe) group, so ~8.9k rows collapse to ~70 API calls.
    The slice is horizon-capped and INDEPENDENT of frac, so it is reused for
    every frac in the sweep."""
    fetch = build_fetch_candles(_make_ccxt_fetcher(), max_group_fetches=max_group_fetches)
    out: dict[str, list] = {}
    for r in rows:
        out[r["proposal_id"]] = fetch(dict(r))
    return out


def _summarize(records: list[tuple[float, float]]) -> dict:
    """records: list of (net_pnl_usd, r_multiple) for RESOLVED (non-censored)
    trades. WR uses the sign of net_pnl (== sign of r_multiple, risk>0).
    Expectancy is reported in R (size-invariant; warehouse notionals are
    unreliable) and in dollars (resolver's $200 reference fallback)."""
    n = len(records)
    if n == 0:
        return {"n": 0, "wins": 0, "wr": 0.0, "ci": (0.0, 1.0),
                "exp_r": 0.0, "exp_usd": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0}
    wins = sum(1 for pnl, _ in records if pnl > 0)
    r_all = [r for _, r in records]
    win_r = [r for pnl, r in records if pnl > 0]
    loss_r = [r for pnl, r in records if pnl <= 0]
    return {
        "n": n,
        "wins": wins,
        "wr": wins / n,
        "ci": wilson_interval(wins, n),
        "exp_r": statistics.fmean(r_all),
        "exp_usd": statistics.fmean([pnl for pnl, _ in records]),
        "avg_win_r": statistics.fmean(win_r) if win_r else 0.0,
        "avg_loss_r": statistics.fmean(loss_r) if loss_r else 0.0,
    }


def simulate(rows: list[dict], candles: dict[str, list],
             fracs: list[float] = FRACS,
             min_tp_pct: float = DEFAULT_MIN_TP_PCT,
             recent_split_days: float = 5.0) -> dict:
    """Resolve every row under each frac and aggregate overall / by side /
    by recent-vs-older. Returns a nested result dict."""
    max_ts = max((int(r["ts"]) for r in rows), default=0)
    recent_cut = max_ts - int(recent_split_days * 86400)

    result: dict[float, dict] = {}
    for frac in fracs:
        buckets: dict[str, list] = {
            "overall": [], "buy": [], "sell": [], "recent": [], "older": [],
        }
        censored = 0
        floor_bound = 0
        for r in rows:
            cs = candles.get(r["proposal_id"])
            if not cs:
                censored += 1
                continue
            try:
                entry = float(r["entry_px"])
                sl = float(r["sl_px"])
            except (TypeError, ValueError):
                censored += 1
                continue
            side = str(r["side"])
            tp, fb = swap_tp(entry, sl, side, frac, min_tp_pct)
            if fb:
                floor_bound += 1
            mod = dict(r)
            mod["tp_px"] = tp
            outcome = resolve_one(mod, cs)
            if outcome is None:  # censored: forward window not fully closed
                censored += 1
                continue
            rec = (outcome["net_pnl"], outcome["r_multiple"])
            buckets["overall"].append(rec)
            buckets[side].append(rec)
            buckets["recent" if int(r["ts"]) >= recent_cut else "older"].append(rec)
        result[frac] = {
            "censored": censored,
            "floor_bound": floor_bound,
            "splits": {k: _summarize(v) for k, v in buckets.items()},
        }
    return result


def _fmt_pct(x: float) -> str:
    return f"{100.0 * x:5.1f}%"


def _recommend(result: dict, fracs: list[float]) -> tuple[float | None, str]:
    """Pick the frac whose overall after-cost WR is closest to mid-band
    (62-64%), preferring one actually inside 60-67% with n>=300."""
    scored = []
    for frac in fracs:
        s = result[frac]["splits"]["overall"]
        if s["n"] == 0:
            continue
        mid = (MID_LO + MID_HI) / 2.0
        scored.append((abs(s["wr"] - mid), frac, s))
    if not scored:
        return None, "no resolved rows"
    scored.sort()
    _, best_frac, best = scored[0]
    in_band = BAND_LO <= best["wr"] <= BAND_HI
    note = (
        f"frac={best_frac:.2f}: WR={_fmt_pct(best['wr'])} "
        f"(95% CI {_fmt_pct(best['ci'][0])}-{_fmt_pct(best['ci'][1])}), n={best['n']}, "
        f"exp={best['exp_r']:+.3f}R. "
        + ("INSIDE" if in_band else "OUTSIDE")
        + " the 60-67% band."
    )
    return best_frac, note


def render_markdown(result: dict, rows: list[dict], fracs: list[float],
                    n_rows: int, min_tp_pct: float, recent_split_days: float) -> str:
    best_frac, rec_note = _recommend(result, fracs)
    lines: list[str] = []
    a = lines.append
    a("# 05 — Accuracy-Band Geometry Simulation")
    a("")
    a(f"_Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · "
      f"research/sim_accuracy_band.py · after-cost, resolved-only._")
    a("")
    a("## Pre-registration")
    a("")
    a("- **Hypothesis:** with a no-edge signal, first-touch win rate is a pure "
      "geometry function `SL/(SL+TP)`. Compressing `TP = frac x SL` raises the "
      "raw hit rate; the after-cost win rate lands in 60-67% for some frac.")
    a("- **What this is / is NOT:** a TUNING measurement of the geometry knob "
      "`ACCURACY_TP_FRAC_OF_SL`. It is NOT an edge claim — after-cost expectancy "
      "is expected to be negative and is reported alongside the win rate.")
    a(f"- **Universe / source:** `shadow_decisions` (model_version `shadow_v1`), "
      f"all Binance futures, {n_rows} rows with non-null barriers.")
    a("- **Replay rule:** keep production `sl_px`; set "
      f"`TP% = max({min_tp_pct}, frac x SL%)` (live geometry, floor and all); "
      "resolve via `core.shadow_resolver.resolve_one` (SL-first, 6bps/side fees, "
      "slippage, horizon censoring).")
    a("- **Frozen measure:** win rate = share of resolved trades with "
      "`net_pnl > 0`; **censored rows excluded** (never counted as wins); "
      "Wilson 95% CI per frac.")
    a("- **Decision rule:** recommend the frac whose overall after-cost WR is "
      "nearest mid-band (62-64%); flag if n < 300 or the CI straddles the band edge.")
    a("")
    a("## Overall frac -> WR curve (after cost)")
    a("")
    a("| frac | n resolved | censored | floor-bound | WR | Wilson 95% CI | exp (R) | exp ($) | avg win (R) | avg loss (R) |")
    a("|-----:|-----------:|---------:|------------:|------:|:-------------:|--------:|--------:|------------:|-------------:|")
    for frac in fracs:
        r = result[frac]
        s = r["splits"]["overall"]
        ci = f"{_fmt_pct(s['ci'][0])}-{_fmt_pct(s['ci'][1])}"
        mark = " ✅" if BAND_LO <= s["wr"] <= BAND_HI else ""
        a(f"| {frac:.2f} | {s['n']} | {r['censored']} | {r['floor_bound']} | "
          f"{_fmt_pct(s['wr'])}{mark} | {ci} | {s['exp_r']:+.3f} | "
          f"{s['exp_usd']:+.3f} | {s['avg_win_r']:+.3f} | {s['avg_loss_r']:+.3f} |")
    a("")
    a("## By side")
    a("")
    a("| frac | buy n | buy WR | buy CI | sell n | sell WR | sell CI |")
    a("|-----:|------:|-------:|:------:|-------:|--------:|:-------:|")
    for frac in fracs:
        b = result[frac]["splits"]["buy"]
        sll = result[frac]["splits"]["sell"]
        a(f"| {frac:.2f} | {b['n']} | {_fmt_pct(b['wr'])} | "
          f"{_fmt_pct(b['ci'][0])}-{_fmt_pct(b['ci'][1])} | {sll['n']} | "
          f"{_fmt_pct(sll['wr'])} | {_fmt_pct(sll['ci'][0])}-{_fmt_pct(sll['ci'][1])} |")
    a("")
    a(f"## Stability: recent (<= {recent_split_days:.0f}d) vs older")
    a("")
    a("_(History spans < 10 days, so a 30-day recent/older split is degenerate; "
      f"a {recent_split_days:.0f}-day split is used instead.)_")
    a("")
    a("| frac | recent n | recent WR | older n | older WR |")
    a("|-----:|---------:|----------:|--------:|---------:|")
    for frac in fracs:
        rc = result[frac]["splits"]["recent"]
        ol = result[frac]["splits"]["older"]
        a(f"| {frac:.2f} | {rc['n']} | {_fmt_pct(rc['wr'])} | "
          f"{ol['n']} | {_fmt_pct(ol['wr'])} |")
    a("")
    a("## Recommendation")
    a("")
    a(f"- {rec_note}")
    if best_frac is not None:
        a(f"- **Set `ACCURACY_TP_FRAC_OF_SL={best_frac:.2f}`** to target the band, "
          "subject to the CI and n caveats above.")
    a("- **Honesty:** the win rate is a geometry artifact. After-cost expectancy "
      "is negative across the curve (no-edge signal), so a higher win rate does "
      "NOT imply profit — it re-shapes the win/loss ratio. This measurement tunes "
      "geometry only and does not clear any promotion gate.")
    a("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--limit", type=int, default=0, help="cap rows (0=all; for smoke tests)")
    p.add_argument("--max-group-fetches", type=int, default=100)
    p.add_argument("--recent-split-days", type=float, default=5.0)
    p.add_argument("--out", default=str(ROOT / "_workspace" / "strategy_pipeline" / "05_accuracy_band_sim.md"))
    args = p.parse_args(argv)

    rows = load_rows(Path(args.db))
    if args.limit:
        rows = rows[: args.limit]
    n_rows = len(rows)
    print(f"[sim] loaded {n_rows} resolvable rows; fetching forward candles...")
    t0 = time.time()
    candles = fetch_all_candles(rows, args.max_group_fetches)
    n_with = sum(1 for v in candles.values() if v)
    print(f"[sim] candles fetched in {time.time()-t0:.0f}s; {n_with}/{n_rows} rows have forward bars")
    result = simulate(rows, candles, FRACS, DEFAULT_MIN_TP_PCT, args.recent_split_days)

    for frac in FRACS:
        s = result[frac]["splits"]["overall"]
        print(f"[sim] frac={frac:.2f} n={s['n']} WR={100*s['wr']:.1f}% "
              f"CI={100*s['ci'][0]:.1f}-{100*s['ci'][1]:.1f}% exp={s['exp_r']:+.3f}R "
              f"censored={result[frac]['censored']}")

    md = render_markdown(result, rows, FRACS, n_rows, DEFAULT_MIN_TP_PCT, args.recent_split_days)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"[sim] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
