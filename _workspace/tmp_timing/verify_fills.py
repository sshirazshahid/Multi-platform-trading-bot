"""Check whether OPEN proposals filled or rejected after scalp-off restart."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
hb = json.loads((ROOT / "data" / "heartbeat.json").read_text(encoding="utf-8"))
boot = float(hb.get("paper_profile_started_at") or 0)
boot_iso = datetime.fromtimestamp(boot, tz=timezone.utc).isoformat()
print("boot", boot_iso, "pid", hb.get("pid"), "open_positions", hb.get("open_positions"))

con = sqlite3.connect(str(ROOT / "data" / "warehouse.sqlite"))
con.row_factory = sqlite3.Row

# decision_events after boot
rows = con.execute(
    "SELECT occurred_at, action, canonical_symbol, strategy_id, payload_json "
    "FROM decision_events WHERE occurred_at >= ? ORDER BY occurred_at DESC LIMIT 40",
    (boot_iso.replace("+00:00", "Z"),),
).fetchall()
print("decision_events", len(rows))
reasons = Counter()
for r in rows:
    p = {}
    try:
        p = json.loads(r["payload_json"] or "{}")
    except json.JSONDecodeError:
        pass
    ctx = p.get("context") or {}
    outcome = p.get("outcome") or {}
    rr = (
        ctx.get("reject_reason")
        or outcome.get("reject_reason")
        or p.get("reject_reason")
        or outcome.get("status")
        or ""
    )
    if not rr and isinstance(ctx, dict):
        rr = ctx.get("terminal_reason") or ctx.get("status") or ""
    reasons[str(rr or r["action"])[:100]] += 1
    if len(reasons) <= 12:
        print(
            " ",
            r["occurred_at"],
            r["canonical_symbol"],
            r["action"],
            "rr=",
            rr or list(p.keys())[:6],
        )

print("reason_counts", reasons.most_common(15))

# positions.json
pos = ROOT / "data" / "positions.json"
if pos.is_file():
    pj = json.loads(pos.read_text(encoding="utf-8"))
    open_p = pj.get("open") or pj.get("positions") or []
    if isinstance(open_p, dict):
        open_p = list(open_p.values())
    print("positions_open", len(open_p) if isinstance(open_p, list) else type(open_p))
    if isinstance(open_p, list):
        for p in open_p[:8]:
            if isinstance(p, dict):
                print(" ", p.get("symbol"), p.get("side"), p.get("status"), p.get("entry_px"))

# trade_events after boot epoch
tes = con.execute(
    "SELECT event_type, event_ts, symbol, side FROM trade_events WHERE event_ts >= ? "
    "ORDER BY event_ts DESC LIMIT 20",
    (boot,),
).fetchall()
print("trade_events_post_boot", len(tes))
for t in tes:
    print(" ", t["event_type"], t["symbol"], t["event_ts"])

con.close()

# MC funnel via state helper if available
try:
    from mission_control import state as st

    drought = st.load_open_funnel_status(hours=2) if hasattr(st, "load_open_funnel_status") else None
    if drought:
        print("mc_funnel", {k: drought.get(k) for k in (
            "drought_status", "open_attempts", "filled", "top_reject_reasons", "status"
        ) if k in drought or True})
except Exception as e:
    print("mc_funnel_err", type(e).__name__, e)
