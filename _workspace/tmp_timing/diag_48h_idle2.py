import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

cut = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
con = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT occurred_at, action, strategy_id, canonical_symbol, venue, payload_json "
    "FROM decision_events WHERE occurred_at >= ? ORDER BY occurred_at DESC LIMIT 5",
    (cut,),
).fetchall()
print("sample recent decision_events:")
for r in rows:
    p = json.loads(r["payload_json"] or "{}")
    print(
        r["occurred_at"],
        r["action"],
        r["strategy_id"],
        r["canonical_symbol"],
        r["venue"],
        "payload_keys",
        sorted(p.keys())[:20],
    )
    interesting = {
        k: p.get(k)
        for k in (
            "reject_reason",
            "reason",
            "status",
            "allowed",
            "filled",
            "block_reason",
            "gate",
            "economic_gate",
            "outcome",
            "error",
        )
        if k in p
    }
    print("  interesting", interesting or {k: p.get(k) for k in list(p)[:8]})

# All payload keys frequency
keys = Counter()
for (pj,) in con.execute(
    "SELECT payload_json FROM decision_events WHERE occurred_at >= ?", (cut,)
):
    try:
        p = json.loads(pj or "{}")
    except json.JSONDecodeError:
        continue
    keys.update(p.keys())
print("payload key freq", keys.most_common(30))

# strategy_id breakdown
print(
    "by strategy",
    [
        (r[0], r[1])
        for r in con.execute(
            "SELECT strategy_id, COUNT(*) FROM decision_events WHERE occurred_at>=? "
            "GROUP BY strategy_id ORDER BY 2 DESC",
            (cut,),
        )
    ],
)

# candidates - why 0?
print(
    "cand total",
    con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
)
print(
    "cand max ts",
    con.execute("SELECT MAX(ts) FROM candidates").fetchone()[0],
)
print(
    "cand decisions 48h-ish",
    [
        (r[0], r[1])
        for r in con.execute(
            "SELECT decision, COUNT(*) FROM candidates WHERE ts >= ? GROUP BY decision ORDER BY 2 DESC",
            (cut,),
        )
    ],
)

# trades
print(
    "trades total",
    con.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
)
print(
    "last trades",
    [
        dict(r)
        for r in con.execute(
            "SELECT id, ts_entry, ts_exit, symbol, status, realized_pnl FROM trades "
            "ORDER BY id DESC LIMIT 5"
        )
    ],
)

# trade_events time col
print(
    "trade_events max",
    con.execute("SELECT MAX(event_ts) FROM trade_events").fetchone()[0],
)
print(
    "trade_events 48h",
    con.execute(
        "SELECT COUNT(*) FROM trade_events WHERE event_ts >= ?", (cut,)
    ).fetchone()[0],
)

con.close()

from mcp_server import warehouse_reader as wr

print("F1", wr.f1_edge_status(lookback_hours=48))
print("FUNNEL", wr.open_funnel_status(lookback_hours=48))
