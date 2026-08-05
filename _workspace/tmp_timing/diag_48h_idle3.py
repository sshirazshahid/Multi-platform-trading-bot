import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

cut = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
cut_u = time.time() - 48 * 3600
con = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
con.row_factory = sqlite3.Row

outcomes = Counter()
codes = Counter()
ctx_reasons = Counter()
for r in con.execute(
    "SELECT payload_json FROM decision_events WHERE occurred_at>=?", (cut,)
):
    p = json.loads(r["payload_json"] or "{}")
    outcomes[str(p.get("outcome"))] += 1
    for c in p.get("reason_codes") or []:
        codes[str(c)] += 1
    ctx = p.get("context") if isinstance(p.get("context"), dict) else {}
    for k in ("reason", "reject_reason", "block_reason", "terminal_stage", "terminal_outcome"):
        if ctx.get(k) is not None:
            ctx_reasons[f"{k}={ctx.get(k)}"] += 1

print("outcomes", outcomes.most_common())
print("reason_codes", codes.most_common(40))
print("ctx", ctx_reasons.most_common(40))

r = con.execute(
    "SELECT payload_json FROM decision_events WHERE occurred_at>=? ORDER BY occurred_at DESC LIMIT 1",
    (cut,),
).fetchone()
print("FULL", json.dumps(json.loads(r[0]), indent=2)[:3000])

print("cand 48h", con.execute("SELECT COUNT(*) AS n FROM candidates WHERE ts>=?", (cut_u,)).fetchone()["n"])
print(
    "cand decisions",
    [
        (x[0], x[1])
        for x in con.execute(
            "SELECT decision, COUNT(*) FROM candidates WHERE ts>=? GROUP BY decision ORDER BY 2 DESC LIMIT 20",
            (cut_u,),
        )
    ],
)
sk = Counter()
for r in con.execute(
    "SELECT skip_reason FROM candidates WHERE ts>=? AND UPPER(COALESCE(decision,'')) != 'ALLOW'",
    (cut_u,),
):
    sk[str(r[0] or "")[:100]] += 1
print("skip", sk.most_common(20))
print(
    "ALLOW",
    con.execute(
        "SELECT COUNT(*) AS n FROM candidates WHERE ts>=? AND UPPER(decision)='ALLOW'",
        (cut_u,),
    ).fetchone()["n"],
)
print("last_trade_age_h", round((time.time() - 1785348073.75) / 3600, 2))

import sys
sys.path.insert(0, ".")
from mcp_server import warehouse_reader as wr

print("F1", wr.f1_edge_status(lookback_hours=48))
print("FUNNEL", wr.open_funnel_status(lookback_hours=48))
con.close()
