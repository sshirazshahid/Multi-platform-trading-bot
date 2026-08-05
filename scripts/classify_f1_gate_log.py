#!/usr/bin/env python3
"""Classify F1 carry_gate_log.jsonl: regime-idle vs feed-stale (ops honesty).

Does not loosen gates. Prints a 7d histogram and bucket shares relative to
the newest timestamp in the file (handles clock skew).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

FEED_STALE_MARKERS = (
    "feeds_stale",
    "feeds_fresh",
    "funding age",
    "funding_age",
    "stale",
    "no_snapshot",
    "missing funding",
    "no funding",
    "quote age",
)
REGIME_IDLE_MARKERS = (
    "perp_mark",
    "spot_mid",
    "spot_spread",
    "perp_spread",
    "net_edge",
    "edge_bps",
    "contango",
    "funding_rate",
    "trailing_funding",
    "avg_funding",
    "time_to_next_funding",
    "depth",
    "liq_buffer",
    "both_legs",
    "lower_bound",
)


def _bucket(reason: str, feeds_fresh) -> str:
    r = (reason or "").lower()
    if feeds_fresh is False or feeds_fresh == 0:
        return "feed_stale"
    for m in FEED_STALE_MARKERS:
        if m in r:
            return "feed_stale"
    for m in REGIME_IDLE_MARKERS:
        if m in r:
            return "regime_idle"
    if "fresh" in r and "false" in r:
        return "feed_stale"
    return "other"


def load_window(path: Path, days: float) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        return rows
    # Relative to newest log timestamp so clock-skewed / future ts still work.
    max_ts = max(float(r.get("ts") or 0) for r in rows)
    cutoff = max_ts - float(days) * 86400
    return [r for r in rows if float(r.get("ts") or 0) >= cutoff]


def classify(rows: list[dict]) -> dict:
    reasons = Counter((r.get("reason") or "?") for r in rows)
    buckets = Counter()
    for r in rows:
        buckets[_bucket(r.get("reason") or "", r.get("feeds_fresh"))] += 1
    n = max(len(rows), 1)
    fresh = sum(1 for r in rows if r.get("feeds_fresh"))
    ok = sum(1 for r in rows if r.get("ok"))
    return {
        "n": len(rows),
        "fresh_pct": 100.0 * fresh / n,
        "ok_pct": 100.0 * ok / n,
        "buckets": dict(buckets),
        "bucket_pct": {k: 100.0 * v / n for k, v in buckets.items()},
        "reasons": reasons.most_common(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--path",
        type=Path,
        default=Path("data/carry_gate_log.jsonl"),
    )
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = load_window(args.path, args.days)
    report = classify(rows)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"window_days={args.days} n={report['n']}")
    print(f"feeds_fresh_pct={report['fresh_pct']:.1f} ok_pct={report['ok_pct']:.1f}")
    print("buckets:")
    for k, v in sorted(report["buckets"].items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v} ({report['bucket_pct'].get(k, 0):.1f}%)")
    print("top_reasons:")
    for reason, count in report["reasons"][:25]:
        print(f"  {count:5d}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
