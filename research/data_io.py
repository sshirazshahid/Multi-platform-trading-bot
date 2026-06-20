"""data_io.py — tiny, dependency-light loaders so the research labs
(dca_rebalance_lab, funding_carry_lab) can run on REAL data instead of synthetic.

Pure stdlib (csv) — no pandas/ccxt — so it is trivially testable and safe to run
anywhere. Three jobs:
  1. load close-price series from a CSV,
  2. load a funding-rate series from a CSV,
  3. convert exchange/MCP candle payloads (ccxt list-of-lists or Crypto.com /
     CoinDesk dict rows) into a chronological list of closes.

Why this matters: the labs are honest only when fed real, multi-regime,
out-of-sample data. This is the bridge — hand it a CSV (or MCP-pulled candles)
and pipe the result straight into a lab.
"""

from __future__ import annotations

import csv
from pathlib import Path


def load_closes_csv(path: str | Path, close_col: str = "close") -> list[float]:
    """Read a CSV with a header row and return the close column as floats,
    in file order. Raises if the column is missing or no rows parse."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")
    out: list[float] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or close_col not in reader.fieldnames:
            raise ValueError(f"column '{close_col}' not found; have {reader.fieldnames}")
        for row in reader:
            val = row.get(close_col)
            if val is None or val == "":
                continue
            out.append(float(val))
    if not out:
        raise ValueError(f"no numeric rows in '{close_col}'")
    return out


def load_funding_csv(path: str | Path, rate_col: str = "rate") -> list[float]:
    """Read a funding-rate CSV (one rate per settlement) as floats, file order."""
    return load_closes_csv(path, close_col=rate_col)


def closes_from_candles(candles, key: str = "close", chronological: bool = True) -> list[float]:
    """Convert a candle payload to a list of close prices.

    Accepts:
      - list of dicts with a `key` field (Crypto.com / CoinDesk MCP rows), and an
        optional 'timestamp'/'t'/'ts' used to sort ascending when chronological,
      - list/tuple rows (ccxt OHLCV: [ts, open, high, low, close, volume]) where
        close is index 4 and ts is index 0.

    Exchanges often return newest-first; chronological=True sorts oldest-first so
    DCA/return math runs forward in time.
    """
    if not candles:
        raise ValueError("candles is empty")
    rows: list[tuple[float, float]] = []  # (ts_for_sort, close)
    for i, c in enumerate(candles):
        if isinstance(c, dict):
            if key not in c:
                raise ValueError(f"candle dict missing '{key}': {list(c)[:6]}")
            close = float(c[key])
            ts = c.get("timestamp") or c.get("ts") or c.get("t") or i
            ts = float(ts) if not isinstance(ts, str) else _ts_str(ts, i)
        elif isinstance(c, (list, tuple)):
            if len(c) < 5:
                raise ValueError(f"ohlcv row too short: {c}")
            close = float(c[4])
            ts = float(c[0])
        else:
            raise ValueError(f"unsupported candle type: {type(c)}")
        rows.append((ts, close))
    if chronological:
        rows.sort(key=lambda r: r[0])
    return [c for _, c in rows]


def _ts_str(s: str, fallback: int) -> float:
    """Best-effort parse of an ISO/epoch-ish timestamp string for sorting."""
    s = s.strip()
    try:
        return float(s)  # epoch as string
    except ValueError:
        pass
    # ISO 8601 like 2026-06-20T00:00:00Z -> compare lexicographically is fine for
    # sorting, but we need a number; map to ordinal-ish via simple digit strip.
    digits = "".join(ch for ch in s if ch.isdigit())
    return float(digits) if digits else float(fallback)


def write_closes_csv(
    path: str | Path, closes, timestamps=None, close_col: str = "close", ts_col: str = "timestamp"
) -> Path:
    """Write a simple closes CSV (optionally with timestamps). Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if timestamps is not None:
            w.writerow([ts_col, close_col])
            for t, c in zip(timestamps, closes):
                w.writerow([t, c])
        else:
            w.writerow([close_col])
            for c in closes:
                w.writerow([c])
    return path
