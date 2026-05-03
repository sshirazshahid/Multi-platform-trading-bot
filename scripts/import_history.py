"""
scripts/import_history.py — Back-populate the warehouse from existing artifacts.

Sources (in order of priority):
  1. data/positions.json    — open + closed positions the bot has tracked.
  2. data/mcp_decisions.jsonl — per-cycle MCP decisions (feature snapshots).
  3. exchange.fetch_my_trades() — optional, behind --pull-exchange flag.

All inserts are idempotent:
  * trades: UNIQUE INDEX on (exchange, symbol, ts_entry, side) -> re-runs no-op.
  * candidates: append-only, but we tag each import row with a synthetic
    `strategy_family='imported:positions_json'` + the source row id so we can
    de-duplicate later if needed.

Usage:
    python scripts/import_history.py                 # default: positions.json only
    python scripts/import_history.py --mcp           # also import candidate history
    python scripts/import_history.py --pull-exchange # also pull 90d of fills

Spec reference: §4 Historical Data Warehouse. Designed for one-shot bootstrap.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Repo root so `core.warehouse` imports work when invoked from `scripts/`.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load warehouse module directly — `core/__init__.py` eagerly imports the
# full trading stack (schedule, ccxt, …) which aren't needed for a pure
# back-population script and may not be installed on analysis machines.
import importlib.util as _il  # noqa: E402

_spec = _il.spec_from_file_location("warehouse", ROOT / "core" / "warehouse.py")
_mod = _il.module_from_spec(_spec)
sys.modules["warehouse"] = _mod
_spec.loader.exec_module(_mod)
Warehouse = _mod.Warehouse


POSITIONS_JSON = ROOT / "data" / "positions.json"
MCP_DECISIONS  = ROOT / "data" / "mcp_decisions.jsonl"


def _ex(name: str) -> str:
    """Normalize exchange name to the warehouse's lowercase convention."""
    return (name or "").lower().strip()


def import_positions(wh: Warehouse, path: Path = POSITIONS_JSON) -> dict:
    """Load positions.json and mirror both OPEN and CLOSED trades into the warehouse.

    The warehouse schema links trades to candidates via candidate_id, but
    historical positions.json entries don't have a candidate reference — we
    synthesize one by inserting a backfill candidate row per trade so the
    link column is never null for imported data.
    """
    stats = {"open": 0, "closed": 0, "candidates": 0, "skipped": 0}
    if not path.exists():
        print(f"[import] {path} missing — skipping")
        return stats

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = list(data.get("open", [])) + list(data.get("closed", []))
    print(f"[import] {path.name}: {len(rows)} rows")

    for row in rows:
        try:
            ts_entry = float(row.get("open_time") or 0)
            ts_exit  = row.get("close_time")
            if not ts_entry:
                stats["skipped"] += 1
                continue

            ex_norm  = _ex(row.get("exchange", "unknown"))
            sym_norm = row.get("symbol", "")
            side_norm = row.get("side", "")

            # Dedup: if a backfill candidate already exists for this exact
            # (ts, exchange, symbol, side), reuse it instead of inserting a
            # duplicate. Keeps re-runs idempotent on the candidates table.
            existing = wh.query(
                "SELECT id FROM candidates WHERE ts=? AND exchange=? AND symbol=? "
                "AND side=? AND strategy_family LIKE 'imported%' LIMIT 1",
                (ts_entry, ex_norm, sym_norm, side_norm),
            )
            if existing:
                cand_id = int(existing[0]["id"])
            else:
                # 1) Synthesize a backfill candidate so imported trades link
                #    back to *something* in the candidates table. This makes
                #    downstream meta-filter reports consistent.
                cand_id = wh.record_candidate(
                    ts=ts_entry,
                    exchange=ex_norm,
                    symbol=sym_norm,
                    market_type=row.get("market_type", "futures"),
                    side=side_norm,
                    strategy_family="imported:" + (row.get("strategy") or "positions_json"),
                    entry_px=row.get("entry_price"),
                    stop_px=row.get("stop_loss"),
                    target_px=row.get("take_profit"),
                    leverage=row.get("leverage"),
                    size_pct=None,
                    confidence=None,
                    decision="TAKEN",
                    skip_reason=None,
                    features={"imported": True, "source_id": row.get("id")},
                )
                if cand_id > 0:
                    stats["candidates"] += 1

            # 2) Record the trade open. Idempotent on (ex, sym, ts_entry, side).
            trade_id = wh.record_trade_open(
                exchange=_ex(row.get("exchange", "unknown")),
                symbol=row.get("symbol", ""),
                side=row.get("side", ""),
                ts_entry=ts_entry,
                entry_px=float(row.get("entry_price") or 0),
                size=float(row.get("size") or 0),
                leverage=int(row.get("leverage") or 1),
                candidate_id=cand_id if cand_id > 0 else None,
                market_type=row.get("market_type", "futures"),
                strategy_family=row.get("strategy", "imported"),
                fee=float(row.get("entry_fee") or 0.0),
                slippage=0.0,
                mode="PAPER" if row.get("paper_trade") else "CONTROLLED_LIVE",
            )
            if trade_id <= 0:
                stats["skipped"] += 1
                continue

            if ts_exit:
                # 3) If closed, fill in exit fields + r_multiple.
                exit_px = float(row.get("exit_price") or 0)
                entry_px = float(row.get("entry_price") or 0)
                sl = float(row.get("stop_loss") or 0)
                r_mult = None
                if sl > 0 and entry_px > 0 and exit_px > 0 and sl != entry_px:
                    if row.get("side") == "buy":
                        denom = entry_px - sl
                        if denom > 0:
                            r_mult = (exit_px - entry_px) / denom
                    else:
                        denom = sl - entry_px
                        if denom > 0:
                            r_mult = (entry_px - exit_px) / denom
                wh.record_trade_close(
                    trade_id=trade_id,
                    ts_exit=float(ts_exit),
                    exit_px=exit_px,
                    realized_pnl=float(row.get("pnl") or 0.0),
                    r_multiple=round(r_mult, 3) if r_mult is not None else None,
                    hold_sec=float(ts_exit) - ts_entry,
                    exit_reason=row.get("close_reason") or "",
                    fee=float(row.get("total_fees") or 0.0),
                )
                stats["closed"] += 1
            else:
                stats["open"] += 1

        except Exception as e:
            print(f"[import] row {row.get('id', '?')} failed: {e}")
            stats["skipped"] += 1

    return stats


def import_mcp_decisions(wh: Warehouse, path: Path = MCP_DECISIONS,
                         max_rows: int | None = None) -> dict:
    """Import every MCP decision row as a warehouse candidate.

    mcp_decisions.jsonl is one JSON object per line. Schema is fluid across
    rebuilds, so this function is intentionally lenient — it pulls the fields
    it can identify and dumps the rest into features_json for later analysis.
    """
    stats = {"candidates": 0, "skipped": 0}
    if not path.exists():
        print(f"[import] {path} missing — skipping")
        return stats

    count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            count += 1
            if max_rows and count > max_rows:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["skipped"] += 1
                continue

            for action in row.get("actions", []) or []:
                if not isinstance(action, dict):
                    continue
                try:
                    decision = action.get("type", "").upper() or "SKIP"
                    # Map action.type to warehouse decision vocabulary
                    if decision == "OPEN":
                        decision = "TAKEN"
                    elif decision == "HOLD":
                        decision = "SKIP"
                    cid = wh.record_candidate(
                        ts=float(row.get("timestamp") or row.get("ts") or time.time()),
                        exchange=_ex(action.get("exchange", "unknown")),
                        symbol=action.get("symbol", ""),
                        market_type=action.get("market_type", "futures"),
                        side=action.get("side"),
                        strategy_family="imported:mcp_decisions",
                        entry_px=action.get("entry_price"),
                        stop_px=action.get("stop_loss"),
                        target_px=action.get("take_profit"),
                        leverage=action.get("leverage"),
                        size_pct=action.get("size_pct"),
                        confidence=action.get("confidence"),
                        decision=decision,
                        skip_reason=action.get("reason") if decision == "SKIP" else None,
                        features={"action_raw": action,
                                  "cycle_ts": row.get("timestamp")},
                    )
                    if cid > 0:
                        stats["candidates"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception:
                    stats["skipped"] += 1
    return stats


def pull_exchange_fills(wh: Warehouse, days: int = 90) -> dict:
    """Optional: pull the last N days of fills from each connected exchange.

    Not implemented in v1 — requires threading exchange credentials through,
    and the positions.json + mcp_decisions.jsonl cover the historical trade
    set well enough for the initial bootstrap.
    """
    print("[import] --pull-exchange not yet implemented in v1 import_history.py")
    return {"pulled": 0}


def main() -> int:
    ap = argparse.ArgumentParser(description="Back-populate the warehouse from local artifacts.")
    ap.add_argument("--mcp", action="store_true",
                    help="Also import candidates from data/mcp_decisions.jsonl")
    ap.add_argument("--pull-exchange", action="store_true",
                    help="(stub) Pull fills from connected exchanges (not yet implemented)")
    ap.add_argument("--mcp-max-rows", type=int, default=None,
                    help="Cap the number of mcp_decisions rows to import (debug).")
    args = ap.parse_args()

    wh = Warehouse()
    before = wh.counts()
    print(f"[import] warehouse before: {before}")

    total = {"open": 0, "closed": 0, "candidates": 0, "skipped": 0}

    pos_stats = import_positions(wh)
    for k, v in pos_stats.items():
        total[k] = total.get(k, 0) + v
    print(f"[import] positions.json -> {pos_stats}")

    if args.mcp:
        mcp_stats = import_mcp_decisions(wh, max_rows=args.mcp_max_rows)
        for k, v in mcp_stats.items():
            total[k] = total.get(k, 0) + v
        print(f"[import] mcp_decisions.jsonl -> {mcp_stats}")

    if args.pull_exchange:
        pull_exchange_fills(wh)

    after = wh.counts()
    print(f"[import] warehouse after:  {after}")
    print(f"[import] delta: candidates +{after['candidates'] - before['candidates']} "
          f"| trades +{after['trades'] - before['trades']}")
    print(f"[import] totals: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
