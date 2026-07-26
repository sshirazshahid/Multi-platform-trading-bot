"""One-shot status dump for owner audit (PowerShell-safe)."""
import json
import sqlite3
from pathlib import Path

hb_path = Path("data/heartbeat.json")
hb = json.loads(hb_path.read_text(encoding="utf-8")) if hb_path.exists() else {}
print("=== HEARTBEAT ===")
for k in (
    "operating_mode",
    "paper_trading_profile",
    "entry_policy",
    "signal_source",
    "is_halted",
    "halt_reason",
    "open_positions",
    "daily_pnl",
    "pid",
    "cycle_count",
    "last_successful_cycle",
):
    print(f"  {k}: {hb.get(k)}")
print("latch:", Path("data/risk_incident_latch.json").exists())

wh = Path("data/warehouse.sqlite")
if not wh.exists():
    print("no warehouse")
    raise SystemExit(0)

con = sqlite3.connect(f"file:{wh.as_posix()}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cols = [r[1] for r in con.execute("pragma table_info(trades)").fetchall()]
print("=== TRADES COLS ===", cols[:40])

# Flexible recent PnL
pnl_col = "net_pnl" if "net_pnl" in cols else ("pnl" if "pnl" in cols else None)
status_col = "status" if "status" in cols else None
ts_col = None
for c in ("closed_at", "exit_time", "closed_ts", "updated_at"):
    if c in cols:
        ts_col = c
        break

if pnl_col and status_col:
    q = f"SELECT COUNT(*) n, SUM(CASE WHEN {pnl_col}>0 THEN 1 ELSE 0 END) wins, SUM({pnl_col}) pnl, AVG({pnl_col}) avg_pnl FROM trades WHERE lower(COALESCE({status_col},'')) IN ('closed','filled','done')"
    try:
        row = con.execute(q).fetchone()
        print("=== ALL CLOSED ===", dict(row) if row else None)
    except Exception as e:
        print("all_closed_err", e)

# last 30 closed
order = f"ORDER BY {ts_col} DESC" if ts_col else ""
try:
    rows = con.execute(
        f"SELECT symbol, side, {pnl_col} AS pnl, strategy, {ts_col or 'rowid'} AS ts FROM trades WHERE lower(COALESCE({status_col},'')) IN ('closed','filled','done') {order} LIMIT 30"
    ).fetchall()
    print("=== LAST 30 CLOSED ===")
    for r in rows:
        print(dict(r))
except Exception as e:
    print("last30_err", e)

# candidates reject reasons sample if table exists
tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("=== TABLES sample ===", tabs[:40])
if "candidates" in tabs:
    try:
        rr = con.execute(
            "SELECT reject_reason, COUNT(*) c FROM candidates WHERE reject_reason IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 15"
        ).fetchall()
        print("=== TOP REJECT REASONS ===")
        for r in rr:
            print(dict(r))
    except Exception as e:
        print("reject_err", e)

fun = Path("data/promotion_funnel.json")
if fun.exists():
    f = json.loads(fun.read_text(encoding="utf-8"))
    print("=== FUNNEL ===", f.get("generated_utc"))
    for lane in f.get("lanes", []):
        print(
            lane.get("lane"),
            lane.get("state"),
            lane.get("floor_progress"),
            "wr",
            lane.get("wr"),
        )

con.close()
