"""Spot Bitcoin ETF daily net-flow feed (Farside) — WS3b new-information-source.

WHY THIS IS THE RIGHT KIND OF SOURCE: ETF net flows are NON-price, institutional-demand
*quantity* data — orthogonal to the OHLCV/candlestick substrate that every prior screen
(443-alpha sweep, funding, seasonality, Kronos) and the live MCP brain already exhausted.
A faint edge *could* live here precisely because it is not in the charts. Free, daily,
history since the Jan-2024 US spot-BTC-ETF launch. Source: farside.co.uk.

DEPENDENCY DISCIPLINE: the bot runtime must not add heavy HTML parsers, so this module
uses stdlib `html.parser` only (no lxml/bs4) and imports `requests` lazily inside the
fetch function. Importing this module is cheap + side-effect-free.

HONEST PRIOR: expected NO_EDGE (the standing instrument-/source-agnostic result), and even
a real edge is capital-capped at ~$1,300. This is a cheap falsification, screened through
the same frozen time-series gate as the stablecoin screen, NOT a deployment.
"""
from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser

import pandas as pd

FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


class _TableRows(HTMLParser):
    """Collect every <tr> as a list of cell text strings (stdlib, no lxml/bs4)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._in_cell = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._in_cell = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._row.append("".join(self._buf).strip())  # type: ignore[union-attr]
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)


def fetch_etf_flow_html(url: str = FARSIDE_URL, timeout: int = 30) -> str:
    """Fetch the Farside all-data page (lazy `requests` import). Raises on network error."""
    import requests

    return requests.get(url, headers=_UA, timeout=timeout).text


def _to_float(cell: str) -> float | None:
    """Farside cell -> float $M. Handles '1,234.5', '(123.4)' negatives, '-'/'' blanks."""
    x = (cell or "").replace(",", "").replace("$", "").strip()
    if x in ("", "-", "—", "N/A"):
        return None
    neg = x.startswith("(") and x.endswith(")")
    x = x.strip("()")
    try:
        v = float(x)
    except ValueError:
        return None
    return -v if neg else v


def _parse_date(cell: str):
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d-%b-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(cell.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_etf_flow_series(html: str) -> pd.Series:
    """Daily TOTAL net flow ($M), index = UTC-normalized date. Empty Series on parse failure.

    Finds the header row containing both 'Date' and 'Total', then reads the Total column
    from each subsequent data row. Non-date rows (e.g. 'Average'/'Maximum' summary footers)
    naturally drop out because their first cell doesn't parse as a date.
    """
    p = _TableRows()
    p.feed(html)

    date_col = total_col = header_idx = None
    for i, row in enumerate(p.rows):
        low = [c.strip().lower() for c in row]
        if "date" in low and "total" in low:
            header_idx = i
            date_col = low.index("date")
            total_col = low.index("total")
            break
    if header_idx is None:
        return pd.Series(dtype=float)

    recs: dict[pd.Timestamp, float] = {}
    for row in p.rows[header_idx + 1:]:
        if len(row) <= max(date_col, total_col):
            continue
        dt = _parse_date(row[date_col])
        if dt is None:
            continue
        v = _to_float(row[total_col])
        if v is None:
            continue
        recs[pd.Timestamp(dt, tz="UTC").normalize()] = v

    if not recs:
        return pd.Series(dtype=float)
    s = pd.Series(recs).sort_index()
    return s[~s.index.duplicated(keep="last")]


def etf_flow_signal(flows: pd.Series, window: int) -> pd.Series:
    """Causal rolling-sum net flow over `window` trading days (recent cumulative demand).

    Uses only data up to and including each date (pandas rolling is backward-looking), so it
    is look-ahead-free by construction. The screen applies an ADDITIONAL 1-trading-day lag
    before aligning to BTC returns, because Farside publishes day-T flow after the US close
    (i.e. it is only known at ~T+1 UTC, after BTC's day-T 00:00-UTC close).
    """
    return flows.rolling(window, min_periods=max(2, window // 2)).sum().dropna()
