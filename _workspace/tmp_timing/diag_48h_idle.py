"""48h zero-trade diagnosis — read-only."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
now = time.time()
cut48 = now - 48 * 3600
cut_iso = datetime.fromtimestamp(cut48, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

hb = json.loads((ROOT / "data" / "heartbeat.json").read_text(encoding="utf-8"))
ts = hb.get("timestamp")
if isinstance(ts, str):
    try:
        age = now - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        age = None
else:
    age = now - float(ts or 0)
print("=== BOT ===")
print(
    f"age_sec={age:.0f}" if age is not None else f"ts={ts}",
    f"halted={hb.get('is_halted')}",
    f"cycles={hb.get('cycle_count')}",
    f"opens={hb.get('open_positions')}",
    f"last_trade={hb.get('last_trade_time')}",
    f"uptime_h={float(hb.get('uptime_seconds') or 0)/3600:.1f}",
)
print("effective_config", hb.get("effective_config"))
print("entry_policy", hb.get("entry_policy"))

db = ROOT / "data" / "warehouse.sqlite"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print("=== WAREHOUSE TABLES (trade/decision) ===")
print(sorted(t for t in tabs if any(x in t.lower() for x in ("trade", "decision", "candidate", "open", "fill"))))

if "decision_events" in tabs:
    cols = [c[1] for c in con.execute("PRAGMA table_info(decision_events)")]
    print("decision_events cols", cols)
    n = con.execute(
        "SELECT COUNT(*) FROM decision_events WHERE ts >= ?", (cut_iso,)
    ).fetchone()[0]
    print("decision_events 48h", n)
    # group by action/outcome-ish columns
    for col in ("action", "outcome", "decision", "event_type", "status"):
        if col in cols:
            rows = con.execute(
                f"SELECT {col} AS k, COUNT(*) c FROM decision_events "
                f"WHERE ts >= ? GROUP BY {col} ORDER BY c DESC LIMIT 20",
                (cut_iso,),
            ).fetchall()
            print(f"by {col}", [(r["k"], r["c"]) for r in rows])
    reason_col = next(
        (c for c in ("reject_reason", "reason", "block_reason", "detail") if c in cols),
        None,
    )
    if reason_col:
        rows = con.execute(
            f"SELECT COALESCE({reason_col}, '') AS r, COUNT(*) c FROM decision_events "
            f"WHERE ts >= ? GROUP BY r ORDER BY c DESC LIMIT 25",
            (cut_iso,),
        ).fetchall()
        print("top reasons", [(str(r["r"])[:90], r["c"]) for r in rows])

# candidates
if "candidates" in tabs:
    cols = [c[1] for c in con.execute("PRAGMA table_info(candidates)")]
    ts_col = next((c for c in ("ts", "created_at", "timestamp") if c in cols), None)
    if ts_col:
        n = con.execute(
            f"SELECT COUNT(*) FROM candidates WHERE {ts_col} >= ?", (cut_iso,)
        ).fetchone()[0]
        print("candidates 48h", n)
        if "verdict" in cols:
            rows = con.execute(
                f"SELECT verdict, COUNT(*) c FROM candidates WHERE {ts_col} >= ? "
                f"GROUP BY verdict ORDER BY c DESC",
                (cut_iso,),
            ).fetchall()
            print("candidate verdicts", [(r["verdict"], r["c"]) for r in rows])

# mcp decisions jsonl
mcp = ROOT / "data" / "mcp_decisions.jsonl"
if mcp.exists():
    allows = skips = opens = 0
    reasons = Counter()
    n = 0
    with mcp.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = row.get("ts") or row.get("timestamp") or 0
            try:
                if isinstance(t, str):
                    t = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
                else:
                    t = float(t)
            except Exception:
                continue
            if t < cut48:
                continue
            n += 1
            v = str(row.get("verdict") or row.get("action") or row.get("decision") or "")
            if "ALLOW" in v.upper():
                allows += 1
            if "SKIP" in v.upper() or "HOLD" in v.upper():
                skips += 1
            if "OPEN" in v.upper():
                opens += 1
            rr = str(row.get("reason") or row.get("skip_reason") or "")[:60]
            if rr:
                reasons[rr] += 1
    print("=== MCP decisions.jsonl 48h ===", n, "ALLOW", allows, "SKIP/HOLD", skips, "OPEN*", opens)
    print("top mcp reasons", reasons.most_common(10))

# closed positions timestamps
pos = json.loads((ROOT / "data" / "positions.json").read_text(encoding="utf-8"))
closed = pos.get("closed") or []
recent = []
for c in closed:
    for k in ("closed_at", "exit_time", "exit_ts", "closed_ts", "timestamp"):
        v = c.get(k)
        if v is None:
            continue
        try:
            if isinstance(v, str) and "T" in v:
                ts_v = datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            else:
                ts_v = float(v)
            if ts_v >= cut48:
                recent.append(c)
            break
        except Exception:
            pass
print("=== CLOSED POS 48h ===", len(recent), "of", len(closed))
if closed:
    last = closed[-1]
    print("last closed keys sample", {k: last.get(k) for k in list(last)[:12]})

con.close()

# F1 freshness post-fix code vs live log
print("=== F1 NOTE ===")
print("live feeds_fresh_true still 0 in gate log => carry process likely NOT restarted after fix")
print("carry_heartbeat ts age", round(now - float(json.loads((ROOT/'data'/'carry_heartbeat.json').read_text())['ts']), 1), "sec")
