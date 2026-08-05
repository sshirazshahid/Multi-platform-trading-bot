"""Count skip reasons since boot + any ALLOW/OPEN candidates."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WH = ROOT / "data" / "warehouse.sqlite"
HB = ROOT / "data" / "heartbeat.json"

boot = float(json.loads(HB.read_text(encoding="utf-8"))["paper_profile_started_at"])
con = sqlite3.connect(str(WH.resolve()))
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT decision, skip_reason, symbol, exchange FROM candidates WHERE ts >= ?",
    (boot,),
).fetchall()
print(f"post_boot_candidates={len(rows)}")
dec = Counter(r["decision"] for r in rows)
print("decisions", dec)
skips = Counter((r["skip_reason"] or "")[:80] for r in rows)
print("top_skips:")
for k, v in skips.most_common(25):
    print(f"  {v:5d}  {k}")

# family of skip
fam = Counter()
for r in rows:
    s = r["skip_reason"] or ""
    if s.startswith("scalp_veto:quiet"):
        fam["scalp_veto:quiet"] += 1
    elif s.startswith("scalp_veto:"):
        fam["scalp_veto:other"] += 1
    elif s.startswith("scalp_req"):
        fam["scalp_req_fail"] += 1
    elif s.startswith("analysis_only"):
        fam["analysis_only"] += 1
    elif s:
        fam[s.split("(")[0][:60]] += 1
    else:
        fam["(empty)"] += 1
print("families", fam)

allows = [r for r in rows if r["decision"] == "ALLOW"]
print("ALLOW count", len(allows))
for r in allows[:10]:
    print(" ", r["exchange"], r["symbol"], r["skip_reason"])
con.close()
