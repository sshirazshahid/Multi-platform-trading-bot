import json
import sqlite3
from collections import Counter

c = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
reasons = Counter()
econ = Counter()
stages = Counter()
outcomes = Counter()
n = 0
for (payload,) in c.execute(
    "SELECT payload_json FROM decision_events WHERE occurred_at >= datetime('now','-7 day')"
):
    try:
        d = json.loads(payload)
    except Exception:
        continue
    n += 1
    ctx = d.get("context") or {}
    reason = str(ctx.get("reason") or (d.get("reason_codes") or ["?"])[0])
    stage = str(ctx.get("terminal_stage") or "")
    outcome = str(d.get("outcome") or ctx.get("terminal_outcome") or "")
    reasons[reason] += 1
    stages[stage] += 1
    outcomes[outcome] += 1
    if reason.startswith("economic_gate"):
        econ[reason] += 1
print("n7d", n)
print("top reasons", reasons.most_common(15))
print("econ", econ.most_common())
print("stages", stages.most_common())
print("outcomes", outcomes.most_common())
