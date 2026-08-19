"""Historical funding-rate backfill (keyless public ccxt) for the screen re-run.

Fetches REAL per-venue funding-rate history and stores it under
``data/funding_history/`` as one CSV per venue-symbol. This is the store the
re-wired dispersion + listing-short screens read (see
research/screen_funding_dispersion.py and research/screen_listing_short.py).

Three legs:

  * dispersion : the F1 15-coin universe (research.funding_carry_lab
    F1_EXPANDED_UNIVERSE_2026_07_05) x 3 venues (binance, bybit, bitget),
    paginated as far back as each venue serves.
  * listing    : Binance ONLY, the genuine post-backfill listing bases the
    listing-short screen itself identifies (research.screen_listing_short
    .genuine_listing_bases), since 2025-06-01.
  * probe      : the SHADOW-PROBE universe on bybit, resolved from the same
    spec artifact the probes use (bundle_mr_probe_agent.resolve_universe).
    Added 2026-08-20: the probe universe was widened on 2026-07-20 but this
    backfill was not, so probes warned "no usable realized funding history"
    for symbols NEITHER other leg covers -- the remedy the warning named
    could not work, and 33% of pending probe rows had no funding component
    in their after-cost net.

CSV schema (one file per venue-symbol): ts (unix seconds, int),
funding_rate (float, per-interval decimal), venue (str), symbol (str ccxt id).
Idempotent: re-runs merge + dedupe on ts.

Read-only against exchanges (public funding endpoints); writes only local CSVs
under data/ (which is NEVER committed — public repo).

Usage:
    venv\\Scripts\\python.exe scripts\\backfill_funding_history.py --leg all
    venv\\Scripts\\python.exe scripts\\backfill_funding_history.py --leg dispersion
    venv\\Scripts\\python.exe scripts\\backfill_funding_history.py --leg listing --since 2025-06-01
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(_REPO, "data", "funding_history")

# ccxt fetchFundingRateHistory page caps (venue-documented).
PAGE_LIMIT = {"binance": 1000, "bybit": 200, "bitget": 100}
VENUES = ("binance", "bybit", "bitget")

# Dispersion history floor (as far back as the venue serves; perps rarely predate this).
DISPERSION_SINCE = "2021-01-01"
LISTING_SINCE = "2025-06-01"
# Probe rows are short-horizon (12-bar / 4h), so a 90d floor covers every
# open row with margin while keeping the fetch cheap across ~144 symbols.
PROBE_SINCE = "2026-05-01"


def _ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _perp_symbol(base: str) -> str:
    """USDT-margined linear perp ccxt unified symbol."""
    return f"{base}/USDT:USDT"


def make_exchange(venue: str):
    import ccxt

    return getattr(ccxt, venue)({
        "enableRateLimit": True,  # ccxt's built-in throttle
        "options": {"defaultType": "swap"},
    })


def fetch_funding_history_paginated(
    ex, symbol: str, since_ms: int, *, limit: int, max_pages: int = 400
) -> list[dict]:
    """Forward-paginate fetchFundingRateHistory from ``since_ms`` to now.

    Robust to venues that ignore ``since`` or return overlapping windows:
    dedupes on settlement timestamp and stops when a page yields no NEW rows
    or fails to advance the cursor.
    """
    out: dict[int, float] = {}
    cursor = int(since_ms)
    now_ms = ex.milliseconds()
    pages = 0
    while pages < max_pages and cursor < now_ms:
        pages += 1
        try:
            batch = ex.fetch_funding_rate_history(symbol, since=cursor, limit=limit)
        except Exception as e:  # noqa: BLE001 - report and stop this symbol
            raise RuntimeError(f"{type(e).__name__}: {e}") from e
        if not batch:
            break
        before = len(out)
        max_ts = cursor
        for r in batch:
            ts = r.get("timestamp")
            fr = r.get("fundingRate")
            if ts is None or fr is None:
                continue
            out[int(ts)] = float(fr)
            if int(ts) > max_ts:
                max_ts = int(ts)
        added = len(out) - before
        # No new rows, or cursor did not advance -> venue exhausted / repeating.
        if added == 0 or max_ts <= cursor:
            break
        cursor = max_ts + 1
        if len(batch) < limit:
            break  # last (short) page
    if not out:
        # Forward-from-since returns EMPTY on venues (e.g. bybit) when ``since``
        # predates the symbol's funding history. Fall back to backward paging via
        # the unified ``until`` param, which those venues DO honour.
        return _fetch_backward(ex, symbol, since_ms, limit=limit, max_pages=max_pages)
    return [{"timestamp": ts, "fundingRate": fr} for ts, fr in sorted(out.items())]


def _fetch_backward(ex, symbol: str, since_ms: int, *, limit: int, max_pages: int) -> list[dict]:
    """Backward-paginate from now to ``since_ms`` using the unified ``until`` param."""
    out: dict[int, float] = {}
    until = ex.milliseconds()
    pages = 0
    while pages < max_pages and until > since_ms:
        pages += 1
        try:
            batch = ex.fetch_funding_rate_history(symbol, since=None, limit=limit, params={"until": until})
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"{type(e).__name__}: {e}") from e
        if not batch:
            break
        page_min = min(int(r["timestamp"]) for r in batch)
        for r in batch:
            ts = r.get("timestamp")
            fr = r.get("fundingRate")
            if ts is None or fr is None:
                continue
            if int(ts) >= since_ms:
                out[int(ts)] = float(fr)
        if page_min >= until:  # no progress
            break
        until = page_min - 1
        if len(batch) < limit:
            break
    return [{"timestamp": ts, "fundingRate": fr} for ts, fr in sorted(out.items())]


def write_history_csv(venue: str, base: str, symbol: str, rows: list[dict]) -> tuple[int, int]:
    """Idempotently merge ``rows`` into data/funding_history/{venue}_{base}.csv.

    Returns (total_rows_after, newly_added). Dedupes on ts (seconds).
    """
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{venue}_{base}.csv")
    new = pd.DataFrame(
        {
            "ts": [int(r["timestamp"] // 1000) for r in rows],
            "funding_rate": [float(r["fundingRate"]) for r in rows],
            "venue": venue,
            "symbol": symbol,
        }
    )
    if os.path.exists(path):
        old = pd.read_csv(path)
        before = set(old["ts"].astype("int64").tolist())
        combined = pd.concat([old, new], ignore_index=True)
    else:
        before = set()
        combined = new
    combined = (
        combined.drop_duplicates("ts", keep="last")
        .sort_values("ts")
        .reset_index(drop=True)
    )
    combined.to_csv(path, index=False)
    added = len({int(t) for t in new["ts"].tolist()} - before)
    return len(combined), added


def backfill_dispersion(since_ms: int) -> list[dict]:
    from research.funding_carry_lab import F1_EXPANDED_UNIVERSE_2026_07_05

    bases = [s.split("/")[0] for s in F1_EXPANDED_UNIVERSE_2026_07_05]
    report = []
    for venue in VENUES:
        print(f"[dispersion] venue={venue} loading markets...", flush=True)
        try:
            ex = make_exchange(venue)
            ex.load_markets()
        except Exception as e:  # noqa: BLE001
            print(f"[dispersion] {venue}: load_markets FAILED {type(e).__name__}: {e}", flush=True)
            for base in bases:
                report.append({"leg": "dispersion", "venue": venue, "base": base,
                               "status": "venue_down", "rows": 0, "added": 0})
            continue
        limit = PAGE_LIMIT[venue]
        for base in bases:
            sym = _perp_symbol(base)
            if sym not in ex.markets:
                print(f"[dispersion] {venue} {base}: NOT LISTED ({sym})", flush=True)
                report.append({"leg": "dispersion", "venue": venue, "base": base,
                               "status": "not_listed", "rows": 0, "added": 0})
                continue
            try:
                rows = fetch_funding_history_paginated(ex, sym, since_ms, limit=limit)
            except RuntimeError as e:
                print(f"[dispersion] {venue} {base}: FETCH ERROR {e}", flush=True)
                report.append({"leg": "dispersion", "venue": venue, "base": base,
                               "status": "fetch_error", "rows": 0, "added": 0, "err": str(e)})
                continue
            total, added = write_history_csv(venue, base, sym, rows) if rows else (0, 0)
            first_ts = rows[0]["timestamp"] // 1000 if rows else None
            last_ts = rows[-1]["timestamp"] // 1000 if rows else None
            print(f"[dispersion] {venue} {base}: fetched={len(rows)} total={total} added={added} "
                  f"first={first_ts} last={last_ts}", flush=True)
            report.append({"leg": "dispersion", "venue": venue, "base": base,
                           "status": "ok" if rows else "empty", "rows": total, "added": added,
                           "first_ts": first_ts, "last_ts": last_ts})
            time.sleep(0.1)
    return report


def backfill_listing(since_ms: int) -> list[dict]:
    from research.screen_listing_short import genuine_listing_bases

    bases = genuine_listing_bases()
    print(f"[listing] {len(bases)} genuine listing bases from screen; Binance-only funding fetch", flush=True)
    report = []
    venue = "binance"
    try:
        ex = make_exchange(venue)
        ex.load_markets()
    except Exception as e:  # noqa: BLE001
        print(f"[listing] {venue}: load_markets FAILED {type(e).__name__}: {e}", flush=True)
        return [{"leg": "listing", "venue": venue, "base": b, "status": "venue_down",
                 "rows": 0, "added": 0} for b in bases]
    limit = PAGE_LIMIT[venue]
    for base in bases:
        sym = _perp_symbol(base)
        if sym not in ex.markets:
            report.append({"leg": "listing", "venue": venue, "base": base,
                           "status": "not_on_binance", "rows": 0, "added": 0})
            continue
        try:
            rows = fetch_funding_history_paginated(ex, sym, since_ms, limit=limit)
        except RuntimeError as e:
            print(f"[listing] {venue} {base}: FETCH ERROR {e}", flush=True)
            report.append({"leg": "listing", "venue": venue, "base": base,
                           "status": "fetch_error", "rows": 0, "added": 0, "err": str(e)})
            continue
        total, added = write_history_csv(venue, base, sym, rows) if rows else (0, 0)
        first_ts = rows[0]["timestamp"] // 1000 if rows else None
        last_ts = rows[-1]["timestamp"] // 1000 if rows else None
        print(f"[listing] {venue} {base}: fetched={len(rows)} total={total} added={added} "
              f"first={first_ts} last={last_ts}", flush=True)
        report.append({"leg": "listing", "venue": venue, "base": base,
                       "status": "ok" if rows else "empty", "rows": total, "added": added,
                       "first_ts": first_ts, "last_ts": last_ts})
        time.sleep(0.05)
    return report


def _probe_bases(venue: str = "bybit") -> tuple:
    """Bases of the SHADOW-PROBE universe, from the probes' own resolver.

    2026-08-20: the bundle-MR probe universe was widened on 2026-07-20 (frozen
    5-major basket -> spec-derived, ~144 symbols) but this backfill was never
    extended, so every probe tick warned "no usable realized funding history"
    for symbols NEITHER leg covered — the remedy the warning itself named could
    not work. Measured: 27 of 82 pending bybit probe rows (33%) had no funding
    history, so their after-cost net (what the promotion gate reads) was
    missing its funding component.

    Delegates to `bundle_mr_probe_agent.resolve_universe` — the SAME artifact
    the probes resolve — so a future universe edit propagates here
    automatically instead of silently drifting apart again.
    """
    from core.agents.bundle_mr_probe_agent import resolve_universe

    symbols, _skipped = resolve_universe(venue=venue)
    return tuple(dict.fromkeys(s.split("/")[0].strip().upper() for s in symbols))


def backfill_probe(since_ms: int, venue: str = "bybit") -> list[dict]:
    """Realized funding history for the shadow-probe universe."""
    bases = _probe_bases(venue)
    print(f"[probe] {len(bases)} probe-universe bases; venue={venue}", flush=True)
    report: list[dict] = []
    try:
        ex = make_exchange(venue)
        ex.load_markets()
    except Exception as e:  # noqa: BLE001
        print(f"[probe] {venue}: load_markets FAILED {type(e).__name__}: {e}", flush=True)
        return [{"leg": "probe", "venue": venue, "base": b, "status": "venue_down",
                 "rows": 0, "added": 0} for b in bases]
    limit = PAGE_LIMIT.get(venue, 200)
    for base in bases:
        sym = _perp_symbol(base)
        if sym not in ex.markets:
            report.append({"leg": "probe", "venue": venue, "base": base,
                           "status": "not_listed", "rows": 0, "added": 0})
            continue
        try:
            rows = fetch_funding_history_paginated(ex, sym, since_ms, limit=limit)
        except RuntimeError as e:
            print(f"[probe] {venue} {base}: FETCH ERROR {e}", flush=True)
            report.append({"leg": "probe", "venue": venue, "base": base,
                           "status": "fetch_error", "rows": 0, "added": 0, "err": str(e)})
            continue
        total, added = write_history_csv(venue, base, sym, rows) if rows else (0, 0)
        print(f"[probe] {venue} {base}: fetched={len(rows)} total={total} added={added}",
              flush=True)
        report.append({"leg": "probe", "venue": venue, "base": base,
                       "status": "ok" if rows else "empty", "rows": total, "added": added})
        time.sleep(0.05)
    return report


def _summarize(report: list[dict]) -> None:
    from collections import Counter

    by_leg: dict[str, Counter] = {}
    covered: dict[str, int] = {}
    for r in report:
        by_leg.setdefault(r["leg"], Counter())[r["status"]] += 1
        if r["status"] == "ok" and r.get("rows", 0) > 0:
            covered[r["leg"]] = covered.get(r["leg"], 0) + 1
    print("\n=== BACKFILL SUMMARY ===", flush=True)
    for leg, cnt in by_leg.items():
        print(f"[{leg}] statuses: {dict(cnt)}  covered_with_rows={covered.get(leg, 0)}", flush=True)
    print(f"history dir: {HISTORY_DIR}", flush=True)


def main() -> None:
    # Some scraped listing bases are non-ASCII (e.g. CJK junk tokens); Windows'
    # default cp1252 stdout cannot encode them. Force UTF-8 so a print never
    # aborts the harvest.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - best-effort
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=["dispersion", "listing", "probe", "all"],
                    default="all")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD floor (default per-leg)")
    args = ap.parse_args()

    report: list[dict] = []
    if args.leg in ("dispersion", "all"):
        since = _ms(args.since) if args.since else _ms(DISPERSION_SINCE)
        report += backfill_dispersion(since)
    if args.leg in ("listing", "all"):
        since = _ms(args.since) if args.since else _ms(LISTING_SINCE)
        report += backfill_listing(since)
    if args.leg in ("probe", "all"):
        since = _ms(args.since) if args.since else _ms(PROBE_SINCE)
        report += backfill_probe(since)
    _summarize(report)


if __name__ == "__main__":
    main()
