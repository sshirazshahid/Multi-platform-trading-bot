#!/usr/bin/env python3
"""One-shot snapshot: open funnel (post-restart window) + F1 edge status.

Writes a JSON line to ``_workspace/strategy_pipeline/50_funnel_watch_2026-07-31.jsonl``
and prints a one-line human summary. Used by the 1h watch loop.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import warehouse_reader as wr  # noqa: E402

OUT = ROOT / "_workspace" / "strategy_pipeline" / "50_funnel_watch_2026-07-31.jsonl"
# Post drought-fix AccBand restart epoch (local 23:08:38 → ~1785521312)
RESTART_CUT_HOURS = max(1.0, (time.time() - 1785521312.0) / 3600.0)


def main() -> int:
    lookback = min(6.0, max(1.0, RESTART_CUT_HOURS + 0.25))
    funnel = wr.open_funnel_status(lookback_hours=lookback)
    try:
        f1 = wr.f1_edge_status(lookback_hours=6.0)
    except wr.WarehouseError as exc:
        f1 = {"status": "error", "error": str(exc)}

    econ_missing = 0
    for row in funnel.get("econ_reasons") or []:
        if "model_missing" in str(row.get("reason") or ""):
            econ_missing += int(row.get("count") or 0)

    snap = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback,
        "funnel": {
            "drought_status": funnel.get("drought_status"),
            "status": funnel.get("status"),
            "open_attempts": funnel.get("open_attempts"),
            "filled": funnel.get("filled"),
            "econ_blocked": funnel.get("econ_blocked"),
            "econ_block_rate": funnel.get("econ_block_rate"),
            "econ_model_missing": econ_missing,
            "other_rejected": funnel.get("other_rejected"),
            "top_reject_families": funnel.get("top_reject_families"),
            "top_reject_reasons": (funnel.get("top_reject_reasons") or [])[:8],
        },
        "f1": {
            "status": f1.get("status"),
            "checks": f1.get("checks") or f1.get("n"),
            "ok": f1.get("ok"),
            "feeds_fresh_rate": f1.get("feeds_fresh_rate"),
            "best": f1.get("best"),
            "reason_families": f1.get("top_reject_families"),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, default=str) + "\n")

    f = snap["funnel"]
    e = snap["f1"]
    print(
        f"FUNNEL drought={f['drought_status']} attempts={f['open_attempts']} "
        f"filled={f['filled']} econ_blocked={f['econ_blocked']} "
        f"model_missing={f['econ_model_missing']} | "
        f"F1 status={e['status']} checks={e['checks']} ok={e['ok']} "
        f"fresh={e.get('feeds_fresh_rate')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
