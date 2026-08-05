"""Score / skip / funnel deep dive for post-restart drought."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WH = ROOT / "data" / "warehouse.sqlite"
LOG = ROOT / "data" / "mcp_decisions.jsonl"
HB = ROOT / "data" / "heartbeat.json"


def main() -> None:
    hb = json.loads(HB.read_text(encoding="utf-8"))
    boot = float(str(hb.get("paper_profile_started_at") or 0))
    print("boot_epoch", boot, "pid", hb.get("pid"))
    print("effective", hb.get("effective_config"))
    print("entry_policy", hb.get("entry_policy"))

    con = sqlite3.connect(str(WH.resolve()))
    con.row_factory = sqlite3.Row
    # newest candidates regardless of window
    rows = con.execute(
        "SELECT id, ts, symbol, exchange, decision, skip_reason, confidence, "
        "strategy_family, features_json FROM candidates ORDER BY id DESC LIMIT 80"
    ).fetchall()
    print("newest_candidates", len(rows))
    skips: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    for r in rows[:15]:
        print(
            f"  id={r['id']} ts={r['ts']} {r['exchange']}:{r['symbol']} "
            f"dec={r['decision']} skip={r['skip_reason']!r} fam={r['strategy_family']}"
        )
    for r in rows:
        decisions[str(r["decision"])] += 1
        if r["skip_reason"]:
            skips[str(r["skip_reason"])[:100]] += 1
    print("decisions", decisions.most_common())
    print("skips", skips.most_common(20))

    # decision_events with enter_long payloads
    erows = con.execute(
        "SELECT occurred_at, action, canonical_symbol, strategy_id, payload_json "
        "FROM decision_events WHERE action LIKE '%enter%' OR action LIKE '%OPEN%' "
        "OR action LIKE '%open%' ORDER BY rowid DESC LIMIT 30"
    ).fetchall()
    print("enter-like events", len(erows))
    for r in erows[:10]:
        p = {}
        try:
            p = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            pass
        print(
            " ",
            r["occurred_at"],
            r["action"],
            r["canonical_symbol"],
            r["strategy_id"],
            "reject=",
            p.get("reject_reason") or p.get("status") or list(p.keys())[:8],
        )

    # jsonl since boot ISO
    boot_iso = datetime.fromtimestamp(boot, tz=timezone.utc).isoformat() if boot else ""
    print("boot_iso", boot_iso)
    empty = 0
    opens = 0
    scores = []
    if LOG.is_file():
        for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "portfolio":
                continue
            ts = str(o.get("ts") or "")
            if boot_iso and ts < boot_iso:
                continue
            dec = o.get("decisions") or {}
            acts = dec.get("actions") or []
            if not acts:
                empty += 1
            for a in acts:
                if str(a.get("type", "")).upper() == "OPEN":
                    opens += 1
                    scores.append(a.get("mcp_score"))
    print(f"post_boot portfolio empty={empty} OPEN={opens} scores={scores}")

    # open_funnel from warehouse trade_events?
    tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    if "trade_events" in tabs:
        cols = [r[1] for r in con.execute("PRAGMA table_info(trade_events)")]
        print("trade_events cols", cols)
        trows = con.execute(
            "SELECT * FROM trade_events ORDER BY rowid DESC LIMIT 20"
        ).fetchall()
        for r in trows[:10]:
            d = dict(r)
            print(" te", {k: d[k] for k in list(d)[:12]})

    con.close()

    # Live config import as supervisor would see from .env file alone
    import os
    import sys

    sys.path.insert(0, str(ROOT))
    # Don't mutate - just parse .env lines
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    keys = [
        "APPROVED_PAPER_STRATEGIES",
        "MCP_ENTRY_MIN_SCORE",
        "BAND_REGIME_FILTER_ENABLED",
        "PAPER_TRADING_PROFILE",
        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE",
        "ENTRY_POLICY",
    ]
    print("dotenv_file", {k: env.get(k) for k in keys})


if __name__ == "__main__":
    main()
