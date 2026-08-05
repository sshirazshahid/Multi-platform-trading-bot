"""Verify post-restart: scalp off + allow/open progress."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Simulate child env pin
from scripts.launcher_supervisor import _safe_worker_env

env = _safe_worker_env(ROOT)
print("pinned SCALP_MODE_ENABLED=", env.get("SCALP_MODE_ENABLED"))
print("pinned APPROVED=", env.get("APPROVED_PAPER_STRATEGIES"))
print("pinned BAND=", env.get("BAND_REGIME_FILTER_ENABLED"))
print("pinned FLOOR=", env.get("MCP_ENTRY_MIN_SCORE"))

# Force config reload under pinned env
for k, v in env.items():
    if k in (
        "OPERATING_MODE",
        "PAPER_TRADING_PROFILE",
        "SCALP_MODE_ENABLED",
        "MCP_ENTRY_MIN_SCORE",
        "ACCURACY_TARGET_MODE",
        "BAND_REGIME_FILTER_ENABLED",
        "APPROVED_PAPER_STRATEGIES",
        "ENTRY_POLICY",
        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE",
    ):
        os.environ[k] = v
# clear cached config module
for mod in list(sys.modules):
    if mod == "config" or mod.startswith("config."):
        del sys.modules[mod]
import config as cfg

print("config.SCALP_MODE.enabled=", cfg.SCALP_MODE.get("enabled"))
print("config.MCP_ENTRY_MIN_SCORE=", cfg.MCP_ENTRY_MIN_SCORE)
print("config.BAND_REGIME=", cfg.BAND_REGIME_FILTER_ENABLED)
print("config.ACCURACY=", getattr(cfg, "ACCURACY_TARGET_MODE", None))

hb_path = ROOT / "data" / "heartbeat.json"
hb = json.loads(hb_path.read_text(encoding="utf-8"))
print("heartbeat pid", hb.get("pid"), "uptime", hb.get("uptime_seconds"), "cycles", hb.get("cycle_count"))
print("hb effective", hb.get("effective_config"))
print("hb ts", hb.get("timestamp"))

boot = float(hb.get("paper_profile_started_at") or 0)
con = sqlite3.connect(str(ROOT / "data" / "warehouse.sqlite"))
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT decision, skip_reason FROM candidates WHERE ts >= ?", (boot,)
).fetchall()
print(f"post_boot_candidates={len(rows)}")
print("decisions", Counter(r["decision"] for r in rows))
skips = Counter()
for r in rows:
    s = r["skip_reason"] or ""
    if s.startswith("scalp_veto:quiet"):
        skips["scalp_veto:quiet"] += 1
    elif s.startswith("scalp_"):
        skips["scalp_*"] += 1
    elif s.startswith("analysis_only"):
        skips["analysis_only"] += 1
    elif s:
        skips[s.split("(")[0][:50]] += 1
print("skip_families", skips.most_common(15))
allows = [r for r in rows if r["decision"] == "ALLOW"]
print("ALLOW", len(allows))

# funnel snapshot if present
funnel = ROOT / "data" / "promotion_funnel.json"
if funnel.is_file():
    try:
        f = json.loads(funnel.read_text(encoding="utf-8"))
        of = f.get("open_funnel") or f.get("drought") or {}
        print("funnel_keys", list(f.keys())[:12])
    except json.JSONDecodeError:
        pass

# recent OPEN in jsonl after boot
boot_iso = datetime.fromtimestamp(boot, tz=timezone.utc).isoformat() if boot else ""
opens = 0
log = ROOT / "data" / "mcp_decisions.jsonl"
if log.is_file() and boot_iso:
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("type") != "portfolio":
            continue
        if str(o.get("ts") or "") < boot_iso:
            continue
        for a in (o.get("decisions") or {}).get("actions") or []:
            if str(a.get("type", "")).upper() == "OPEN":
                opens += 1
                print("OPEN", o.get("ts"), a.get("symbol"), a.get("mcp_score"), a.get("source"))
print("post_boot_OPEN_actions", opens)
con.close()
