"""Historical token-unlock calendar backfill — keyless DefiLlama datasets bucket.

Unblocks screen 08b (pre-unlock short). Audit 08d finding C2-a: the emissions
dataset behind the paywalled api.llama.fi/emissions is served keylessly from
`https://defillama-datasets.llama.fi/`:

  * /emissionsProtocolsList  -> JSON array of protocol slugs (~342)
  * /emissionsIndex          -> per-protocol summary (gecko_id, events, circSupply)
  * /emissions/{slug}        -> metadata.events[] (timestamp, noOfTokens, category,
                                unlockType) + documentedData daily unlocked series
                                + categories (insiders/privateSale/noncirculating/...)

Flow (constructions frozen in 08b's execution addendum BEFORE this ran):
  1. emissionsIndex -> protocols with >=1 cliff event inside 2023-06 -> 2026-06.
  2. gecko_id -> ticker via keyless CoinGecko /api/v3/coins/list.
  3. Cross-reference tickers against USDT-linear-perp bases on binance/bybit/bitget
     (keyless ccxt load_markets). Unmatched protocols are counted, not guessed.
  4. Per matched protocol: fetch /emissions/{slug}; write one JSON per symbol to
     data/unlock_calendar/{SYMBOL}.json with merged cliff events (insider and
     noncirculating tokens EXCLUDED from event size but recorded), the documented
     circulating-supply step series, and the unlock/circ ratio per event.
  5. --with-funding: extend data/funding_history/ to matched bases lacking coverage,
     reusing scripts/backfill_funding_history.py machinery (real prints only).

Degrades gracefully: per-protocol fetch errors are reported and skipped — never
fabricated. Window per the frozen 08b pre-registration: 2023-06 -> 2026-06.

Usage:
    venv\\Scripts\\python.exe scripts\\backfill_unlock_calendar.py
    venv\\Scripts\\python.exe scripts\\backfill_unlock_calendar.py --with-funding
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "data", "unlock_calendar")

BUCKET = "https://defillama-datasets.llama.fi"
COINGECKO_LIST = "https://api.coingecko.com/api/v3/coins/list"
VENUES = ("binance", "bybit", "bitget")
# Frozen 08b event window.
WIN_LO = int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp())
WIN_HI = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
# Event-size exclusions frozen in the 08b execution addendum.
EXCLUDED_CATEGORIES = {"insiders", "noncirculating"}
FUNDING_SINCE = "2023-04-01"  # covers W1 entries (T-28d) for T >= 2023-06-01


# ── Pure functions (unit-tested offline) ─────────────────────────────────────
def _tokens(ev: dict) -> float:
    """Sum noOfTokens (the bucket serves it as a list)."""
    v = ev.get("noOfTokens")
    if isinstance(v, (int, float)):
        return float(v)
    return float(sum(x for x in (v or []) if isinstance(x, (int, float))))


def extract_cliff_events(payload: dict, win_lo: int = WIN_LO, win_hi: int = WIN_HI) -> list[dict]:
    """Merged cliff events inside [win_lo, win_hi) from metadata.events[].

    Same-timestamp allocations merge into ONE event. Event size counts only
    allocations whose category is NOT in EXCLUDED_CATEGORIES (insiders and
    noncirculating excluded per the frozen 08b addendum; excluded token counts
    are recorded on the event for the audit)."""
    by_ts: dict[int, dict] = {}
    for ev in (payload.get("metadata") or {}).get("events") or []:
        if ev.get("unlockType") != "cliff":
            continue
        ts = ev.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        ts = int(ts)
        if not (win_lo <= ts < win_hi):
            continue
        cat = str(ev.get("category") or "Uncategorized")
        e = by_ts.setdefault(
            ts,
            {
                "ts": ts,
                "tokens": 0.0,
                "categories": [],
                "excluded_tokens": 0.0,
                "excluded_categories": [],
                "n_allocations": 0,
            },
        )
        e["n_allocations"] += 1
        if cat in EXCLUDED_CATEGORIES:
            e["excluded_tokens"] += _tokens(ev)
            e["excluded_categories"].append(cat)
        else:
            e["tokens"] += _tokens(ev)
            e["categories"].append(cat)
    return [by_ts[t] for t in sorted(by_ts)]


def build_circ_series(payload: dict) -> list[list]:
    """Documented circulating-supply step series [(ts, circ)], ascending.

    circ(t) = sum of cumulative `unlocked` across documentedData.data labels NOT
    mapped to categories['noncirculating'] (frozen 08b addendum §2)."""
    noncirc_labels = set((payload.get("categories") or {}).get("noncirculating") or [])
    total: dict[int, float] = {}
    for alloc in (payload.get("documentedData") or {}).get("data") or []:
        if alloc.get("label") in noncirc_labels:
            continue
        for pt in alloc.get("data") or []:
            ts = pt.get("timestamp")
            u = pt.get("unlocked")
            if isinstance(ts, (int, float)) and isinstance(u, (int, float)):
                total[int(ts)] = total.get(int(ts), 0.0) + float(u)
    return [[t, total[t]] for t in sorted(total)]


def circ_at(series: list[list], ts: int) -> float | None:
    """As-of lookup on the ascending step series; None before the first point."""
    val = None
    for t, c in series:
        if t <= ts:
            val = c
        else:
            break
    return val


def map_gecko_to_symbol(coins_list: list[dict]) -> dict[str, str]:
    """gecko_id -> UPPERCASE ticker from CoinGecko /coins/list rows."""
    out = {}
    for c in coins_list or []:
        gid, sym = c.get("id"), c.get("symbol")
        if gid and sym:
            out[str(gid)] = str(sym).upper()
    return out


def index_protocols_in_window(
    index_data: list[dict], win_lo: int = WIN_LO, win_hi: int = WIN_HI
) -> list[dict]:
    """Protocols from emissionsIndex with >=1 cliff event inside the window."""
    out = []
    for p in index_data or []:
        evs = p.get("events") or []
        if any(
            e.get("unlockType") == "cliff"
            and isinstance(e.get("timestamp"), (int, float))
            and win_lo <= int(e["timestamp"]) < win_hi
            for e in evs
        ):
            out.append(
                {
                    "slug": p.get("protocolSlug"),
                    "gecko_id": p.get("gecko_id"),
                    "name": p.get("name"),
                    "circSupply": p.get("circSupply"),
                    "maxSupply": p.get("maxSupply"),
                }
            )
    return out


# ── Network helpers ──────────────────────────────────────────────────────────
def _get_json(url: str, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 429:
                time.sleep(10 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


def perp_bases_by_venue() -> dict[str, set]:
    """USDT-linear-perp base tickers per venue (keyless ccxt load_markets)."""
    import ccxt

    out: dict[str, set] = {}
    for venue in VENUES:
        try:
            ex = getattr(ccxt, venue)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            ex.load_markets()
            out[venue] = {
                m["base"]
                for m in ex.markets.values()
                if m.get("swap") and m.get("linear") and m.get("quote") == "USDT"
            }
        except Exception as e:  # noqa: BLE001
            print(f"[markets] {venue}: FAILED {type(e).__name__}: {e}", flush=True)
            out[venue] = set()
    return out


def backfill_funding_for_bases(bases_venues: dict[str, list[str]]) -> None:
    """Extend data/funding_history/ to calendar bases, reusing the existing
    harvest machinery (real venue prints only)."""
    from scripts.backfill_funding_history import (
        PAGE_LIMIT,
        _ms,
        _perp_symbol,
        fetch_funding_history_paginated,
        make_exchange,
        write_history_csv,
    )

    since_ms = _ms(FUNDING_SINCE)
    hist_dir = os.path.join(_REPO, "data", "funding_history")
    for venue in VENUES:
        bases = [b for b, vs in bases_venues.items() if venue in vs]
        if not bases:
            continue
        try:
            ex = make_exchange(venue)
            ex.load_markets()
        except Exception as e:  # noqa: BLE001
            print(f"[funding] {venue}: load_markets FAILED {e}", flush=True)
            continue
        for base in sorted(bases):
            path = os.path.join(hist_dir, f"{venue}_{base}.csv")
            if os.path.exists(path):
                import pandas as pd

                try:
                    first = int(pd.read_csv(path)["ts"].min())
                except Exception:  # noqa: BLE001
                    first = None
                if first is not None and first <= since_ms // 1000 + 8 * 3600:
                    print(f"[funding] {venue} {base}: already covered from {first}", flush=True)
                    continue
            sym = _perp_symbol(base)
            if sym not in ex.markets:
                continue
            try:
                rows = fetch_funding_history_paginated(ex, sym, since_ms, limit=PAGE_LIMIT[venue])
            except RuntimeError as e:
                print(f"[funding] {venue} {base}: FETCH ERROR {e}", flush=True)
                continue
            if rows:
                total, added = write_history_csv(venue, base, sym, rows)
                print(f"[funding] {venue} {base}: rows={total} added={added}", flush=True)
            time.sleep(0.1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--with-funding",
        action="store_true",
        help="also extend data/funding_history/ to matched bases",
    )
    ap.add_argument(
        "--forward-days",
        type=int,
        default=0,
        help=(
            "extend the event window's upper bound to now + N days (keeps the frozen "
            "historical lower bound). Needed by the FORWARD unlock-short shadow probe "
            "(core/agents/unlock_short_probe_agent.py): the frozen screen window ends "
            "2026-06-01, so without this the calendar carries no upcoming events. "
            "Purely additive data refresh — the probe snapshots events as-of signal "
            "time, so re-runs never rewrite already-logged signals."
        ),
    )
    args = ap.parse_args()

    win_lo, win_hi = WIN_LO, WIN_HI
    if args.forward_days > 0:
        win_hi = int(time.time()) + args.forward_days * 24 * 3600
        print(f"[window] forward mode: events up to now+{args.forward_days}d", flush=True)

    print("[1/5] emissionsIndex ...", flush=True)
    index = _get_json(f"{BUCKET}/emissionsIndex").get("data") or []
    in_window = index_protocols_in_window(index, win_lo, win_hi)
    print(
        f"  protocols total={len(index)} with in-window cliff events={len(in_window)}", flush=True
    )

    print("[2/5] CoinGecko coins/list for gecko_id -> ticker ...", flush=True)
    g2s = map_gecko_to_symbol(_get_json(COINGECKO_LIST))
    print(f"  mapped ids={len(g2s)}", flush=True)

    print("[3/5] venue perp bases (ccxt, keyless) ...", flush=True)
    venue_bases = perp_bases_by_venue()
    for v, s in venue_bases.items():
        print(f"  {v}: {len(s)} USDT-linear perp bases", flush=True)

    print("[4/5] cross-reference + per-slug fetch ...", flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    matched, unmatched_no_ticker, unmatched_no_perp, fetch_errors = [], [], [], []
    bases_venues: dict[str, list[str]] = {}
    for p in in_window:
        sym = g2s.get(str(p.get("gecko_id")))
        if not sym:
            unmatched_no_ticker.append(p["slug"])
            continue
        venues = [v for v in VENUES if sym in venue_bases[v]]
        if not venues:
            unmatched_no_perp.append(f"{p['slug']}({sym})")
            continue
        try:
            payload = _get_json(f"{BUCKET}/emissions/{p['slug']}")
        except RuntimeError as e:
            print(f"  {p['slug']}: FETCH ERROR {e}", flush=True)
            fetch_errors.append(p["slug"])
            continue
        events = extract_cliff_events(payload, win_lo, win_hi)
        circ = build_circ_series(payload)
        for e in events:
            c = circ_at(circ, e["ts"])
            e["circ_documented"] = c
            e["ratio"] = (e["tokens"] / c) if (c and c > 0) else None
        now_circ = circ_at(circ, int(time.time()))
        doc = {
            "symbol": sym,
            "slug": p["slug"],
            "gecko_id": p["gecko_id"],
            "name": p["name"],
            "venues_with_perp": venues,
            "window": [win_lo, win_hi],
            "excluded_event_categories": sorted(EXCLUDED_CATEGORIES),
            "max_supply": p.get("maxSupply"),
            "circ_calibration": {
                "documented_now": now_circ,
                "index_circSupply_now": p.get("circSupply"),
                "ratio": (now_circ / p["circSupply"])
                if (now_circ and p.get("circSupply"))
                else None,
            },
            "events": events,
            "circ_series": circ,
            "source": f"{BUCKET}/emissions/{p['slug']} (keyless bucket, fetched "
            f"{datetime.now(timezone.utc).isoformat()})",
        }
        out_path = os.path.join(OUT_DIR, f"{sym}.json")
        # A ticker can map to >1 protocol; keep the one with more in-window events.
        if os.path.exists(out_path):
            try:
                prev = json.load(open(out_path, encoding="utf-8"))
                if len(prev.get("events") or []) >= len(events):
                    print(f"  {p['slug']} -> {sym}: kept existing ({prev.get('slug')})", flush=True)
                    continue
            except Exception:  # noqa: BLE001
                pass
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        matched.append((p["slug"], sym, len(events)))
        bases_venues[sym] = venues
        print(
            f"  {p['slug']} -> {sym}: {len(events)} merged in-window cliff events "
            f"(venues: {','.join(venues)})",
            flush=True,
        )
        time.sleep(0.15)

    print("\n=== UNLOCK CALENDAR SUMMARY ===", flush=True)
    print(f"matched (written): {len(matched)}", flush=True)
    print(f"unmatched: no gecko->ticker map: {len(unmatched_no_ticker)}", flush=True)
    print(f"unmatched: ticker has no perp on 3 venues: {len(unmatched_no_perp)}", flush=True)
    print(f"fetch errors: {fetch_errors}", flush=True)
    print(f"no-perp list: {unmatched_no_perp}", flush=True)

    if args.with_funding:
        print("\n[5/5] funding-history extension for matched bases ...", flush=True)
        backfill_funding_for_bases(bases_venues)
    else:
        print("\n[5/5] skipped (--with-funding not set)", flush=True)


if __name__ == "__main__":
    main()
