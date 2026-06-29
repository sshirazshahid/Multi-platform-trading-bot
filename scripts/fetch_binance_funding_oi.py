#!/usr/bin/env python3
"""Download keyless Binance USDT-M funding-rate + open-interest history.

Pulls two complementary local datasets for backtests from Binance's **public**
futures market-data endpoints (no API key required), writing one CSV per
symbol under ``data/funding_oi/``:

* ``<SYM>_funding.csv`` — full funding-rate history (back to ~2019, 8h cadence)
  via ``/fapi/v1/fundingRate`` (ccxt ``fetch_funding_rate_history``).
* ``<SYM>_oi.csv`` — open-interest history via ``/futures/data/openInterestHist``
  (ccxt ``fetch_open_interest_history``).

Distinct from ``scripts/harvest_derivs.py``, which snapshots *current* derivs
values forward into ``data/derivs_history.jsonl``; this script backfills the
historical *time-series* into CSV.

Caveats (documented limits, NOT bugs):
  * **OI depth is capped at ~30 days by the exchange.** Older ``startTime``
    values are rejected with ``-1130``; this script clamps OI requests inside
    the live window, so the OI CSV only ever spans the most recent ~30 days.
    Run it periodically (e.g. daily) to accumulate a longer local series.
  * **Survivorship.** Only currently-listed perps are reachable via the live
    API; delisted symbols return nothing.
  * Output lands in ``data/`` (git-ignored) — never commit these CSVs.

Usage:
    python scripts/fetch_binance_funding_oi.py
    python scripts/fetch_binance_funding_oi.py --symbols BTC ETH SOL --oi-period 4h
    python scripts/fetch_binance_funding_oi.py --since 2023-01-01 --out-dir data/funding_oi
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# --- Defaults ---------------------------------------------------------------
# The bot's long-only TSMOM majors (see SIGNAL_SOURCE=tsmom universe).
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
DEFAULT_SINCE = "2019-01-01"  # clamped per-endpoint; funding earliest is ~2019-09
DEFAULT_OI_PERIOD = "1h"
DEFAULT_OUT_DIR = "data/funding_oi"

OI_WINDOW_DAYS = 30  # exchange-enforced max look-back for openInterestHist
_MS_PER_DAY = 86_400_000


def to_perp(symbol: str) -> str:
    """Normalise a bare base ('BTC') or pair to the ccxt USDT-M perp id."""
    s = symbol.upper().strip()
    if ":" in s:
        return s
    if s.endswith("/USDT"):
        return f"{s}:USDT"
    if s.endswith("USDT"):
        s = s[: -len("USDT")]
    return f"{s}/USDT:USDT"


def dedup_by_timestamp(rows: list[dict]) -> list[dict]:
    """Return rows uniquified by 'timestamp' and sorted ascending.

    Pure helper (no network) so pagination/merge logic is unit-testable.
    Later duplicates win, which is harmless since the value for a given
    funding/OI timestamp is stable.
    """
    by_ts: dict[int, dict] = {}
    for r in rows:
        ts = r.get("timestamp")
        if ts is None:
            continue
        by_ts[ts] = r
    return [by_ts[ts] for ts in sorted(by_ts)]


def fetch_funding(ex, perp: str, since_ms: int) -> list[dict]:
    """Paginate full funding-rate history from ``since_ms`` to now."""
    out: list[dict] = []
    since = since_ms
    now = ex.milliseconds()
    while since < now:
        batch = ex.fetch_funding_rate_history(perp, since=since, limit=1000)
        if not batch:
            break
        out.extend(batch)
        last = batch[-1]["timestamp"]
        if last is None or last < since:
            break
        since = last + 1
        if len(batch) < 1000:
            break  # reached the tip
        time.sleep(ex.rateLimit / 1000.0)
    return dedup_by_timestamp(out)


def fetch_oi(ex, perp: str, period: str) -> list[dict]:
    """Paginate OI history across the full available ~30-day window."""
    out: list[dict] = []
    now = ex.milliseconds()
    # Stay strictly inside the 30-day window or Binance returns -1130.
    since = now - (OI_WINDOW_DAYS * _MS_PER_DAY) + 60_000
    while since < now:
        batch = ex.fetch_open_interest_history(perp, period, since=since, limit=500)
        if not batch:
            break
        out.extend(batch)
        last = batch[-1]["timestamp"]
        if last is None or last < since:
            break
        since = last + 1
        if len(batch) < 500:
            break
        time.sleep(ex.rateLimit / 1000.0)
    return dedup_by_timestamp(out)


def read_existing_rows(path: Path, columns: list[tuple[str, str]]) -> list[dict]:
    """Read an existing CSV back into row dicts keyed by ccxt row_key.

    Reverses the (header, row_key) column map so previously-written rows can be
    merged with a fresh fetch. Returns [] if the file is absent. Rows whose
    timestamp won't parse to int are skipped (defensive against partial writes).
    """
    if not path.exists():
        return []
    keymap = {header: key for header, key in columns}
    out: list[dict] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            row = {keymap.get(h, h): v for h, v in rec.items()}
            try:
                row["timestamp"] = int(row.get("timestamp"))
            except (TypeError, ValueError):
                continue
            out.append(row)
    return out


def write_csv(
    path: Path, rows: list[dict], columns: list[tuple[str, str]], merge: bool = False
) -> int:
    """Write ``rows`` to ``path`` selecting (csv_header, row_key) columns.

    When ``merge`` is set and ``path`` already exists, previously-written rows
    are unioned with ``rows`` by timestamp (fresh rows win on overlap). This is
    what lets a periodic run *accumulate* OI history beyond the exchange's
    ~30-day live window instead of overwriting it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if merge:
        rows = dedup_by_timestamp(read_existing_rows(path, columns) + list(rows))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([h for h, _ in columns])
        for r in rows:
            writer.writerow([r.get(k) for _, k in columns])
    return len(rows)


def _span(rows: list[dict]) -> str:
    if not rows:
        return "(empty)"
    return f"{rows[0].get('datetime')} -> {rows[-1].get('datetime')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Bases or pairs (default: TSMOM majors)",
    )
    parser.add_argument(
        "--since", default=DEFAULT_SINCE, help="Funding history start date YYYY-MM-DD"
    )
    parser.add_argument(
        "--oi-period", default=DEFAULT_OI_PERIOD, help="OI sampling period (5m,15m,1h,4h,...)"
    )
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--no-oi", action="store_true", help="Skip open interest")
    parser.add_argument("--no-funding", action="store_true", help="Skip funding")
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Overwrite CSVs instead of accumulating (merge is on by default)",
    )
    args = parser.parse_args(argv)
    merge = not args.no_merge

    try:
        import ccxt
    except ImportError:
        print("ccxt is required: pip install ccxt", file=sys.stderr)
        return 2

    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    since_ms = ex.parse8601(f"{args.since}T00:00:00Z")
    out_dir = Path(args.out_dir)

    print(f"Binance USDT-M public download -> {out_dir}/  (keyless)")
    print(f"  symbols={args.symbols}  funding_since={args.since}  oi_period={args.oi_period}")
    print(f"  NOTE: OI is exchange-capped at ~{OI_WINDOW_DAYS}d look-back.\n")

    rc = 0
    for sym in args.symbols:
        perp = to_perp(sym)
        base = perp.split("/")[0]
        if not args.no_funding:
            try:
                fr = fetch_funding(ex, perp, since_ms)
                n = write_csv(
                    out_dir / f"{base}_funding.csv",
                    fr,
                    [
                        ("timestamp", "timestamp"),
                        ("datetime", "datetime"),
                        ("funding_rate", "fundingRate"),
                    ],
                    merge=merge,
                )
                print(f"  {base} funding: {n:>6} rows  {_span(fr)}")
            except Exception as exc:  # noqa: BLE001 - report and continue per symbol
                print(f"  {base} funding: FAILED ({type(exc).__name__}: {exc})")
                rc = 1
        if not args.no_oi:
            try:
                oi = fetch_oi(ex, perp, args.oi_period)
                n = write_csv(
                    out_dir / f"{base}_oi.csv",
                    oi,
                    [
                        ("timestamp", "timestamp"),
                        ("datetime", "datetime"),
                        ("open_interest_base", "openInterestAmount"),
                        ("open_interest_usd", "openInterestValue"),
                    ],
                    merge=merge,
                )
                print(f"  {base} OI:      {n:>6} rows  {_span(oi)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {base} OI:      FAILED ({type(exc).__name__}: {exc})")
                rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
