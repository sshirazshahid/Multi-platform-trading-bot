"""Backfill positions.json closed list from warehouse (sister to backfill_warehouse_closes.py).

Background
----------
`backfill_warehouse_closes.py` recovered orphaned warehouse rows. But the
dashboard's Performance panel + several other panels read closed trades
from `data/positions.json` (the bot's runtime persistence), NOT from the
warehouse. So warehouse-only fixes don't show up until positions.json
also reflects the recovery.

Symptom: even after backfilling the warehouse, dashboard "Best PnL"
still showed the pre-backfill value because positions.json closed
contained only a synthetic `RECONCILED-*` record at +$0.218 (the close
Binance's ledger reported, not the bot's authoritative +$5.4238 from
the live close path).

This script
-----------
For every warehouse row with `status='CLOSED'` and a meaningful
`realized_pnl`, find the corresponding entry in positions.json closed:

  - If a synthetic RECONCILED-* record exists with overlapping
    close_time (±60s, same exchange/symbol/side), REPLACE its PnL
    fields with the warehouse values. The synthetic record came from
    Binance's reconciliation ledger which doesn't always carry
    accurate dollar PnL; the warehouse value (set by the bot's own
    close path or by backfill_warehouse_closes.py) is authoritative.
  - If NO matching record exists, INSERT a new closed entry.

Idempotent: re-running with no new warehouse changes produces zero
edits.

Safety
------
Writes a backup `data/positions.json.bak-<timestamp>` before any edit.
If the bot is currently running (heartbeat fresh < 120s), a clear
warning is printed — the bot's `_save()` may overwrite our edits the
next time it opens or closes a position. In practice this matters most
for trades the bot is unlikely to touch again.

Usage
-----
    python scripts/backfill_positions_json.py            # dry-run preview
    python scripts/backfill_positions_json.py --commit   # apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WH_PATH = ROOT / "data" / "warehouse.sqlite"
POS_PATH = ROOT / "data" / "positions.json"
HB_PATH = ROOT / "data" / "heartbeat.json"

# How close (in seconds) two close_times must be to be considered the
# same trade in different stores. The warehouse record_trade_close ts
# and the synthetic reconciled record can differ by a few seconds; 60s
# is a safe upper bound.
CLOSE_TIME_TOL = 60.0


def fetch_warehouse_closed(conn) -> list[dict]:
    """Yield warehouse rows for CLOSED trades with a realized_pnl."""
    rows = conn.execute(
        "SELECT id, ts_entry, ts_exit, exchange, symbol, side, "
        "       realized_pnl, fee, exit_reason, leverage, "
        "       entry_px, exit_px, size, hold_sec, mode "
        "FROM trades "
        "WHERE status='CLOSED' AND realized_pnl IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def find_pos_match(wh: dict, closed_list: list[dict]) -> int | None:
    """Return the index in closed_list of the position matching warehouse row."""
    sym = wh["symbol"]
    side = wh["side"]
    ex = (wh["exchange"] or "").lower()
    ts_exit = float(wh["ts_exit"] or 0)
    if ts_exit <= 0:
        return None
    for i, p in enumerate(closed_list):
        if (p.get("symbol") or "") != sym:
            continue
        if (p.get("side") or "") != side:
            continue
        if ((p.get("exchange") or "").lower()) != ex:
            continue
        ct = float(p.get("close_time") or 0)
        if ct <= 0:
            continue
        if abs(ct - ts_exit) <= CLOSE_TIME_TOL:
            return i
    return None


def build_synthetic(wh: dict) -> dict:
    """Build a positions.json-shaped dict for a warehouse-only close."""
    sym_id = wh["symbol"].replace("/", "_").replace(":", "_")
    ex = (wh["exchange"] or "").lower()
    return {
        "id": f"WAREHOUSE-{ex}-{sym_id}-{int(wh['ts_exit'] or 0)}",
        "exchange": ex,
        "symbol": wh["symbol"],
        "side": wh["side"],
        "market_type": "futures",
        "strategy": "warehouse_backfill",
        "entry_price": float(wh.get("entry_px") or 0),
        "size": float(wh.get("size") or 0),
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "leverage": int(wh.get("leverage") or 1),
        "open_time": float(wh.get("ts_entry") or 0),
        "close_time": float(wh.get("ts_exit") or 0),
        "exit_price": float(wh.get("exit_px") or 0),
        "entry_fee": 0.0,
        "exit_fee": 0.0,
        "total_fees": float(wh.get("fee") or 0),
        "gross_pnl": float(wh["realized_pnl"]),
        "pnl": float(wh["realized_pnl"]),
        "pnl_pct": None,
        "close_reason": str(wh.get("exit_reason") or "warehouse_backfill"),
        "order_id": None,
        "partial_taken": False,
        "entry_mid": 0.0,
        "exit_mid": 0.0,
        "funding_paid": 0.0,
        "paper_trade": False,
    }


def heartbeat_age_sec() -> float | None:
    if not HB_PATH.exists():
        return None
    try:
        hb = json.loads(HB_PATH.read_text())
        ts = hb.get("timestamp", "")
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="Actually write positions.json. Default is dry-run.")
    args = ap.parse_args()

    if not WH_PATH.exists():
        print(f"warehouse not found: {WH_PATH}", file=sys.stderr)
        return 1
    if not POS_PATH.exists():
        print(f"positions.json not found: {POS_PATH}", file=sys.stderr)
        return 1

    # Heartbeat warning
    age = heartbeat_age_sec()
    if age is not None and age < 120:
        print(f"WARNING: heartbeat is fresh ({age:.0f}s old) — bot is running.")
        print("         Edits to positions.json may be overwritten on the bot's")
        print("         next position open/close. Consider stopping the bot first.")
        print()

    # Load warehouse + positions
    conn = sqlite3.connect(WH_PATH)
    conn.row_factory = sqlite3.Row
    wh_closed = fetch_warehouse_closed(conn)
    pos = json.loads(POS_PATH.read_text())
    closed_list = pos.get("closed", [])

    print(f"warehouse closed: {len(wh_closed)}")
    print(f"positions.json closed: {len(closed_list)}")
    print()

    # Walk warehouse rows, find matches in positions.json.
    #
    # 2026-04-30: only act when positions.json has a SYNTHETIC reconciled
    # record (or no record at all). For "real" closes — close_reason like
    # "mcp_take_profit", "trailing_stop", "stop_loss", "ghost_sync",
    # "AGE_LIMIT", etc. — the positions.json values are authoritative
    # (computed by the bot's own close path with proper net-of-fees PnL).
    # We DON'T overwrite those because the warehouse `realized_pnl` field
    # is sometimes stored under different accounting (gross-only or
    # margin-based) for older trades, so blind replacement would corrupt
    # the closed-list aggregate stats. The recovery target is purely
    # the synthetic reconciled records that under-reported real PnL.
    SYNTHETIC_REASONS = {
        "reconciled_no_context",
        "reconciled_from_exchange",
        "ghost_force_close",
    }

    n_replaced = 0
    n_inserted = 0
    actions: list[tuple[str, dict, dict | None]] = []

    for wh_row in wh_closed:
        idx = find_pos_match(wh_row, closed_list)
        if idx is None:
            actions.append(("INSERT", wh_row, None))
            continue
        existing = closed_list[idx]
        existing_pnl = float(existing.get("pnl") or 0)
        wh_pnl = float(wh_row["realized_pnl"])
        # Idempotent: skip when values already match.
        if abs(existing_pnl - wh_pnl) < 0.001:
            continue
        # Don't overwrite REAL closed records — they're the canonical
        # post-fee net-PnL source. Only patch synthetic reconciled records.
        existing_reason = (existing.get("close_reason") or
                           existing.get("exit_reason") or "")
        existing_id = str(existing.get("id") or "")
        is_synthetic = (
            existing_reason in SYNTHETIC_REASONS
            or existing_id.startswith("RECONCILED-")
            or existing_id.startswith("MANUAL-")
        )
        if not is_synthetic:
            # Real record with mismatched PnL — log but don't touch.
            continue
        actions.append(("REPLACE", wh_row, existing))

    if not actions:
        print("nothing to backfill — positions.json already matches warehouse.")
        return 0

    print(f"actions ({len(actions)}):")
    print("  {:<8}  {:<8}  {:<24}  {:<7}  {:>10}  {:>10}".format(
        "action", "id", "symbol", "side", "old_pnl", "new_pnl"))
    for kind, wh_row, existing in actions[:30]:
        old = float(existing.get("pnl") or 0) if existing else 0.0
        new = float(wh_row["realized_pnl"])
        old_s = "{:+.4f}".format(old) if existing else "(new)"
        print("  {:<8}  {:<8}  {:<24}  {:<7}  {:>10}  {:>+10.4f}".format(
            kind, str(wh_row["id"]), wh_row["symbol"][:24],
            wh_row["side"], old_s, new))
    if len(actions) > 30:
        print(f"  ... and {len(actions) - 30} more")

    if not args.commit:
        print()
        print("DRY RUN — no changes made. Re-run with --commit to apply.")
        return 0

    # Backup
    bk = POS_PATH.with_suffix(f".json.bak-{int(time.time())}")
    bk.write_bytes(POS_PATH.read_bytes())
    print()
    print(f"backup: {bk}")

    # Apply
    for kind, wh_row, _existing in actions:
        if kind == "REPLACE":
            idx = find_pos_match(wh_row, closed_list)
            if idx is None:
                continue
            closed_list[idx]["pnl"] = float(wh_row["realized_pnl"])
            closed_list[idx]["gross_pnl"] = float(wh_row["realized_pnl"])
            closed_list[idx]["close_reason"] = str(
                wh_row.get("exit_reason") or closed_list[idx].get("close_reason") or "")
            closed_list[idx]["total_fees"] = float(
                wh_row.get("fee") or closed_list[idx].get("total_fees") or 0)
            # Patch entry context if warehouse has it and positions.json doesn't.
            if not closed_list[idx].get("entry_price") and wh_row.get("entry_px"):
                closed_list[idx]["entry_price"] = float(wh_row["entry_px"])
            if not closed_list[idx].get("size") and wh_row.get("size"):
                closed_list[idx]["size"] = float(wh_row["size"])
            if not closed_list[idx].get("exit_price") and wh_row.get("exit_px"):
                closed_list[idx]["exit_price"] = float(wh_row["exit_px"])
            n_replaced += 1
        else:
            closed_list.append(build_synthetic(wh_row))
            n_inserted += 1

    pos["closed"] = closed_list
    POS_PATH.write_text(json.dumps(pos, indent=2))
    print(f"applied: {n_replaced} replaced, {n_inserted} inserted")
    print(f"wrote: {POS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
