#!/usr/bin/env python3
"""7-day auto-review for UNIVERSE_FLOW_LOOSEN_V1.

Compares opens / rejects / simple WR in the loosen window vs the prior
equal-length baseline window. Prints KEEP or REVERT.

Usage:
  python scripts/review_universe_flow_loosen.py
  python scripts/review_universe_flow_loosen.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_cohort(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_cohort(path: Path, *, enabled: bool, review_days: float) -> dict:
    existing = _load_cohort(path)
    if existing.get("started_at_utc") and existing.get("enabled") == enabled:
        return existing
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 1,
        "enabled": enabled,
        "started_at_utc": now.isoformat(),
        "review_after_days": review_days,
        "baseline": {
            "max_spread_pct": 0.005,
            "min_depth_usd": 2000,
            "min_range_of_change": 0.02,
            "min_trend_efficiency": 0.20,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _window_stats(
    decisions_path: Path,
    positions_path: Path,
    *,
    start: datetime,
    end: datetime,
) -> dict:
    open_decisions = 0
    universe_rejects = 0
    band_rejects = 0
    econ_rejects = 0
    other_rejects = 0
    if decisions_path.exists():
        with decisions_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                dt = _parse_ts(row.get("ts"))
                if dt is None or dt < start or dt >= end:
                    continue
                typ = row.get("type")
                if typ == "rejection":
                    reason = str(row.get("reason") or "")
                    if reason.startswith("universe_filter"):
                        universe_rejects += 1
                    elif reason.startswith("band_regime_filter"):
                        band_rejects += 1
                    elif reason.startswith("economic_gate"):
                        econ_rejects += 1
                    else:
                        other_rejects += 1
                elif typ == "portfolio":
                    for action in (row.get("decisions") or {}).get("actions") or []:
                        if action.get("type") == "OPEN":
                            open_decisions += 1

    fills = 0
    wins = 0
    losses = 0
    pnl_sum = 0.0
    if positions_path.exists():
        try:
            pos = json.loads(positions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pos = {}
        closed = pos.get("closed") or pos.get("closed_positions") or []
        if isinstance(closed, dict):
            closed = list(closed.values())
        for trade in closed:
            if not isinstance(trade, dict):
                continue
            raw_close = trade.get("close_time") or trade.get("exit_time")
            try:
                close_ts = float(raw_close)
            except (TypeError, ValueError):
                continue
            close_dt = datetime.fromtimestamp(close_ts, tz=timezone.utc)
            if close_dt < start or close_dt >= end:
                continue
            fills += 1
            pnl = trade.get("pnl")
            try:
                pnl_f = float(pnl)
            except (TypeError, ValueError):
                continue
            pnl_sum += pnl_f
            if pnl_f > 0:
                wins += 1
            elif pnl_f < 0:
                losses += 1

    decided = wins + losses
    wr = (wins / decided) if decided else None
    return {
        "open_decisions": open_decisions,
        "universe_rejects": universe_rejects,
        "band_rejects": band_rejects,
        "econ_rejects": econ_rejects,
        "other_rejects": other_rejects,
        "closed_fills": fills,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "net_pnl": pnl_sum,
    }


def decide(baseline: dict, loosen: dict, *, min_fills: int = 8) -> tuple[str, str]:
    """Return (KEEP|REVERT|WAIT, reason). Accuracy-first hybrid bar."""
    fills = int(loosen.get("closed_fills") or 0)
    if fills < min_fills:
        return (
            "WAIT",
            f"only {fills} closed fills in loosen window (need ≥{min_fills}); keep running",
        )

    base_wr = baseline.get("win_rate")
    loose_wr = loosen.get("win_rate")
    if base_wr is not None and loose_wr is not None and loose_wr < (base_wr - 0.05):
        return (
            "REVERT",
            f"WR dropped {loose_wr:.1%} vs baseline {base_wr:.1%} (>5pp) — accuracy first",
        )

    base_pnl = float(baseline.get("net_pnl") or 0.0)
    loose_pnl = float(loosen.get("net_pnl") or 0.0)
    if loose_pnl < base_pnl and loose_pnl < 0:
        return (
            "REVERT",
            f"net PnL worsened ({loose_pnl:.2f} vs baseline {base_pnl:.2f})",
        )

    base_uni = int(baseline.get("universe_rejects") or 0)
    loose_uni = int(loosen.get("universe_rejects") or 0)
    more_fills = fills >= max(1, int(baseline.get("closed_fills") or 0))
    fewer_uni = loose_uni <= base_uni
    if more_fills or fewer_uni:
        return (
            "KEEP",
            "fills/universe-rejects improved without material WR or PnL damage",
        )
    return ("REVERT", "no clear flow benefit — restore baseline thresholds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--ensure-cohort",
        action="store_true",
        help="Write cohort start marker if missing (also done at bot boot).",
    )
    args = parser.parse_args()

    try:
        from config import UNIVERSE_FLOW_LOOSEN
    except Exception as exc:  # pragma: no cover
        print(f"config unavailable: {exc}", file=sys.stderr)
        return 2

    cfg = UNIVERSE_FLOW_LOOSEN or {}
    cohort_path = ROOT / str(cfg.get("cohort_path") or "data/universe_flow_loosen_cohort.json")
    review_days = float(cfg.get("review_after_days") or 7)
    enabled = bool(cfg.get("enabled"))

    if args.ensure_cohort or enabled:
        cohort = _ensure_cohort(cohort_path, enabled=enabled, review_days=review_days)
    else:
        cohort = _load_cohort(cohort_path)

    started = _parse_ts(cohort.get("started_at_utc"))
    now = datetime.now(timezone.utc)
    if started is None:
        report = {
            "ok": False,
            "verdict": "WAIT",
            "reason": "no cohort started_at — enable UNIVERSE_FLOW_LOOSEN_V1 and restart bot",
            "enabled": enabled,
        }
        print(json.dumps(report, indent=2) if args.json else report["reason"])
        return 0

    elapsed = (now - started).total_seconds() / 86400.0
    window = timedelta(days=review_days)
    loosen_end = min(now, started + window)
    baseline_end = started
    baseline_start = started - window

    decisions = ROOT / "data" / "mcp_decisions.jsonl"
    positions = ROOT / "data" / "positions.json"
    baseline = _window_stats(
        decisions, positions, start=baseline_start, end=baseline_end
    )
    loosen = _window_stats(decisions, positions, start=started, end=loosen_end)

    if elapsed + 1e-9 < review_days:
        verdict, reason = (
            "WAIT",
            f"day {elapsed:.1f}/{review_days:g} — review after full window",
        )
    else:
        verdict, reason = decide(baseline, loosen)

    report = {
        "ok": True,
        "verdict": verdict,
        "reason": reason,
        "enabled": enabled,
        "started_at_utc": started.isoformat(),
        "elapsed_days": round(elapsed, 3),
        "review_after_days": review_days,
        "baseline_window": {
            "start": baseline_start.isoformat(),
            "end": baseline_end.isoformat(),
            "stats": baseline,
        },
        "loosen_window": {
            "start": started.isoformat(),
            "end": loosen_end.isoformat(),
            "stats": loosen,
        },
        "action_hint": {
            "KEEP": "leave UNIVERSE_FLOW_LOOSEN_V1=true",
            "REVERT": "set UNIVERSE_FLOW_LOOSEN_V1=false and restart TradingBot-24x7",
            "WAIT": "do nothing yet; re-run this script after day 7",
        }.get(verdict),
    }

    out_path = ROOT / "data" / "universe_flow_loosen_review.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"VERDICT: {verdict}")
        print(f"REASON : {reason}")
        print(f"HINT   : {report['action_hint']}")
        print(f"Wrote  : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
