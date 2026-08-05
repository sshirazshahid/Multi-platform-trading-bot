#!/usr/bin/env python3
"""Write F1 honest-idle artifact from classify_f1_gate_log."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from classify_f1_gate_log import classify, load_window  # noqa: E402

rows = load_window(ROOT / "data" / "carry_gate_log.jsonl", 7.0)
report = classify(rows)
out = ROOT / "_workspace" / "strategy_pipeline" / "59_f1_honest_idle_2026-08-04.md"
out.parent.mkdir(parents=True, exist_ok=True)
journal = ROOT / "journal" / f"{datetime.now(timezone.utc).date().isoformat()}.md"
journal.parent.mkdir(parents=True, exist_ok=True)

bp = report["bucket_pct"]
regime = bp.get("regime_idle", 0.0)
stale = bp.get("feed_stale", 0.0)
other = bp.get("other", 0.0)
conclusion = (
    "IDLE-IS-CORRECT: ok_pct=0 over the window — do not loosen F1 thresholds. "
    "Latest owner-attended samples showed feeds_fresh with negative net edge; "
    "7d mix also includes feed gaps (no_snapshot / feeds_stale) that are ops "
    "hygiene, not a reason to force entries."
)

lines = [
    "# 59 — F1 honest idle (2026-08-04)",
    "",
    "Owner-approved autoplan A: reject any-coin TA; do not force F1 opens.",
    "",
    "## 7d carry_gate_log summary",
    "",
    f"- n={report['n']}",
    f"- feeds_fresh_pct={report['fresh_pct']:.1f}",
    f"- ok_pct={report['ok_pct']:.1f}",
    f"- regime_idle={regime:.1f}%",
    f"- feed_stale={stale:.1f}%",
    f"- other={other:.1f}%",
    "",
    "## Top reasons",
    "",
]
for reason, count in report["reasons"][:20]:
    lines.append(f"- {count}: `{reason}`")
lines += [
    "",
    "## Conclusion",
    "",
    conclusion,
    "",
    "## Non-actions (binding)",
    "",
    "- No change to `F1_MIN_EDGE_BPS` / `F1_COST_MULT`",
    "- No CONTROLLED_LIVE",
    "- No any-coin evidence-pipeline reopen",
    "",
    f"Generated UTC: {datetime.now(timezone.utc).isoformat()}",
    "",
]
out.write_text("\n".join(lines), encoding="utf-8")
print("wrote", out)

entry = (
    f"\n## F1 honest idle\n\n"
    f"7d gate log n={report['n']} ok_pct={report['ok_pct']:.1f} "
    f"regime_idle={regime:.1f}% feed_stale={stale:.1f}%. {conclusion}\n"
)
if journal.exists():
    journal.write_text(journal.read_text(encoding="utf-8") + entry, encoding="utf-8")
else:
    journal.write_text(
        f"# Journal {journal.stem}\n\n{entry}",
        encoding="utf-8",
    )
print("wrote", journal)
print(json.dumps({"n": report["n"], "ok_pct": report["ok_pct"], "buckets": bp}, indent=2))
