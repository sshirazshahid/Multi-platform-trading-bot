"""Exchange-announcement event harvester (delisting + tier-change reopen clocks).

WHY (32_deep_research_futures_2026-07-24): the delisting forced-flow family is OPEN with
n=34 < 30-covered-event floor, and leverage-tier/margin-change events are a harvest-first
sibling class with zero crypto studies. Binance flips a perp's ``deliveryDate`` in
``fapi/v1/exchangeInfo`` at announcement moment (FMZ, May-2026) — a structured,
point-in-time-verifiable detector. This script polls public instrument metadata on all
three venues daily, diffs against the last state, and appends PIT event rows.

Event classes (kept separate — pooling heterogeneous events is a multiplicity trap):
  delisting_signal  delivery/off timestamp appeared or changed
  tier_change       max/min leverage changed (Bybit/Bitget; Binance brackets are not public)
  status_change     instrument status flipped (e.g. TRADING -> SETTLING)
  new_symbol        instrument appeared (listing-short context)
  removed_symbol    instrument disappeared from metadata

Read-only w.r.t. the bot; no trading logic; keyless public REST; fail-soft per venue.

Run: python scripts/harvest_exchange_announcements.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "exchange_announcement_events"
STATE_PATH = OUT_DIR / "state.json"
EVENTS_PATH = OUT_DIR / "events.jsonl"

BINANCE_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BYBIT_URL = "https://api.bybit.com/v5/market/instruments-info"
BITGET_URL = "https://api.bitget.com/api/v2/mix/market/contracts"

# field -> event class (fields not listed are ignored in diffs)
FIELD_CLASS = {
    "delivery_date": "delisting_signal",
    "delivery_time": "delisting_signal",
    "off_time": "delisting_signal",
    "max_leverage": "tier_change",
    "min_leverage": "tier_change",
    "status": "status_change",
}


def _get(url: str, params: dict | None = None, retries: int = 2, timeout: int = 30) -> dict:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — retried, then raised to per-venue handler
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def normalize_binance(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in payload.get("symbols", []):
        sym = s.get("symbol")
        if not sym:
            continue
        out[sym] = {
            "status": s.get("status"),
            "delivery_date": s.get("deliveryDate"),
            "contract_type": s.get("contractType"),
        }
    return out


def normalize_bybit_pages(pages: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for page in pages:
        for it in (page.get("result") or {}).get("list", []):
            sym = it.get("symbol")
            if not sym:
                continue
            lev = it.get("leverageFilter") or {}
            out[sym] = {
                "status": it.get("status"),
                "delivery_time": it.get("deliveryTime"),
                "max_leverage": lev.get("maxLeverage"),
            }
    return out


def normalize_bitget(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in payload.get("data") or []:
        sym = d.get("symbol")
        if not sym:
            continue
        out[sym] = {
            "status": d.get("symbolStatus"),
            "off_time": d.get("offTime"),
            "max_leverage": d.get("maxLever"),
            "min_leverage": d.get("minLever"),
        }
    return out


def fetch_binance() -> dict[str, dict]:
    return normalize_binance(_get(BINANCE_URL))


def fetch_bybit() -> dict[str, dict]:
    pages: list[dict] = []
    cursor = ""
    for _ in range(20):  # hard page cap
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = _get(BYBIT_URL, params=params)
        pages.append(page)
        cursor = (page.get("result") or {}).get("nextPageCursor") or ""
        if not cursor:
            break
    return normalize_bybit_pages(pages)


def fetch_bitget() -> dict[str, dict]:
    return normalize_bitget(_get(BITGET_URL, params={"productType": "USDT-FUTURES"}))


def diff_venue(old: dict[str, dict], new: dict[str, dict], venue: str, ts_utc: str) -> list[dict]:
    """Diff two normalized symbol maps into PIT event rows."""
    events: list[dict] = []
    for sym in sorted(set(new) - set(old)):
        events.append(
            {"ts_utc": ts_utc, "venue": venue, "symbol": sym, "event_class": "new_symbol",
             "changed": {"state": [None, new[sym]]}}
        )
    for sym in sorted(set(old) - set(new)):
        events.append(
            {"ts_utc": ts_utc, "venue": venue, "symbol": sym, "event_class": "removed_symbol",
             "changed": {"state": [old[sym], None]}}
        )
    for sym in sorted(set(old) & set(new)):
        by_class: dict[str, dict] = {}
        for field, cls in FIELD_CLASS.items():
            if field in old[sym] or field in new[sym]:
                o, n = old[sym].get(field), new[sym].get(field)
                if o != n:
                    by_class.setdefault(cls, {})[field] = [o, n]
        for cls, changed in by_class.items():
            events.append(
                {"ts_utc": ts_utc, "venue": venue, "symbol": sym,
                 "event_class": cls, "changed": changed}
            )
    return events


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"venues": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — corrupted state = re-baseline, never crash
        return {"venues": {}}


def save_state_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def append_events(path: Path, events: list[dict]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, separators=(",", ":")) + "\n")


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    ts_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = load_state(STATE_PATH)
    venues = state.setdefault("venues", {})
    fetchers = {"binance": fetch_binance, "bybit": fetch_bybit, "bitget": fetch_bitget}

    all_events: list[dict] = []
    failures = 0
    for venue, fetch in fetchers.items():
        try:
            new_map = fetch()
        except Exception as exc:  # noqa: BLE001 — one venue down must not lose the others
            failures += 1
            print(f"[announce_harvest] ERROR {venue}: {exc}", file=sys.stderr)
            continue
        old_map = venues.get(venue)
        if old_map is None:
            venues[venue] = new_map
            print(f"[announce_harvest] {venue}: baseline established ({len(new_map)} symbols)")
            continue
        events = diff_venue(old_map, new_map, venue, ts_utc)
        all_events.extend(events)
        venues[venue] = new_map
        print(f"[announce_harvest] {venue}: {len(new_map)} symbols, {len(events)} events")

    append_events(EVENTS_PATH, all_events)
    state["updated_utc"] = ts_utc
    save_state_atomic(STATE_PATH, state)
    if all_events:
        classes: dict[str, int] = {}
        for ev in all_events:
            classes[ev["event_class"]] = classes.get(ev["event_class"], 0) + 1
        print(f"[announce_harvest] appended {len(all_events)} events: {classes}")
    return 1 if failures == len(fetchers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
