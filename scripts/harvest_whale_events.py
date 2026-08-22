#!/usr/bin/env python3
"""Log-only whale / large-transfer harvester (data-scraper-agent pattern, bot-fit).

COLLECT → (rule classify) → STORE under data/whale_events/YYYY-MM-DD.jsonl
with available_at_utc PIT stamps. Never wires MCP / LONG / SHORT / CONTROLLED_LIVE.

Sources (priority):
  1. Whale Alert REST v1 when WHALE_ALERT_API_KEY is set
  2. Optional inbox JSON dumps (WHALE_EVENTS_INBOX) for Dune/Bitquery/manual exports
  3. Keyless fallback: blockchain.info unconfirmed BTC txs above WHALE_MIN_USD
     (unlabeled — accrual only)

No Gemini/Notion on this path (De-Emotion + existing warehouse/MC storage).

Usage:
  python scripts/harvest_whale_events.py --once
  python scripts/harvest_whale_events.py --once --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.whale_events import (  # noqa: E402
    HONESTY,
    append_events,
    day_jsonl_path,
    normalize_btc_mempool_tx,
    normalize_whale_alert_social,
    normalize_whale_alert_transaction,
    write_status,
)

STATUS_PATH = ROOT / "data" / "whale_events_status.json"
DEFAULT_MIN_USD = 1_000_000.0
WA_REST = "https://api.whale-alert.io/v1/transactions"
BTC_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
MEMPOOL_URL = "https://blockchain.info/unconfirmed-transactions?format=json"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _http_get_json(url: str, *, params: dict | None = None, timeout: float = 20.0) -> Any:
    import requests

    resp = requests.get(url, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_whale_alert_rest(
    api_key: str,
    *,
    min_usd: float,
    lookback_sec: int = 3600,
) -> list[dict[str, Any]]:
    start = int(time.time()) - int(lookback_sec)
    payload = _http_get_json(
        WA_REST,
        params={
            "api_key": api_key,
            "min_value": int(min_usd),
            "start": start,
            "limit": 100,
        },
    )
    if not isinstance(payload, dict):
        return []
    if payload.get("result") == "error":
        raise RuntimeError(str(payload.get("message") or "whale_alert_error"))
    out: list[dict[str, Any]] = []
    for raw in payload.get("transactions") or []:
        row = normalize_whale_alert_transaction(raw)
        if row:
            out.append(row)
    return out


def fetch_btc_mempool_large(*, min_usd: float) -> list[dict[str, Any]]:
    price = _http_get_json(BTC_PRICE_URL, params={"symbol": "BTCUSDT"})
    btc_usd = float(price.get("price") or 0)
    if btc_usd <= 0:
        return []
    payload = _http_get_json(MEMPOOL_URL)
    txs = payload.get("txs") if isinstance(payload, dict) else None
    if not isinstance(txs, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in txs:
        row = normalize_btc_mempool_tx(raw, btc_usd=btc_usd, min_usd=min_usd)
        if row:
            out.append(row)
    return out


def ingest_inbox(inbox: Path) -> list[dict[str, Any]]:
    """Accept Whale Alert-shaped JSON files or {transactions:[...]} / {text:...} dumps."""
    if not inbox.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(inbox.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows: list[Any] = []
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("transactions"), list):
                rows = payload["transactions"]
            else:
                rows = [payload]
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            if "amount_usd" in raw or "from" in raw:
                row = normalize_whale_alert_transaction(raw)
            elif "text" in raw:
                row = normalize_whale_alert_social(raw)
            else:
                row = None
            if row:
                row["source"] = row.get("source") or "inbox"
                row["extra"] = {**(row.get("extra") or {}), "inbox_file": path.name}
                out.append(row)
    return out


def run_once(*, dry_run: bool, min_usd: float, lookback_sec: int) -> dict[str, Any]:
    api_key = (os.environ.get("WHALE_ALERT_API_KEY") or "").strip()
    inbox_raw = (os.environ.get("WHALE_EVENTS_INBOX") or "").strip()
    inbox = Path(inbox_raw) if inbox_raw else ROOT / "data" / "whale_events_inbox"
    sources_used: list[str] = []
    events: list[dict[str, Any]] = []
    errors: list[str] = []

    if api_key:
        try:
            batch = fetch_whale_alert_rest(api_key, min_usd=min_usd, lookback_sec=lookback_sec)
            events.extend(batch)
            sources_used.append("whale_alert_rest")
        except Exception as exc:  # noqa: BLE001 — status must record vendor failures
            errors.append(f"whale_alert_rest: {exc}")
    else:
        errors.append("WHALE_ALERT_API_KEY unset — skipping labeled Whale Alert REST")

    try:
        inbox_batch = ingest_inbox(inbox)
        if inbox_batch:
            events.extend(inbox_batch)
            sources_used.append("inbox")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"inbox: {exc}")

    # Keyless accrual always attempted (weak labels) so the pipeline accrues without a paid key.
    try:
        mem = fetch_btc_mempool_large(min_usd=min_usd)
        events.extend(mem)
        sources_used.append("blockchain_info_mempool")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"mempool: {exc}")

    # Dedupe within this batch by event_id
    by_id: dict[str, dict[str, Any]] = {}
    for ev in events:
        by_id[str(ev["event_id"])] = ev
    unique = list(by_id.values())

    out_path = day_jsonl_path(ROOT)
    added = skipped = 0
    if not dry_run:
        added, skipped = append_events(out_path, unique)
        write_status(
            STATUS_PATH,
            {
                "ok": True,
                "dry_run": False,
                "sources_used": sources_used,
                "fetched": len(unique),
                "added": added,
                "skipped": skipped,
                "min_usd": min_usd,
                "jsonl": str(out_path.relative_to(ROOT)).replace("\\", "/"),
                "has_whale_alert_key": bool(api_key),
                "errors": errors,
            },
        )
    else:
        write_status(
            STATUS_PATH,
            {
                "ok": True,
                "dry_run": True,
                "sources_used": sources_used,
                "fetched": len(unique),
                "added": 0,
                "skipped": 0,
                "min_usd": min_usd,
                "jsonl": str(out_path.relative_to(ROOT)).replace("\\", "/"),
                "has_whale_alert_key": bool(api_key),
                "errors": errors,
            },
        )

    return {
        "fetched": len(unique),
        "added": added,
        "skipped": skipped,
        "sources_used": sources_used,
        "errors": errors,
        "jsonl": str(out_path),
        "honesty": HONESTY,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Log-only whale event harvest")
    parser.add_argument("--once", action="store_true", default=True, help="Single tick (default)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/classify but skip JSONL append")
    parser.add_argument(
        "--min-usd",
        type=float,
        default=_env_float("WHALE_MIN_USD", DEFAULT_MIN_USD),
        help="Minimum USD notional (default env WHALE_MIN_USD or 1e6)",
    )
    parser.add_argument("--lookback-sec", type=int, default=3600)
    args = parser.parse_args()

    summary = run_once(dry_run=args.dry_run, min_usd=args.min_usd, lookback_sec=args.lookback_sec)
    print(json.dumps(summary, indent=2))
    # Non-zero only on total failure (no sources produced data AND hard errors)
    if summary["fetched"] == 0 and summary["errors"] and "mempool" in " ".join(summary["errors"]):
        # soft: still exit 0 if status written — accrual ops should not flap
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
