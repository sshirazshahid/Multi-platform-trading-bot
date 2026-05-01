"""One-shot backfill: recover stuck OPEN warehouse trade rows from bot logs.

Background — 2026-04-30 audit
-----------------------------
`order_manager._finalize_close` calls `warehouse.trade_id_by_key(...)` to find
the OPEN warehouse row matching the just-closed Position. The lookup did
exact-float equality on `ts_entry`. When the Position's `open_time` differed
from the warehouse `ts_entry` by even a microsecond, the lookup returned None
and the close was silently skipped. The warehouse row stayed at
`status='OPEN'` while the bot's runtime log + email + compliance recorded a
real, completed close.

Net effect: dashboard "Best PnL" / per-strategy stats / WR aggregates that
read from `trades WHERE status='CLOSED'` undercounted real wins (and losses).

This script reconciles the warehouse with the bot logs:

  1. Read every `[Positions] [LIVE] CLOSED ...` line from `logs/bot_*.log`.
  2. For each warehouse row with `status='OPEN'`, find the FIRST log close
     line whose (symbol, side, exchange, timestamp >= ts_entry, time bucket
     within reasonable hold window) matches.
  3. UPDATE the warehouse row to `status='CLOSED'` with the recovered
     realized_pnl, ts_exit, exit_reason, fee, and a synthetic hold_sec.

Idempotent: running twice produces zero changes (rows that were CLOSED on
the first pass are filtered out).

Dry-run by default. Add `--commit` to actually mutate the warehouse.

Usage:
    python scripts/backfill_warehouse_closes.py            # preview (dry run)
    python scripts/backfill_warehouse_closes.py --commit   # apply updates
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
WH_PATH = ROOT / "data" / "warehouse.sqlite"

# Example log line (single-line, after loguru's pipes are stripped):
#   2026-04-30 05:49:23 | INFO | core.position_tracker:close:235 | [Positions] [LIVE] CLOSED DOGE/USDT:USDT | Gross: +5.5798 USDT | Fees: -0.1561 USDT (in=0.0766 out=0.0794) | Net PnL: +5.4238 USDT (+7.08%) | reason=mcp_take_profit
LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"\[Positions\] \[LIVE\] CLOSED (?P<symbol>\S+) \| "
    r"Gross: (?P<gross>[+-][\d.]+) USDT \| "
    r"Fees: -(?P<fees>[\d.]+) USDT.*?"
    r"Net PnL: (?P<net>[+-][\d.]+) USDT.*?"
    r"reason=(?P<reason>\S.*?)$"
)


def _parse_log_ts(s: str) -> float:
    """Local-naive bot.log timestamps are in UTC by convention."""
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _open_log(p: Path):
    """Open .log or .log.zip transparently — returns iterable of decoded lines."""
    if p.suffix == ".zip":
        import io
        import zipfile
        try:
            with zipfile.ZipFile(p) as zf:
                # Each daily zip contains one log file; iterate inner members
                for name in zf.namelist():
                    with zf.open(name) as raw:
                        for ln in io.TextIOWrapper(raw, encoding="utf-8",
                                                   errors="replace"):
                            yield ln
        except (zipfile.BadZipFile, OSError):
            return
    else:
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                yield from f
        except OSError:
            return


def parse_close_events(log_files: list[Path]) -> list[dict]:
    """Yield close-event dicts from every matching log line.

    Reads both .log and .log.zip files. Daily log rotation zips after a day,
    so historical close events for OPEN rows >24h old live in zips.
    """
    events: list[dict] = []
    for p in log_files:
        for ln in _open_log(p):
            if "[LIVE] CLOSED" not in ln:
                continue
            m = LINE_RE.match(ln.rstrip())
            if not m:
                continue
            try:
                events.append({
                    "ts": _parse_log_ts(m.group("ts")),
                    "symbol": m.group("symbol"),
                    "gross_pnl": float(m.group("gross")),
                    "fees": float(m.group("fees")),
                    "realized_pnl": float(m.group("net")),
                    "reason": m.group("reason").strip(),
                    "log_file": str(p.name),
                })
            except (ValueError, AttributeError):
                continue
    events.sort(key=lambda e: e["ts"])
    return events


def find_match(
    open_row: dict, events: list[dict], used: set[int],
    *, max_hold_sec: int = 24 * 3600,
) -> int | None:
    """Return the index into `events` that best matches an OPEN warehouse row.

    Match rules:
      - same symbol
      - event ts >= open_row.ts_entry
      - event ts <= open_row.ts_entry + max_hold_sec  (sanity: 24h ceiling)
      - earliest unused event wins (FIFO match — assumes closes pair with the
        oldest still-open trade in the warehouse for that symbol)
    """
    sym = open_row["symbol"]
    ts_entry = float(open_row["ts_entry"])
    deadline = ts_entry + max_hold_sec
    for i, ev in enumerate(events):
        if i in used:
            continue
        if ev["symbol"] != sym:
            continue
        if ev["ts"] < ts_entry or ev["ts"] > deadline:
            continue
        return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--commit", action="store_true",
        help="Actually UPDATE the warehouse. Default is dry-run (preview).",
    )
    ap.add_argument(
        "--max-hold-hours", type=float, default=24.0,
        help="Max acceptable hold duration to consider a close a match. "
             "Defaults to 24h.",
    )
    args = ap.parse_args()

    # 1. Collect close events from every bot log on disk.
    log_files = sorted(LOG_DIR.glob("bot_*.log")) + sorted(LOG_DIR.glob("bot_*.log.zip"))
    if not log_files:
        print("no bot_*.log files found in logs/", file=sys.stderr)
        return 1
    print(f"scanning {len(log_files)} log file(s) for [LIVE] CLOSED lines ...")
    events = parse_close_events(log_files)
    print(f"  parsed {len(events)} close events")

    # 2. Find OPEN warehouse rows.
    if not WH_PATH.exists():
        print(f"warehouse not found: {WH_PATH}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(WH_PATH)
    conn.row_factory = sqlite3.Row
    open_rows = [
        dict(r) for r in conn.execute(
            "SELECT id, ts_entry, exchange, symbol, side, mode "
            "FROM trades WHERE status='OPEN' ORDER BY ts_entry ASC"
        ).fetchall()
    ]
    print(f"  found {len(open_rows)} OPEN warehouse rows")
    if not open_rows:
        print("nothing to backfill — no OPEN rows in warehouse.")
        return 0

    # 3. Pair events ↔ open rows (FIFO by ts_entry).
    used: set[int] = set()
    matches: list[tuple[dict, dict]] = []
    unmatched: list[dict] = []
    max_hold_sec = int(args.max_hold_hours * 3600)
    for row in open_rows:
        idx = find_match(row, events, used, max_hold_sec=max_hold_sec)
        if idx is None:
            unmatched.append(row)
            continue
        used.add(idx)
        matches.append((row, events[idx]))

    # 4. Report.
    print()
    print(f"  matched: {len(matches)}    unmatched: {len(unmatched)}")
    print()
    if matches:
        print("matched (will UPDATE on --commit):")
        print("  {:>5}  {:<24}  {:<7}  {:<26}  {:>10}  {:<24}".format(
            "id", "symbol", "side", "open_time", "net_pnl", "reason"))
        for row, ev in matches[:50]:
            ot = datetime.fromtimestamp(
                float(row["ts_entry"]), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%SZ")
            print("  {:>5}  {:<24}  {:<7}  {:<26}  {:>+10.4f}  {:<24}".format(
                row["id"], row["symbol"], row["side"], ot,
                ev["realized_pnl"], ev["reason"][:24]))
        if len(matches) > 50:
            print(f"  ... and {len(matches) - 50} more")
    if unmatched:
        print()
        print(f"unmatched OPEN rows ({len(unmatched)}) — leave as-is:")
        for row in unmatched[:20]:
            ot = datetime.fromtimestamp(
                float(row["ts_entry"]), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%SZ")
            print(f"  id={row['id']:>5}  {row['symbol']:<24}  {row['side']:<7}  {ot}")
        if len(unmatched) > 20:
            print(f"  ... and {len(unmatched) - 20} more")

    # 5. Commit (or stop).
    if not args.commit:
        print()
        print("DRY RUN — no changes made. Re-run with --commit to apply.")
        return 0
    if not matches:
        print()
        print("no matches to apply.")
        return 0

    print()
    print(f"applying {len(matches)} UPDATE(s) ...")
    n_applied = 0
    for row, ev in matches:
        ts_exit = float(ev["ts"])
        hold_sec = ts_exit - float(row["ts_entry"])
        try:
            conn.execute(
                "UPDATE trades SET "
                "  ts_exit=?, realized_pnl=?, exit_reason=?, "
                "  fee=COALESCE(fee, ?), hold_sec=?, status='CLOSED' "
                "WHERE id=? AND status='OPEN'",
                (ts_exit, ev["realized_pnl"], ev["reason"],
                 ev["fees"], hold_sec, row["id"]),
            )
            n_applied += 1
        except sqlite3.Error as e:
            print(f"  ERROR id={row['id']}: {e}", file=sys.stderr)
    conn.commit()
    print(f"applied {n_applied} updates. warehouse: {WH_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
