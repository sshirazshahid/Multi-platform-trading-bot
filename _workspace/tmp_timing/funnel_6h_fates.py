import sqlite3
import time
import json

c = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
since = time.time() - 6 * 3600
q = """
SELECT CASE
 WHEN skip_reason LIKE 'scalp_veto:quiet%' THEN 'scalp_veto:quiet'
 WHEN skip_reason LIKE 'scalp_veto:ranging%' THEN 'scalp_veto:ranging'
 WHEN skip_reason LIKE 'scalp_veto:%' THEN 'scalp_veto:other'
 WHEN skip_reason LIKE 'scalp_req_fail%' THEN 'scalp_req_fail'
 WHEN skip_reason LIKE 'vwap_near=%' THEN 'bare_indicator_string'
 WHEN skip_reason IS NULL OR skip_reason='' THEN '(empty)'
 ELSE substr(skip_reason,1,40)
END fam, COUNT(1) n
FROM candidates WHERE ts>? AND decision='SKIP'
GROUP BY 1 ORDER BY n DESC LIMIT 20
"""
print("6h families:")
for r in c.execute(q, (since,)):
    print(r)

print("ALLOW", c.execute(
    "SELECT COUNT(1) FROM candidates WHERE ts>? AND decision='ALLOW'", (since,)
).fetchone())

# fate of allows via decision_events candidate_id
allows = list(c.execute(
    "SELECT id, symbol, confidence, skip_reason FROM candidates WHERE ts>? AND decision='ALLOW' ORDER BY ts DESC",
    (since,),
))
print("n_allow", len(allows))
print("sample allows", allows[:5])

# join decision_events
fates = []
for cid, sym, conf, adv in allows:
    row = c.execute(
        "SELECT payload_json FROM decision_events WHERE payload_json LIKE ? LIMIT 1",
        (f'%"candidate_id": {cid}%',),
    ).fetchone()
    if not row:
        row = c.execute(
            "SELECT payload_json FROM decision_events WHERE payload_json LIKE ? LIMIT 1",
            (f'%"candidate_id":{cid}%',),
        ).fetchone()
    if row:
        d = json.loads(row[0])
        ctx = d.get("context") or {}
        fates.append((cid, sym, ctx.get("reason"), d.get("outcome")))
    else:
        fates.append((cid, sym, None, "NO_EVENT"))
from collections import Counter
print("fates", Counter((f[3], (f[2] or "")[:40]) for f in fates))
for f in fates[:15]:
    print(f)
