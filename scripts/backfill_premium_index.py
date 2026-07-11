"""Premium-index (perp-vs-index basis) 15m history backfill — keyless, public REST.

Unblocks screen 08a (funding-settlement-window timing). Fetches the exchange's own
premium-index klines — the exact quantity funding is computed from — for the frozen
15-coin F1 universe (`research.funding_carry_lab.F1_EXPANDED_UNIVERSE_2026_07_05`):

  * Binance: GET https://fapi.binance.com/fapi/v1/premiumIndexKlines
      symbol={BASE}USDT interval=15m, forward-paginated from startTime=0
      (history reaches 2019-12-24 for BTCUSDT — audit 08d verified).
  * Bybit:   GET https://api.bybit.com/v5/market/premium-index-price-kline
      category=linear symbol={BASE}USDT interval=15, backward-paginated via `end`
      (per audit 08d finding C1-a: the pre-registered bybit arm needs bybit basis).

Values are premium FRACTIONS (e.g. -0.00044 = -4.4 bps), stored as-is.

Output: one parquet per venue-symbol under data/premium_index/
  {venue}_{BASE}_15m.parquet  columns: ts (bar OPEN, unix seconds), open, high,
  low, close (premium fractions), venue, symbol.
Idempotent: re-runs merge + dedupe on ts. Degrades gracefully: a symbol/venue that
errors or has no history is reported and skipped — never fabricated.

Usage:
    venv\\Scripts\\python.exe scripts\\backfill_premium_index.py
    venv\\Scripts\\python.exe scripts\\backfill_premium_index.py --venue binance --bases BTC ETH
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "data", "premium_index")

BINANCE_URL = "https://fapi.binance.com/fapi/v1/premiumIndexKlines"
BYBIT_URL = "https://api.bybit.com/v5/market/premium-index-price-kline"
BINANCE_LIMIT = 1500
BYBIT_LIMIT = 1000
BAR_MS = 15 * 60 * 1000
# polite throttles (binance premiumIndexKlines weight=10 at limit 1500; 2400/min budget)
BINANCE_SLEEP = 0.35
BYBIT_SLEEP = 0.25


# ── Pure parsers (unit-tested offline) ───────────────────────────────────────
def parse_binance_klines(raw: list) -> list[tuple[int, float, float, float, float]]:
    """Binance kline rows -> [(ts_open_sec, open, high, low, close)].

    Row: [openTime_ms, open, high, low, close, vol, closeTime_ms, ...] with string
    numerics. Rows missing fields are skipped, never guessed."""
    out = []
    for row in raw or []:
        try:
            out.append(
                (int(row[0]) // 1000, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            )
        except (TypeError, ValueError, IndexError):
            continue
    return out


def parse_bybit_klines(result: dict) -> list[tuple[int, float, float, float, float]]:
    """Bybit v5 premium-index-price-kline result -> ascending [(ts_open_sec, o,h,l,c)].

    result["list"] rows: [startTime_ms_str, open, high, low, close], newest first."""
    out = []
    for row in (result or {}).get("list") or []:
        try:
            out.append(
                (int(row[0]) // 1000, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            )
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def merge_bars_parquet(
    path: str, bars: list[tuple[int, float, float, float, float]], venue: str, symbol: str
) -> tuple[int, int]:
    """Idempotently merge bars into ``path``. Returns (total_after, newly_added)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close"])
    new["ts"] = new["ts"].astype("int64")
    new["venue"] = venue
    new["symbol"] = symbol
    if os.path.exists(path):
        old = pd.read_parquet(path)
        before = set(old["ts"].astype("int64").tolist())
        combined = pd.concat([old, new], ignore_index=True)
    else:
        before = set()
        combined = new
    combined = combined.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    combined.to_parquet(path, index=False)
    added = len({int(t) for t in new["ts"].tolist()} - before)
    return len(combined), added


# ── Fetchers ─────────────────────────────────────────────────────────────────
def _get_json(url: str, params: dict, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:  # rate-limited: back off, retry
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - retry then surface
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


def fetch_binance_premium(base: str, max_pages: int = 400) -> list[tuple]:
    """Forward-paginate premiumIndexKlines from the symbol's first bar to now."""
    sym = f"{base}USDT"
    bars: dict[int, tuple] = {}
    cursor = 0
    for _ in range(max_pages):
        raw = _get_json(
            BINANCE_URL,
            {"symbol": sym, "interval": "15m", "startTime": cursor, "limit": BINANCE_LIMIT},
        )
        page = parse_binance_klines(raw)
        if not page:
            break
        for b in page:
            bars[b[0]] = b
        new_cursor = (page[-1][0] * 1000) + BAR_MS
        if new_cursor <= cursor:
            break
        cursor = new_cursor
        time.sleep(BINANCE_SLEEP)
        if len(page) < BINANCE_LIMIT:
            break
    return [bars[t] for t in sorted(bars)]


def fetch_bybit_premium(base: str, max_pages: int = 500) -> list[tuple]:
    """Backward-paginate bybit premium-index klines from now to first available."""
    sym = f"{base}USDT"
    bars: dict[int, tuple] = {}
    end = int(time.time() * 1000)
    for _ in range(max_pages):
        data = _get_json(
            BYBIT_URL,
            {
                "category": "linear",
                "symbol": sym,
                "interval": "15",
                "end": end,
                "limit": BYBIT_LIMIT,
            },
        )
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit retCode={data.get('retCode')} {data.get('retMsg')}")
        page = parse_bybit_klines(data.get("result") or {})
        if not page:
            break
        for b in page:
            bars[b[0]] = b
        page_min_ms = page[0][0] * 1000
        if page_min_ms >= end:
            break
        end = page_min_ms - 1
        time.sleep(BYBIT_SLEEP)
        if len(page) < BYBIT_LIMIT:
            break
    return [bars[t] for t in sorted(bars)]


def main() -> None:
    from research.funding_carry_lab import F1_EXPANDED_UNIVERSE_2026_07_05

    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=["binance", "bybit", "all"], default="all")
    ap.add_argument(
        "--bases", nargs="*", default=None, help="override bases (default: 15-coin F1 universe)"
    )
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip a venue-base whose parquet already exists (parallel-worker coordination)",
    )
    args = ap.parse_args()

    bases = args.bases or [s.split("/")[0] for s in F1_EXPANDED_UNIVERSE_2026_07_05]
    venues = ["binance", "bybit"] if args.venue == "all" else [args.venue]
    report = []
    for venue in venues:
        for base in bases:
            if args.skip_existing and os.path.exists(
                os.path.join(OUT_DIR, f"{venue}_{base}_15m.parquet")
            ):
                print(f"[{venue}] {base}: exists, skipped", flush=True)
                report.append((venue, base, "skipped_existing", 0, 0))
                continue
            try:
                bars = (
                    fetch_binance_premium(base) if venue == "binance" else fetch_bybit_premium(base)
                )
            except RuntimeError as e:
                print(f"[{venue}] {base}: FETCH ERROR {e}", flush=True)
                report.append((venue, base, "fetch_error", 0, 0))
                continue
            if not bars:
                print(f"[{venue}] {base}: no history served", flush=True)
                report.append((venue, base, "empty", 0, 0))
                continue
            path = os.path.join(OUT_DIR, f"{venue}_{base}_15m.parquet")
            total, added = merge_bars_parquet(path, bars, venue, f"{base}USDT")
            print(
                f"[{venue}] {base}: fetched={len(bars)} total={total} added={added} "
                f"first={bars[0][0]} last={bars[-1][0]}",
                flush=True,
            )
            report.append((venue, base, "ok", total, added))
    print("\n=== PREMIUM-INDEX BACKFILL SUMMARY ===", flush=True)
    for venue, base, status, total, added in report:
        print(f"{venue:8s} {base:6s} {status:12s} rows={total} added={added}", flush=True)


if __name__ == "__main__":
    main()
