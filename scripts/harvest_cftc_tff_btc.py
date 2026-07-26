"""Harvest CFTC TFF Bitcoin rows (futures-only + futures+options) for C1 screen.

Persists raw JSON artifacts under data/cftc_cot/ (gitignored via /data/) and
writes a manifest with SHA-256 hashes. Never commits raw dumps.

Usage:
  python scripts/harvest_cftc_tff_btc.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "cftc_cot"

# CFTC PRE Socrata datasets (verified 2026-07-23)
FUT_ONLY = "gpe5-46if"
COMBINED = "yw9f-hn96"
BASE = "https://publicreporting.cftc.gov/resource"

# Standard CME BTC only (paper uses this; micro is optional diagnostic)
CME_BTC = "BITCOIN - CHICAGO MERCANTILE EXCHANGE"
CME_MBT = "MICRO BITCOIN - CHICAGO MERCANTILE EXCHANGE"


def _fetch(dataset: str, markets: list[str]) -> list[dict]:
    in_list = ",".join(f"'{m}'" for m in markets)
    url = f"{BASE}/{dataset}.json"
    params = {
        "$where": f"market_and_exchange_names in({in_list})",
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "50000",
    }
    headers = {"User-Agent": "TradingBot-C1-harvest/1.0 (research; local only)"}
    r = requests.get(url, params=params, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected PRE payload type: {type(data)}")
    return data


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    markets = [CME_BTC, CME_MBT]
    written = []
    for label, dataset in (("fut_only", FUT_ONLY), ("combined", COMBINED)):
        rows = _fetch(dataset, markets)
        path = OUT_DIR / f"tff_{label}_btc_raw.json"
        blob = json.dumps(rows, indent=2, sort_keys=True).encode("utf-8")
        path.write_bytes(blob)
        written.append(
            {
                "label": label,
                "dataset_id": dataset,
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "n_rows": len(rows),
                "sha256": _sha256_bytes(blob),
                "markets": markets,
            }
        )
        print(f"[harvest] {label}: n={len(rows)} sha256={written[-1]['sha256'][:16]}…")

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "CFTC PRE Socrata",
        "endpoints": {
            "fut_only": f"{BASE}/{FUT_ONLY}.json",
            "combined": f"{BASE}/{COMBINED}.json",
        },
        "assumption_archived_equals_published": True,
        "note": (
            "PRE returns current published vintage; CFTC occasionally revises. "
            "Screen treats archived==published as an explicit assumption."
        ),
        "artifacts": written,
    }
    man_path = OUT_DIR / "manifest.json"
    man_blob = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    man_path.write_bytes(man_blob)
    print(f"[harvest] manifest sha256={_sha256_bytes(man_blob)}")
    print(json.dumps({"ok": True, "artifacts": written}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
