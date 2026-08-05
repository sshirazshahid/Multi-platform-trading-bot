"""Diagnose open-funnel drought from warehouse + decision log."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WH = ROOT / "data" / "warehouse.sqlite"
LOG = ROOT / "data" / "mcp_decisions.jsonl"


def main() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=8)
    con = sqlite3.connect(str(WH.resolve()))
    con.row_factory = sqlite3.Row
    tabs = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    print("has", sorted(t for t in tabs if any(x in t for x in ("decision", "candidate", "trade"))))

    if "decision_events" in tabs:
        cols = [r[1] for r in con.execute("PRAGMA table_info(decision_events)")]
        print("decision_events cols:", cols)
        rows = con.execute(
            "SELECT * FROM decision_events ORDER BY rowid DESC LIMIT 200"
        ).fetchall()
        reasons: Counter[str] = Counter()
        opens = 0
        for r in rows:
            d = dict(r)
            ts = str(d.get("ts") or d.get("created_at") or "")
            if ts and ts < cutoff.isoformat():
                continue
            reason = str(d.get("reason") or d.get("reject_reason") or "")
            action = str(d.get("action") or d.get("decision") or "")
            codes = d.get("reason_codes")
            if isinstance(codes, str):
                try:
                    codes = json.loads(codes)
                except json.JSONDecodeError:
                    codes = [codes]
            if codes:
                for c in codes:
                    reasons[str(c)] += 1
            elif reason:
                reasons[reason.split()[0][:80]] += 1
            if "open" in action.lower() or "OPEN" in str(d):
                opens += 1
            payload = d.get("payload") or d.get("details")
            if isinstance(payload, str) and payload.startswith("{"):
                try:
                    p = json.loads(payload)
                    rr = p.get("reject_reason") or p.get("reason")
                    if rr:
                        reasons[str(rr)[:100]] += 1
                except json.JSONDecodeError:
                    pass
        print("decision_events recent opens-ish", opens)
        print("top reasons", reasons.most_common(20))

    # JSONL portfolio actions
    actions = 0
    empty = 0
    if LOG.is_file():
        for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(o.get("ts") or "")
            if ts and ts < cutoff.isoformat():
                continue
            if o.get("type") != "portfolio":
                continue
            dec = o.get("decisions") or {}
            acts = dec.get("actions") if isinstance(dec, dict) else None
            if acts == []:
                empty += 1
            elif acts:
                actions += len(acts)
                print("sample action", acts[:2])
        print(f"portfolio empty_cycles={empty} action_items={actions}")

    con.close()


if __name__ == "__main__":
    main()
