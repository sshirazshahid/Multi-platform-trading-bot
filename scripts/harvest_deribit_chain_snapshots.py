"""Deribit BTC option-chain snapshot archiver (C2 gamma-expiry prereg 33_*).

WHY: the C2 screen (33_prereg_c2_gamma_expiry, frozen 2026-07-24) needs point-in-time
per-instrument open_interest + mark_iv to compute ATM-OI percentile and net GEX at
07:30 UTC each day. Deribit's public API serves only the CURRENT chain (no free
history) and the existing skew harvester stores hourly RR25/ATM-IV means only —
raw rows must be archived forward. One-shot: each invocation appends ONE snapshot
line; Task Scheduler runs it 2x/day (07:30 + 19:30 UTC).

Read-only w.r.t. the bot; touches no order path; keyless public REST.

Run:  python scripts/harvest_deribit_chain_snapshots.py            (BTC)
      python scripts/harvest_deribit_chain_snapshots.py BTC ETH
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "deribit_chain_snapshots"
SUMMARY_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
KEEP_FIELDS = (
    "instrument_name",
    "open_interest",
    "mark_price",
    "mark_iv",
    "underlying_price",
    "underlying_index",
    "bid_price",
    "ask_price",
    "volume",
    "creation_timestamp",
)
MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_NAME_RE = re.compile(
    r"^(?P<ccy>[A-Z]+)-(?P<day>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})-(?P<strike>\d+(?:\.\d+)?)-(?P<typ>[CP])$"
)


def parse_instrument_name(name: str) -> dict | None:
    """``BTC-25JUL26-118000-C`` -> {currency, expiry_date, strike, opt_type} or None."""
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    mon = MONTHS.get(m.group("mon"))
    if mon is None:
        return None
    try:
        expiry = dt.date(2000 + int(m.group("yy")), mon, int(m.group("day")))
    except ValueError:
        return None
    return {
        "currency": m.group("ccy"),
        "expiry_date": expiry.isoformat(),
        "strike": float(m.group("strike")),
        "opt_type": "call" if m.group("typ") == "C" else "put",
    }


def atm_open_interest(
    rows: list[dict], spot: float, expiry_date: str, band_pct: float = 2.5
) -> float:
    """Sum open_interest of instruments at ``expiry_date`` with strike within
    ±band_pct% of spot (the prereg's frozen ATM definition)."""
    if spot <= 0:
        return 0.0
    lo, hi = spot * (1 - band_pct / 100.0), spot * (1 + band_pct / 100.0)
    total = 0.0
    for row in rows:
        meta = parse_instrument_name(row.get("instrument_name", ""))
        if not meta or meta["expiry_date"] != expiry_date:
            continue
        if lo <= meta["strike"] <= hi:
            total += float(row.get("open_interest") or 0.0)
    return total


def fetch_chain(currency: str, retries: int = 3, timeout: int = 30) -> list[dict]:
    """Fetch the full option chain book summary; raise on final failure."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                SUMMARY_URL,
                params={"currency": currency, "kind": "option"},
                timeout=timeout,
            )
            resp.raise_for_status()
            rows = resp.json().get("result") or []
            if not rows:
                raise RuntimeError(f"{currency}: empty chain result")
            return [{k: r.get(k) for k in KEEP_FIELDS} for r in rows]
        except Exception as exc:  # noqa: BLE001 — fail-soft with retry
            last_exc = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"{currency}: chain fetch failed after {retries} tries: {last_exc}")


def append_snapshot(out_dir: Path, currency: str, rows: list[dict], now: dt.datetime) -> Path:
    """Append one JSONL snapshot line to the monthly archive file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{currency}_{now:%Y-%m}.jsonl"
    line = json.dumps(
        {
            "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "unix_ms": int(now.timestamp() * 1000),
            "currency": currency,
            "n_instruments": len(rows),
            "rows": rows,
        },
        separators=(",", ":"),
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def main() -> int:
    currencies = [c.upper() for c in sys.argv[1:]] or ["BTC"]
    now = dt.datetime.now(dt.timezone.utc)
    failures = 0
    for ccy in currencies:
        try:
            rows = fetch_chain(ccy)
            path = append_snapshot(OUT_DIR, ccy, rows, now)
            spots = [float(r.get("underlying_price") or 0) for r in rows]
            spots = [s for s in spots if s > 0]
            spot = sorted(spots)[len(spots) // 2] if spots else 0.0
            today_atm = atm_open_interest(rows, spot, now.date().isoformat())
            print(
                f"[deribit_snap] {ccy}: {len(rows)} instruments, spot~{spot:.0f}, "
                f"today-expiry ATM OI={today_atm:.1f} -> {path.name}"
            )
        except Exception as exc:  # noqa: BLE001 — one currency failing must not kill others
            failures += 1
            print(f"[deribit_snap] ERROR {ccy}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
