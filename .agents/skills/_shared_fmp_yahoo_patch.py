"""Shared FMP helpers for the equity skills.

Two independent concerns live here:

1. Yahoo Finance chart fallback for FMP-plan-restricted symbols — used by the
   Druckenmiller upstream skill FMP clients when stable EOD returns 402.
2. ``resolve_fmp_key`` — locates ``FMP_API_KEY`` without requiring the operator
   to export anything (the key lives in the repo-root ``.env``).

Not a public API — imported by sibling skill scripts via sys.path injection.
Nothing here is imported by the crypto trading bot.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote as url_quote

import requests

try:  # optional dependency — a manual parser covers its absence
    from dotenv import dotenv_values as _dotenv_values
except ImportError:  # pragma: no cover — dotenv is present in this repo's venv
    _dotenv_values = None

FMP_ENV_VAR = "FMP_API_KEY"


def find_dotenv(start: Any = None) -> Optional[Path]:
    """Return the nearest ``.env`` walking up from *start* (default: this file).

    No absolute path is hardcoded: the search climbs from the module (or an
    explicit *start* file/directory) to the filesystem root and stops at the
    first ``.env`` it can see.  Returns ``None`` when there is none.
    """
    try:
        base = Path(start).resolve() if start is not None else Path(__file__).resolve().parent
    except OSError:  # pragma: no cover — unresolvable path
        return None
    if base.is_file():
        base = base.parent
    for directory in (base, *base.parents):
        candidate = directory / ".env"
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover — permission-denied ancestor
            continue
    return None


def _dotenv_lookup(path: Path, name: str) -> Optional[str]:
    """Read a single variable out of *path*. Never raises, never logs."""
    if _dotenv_values is not None:
        try:
            return (_dotenv_values(str(path)) or {}).get(name) or None
        except (OSError, UnicodeDecodeError, ValueError):
            return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value or None
    return None


def resolve_fmp_key(
    api_key: Optional[str] = None,
    *,
    start: Any = None,
    env_var: str = FMP_ENV_VAR,
) -> Optional[str]:
    """Resolve the FMP API key: explicit argument -> os.environ -> repo ``.env``.

    An already-set ``os.environ`` value always wins over ``.env``; a value read
    from ``.env`` is published back into ``os.environ`` with ``setdefault`` so
    child processes inherit it without ever overriding the caller.  Returns
    ``None`` when nothing is found, leaving each caller's own "key required"
    error untouched.  The value is never printed, logged, or embedded in an
    exception message.
    """
    if api_key:
        return api_key
    existing = os.environ.get(env_var)
    if existing:
        return existing
    path = find_dotenv(start)
    if path is None:
        return None
    value = _dotenv_lookup(path, env_var)
    if not value:
        return None
    os.environ.setdefault(env_var, value)
    return value


def is_eod_bar(item: Any) -> bool:
    """True when ``item`` looks like one FMP/Yahoo daily bar (not an error blob).

    Stable ``/historical-price-eod/full`` returns a bare JSON array of objects
    with ``date`` + ``close`` (and OHLCV). Dividend-adjusted uses ``adjClose``.
    Error / wrong-shape payloads are often non-empty lists of non-bars
    (``[1,2,3]``, ``[{"Error Message": ...}]``) — those must NOT short-circuit
    endpoint fallback.
    """
    if not isinstance(item, dict):
        return False
    date = item.get("date")
    if not date or not isinstance(date, str):
        return False
    for key in ("close", "adjClose", "price"):
        val = item.get(key)
        if val is None:
            continue
        try:
            float(val)
            return True
        except (TypeError, ValueError):
            continue
    return False


def looks_like_eod_bar_list(data: Any, *, sample_size: int = 5) -> bool:
    """True when ``data`` is a non-empty list of EOD bar dicts."""
    if not isinstance(data, list) or not data:
        return False
    sample = data[: min(sample_size, len(data))]
    return all(is_eod_bar(x) for x in sample)


def yahoo_historical(symbol: str, days: int = 365, timeout: int = 30) -> Optional[dict]:
    """Return FMP-shaped historical dict (newest first) or None."""
    # Yahoo accepts ^GSPC / ^IXIC; quote caret for URL path safety
    path_sym = url_quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{path_sym}"
    try:
        response = requests.get(
            url,
            params={"interval": "1d", "range": "5y"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return None
        res = result[0]
        timestamps = res.get("timestamp") or []
        quote_blocks = (res.get("indicators") or {}).get("quote") or [{}]
        q0 = quote_blocks[0] if quote_blocks else {}
        adj_list = ((res.get("indicators") or {}).get("adjclose") or [{}])
        adj = (adj_list[0] or {}).get("adjclose") if adj_list else None
        opens = q0.get("open") or []
        highs = q0.get("high") or []
        lows = q0.get("low") or []
        closes = q0.get("close") or []
        volumes = q0.get("volume") or []

        bars = []
        for i in range(len(timestamps) - 1, -1, -1):
            close = closes[i] if i < len(closes) else None
            if close is None:
                continue
            day = dt.datetime.fromtimestamp(timestamps[i], tz=dt.timezone.utc).strftime(
                "%Y-%m-%d"
            )
            adj_close = adj[i] if adj and i < len(adj) and adj[i] is not None else close
            bars.append(
                {
                    "date": day,
                    "open": opens[i] if i < len(opens) else close,
                    "high": highs[i] if i < len(highs) else close,
                    "low": lows[i] if i < len(lows) else close,
                    "close": close,
                    "volume": volumes[i] if i < len(volumes) else 0,
                    "adjClose": adj_close,
                }
            )
            if len(bars) >= days:
                break
        if not bars:
            return None
        return {"symbol": symbol, "historical": bars}
    except (requests.RequestException, KeyError, TypeError, ValueError, IndexError):
        return None
