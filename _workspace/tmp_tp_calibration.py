"""TP/MFE calibration report from warehouse closed trades (research-only).

Source for the numbers in 23_owner_audit_2026-07-23.md §2.
Run: python _workspace/tmp_tp_calibration.py
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.sqlite"

con = sqlite3.connect(str(DB))
cur = con.cursor()
rows = cur.execute(
    "SELECT mfe, mae, exit_reason, realized_pnl, hold_sec, strategy_family, ts_exit "
    "FROM trades WHERE ts_exit IS NOT NULL AND realized_pnl IS NOT NULL "
    "ORDER BY ts_exit DESC LIMIT 2000"
).fetchall()
con.close()
print("closed rows pulled:", len(rows))

have = [r for r in rows if r[0] is not None]
print("rows with MFE:", len(have))
if not have:
    print(json.dumps({"verdict": "NO_MFE_DATA"}))
    raise SystemExit(0)

mfe = np.array([float(r[0]) for r in have])
mae = np.array([float(r[1]) for r in have if r[1] is not None])

# Detect units: if median abs < 0.2 assume fraction, convert to pct
scale = 100.0 if np.median(np.abs(mfe[mfe != 0])) < 0.2 else 1.0
mfe_pct = mfe * scale
mae_pct = mae * scale

out = {
    "n": len(mfe_pct),
    "unit_scale_applied": scale,
    "mfe_pctiles_pct": {p: round(float(np.percentile(mfe_pct, p)), 3) for p in (10, 25, 50, 75, 90)},
    "mae_pctiles_pct": {p: round(float(np.percentile(mae_pct, p)), 3) for p in (10, 25, 50, 75, 90)} if len(mae_pct) else None,
}
for tp in (0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0):
    out[f"tp_hit_rate_at_{tp}pct"] = round(float(np.mean(mfe_pct >= tp)), 3)

out["exit_reasons_recent2000"] = dict(Counter(r[2] for r in rows).most_common(12))

holds = np.array([float(r[4]) / 3600.0 for r in rows if r[4]])
if len(holds):
    out["hold_hours_pctiles"] = {p: round(float(np.percentile(holds, p)), 2) for p in (25, 50, 75, 90)}

pnl = np.array([float(r[3]) for r in rows])
out["recent2000_wr"] = round(float(np.mean(pnl > 0)), 3)
out["recent2000_pnl_usd"] = round(float(pnl.sum()), 2)
out["families_recent2000"] = dict(Counter(r[5] for r in rows).most_common(8))

print(json.dumps(out, indent=2))
