"""scripts/refit_age_cutoffs.py — empirical age-cutoff refit (Patch #2).

Per spec §4 (`docs/superpowers/specs/2026-05-18-bleed-fix-sprint-and-rebuild-design.md`):

    Refits the per-tier `max_position_age_hours` cutoff (AGE_LIMIT) using a
    60-day window from data/warehouse.sqlite, with 45-day fit / 15-day
    holdout split. Per leverage tier (STANDARD/CONVICTION/AGGRESSIVE keyed
    by mcp_score band), searches a grid of candidate cutoffs in minutes,
    picks the fit-set argmax, and validates that the proposed cutoff
    strictly improves on the current cutoff over the holdout window.

    Only when (a) the tier has ≥ min_sample (default 20) fit-set rows AND
    (b) the holdout strictly improves does the tier's proposed cutoff land
    in `data/models/age_cutoffs.json`. Otherwise the tier is omitted from
    the JSON and the runtime falls back to RISK config constants.

    STAR symbols are not refit (Phase 14 evidence: TP-extender × 1.25 is
    the right knob for them, not age-cutoff). They are filtered out of
    the fit set entirely.

USAGE
    python scripts/refit_age_cutoffs.py --days 60                # dry-run
    python scripts/refit_age_cutoffs.py --days 60 --commit       # write JSON

EXIT CODES
    0 — success (whether JSON was written or not)
    1 — no warehouse data / unrecoverable error
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# ── Tier definitions (mcp_score bands; mirror config STANDARD/CONVICTION/AGGRESSIVE) ─
# Phase 51 (2026-05-14) tiered leverage scheme keys off the SAME score bands.
TIERS = {
    "STANDARD": (65, 74),
    "CONVICTION": (75, 84),
    "AGGRESSIVE": (85, 200),  # 101 is the theoretical max, 200 is a safe upper bound
}

# Default candidate cutoff grid (minutes). Anchors at Phase-15 evidence
# (30/60/120 buckets) and the spec's 119-min median TP fire time.
DEFAULT_CANDIDATES_MIN = [30, 45, 60, 75, 90, 120, 150, 180, 240, 300, 360]

# Default current cutoff (minutes) to compare against in holdout validation.
# RISK["max_position_age_hours"] = 1.25h = 75min at sprint-kickoff time.
DEFAULT_CURRENT_CUTOFF_MIN = 75

# STAR symbols are excluded from refit (see Phase 14).
STAR_SYMBOLS_DEFAULT = {
    "ATOM/USDT:USDT",
    "ARB/USDT:USDT",
    "DOGE/USDT:USDT",
}


# ─────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────


def load_trades(warehouse_path: Path, days: int) -> list[dict]:
    """Pull CLOSED trades from warehouse over the last `days` days.

    Returns a list of dicts with keys: ts_entry, ts_exit, mcp_score,
    realized_pnl, exit_reason, symbol, age_min.
    """
    if not warehouse_path.exists():
        return []
    cutoff_ts = time.time() - days * 86400
    rows: list[dict] = []
    with sqlite3.connect(str(warehouse_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT ts_entry, ts_exit, mcp_score, realized_pnl, "
            "exit_reason, symbol "
            "FROM trades "
            "WHERE status='CLOSED' AND ts_entry >= ? "
            "  AND ts_exit IS NOT NULL "
            "  AND mcp_score IS NOT NULL "
            "  AND realized_pnl IS NOT NULL "
            "ORDER BY ts_entry ASC",
            (cutoff_ts,),
        )
        for r in cur.fetchall():
            ts_entry = float(r["ts_entry"])
            ts_exit = float(r["ts_exit"])
            age_min = (ts_exit - ts_entry) / 60.0
            if age_min < 0:
                continue  # data hygiene
            rows.append(
                {
                    "ts_entry": ts_entry,
                    "ts_exit": ts_exit,
                    "mcp_score": float(r["mcp_score"]),
                    "realized_pnl": float(r["realized_pnl"]),
                    "exit_reason": r["exit_reason"] or "",
                    "symbol": r["symbol"] or "",
                    "age_min": age_min,
                }
            )
    return rows


def split_fit_holdout(
    rows: list[dict], fit_days: int, holdout_days: int
) -> tuple[list[dict], list[dict]]:
    """Split rows into fit (older) and holdout (newer) by ts_entry.

    Uses an absolute time cut: rows with ts_entry < now - holdout_days * 86400
    go into fit; rows in the trailing `holdout_days` go into holdout.
    """
    now = time.time()
    holdout_cut_ts = now - holdout_days * 86400
    fit_cut_ts = now - (fit_days + holdout_days) * 86400

    fit_rows = [r for r in rows if fit_cut_ts <= r["ts_entry"] < holdout_cut_ts]
    holdout_rows = [r for r in rows if r["ts_entry"] >= holdout_cut_ts]
    return fit_rows, holdout_rows


# ─────────────────────────────────────────────────────────────────────
# Replay model
# ─────────────────────────────────────────────────────────────────────


def _replay_pnl_under_cutoff(rows: list[dict], cutoff_min: float) -> float:
    """Sum PnL over `rows` assuming any trade whose age_min > cutoff_min
    would have been closed AT the cutoff for ~zero realized PnL.

    Rationale (spec §4.2): we can't reconstruct mark-to-cutoff prices
    from warehouse alone. The conservative replay assumes early-cut
    trades book PnL=0 (no slip, no gain) — this UNDER-estimates the
    cost of cutoffs that kill winners (which still book +realized at
    their actual exit if kept), and is neutral for losers (cutting a
    loser earlier ≈ similar realized).

    The argmax over candidates under this model picks the cutoff that
    PRESERVES the most realized PnL — winners stay whole, losers may
    miss being cut but the PnL replay doesn't credit early-cut as a
    gain. This biases the fit toward larger cutoffs that keep winners
    alive, which matches the spec's intent (median winner fires at
    119 min).
    """
    total = 0.0
    for r in rows:
        if r["age_min"] <= cutoff_min:
            total += r["realized_pnl"]
        else:
            # Closed at cutoff with no realized PnL (conservative)
            total += 0.0
    return total


# ─────────────────────────────────────────────────────────────────────
# Fit / validate
# ─────────────────────────────────────────────────────────────────────


def fit_cutoff_for_tier(
    rows: list[dict],
    tier_score_lo: float,
    tier_score_hi: float,
    candidate_cutoffs_min: list[float],
    min_sample: int = 20,
) -> tuple[float | None, float | None]:
    """Argmax-search candidate cutoffs for the score band [lo, hi].

    Returns (c_star_min, fit_pnl) on success; (None, None) when the tier
    sample is below `min_sample`.
    """
    tier_rows = [r for r in rows if tier_score_lo <= r["mcp_score"] <= tier_score_hi]
    if len(tier_rows) < min_sample:
        return None, None

    best_c: float | None = None
    best_pnl = float("-inf")
    for c in candidate_cutoffs_min:
        pnl = _replay_pnl_under_cutoff(tier_rows, c)
        if pnl > best_pnl:
            best_pnl = pnl
            best_c = float(c)
    return best_c, best_pnl


def validate_on_holdout(
    holdout_rows: list[dict],
    current_cutoff_min: float,
    proposed_cutoff_min: float,
) -> bool:
    """Strict-improvement check: proposed > current on holdout PnL.

    Returns True ONLY if holdout_sum(proposed) > holdout_sum(current).
    Equal or worse → False (keep current; do not write JSON).
    """
    if not holdout_rows:
        # No holdout data → we cannot validate → REJECT (conservative).
        return False
    current_pnl = _replay_pnl_under_cutoff(holdout_rows, current_cutoff_min)
    proposed_pnl = _replay_pnl_under_cutoff(holdout_rows, proposed_cutoff_min)
    return proposed_pnl > current_pnl


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def _filter_out_stars(rows: list[dict], star_symbols: set) -> list[dict]:
    return [r for r in rows if r["symbol"] not in star_symbols]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--days", type=int, default=60, help="total warehouse window (default 60).")
    p.add_argument("--fit-days", type=int, default=45, help="fit window in days (default 45).")
    p.add_argument(
        "--holdout-days", type=int, default=15, help="holdout window in days (default 15)."
    )
    p.add_argument(
        "--min-sample", type=int, default=20, help="minimum fit rows per tier (default 20)."
    )
    p.add_argument(
        "--current-cutoff-min",
        type=float,
        default=DEFAULT_CURRENT_CUTOFF_MIN,
        help="current cutoff (min) for strict-improvement compare "
        f"(default {DEFAULT_CURRENT_CUTOFF_MIN} = 1.25h).",
    )
    p.add_argument(
        "--warehouse",
        default=str(Path(__file__).resolve().parents[1] / "data" / "warehouse.sqlite"),
        help="path to warehouse.sqlite.",
    )
    p.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "data" / "models" / "age_cutoffs.json"),
        help="output path for age_cutoffs.json.",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="actually write the JSON if validation passes (without this, prints only).",
    )
    args = p.parse_args(argv)

    warehouse = Path(args.warehouse)
    out_path = Path(args.out)

    if args.fit_days + args.holdout_days != args.days:
        # Re-derive: take holdout from the trailing args.holdout_days,
        # fit from the prior args.fit_days. If they sum != days we still
        # honor the explicit fit/holdout days (days is informational).
        pass

    print(f"[refit] warehouse: {warehouse}")
    print(f"[refit] window:    {args.days}d ({args.fit_days}d fit / {args.holdout_days}d holdout)")
    print(f"[refit] current cutoff (min): {args.current_cutoff_min}")

    rows = load_trades(warehouse, args.days)
    if not rows:
        print("[refit] no warehouse data — exiting (no JSON written).")
        return 0

    rows_no_star = _filter_out_stars(rows, STAR_SYMBOLS_DEFAULT)
    print(f"[refit] loaded {len(rows)} closed trades ({len(rows_no_star)} after STAR filter).")

    fit_rows, holdout_rows = split_fit_holdout(
        rows_no_star,
        args.fit_days,
        args.holdout_days,
    )
    print(f"[refit] split: fit={len(fit_rows)}, holdout={len(holdout_rows)}")

    if not fit_rows:
        print("[refit] no fit rows in window — exiting (no JSON written).")
        return 0

    accepted: dict[str, int] = {}
    rejection_log: list[str] = []
    any_tier_had_sample = False

    for tier_name, (lo, hi) in TIERS.items():
        c_star, fit_pnl = fit_cutoff_for_tier(
            fit_rows,
            lo,
            hi,
            DEFAULT_CANDIDATES_MIN,
            min_sample=args.min_sample,
        )
        if c_star is None:
            tier_n = sum(1 for r in fit_rows if lo <= r["mcp_score"] <= hi)
            rejection_log.append(
                f"  {tier_name:<10s} SKIP (tier n={tier_n} < min_sample={args.min_sample})"
            )
            continue

        any_tier_had_sample = True
        ok = validate_on_holdout(
            holdout_rows,
            current_cutoff_min=args.current_cutoff_min,
            proposed_cutoff_min=c_star,
        )
        if ok:
            accepted[tier_name] = int(c_star)
            current_h = _replay_pnl_under_cutoff(holdout_rows, args.current_cutoff_min)
            proposed_h = _replay_pnl_under_cutoff(holdout_rows, c_star)
            print(
                f"  {tier_name:<10s} ACCEPT c*={c_star:.0f}min fit_pnl=${fit_pnl:+.2f} "
                f"holdout: current=${current_h:+.2f} -> proposed=${proposed_h:+.2f}"
            )
        else:
            current_h = (
                _replay_pnl_under_cutoff(holdout_rows, args.current_cutoff_min)
                if holdout_rows
                else 0.0
            )
            proposed_h = _replay_pnl_under_cutoff(holdout_rows, c_star) if holdout_rows else 0.0
            rejection_log.append(
                f"  {tier_name:<10s} REJECT c*={c_star:.0f}min fit_pnl=${fit_pnl:+.2f} "
                f"holdout: current=${current_h:+.2f} >= proposed=${proposed_h:+.2f}"
            )

    if rejection_log:
        print("[refit] rejections:")
        for line in rejection_log:
            print(line)

    if not any_tier_had_sample:
        print("[refit] no tier had enough data — exiting (no JSON written).")
        return 0

    if not accepted:
        print("[refit] no tier passed holdout strict-improvement — no JSON written.")
        return 0

    payload = {
        **accepted,
        "fitted_at": time.time(),
        "fit_sample_size": len(fit_rows),
        "holdout_sample_size": len(holdout_rows),
        "current_cutoff_min": args.current_cutoff_min,
        "candidate_grid_min": DEFAULT_CANDIDATES_MIN,
    }

    if not args.commit:
        print("[refit] dry-run (no --commit). Would write:")
        print(json.dumps(payload, indent=2))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"[refit] WROTE {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
