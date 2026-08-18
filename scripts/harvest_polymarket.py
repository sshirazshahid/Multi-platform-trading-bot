#!/usr/bin/env python3
"""Log-only Polymarket crypto prediction-market harvester.

COLLECT -> STORE under data/polymarket/YYYY-MM-DD.jsonl with available_at_utc
PIT stamps. Never wires MCP / LONG / SHORT / CONTROLLED_LIVE. Nothing here
reaches a trade decision, and nothing may until a pre-registered screen clears
the frozen promotion gate.

WHY THIS DATA (2026-08-18). The standing rule from the August council is "no
new screens without new DATA". Every input the bot has today is BACKWARD-
looking: OHLCV, funding, open interest, L2 depth, aggTrades, COT, skew. A
prediction-market probability is the one class it has never had — a
FORWARD-looking, money-weighted estimate of an event. It appears nowhere in
the ~2,400 refuted families, so it can open a genuinely new question rather
than re-litigate a closed one.

HONEST EXPECTATION: NO_GO, like everything before it. Polymarket crypto
markets are liquid and well arbitraged, and "a public probability leads spot"
is exactly the kind of hypothesis that dies after costs. This harvester exists
so the question can be ASKED with real data, not so the answer can be assumed.

Design notes that protect the future screen:
  * PIT stamp on every row. A screen must be able to prove it read a
    probability that existed BEFORE the outcome.
  * Probability provenance recorded. outcomePrices is last trade and goes
    stale; bestBid/bestAsk is the live book. They are never blended silently.
  * Resolved markets are harvested, not skipped — a closed market's prices are
    the ground-truth label (y) any predictive screen needs.
  * No liquidity or open/closed filtering here. COLLECT -> STORE; filtering is
    the prereg's job, and doing it here would pre-decide the question.

Source: Polymarket Gamma API, public, no auth, no key.

Usage:
  python scripts/harvest_polymarket.py --once
  python scripts/harvest_polymarket.py --once --dry-run
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GAMMA = "https://gamma-api.polymarket.com"
OUT_DIR = ROOT / "data" / "polymarket"
TAG_SLUGS = ("crypto",)
# Two TARGETED passes, not blind pagination. Measured 2026-08-18: paginating
# the untargeted tag returned 3,442 markets of which only 5 were open — the
# live set that actually accrues a forward series was buried under years of
# resolved history. Split explicitly instead:
#   * open markets   -> the accruing probability series (the X variable)
#   * recent closes  -> resolutions, i.e. the ground-truth label (y)
# Same measurement, closed=false alone: 443 open markets. 88x the signal.
EVENT_QUERIES = (
    ("open", "closed=false"),
    ("resolved", "closed=true&order=endDate&ascending=false"),
)
PAGE_LIMIT = 100
MAX_PAGES = 5
TIMEOUT_SEC = 30
USER_AGENT = "trading-bot-research/1.0"


# ── pure helpers (unit-tested; no network, no clock) ────────────────────────

def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json_list(value: Any) -> list:
    """Gamma returns `outcomes`/`outcomePrices` as JSON-encoded STRINGS."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def derive_probability(market: dict) -> tuple[Optional[float], str]:
    """P(first outcome) and where it came from.

    Prefers the live book midpoint over `outcomePrices`, which is the last
    TRADE and can be arbitrarily stale in a thin market. A one-sided book is
    not a midpoint, so it falls back rather than inventing one. Returns
    (None, "none") when nothing usable exists — never a guess.
    """
    bid, ask = _num(market.get("bestBid")), _num(market.get("bestAsk"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0, "book_mid"
    prices = _json_list(market.get("outcomePrices"))
    if prices:
        first = _num(prices[0])
        if first is not None:
            return first, "outcome_prices"
    return None, "none"


def parse_market(event: dict, market: dict, now: str) -> Optional[dict]:
    """One PIT-stamped snapshot row, or None if the market cannot be joined.

    `now` is injected rather than read from the clock so every row in a harvest
    is stamped identically and tests stay deterministic.
    """
    condition_id = market.get("conditionId")
    if not condition_id:
        return None                     # unjoinable across snapshots -> useless
    prob, source = derive_probability(market)
    return {
        "available_at_utc": now,
        "event_title": event.get("title"),
        "event_id": event.get("id"),
        "condition_id": condition_id,
        "slug": market.get("slug"),
        "question": market.get("question"),
        "outcomes": _json_list(market.get("outcomes")),
        "outcome_prices": [_num(p) for p in _json_list(market.get("outcomePrices"))],
        "prob_yes": prob,
        "prob_source": source,
        "best_bid": _num(market.get("bestBid")),
        "best_ask": _num(market.get("bestAsk")),
        "spread": _num(market.get("spread")),
        "last_trade_price": _num(market.get("lastTradePrice")),
        "volume_num": _num(market.get("volumeNum")),
        "liquidity_num": _num(market.get("liquidityNum")),
        "one_week_price_change": _num(market.get("oneWeekPriceChange")),
        "end_date": market.get("endDate"),
        "closed": bool(market.get("closed")),
        "active": bool(market.get("active")),
    }


def write_snapshot(rows: list, out_dir: Path, day: str,
                   dry_run: bool = False) -> Optional[Path]:
    """Append rows to out_dir/<day>.jsonl. Returns the path, or None."""
    if not rows or dry_run:
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:       # append-only
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


# ── network ─────────────────────────────────────────────────────────────────

def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:  # noqa: S310
        return json.loads(resp.read())


def fetch_crypto_events(tag_slugs=TAG_SLUGS, max_pages: int = MAX_PAGES,
                        scope: str = "all") -> list:
    """Every event under the given tags, paginated, deduped by event id.

    scope="open" fetches only live markets — the accruing series, and the only
    pass worth running hourly. A resolved market is terminal: its row never
    changes again, so re-harvesting it every hour writes ~40MB/day of
    duplicates. scope="all" adds the resolution pass that carries the label.
    """
    seen: set = set()
    events: list = []
    for tag in tag_slugs:
        for label, query in EVENT_QUERIES:
            if scope == "open" and label != "open":
                continue
            for page in range(max_pages):
                url = (f"{GAMMA}/events?limit={PAGE_LIMIT}"
                       f"&offset={page * PAGE_LIMIT}&tag_slug={tag}&{query}")
                try:
                    batch = _get(url)
                except (urllib.error.URLError, TimeoutError,
                        ValueError, OSError) as exc:
                    print(f"[Polymarket] fetch failed "
                          f"(tag={tag}, {label}, page={page}): {exc}")
                    break
                if not isinstance(batch, list) or not batch:
                    break
                for event in batch:
                    key = event.get("id")
                    if key and key not in seen:
                        seen.add(key)
                        events.append(event)
                if len(batch) < PAGE_LIMIT:
                    break
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest Polymarket crypto markets.")
    ap.add_argument("--once", action="store_true",
                    help="single harvest pass (the only supported mode)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and summarise, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--scope", choices=("open", "all"), default="all",
                    help="'open' = live markets only (hourly accrual); "
                         "'all' adds the resolution pass (run daily)")
    args = ap.parse_args()

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    events = fetch_crypto_events(scope=args.scope)
    rows = []
    for event in events:
        for market in (event.get("markets") or []):
            row = parse_market(event, market, now)
            if row is not None:
                rows.append(row)

    live = sum(1 for r in rows if not r["closed"])
    priced = sum(1 for r in rows if r["prob_yes"] is not None)
    booked = sum(1 for r in rows if r["prob_source"] == "book_mid")
    print(f"[Polymarket] {len(events)} events -> {len(rows)} markets "
          f"({live} open, {priced} priced, {booked} from live book)")

    path = write_snapshot(rows, Path(args.out_dir),
                          now_dt.strftime("%Y-%m-%d"), dry_run=args.dry_run)
    if path is None:
        print("[Polymarket] nothing written"
              + (" (--dry-run)" if args.dry_run else ""))
    else:
        print(f"[Polymarket] appended {len(rows)} rows -> {path}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
