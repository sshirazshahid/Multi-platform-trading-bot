import json
import sqlite3
from datetime import datetime, timezone, timedelta

cut = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
con = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
con.row_factory = sqlite3.Row
ids = []
for r in con.execute(
    "SELECT payload_json FROM decision_events WHERE occurred_at>=?", (cut,)
):
    p = json.loads(r["payload_json"] or "{}")
    ctx = p.get("context") or {}
    if str(ctx.get("reason", "")).startswith("band_regime_filter:adx"):
        cid = ctx.get("candidate_id")
        if cid:
            ids.append(int(cid))
print("adx-band rejects with candidate_id", len(ids))
if ids:
    q = ",".join("?" for _ in ids[:30])
    rows = con.execute(
        f"SELECT id, features_json, decision FROM candidates WHERE id IN ({q})",
        ids[:30],
    ).fetchall()
    for row in rows[:10]:
        feat = json.loads(row["features_json"] or "{}") if row["features_json"] else {}
        adx_vals = {k: feat.get(k) for k in feat if "adx" in k.lower()}
        print("cand", row["id"], "decision", row["decision"], "adx", adx_vals or list(feat)[:8])
con.close()
