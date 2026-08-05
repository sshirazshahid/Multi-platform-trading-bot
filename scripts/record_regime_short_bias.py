"""Record F&G + 24h long-liq SHORT-bias environment (prereg 61) — log-only.

Does not import bot_engine / mcp_brain / order_manager. Safe for schtask / intel.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.regime_short_bias import (  # noqa: E402
    append_log,
    completed_hour_start,
    evaluate_short_bias,
    sum_long_liq_usd,
    write_snapshot,
)

NEWS_CACHE = ROOT / "data" / "news_cache.json"
LIQ_HIST = ROOT / "data" / "liquidations_history.jsonl"
LATEST = ROOT / "data" / "regime_short_bias_latest.json"
LOG = ROOT / "data" / "regime_short_bias_log.jsonl"
FNG_URL = "https://api.alternative.me/fng/?limit=1"


def fetch_fng() -> dict:
    req = urllib.request.Request(FNG_URL, headers={"User-Agent": "TradingBot-regime-log/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — fixed public URL
        raw = json.loads(resp.read().decode("utf-8"))
    row = (raw.get("data") or [{}])[0]
    return {
        "value": int(row.get("value", 50)),
        "label": row.get("value_classification", "Neutral"),
        "timestamp": str(row.get("timestamp") or ""),
    }


def merge_news_cache(fg: dict) -> None:
    payload: dict = {}
    if NEWS_CACHE.is_file():
        try:
            payload = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    payload["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["fear_greed"] = {
        "value": fg["value"],
        "label": fg["label"],
        "timestamp": fg["timestamp"],
    }
    NEWS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NEWS_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(NEWS_CACHE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print snapshot; do not write")
    ap.add_argument("--offline", action="store_true", help="Use news_cache F&G only (no network)")
    args = ap.parse_args()

    if args.offline:
        fg = {"value": None, "label": None, "timestamp": ""}
        if NEWS_CACHE.is_file():
            try:
                cached = json.loads(NEWS_CACHE.read_text(encoding="utf-8")).get("fear_greed") or {}
                if cached.get("value") is not None:
                    fg = {
                        "value": int(cached["value"]),
                        "label": cached.get("label"),
                        "timestamp": str(cached.get("timestamp") or ""),
                    }
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    else:
        fg = fetch_fng()
        merge_news_cache(fg)

    end_h = completed_hour_start()
    liq = sum_long_liq_usd(LIQ_HIST, end_hour_inclusive=end_h)
    ev = evaluate_short_bias(
        fng_value=fg.get("value"),
        long_usd_24h=float(liq["long_usd_24h"]),
    )
    snap = {
        "ts_unix": time.time(),
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fear_greed": fg,
        "liquidations": liq,
        "evaluation": ev,
        "honesty": (
            "Log-only SHORT-bias environment flag. Does not authorize shorts. "
            "Vendor $208M prints are not this series (Binance forceOrder undercount)."
        ),
    }
    print(json.dumps(snap, indent=2))
    if args.dry_run:
        return 0
    write_snapshot(LATEST, snap)
    append_log(LOG, snap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
