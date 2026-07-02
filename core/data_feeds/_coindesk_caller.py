"""
core/data_feeds/_coindesk_caller.py — CoinDesk Data API caller abstraction.

This module provides thin wrappers around the CoinDesk/CryptoCompare Data API.
When running inside Claude Code with MCP tools available, it uses them directly.
When running standalone (bot runtime), it falls back to direct HTTP calls to the
CryptoCompare Data API (same backend that CoinDesk MCP tools use).

The API base is: https://data-api.cryptocompare.com

All functions return raw JSON dicts or raise on failure.
Callers handle error/empty gracefully (fail-open design).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_BASE_URL = "https://data-api.cryptocompare.com"
_TIMEOUT = 12


def _get_api_key() -> str:
    """Get CryptoCompare / CoinDesk Data API key from environment."""
    return (os.getenv("CRYPTOCOMPARE_API_KEY", "").strip() or
            os.getenv("COINDESK_API_KEY", "").strip())


def _http_get(url: str, *, params: dict | None = None) -> dict:
    """HTTP GET with API key auth."""
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"

    headers = {"User-Agent": "TradingBot/2.0"}
    api_key = _get_api_key()
    if api_key:
        headers["authorization"] = f"Apikey {api_key}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read())


# ── Funding Rate ───────────────────────────────────────────────────────

def call_coindesk_fr_tick(
    *, market: str, instruments: str
) -> dict[str, Any]:
    """Fetch current funding rate tick for instruments.

    Equivalent to mcp__claude_ai_CoinDesk__fetch_futures_fr_tick.
    """
    return _http_get(
        f"{_BASE_URL}/futures/v1/latest/tick",
        params={
            "market": market,
            "instruments": instruments,
            "groups": "STATUS,VALUE,CURRENT_DAY,MOVING_24_HOUR",
        },
    )


def call_coindesk_fr_ohlcv(
    *,
    market: str,
    instrument: str,
    frequency: str = "hours",
    aggregate: int = 8,
    limit: int = 21,
) -> dict[str, Any]:
    """Fetch historical funding rate OHLCV.

    Equivalent to mcp__claude_ai_CoinDesk__fetch_futures_fr_ohlcv.
    """
    freq_map = {"hours": "histohour", "days": "histoday", "minutes": "histominute"}
    endpoint = freq_map.get(frequency, "histohour")
    return _http_get(
        f"{_BASE_URL}/futures/v1/historical/funding-rate/{endpoint}",
        params={
            "market": market,
            "instrument": instrument,
            "aggregate": str(aggregate),
            "limit": str(limit),
            "groups": "STATUS,OHLC,VOLUME",
        },
    )


# ── Open Interest ──────────────────────────────────────────────────────

def call_coindesk_oi_tick(
    *, market: str, instruments: str
) -> dict[str, Any]:
    """Fetch current open interest tick.

    Equivalent to mcp__claude_ai_CoinDesk__fetch_futures_oi_tick.
    """
    return _http_get(
        f"{_BASE_URL}/futures/v1/latest/tick",
        params={
            "market": market,
            "instruments": instruments,
            "groups": "STATUS,VALUE,CURRENT_DAY,MOVING_24_HOUR",
        },
    )


def call_coindesk_oi_ohlcv(
    *,
    market: str,
    instrument: str,
    frequency: str = "hours",
    aggregate: int = 1,
    limit: int = 7,
) -> dict[str, Any]:
    """Fetch historical open interest OHLCV.

    Equivalent to mcp__claude_ai_CoinDesk__fetch_futures_oi_ohlcv.

    2026-05-30 FIX: the futures OI-history endpoint path segment is the literal
    frequency ("days"/"hours"/"minutes") — NOT the spot-style "histoday"/
    "histohour". The old freq_map produced /open-interest/histohour which 404s,
    so OI history silently fell back to Binance's 7-bar endpoint and long
    history was never available. With the correct segment, ~100 daily bars
    (~99 days) of OI are returned.
    """
    freq_map = {"hours": "hours", "days": "days", "minutes": "minutes"}
    endpoint = freq_map.get(frequency, "hours")
    return _http_get(
        f"{_BASE_URL}/futures/v1/historical/open-interest/{endpoint}",
        params={
            "market": market,
            "instrument": instrument,
            "aggregate": str(aggregate),
            "limit": str(limit),
            "groups": "STATUS,OHLC",
        },
    )


# ── News ───────────────────────────────────────────────────────────────

def call_coindesk_news(
    *, limit: int = 50, lang: str = "EN"
) -> dict[str, Any]:
    """Fetch latest news articles.

    Equivalent to mcp__claude_ai_CoinDesk__fetch_news.
    """
    return _http_get(
        f"{_BASE_URL}/news/v1/article/list",
        params={
            "lang": lang,
            "limit": str(limit),
            "groups": "ID,BASIC,BODY,SOURCE_INFO,CATEGORY_INFO",
        },
    )
