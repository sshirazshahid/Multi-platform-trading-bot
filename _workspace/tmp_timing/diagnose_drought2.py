"""Count OPEN reject reasons from candidates + decision payloads."""
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
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    con = sqlite3.connect(str(WH.resolve()))
    con.row_factory = sqlite3.Row

    # candidates schema
    cols = [r[1] for r in con.execute("PRAGMA table_info(candidates)")]
    print("candidates cols:", cols)

    # recent candidates
    qcols = ", ".join(
        c
        for c in cols
        if c
        in {
            "id",
            "ts",
            "created_at",
            "occurred_at",
            "symbol",
            "exchange",
            "action",
            "decision",
            "status",
            "reject_reason",
            "reason",
            "mcp_score",
            "score",
            "payload_json",
            "meta_json",
            "features_json",
        }
    )
    if not qcols:
        qcols = "*"
    rows = con.execute(
        f"SELECT {qcols} FROM candidates ORDER BY rowid DESC LIMIT 300"
    ).fetchall()
    reasons: Counter[str] = Counter()
    status_c: Counter[str] = Counter()
    n = 0
    for r in rows:
        d = dict(r)
        ts = str(
            d.get("ts")
            or d.get("created_at")
            or d.get("occurred_at")
            or ""
        )
        if ts and ts < cutoff:
            continue
        n += 1
        status_c[str(d.get("status") or d.get("decision") or d.get("action") or "?")] += 1
        rr = d.get("reject_reason") or d.get("reason")
        if rr:
            reasons[str(rr)[:120]] += 1
        for key in ("payload_json", "meta_json", "features_json"):
            raw = d.get(key)
            if not isinstance(raw, str) or not raw.startswith("{"):
                continue
            try:
                p = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for k in ("reject_reason", "reason", "block_reason", "gate"):
                if p.get(k):
                    reasons[f"{k}:{p.get(k)}"[:120]] += 1
            codes = p.get("reason_codes") or p.get("reject_reasons")
            if isinstance(codes, list):
                for c in codes:
                    reasons[str(c)[:120]] += 1
    print(f"candidates_in_window={n}")
    print("status", status_c.most_common(15))
    print("reasons", reasons.most_common(25))

    # decision_events payloads for OPEN
    erows = con.execute(
        "SELECT occurred_at, action, canonical_symbol, strategy_id, payload_json "
        "FROM decision_events ORDER BY rowid DESC LIMIT 500"
    ).fetchall()
    er: Counter[str] = Counter()
    eacts: Counter[str] = Counter()
    samples = []
    for r in erows:
        if str(r["occurred_at"] or "") < cutoff:
            continue
        eacts[str(r["action"])] += 1
        payload = {}
        if r["payload_json"]:
            try:
                payload = json.loads(r["payload_json"])
            except json.JSONDecodeError:
                payload = {}
        rr = (
            payload.get("reject_reason")
            or payload.get("reason")
            or payload.get("block_reason")
            or ""
        )
        if rr:
            er[str(rr)[:120]] += 1
        if r["action"] and "OPEN" in str(r["action"]).upper():
            samples.append(
                {
                    "ts": r["occurred_at"],
                    "sym": r["canonical_symbol"],
                    "strat": r["strategy_id"],
                    "action": r["action"],
                    "rr": rr or payload.get("status"),
                    "keys": list(payload.keys())[:20],
                }
            )
    print("decision_events actions", eacts.most_common(20))
    print("decision payload reasons", er.most_common(20))
    print("open samples", samples[:8])

    # OPEN proposals vs empty in jsonl last 24h
    opens = []
    if LOG.is_file():
        for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "portfolio":
                continue
            ts = str(o.get("ts") or "")
            if ts and ts < cutoff:
                continue
            dec = o.get("decisions") or {}
            for a in dec.get("actions") or []:
                if str(a.get("type", "")).upper() == "OPEN":
                    opens.append(
                        {
                            "ts": ts,
                            "sym": a.get("symbol"),
                            "ex": a.get("exchange"),
                            "score": a.get("mcp_score"),
                            "adx": a.get("adx_4h"),
                            "src": a.get("source"),
                        }
                    )
    print(f"jsonl_OPEN_proposals={len(opens)}")
    for x in opens[-10:]:
        print(" ", x)

    con.close()


if __name__ == "__main__":
    main()
