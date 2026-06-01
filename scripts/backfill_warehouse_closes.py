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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
WH_PATH = ROOT / "data" / "warehouse.sqlite"
POS_JSON_PATH = ROOT / "data" / "positions.json"

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
                        yield from io.TextIOWrapper(raw, encoding="utf-8",
                                                   errors="replace")
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
                    "exchange": None,
                    "side": None,
                    "source": "log",
                })
            except (ValueError, AttributeError):
                continue
    events.sort(key=lambda e: e["ts"])
    return events


def parse_close_events_from_positions_json(path: Path) -> list[dict]:
    """Yield close-event dicts from positions.json's closed list.

    Loguru rotates+deletes daily log files after a few weeks, so the log-based
    parser can't recover stuck OPENs older than that window. positions.json
    keeps the canonical record of every closed position with `open_time` and
    `close_time` floats — a more durable source for backfilling older leaks.
    """
    import json
    events: list[dict] = []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return events
    cl = d.get("closed", [])
    if not isinstance(cl, list):
        return events
    for c in cl:
        if not isinstance(c, dict):
            continue
        ct = c.get("close_time") or c.get("closed_at") or c.get("ts_exit")
        sym = c.get("symbol")
        if ct is None or sym is None:
            continue
        try:
            events.append({
                "ts": float(ct),
                "symbol": str(sym),
                "gross_pnl": float(c.get("gross_pnl") or 0.0),
                "fees": float(c.get("total_fees") or 0.0),
                "realized_pnl": float(c.get("pnl") or 0.0),
                "reason": str(c.get("close_reason") or "reconciled_from_positions_json"),
                "log_file": "positions.json",
                "exchange": str(c.get("exchange") or "").lower() or None,
                "side": (str(c.get("side")).lower() if c.get("side") else None),
                "source": "positions_json",
            })
        except (ValueError, TypeError):
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
      - same exchange + side when the event carries those fields (positions.json
        events do; legacy log-parsed events have None and skip this check)
      - event ts >= open_row.ts_entry
      - event ts <= open_row.ts_entry + max_hold_sec  (sanity: 24h ceiling)
      - earliest unused event wins (FIFO match — assumes closes pair with the
        oldest still-open trade in the warehouse for that symbol)
    """
    sym = open_row["symbol"]
    ts_entry = float(open_row["ts_entry"])
    deadline = ts_entry + max_hold_sec
    row_exchange = (open_row.get("exchange") or "").lower()
    row_side = (open_row.get("side") or "").lower()
    for i, ev in enumerate(events):
        if i in used:
            continue
        if ev["symbol"] != sym:
            continue
        ev_ex = ev.get("exchange")
        if ev_ex is not None and row_exchange and ev_ex != row_exchange:
            continue
        ev_side = ev.get("side")
        if ev_side is not None and row_side and ev_side != row_side:
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
    ap.add_argument(
        "--force-ambiguous", action="store_true",
        help="Also commit matches for symbols with >1 OPEN row. These are "
             "FIFO-by-time pairings that may misassign side/exchange because "
             "the close log line carries neither. Default: skip them.",
    )
    args = ap.parse_args()

    # 1. Collect close events from every bot log on disk AND positions.json.
    # Logs rotate (loguru deletes after retention window); positions.json
    # keeps the full historical closed list, which is essential for
    # backfilling stuck OPENs older than the log retention window.
    log_files = sorted(LOG_DIR.glob("bot_*.log")) + sorted(LOG_DIR.glob("bot_*.log.zip"))
    if log_files:
        print(f"scanning {len(log_files)} log file(s) for [LIVE] CLOSED lines ...")
        events = parse_close_events(log_files)
        print(f"  parsed {len(events)} close events from logs")
    else:
        events = []
        print("no bot_*.log files found in logs/ — relying on positions.json only")

    if POS_JSON_PATH.exists():
        pj_events = parse_close_events_from_positions_json(POS_JSON_PATH)
        print(f"  parsed {len(pj_events)} close events from positions.json")
        events.extend(pj_events)
        events.sort(key=lambda e: e["ts"])
    else:
        print(f"  (positions.json not found at {POS_JSON_PATH})")

    if not events:
        print("no close events parsed from any source.", file=sys.stderr)
        return 1

    # 2. Find OPEN warehouse rows.
    if not WH_PATH.exists():
        print(f"warehouse not found: {WH_PATH}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(WH_PATH)
    conn.row_factory = sqlite3.Row
    # Only backfill rows old enough that they cannot be currently-active.
    # Today's live position would otherwise get matched against a stale
    # event. 12h is comfortably longer than the MCP monitor's hold window
    # and the freqtrade AgeFilter cutoff.
    import time as _time
    min_age_cutoff = _time.time() - 12 * 3600
    open_rows = [
        dict(r) for r in conn.execute(
            "SELECT id, ts_entry, exchange, symbol, side, mode "
            "FROM trades WHERE status='OPEN' AND ts_entry < ? "
            "ORDER BY ts_entry ASC",
            (min_age_cutoff,),
        ).fetchall()
    ]
    print(f"  found {len(open_rows)} OPEN warehouse rows")
    if not open_rows:
        print("nothing to backfill — no OPEN rows in warehouse.")
        return 0

    # 3. Pair events ↔ open rows (FIFO by ts_entry).
    # 2026-05-25 — Bug 4 fix: parsed close events carry NO side/exchange
    # (the [LIVE] CLOSED log line omits them — see LINE_RE), so find_match
    # pairs purely on symbol+time. When 2+ OPEN rows share a symbol within
    # overlapping windows (e.g. a long on Binance + a short on Bybit, or
    # the cross-exchange same-symbol case), FIFO-by-time can attach a close
    # to the WRONG side/exchange row, silently corrupting realized_pnl on
    # --commit. Detect symbols with >1 OPEN row and route their matches to
    # a separate "ambiguous" bucket that is NOT committed unless the
    # operator passes --force-ambiguous.
    from collections import Counter
    _sym_counts = Counter(r["symbol"] for r in open_rows)
    _ambiguous_syms = {s for s, c in _sym_counts.items() if c > 1}

    used: set[int] = set()
    matches: list[tuple[dict, dict]] = []
    ambiguous: list[tuple[dict, dict]] = []
    unmatched: list[dict] = []
    max_hold_sec = int(args.max_hold_hours * 3600)
    for row in open_rows:
        idx = find_match(row, events, used, max_hold_sec=max_hold_sec)
        if idx is None:
            unmatched.append(row)
            continue
        used.add(idx)
        if row["symbol"] in _ambiguous_syms:
            ambiguous.append((row, events[idx]))
        else:
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

    if ambiguous:
        print()
        print(f"AMBIGUOUS matches ({len(ambiguous)}) — symbol has >1 OPEN "
              f"row; close log lacks side/exchange so FIFO pairing may "
              f"misassign. {'WILL commit (--force-ambiguous)' if args.force_ambiguous else 'SKIPPED — re-run with --force-ambiguous to apply'}:")
        for row, ev in ambiguous[:20]:
            ot = datetime.fromtimestamp(
                float(row["ts_entry"]), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%SZ")
            print(f"  id={row['id']:>5}  {row['symbol']:<24}  "
                  f"{row['side']:<7}  {row.get('exchange','?'):<8}  {ot}  "
                  f"net={ev['realized_pnl']:+.4f}")
        if len(ambiguous) > 20:
            print(f"  ... and {len(ambiguous) - 20} more")
        if args.force_ambiguous:
            matches = matches + ambiguous

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
