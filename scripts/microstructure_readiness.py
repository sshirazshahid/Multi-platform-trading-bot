"""How many LABELED candidates carry forward-collected microstructure yet?

Microstructure features (oi_delta_6h, depth_ratio, basis_bps, and populated
ob_imbalance/funding_rate) only populate from 2026-05-25 forward via
mcp_brain._microstructure_features. A retrain on them is worth running once
enough labeled candidates carry a populated `ob_imbalance` (the forward-only
marker). Target >= ~500. When READY, the retrain step appends the pending
keys (scripts/train_models._MICROSTRUCTURE_FEATURES_PENDING) to FEATURE_KEYS
AND retrains in the same change (FEATURE_KEYS + model move together).

Read-only.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = 500


def main() -> int:
    db = ROOT / "data" / "warehouse.sqlite"
    if not db.exists():
        print(f"warehouse not found: {db}", file=sys.stderr)
        return 1
    c = sqlite3.connect(str(db))
    total_micro = c.execute(
        "SELECT COUNT(*) FROM candidates "
        "WHERE features_json LIKE '%\"oi_delta_6h\"%'").fetchone()[0]
    labeled_micro = c.execute(
        "SELECT COUNT(*) FROM candidates cd JOIN labels l "
        "  ON l.candidate_id = cd.id "
        "WHERE cd.features_json LIKE '%\"ob_imbalance\"%' "
        "  AND cd.features_json NOT LIKE '%\"ob_imbalance\": 0.0%'").fetchone()[0]
    c.close()
    print(f"candidates with microstructure captured: {total_micro}")
    print(f"LABELED candidates with populated ob_imbalance: {labeled_micro} / target {TARGET}")
    if labeled_micro >= TARGET:
        print("READY — append _MICROSTRUCTURE_FEATURES_PENDING to FEATURE_KEYS "
              "and retrain (same change). The honest gate decides edge.")
    else:
        print(f"NOT READY — need {TARGET - labeled_micro} more. "
              f"Keep the bot running and labels flowing (build_labels.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
