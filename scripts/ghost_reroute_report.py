"""Patch #0 instrumentation reader.

Reads bot log files in logs/, extracts GHOST_REROUTE_INSTRUMENT log lines,
prints a summary for the Day 4–5 decision gate.

Usage:
    python scripts/ghost_reroute_report.py --since 2026-05-19T12:00:00
    python scripts/ghost_reroute_report.py --since 2026-05-19 --markdown
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Match: "GHOST_REROUTE_INSTRUMENT: symbol=... upnl_pct=0.0234 notional=42.5 sl_alive=True tp_alive=True would_reroute=True reason=ghost_sync"
# 2026-05-19 follow-up: notional emitted by producer (was hardcoded $50
# proxy in this script — caused mean_saved_pnl > 0 gate decision to
# depend on a ~4x guess).
# uPnL may be `nan` (when caller could not fetch a current mark);
# regex matches the literal "nan" alongside signed decimals.
LINE_RE = re.compile(
    r"GHOST_REROUTE_INSTRUMENT: "
    r"symbol=(?P<symbol>\S+) "
    r"side=(?P<side>\S+) "
    r"exchange=(?P<exchange>\S+) "
    r"upnl_pct=(?P<upnl>nan|-?\d+\.\d+) "
    r"notional=(?P<notional>-?\d+\.\d+) "
    r"sl_alive=(?P<sl>\S+) "
    r"tp_alive=(?P<tp>\S+) "
    r"would_reroute=(?P<reroute>True|False) "
    r"reason=(?P<reason>\S+)"
)
# Bot log timestamp prefix like "2026-05-19 14:23:01.234 | INFO ..."
TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_since(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Could not parse --since value: {s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="ISO date or datetime")
    ap.add_argument("--logs", default="logs/", help="logs directory")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    since = parse_since(args.since)
    logs_dir = Path(args.logs)
    if not logs_dir.exists():
        print(f"ERROR: logs dir {logs_dir} does not exist", file=sys.stderr)
        sys.exit(2)

    events = []  # list of dicts
    for log_path in sorted(logs_dir.glob("bot_*.log")):
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ts_m = TS_RE.match(line)
                line_m = LINE_RE.search(line)
                if not ts_m or not line_m:
                    continue
                try:
                    ts = datetime.strptime(ts_m.group("ts"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts < since:
                    continue
                # float("nan") is fine; just won't pass uPnL>0 in the
                # would_reroute gate (already enforced producer-side).
                events.append({
                    "ts": ts,
                    "symbol": line_m.group("symbol"),
                    "side": line_m.group("side"),
                    "exchange": line_m.group("exchange"),
                    "upnl": float(line_m.group("upnl")),
                    "notional": float(line_m.group("notional")),
                    "would_reroute": line_m.group("reroute") == "True",
                    "reason": line_m.group("reason"),
                })

    if not events:
        print("No GHOST_REROUTE_INSTRUMENT events found in window.")
        sys.exit(0)

    total = len(events)
    rerouted = sum(1 for e in events if e["would_reroute"])
    # Expected-saved-pnl per event: uPnL fraction × real position notional
    # (size × entry_price, emitted by the producer). Previous version
    # hardcoded a $50 proxy — since real notionals vary ~5x, the
    # mean_saved_pnl > 0 gate decision was effectively a ~4x guess.
    saved_pnls = [e["upnl"] * e["notional"] for e in events if e["would_reroute"]]
    by_reason = Counter(e["reason"] for e in events if e["would_reroute"])
    by_exchange = Counter(e["exchange"] for e in events if e["would_reroute"])

    span_hours = (max(e["ts"] for e in events) - since).total_seconds() / 3600
    per_24h = rerouted / max(span_hours / 24.0, 1e-9)
    mean_saved = sum(saved_pnls) / max(len(saved_pnls), 1)

    if args.markdown:
        print(f"# Patch #0 instrumentation report (since {since.isoformat()})\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Total ghost-detection events | {total} |")
        print(f"| would_reroute=True events | {rerouted} |")
        print(f"| Window hours observed | {span_hours:.1f} |")
        print(f"| Rate (events/24h) | {per_24h:.2f} |")
        print(f"| Mean expected-saved-pnl | ${mean_saved:.3f} |")
        print(f"| Sum expected-saved-pnl | ${sum(saved_pnls):.2f} |")
        print("\n### Decision gate (Day 4–5)\n")
        gate_pass = (per_24h >= 5.0 and mean_saved > 0)
        print(f"- Rate ≥ 5/24h: {'YES' if per_24h >= 5.0 else 'NO'}")
        print(f"- Mean expected-saved-pnl > $0: {'YES' if mean_saved > 0 else 'NO'}")
        print(f"- **Gate: {'PASS — ship Patch #1' if gate_pass else 'FAIL — deprioritize Patch #1'}**")
        print("\n### Reason breakdown")
        for r, c in by_reason.most_common():
            print(f"- {r}: {c}")
        print("\n### Exchange breakdown")
        for ex, c in by_exchange.most_common():
            print(f"- {ex}: {c}")
    else:
        print(f"=== Patch #0 instrumentation report (since {since.isoformat()}) ===")
        print(f"  total events:    {total}")
        print(f"  would_reroute:   {rerouted}")
        print(f"  window hours:    {span_hours:.1f}")
        print(f"  rate per 24h:    {per_24h:.2f}")
        print(f"  mean saved pnl:  ${mean_saved:.3f}")
        print(f"  sum saved pnl:   ${sum(saved_pnls):.2f}")
        print(f"  gate pass:       {per_24h >= 5.0 and mean_saved > 0}")


if __name__ == "__main__":
    main()
