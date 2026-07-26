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
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import CancelledError
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

_BASE_URL = "https://data-api.cryptocompare.com"
_TIMEOUT = 12.0
_DEFAULT_CALL_DEADLINE_SECONDS = 12.0
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.25
_MAX_BACKOFF_SECONDS = 2.0
_MAX_RETRY_AFTER_SECONDS = 5.0
_HOST_MIN_INTERVAL_SECONDS = 0.2
_MAX_THROTTLE_WAIT_SECONDS = 2.0
_MAX_TRACKED_HOSTS = 32

_HOST_THROTTLE_LOCK = threading.Lock()
_HOST_NEXT_REQUEST: dict[str, float] = {}
_CALL_CONTEXT = threading.local()


class _DeadlineExceeded(TimeoutError):
    """Raised when an HTTP call cannot complete inside its call budget."""


def _get_api_key() -> str:
    """Get CryptoCompare / CoinDesk Data API key from environment."""
    return (os.getenv("CRYPTOCOMPARE_API_KEY", "").strip() or
            os.getenv("COINDESK_API_KEY", "").strip())


@contextmanager
def coindesk_call_context(
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
):
    """Apply a monotonic deadline and cancellation event to this worker thread."""
    had_deadline = hasattr(_CALL_CONTEXT, "deadline")
    had_cancel_event = hasattr(_CALL_CONTEXT, "cancel_event")
    previous_deadline = getattr(_CALL_CONTEXT, "deadline", None)
    previous_cancel_event = getattr(_CALL_CONTEXT, "cancel_event", None)

    if deadline is None:
        effective_deadline = previous_deadline
    elif previous_deadline is None:
        effective_deadline = deadline
    else:
        effective_deadline = min(deadline, previous_deadline)
    effective_cancel_event = cancel_event or previous_cancel_event

    _CALL_CONTEXT.deadline = effective_deadline
    _CALL_CONTEXT.cancel_event = effective_cancel_event
    try:
        yield
    finally:
        if had_deadline:
            _CALL_CONTEXT.deadline = previous_deadline
        else:
            del _CALL_CONTEXT.deadline
        if had_cancel_event:
            _CALL_CONTEXT.cancel_event = previous_cancel_event
        else:
            del _CALL_CONTEXT.cancel_event


def _resolve_call_limits(
    deadline: float | None,
    cancel_event: threading.Event | None,
) -> tuple[float, threading.Event | None]:
    context_deadline = getattr(_CALL_CONTEXT, "deadline", None)
    if deadline is None:
        deadline = context_deadline
    elif context_deadline is not None:
        deadline = min(deadline, context_deadline)
    if deadline is None:
        deadline = time.monotonic() + _DEFAULT_CALL_DEADLINE_SECONDS
    if cancel_event is None:
        cancel_event = getattr(_CALL_CONTEXT, "cancel_event", None)
    return deadline, cancel_event


def _ensure_active(
    *, deadline: float, cancel_event: threading.Event | None
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("CoinDesk call cancelled")
    if time.monotonic() >= deadline:
        raise _DeadlineExceeded("CoinDesk call deadline exceeded")


def _wait_or_cancel(
    delay: float,
    *,
    deadline: float,
    cancel_event: threading.Event | None,
) -> None:
    _ensure_active(deadline=deadline, cancel_event=cancel_event)
    delay = max(0.0, delay)
    remaining = deadline - time.monotonic()
    if delay >= remaining:
        raise _DeadlineExceeded("CoinDesk call deadline exceeded before retry")
    if cancel_event is not None:
        if cancel_event.wait(delay):
            raise CancelledError("CoinDesk call cancelled")
    elif delay:
        time.sleep(delay)
    _ensure_active(deadline=deadline, cancel_event=cancel_event)


def _acquire_host_slot(
    host: str,
    *,
    deadline: float,
    cancel_event: threading.Event | None,
) -> None:
    _ensure_active(deadline=deadline, cancel_event=cancel_event)
    now = time.monotonic()
    with _HOST_THROTTLE_LOCK:
        if host not in _HOST_NEXT_REQUEST and len(_HOST_NEXT_REQUEST) >= _MAX_TRACKED_HOSTS:
            expired = [
                tracked_host
                for tracked_host, next_at in _HOST_NEXT_REQUEST.items()
                if next_at <= now
            ]
            for tracked_host in expired:
                _HOST_NEXT_REQUEST.pop(tracked_host, None)
            if len(_HOST_NEXT_REQUEST) >= _MAX_TRACKED_HOSTS:
                oldest_host = min(_HOST_NEXT_REQUEST, key=_HOST_NEXT_REQUEST.get)
                _HOST_NEXT_REQUEST.pop(oldest_host, None)

        slot = max(now, _HOST_NEXT_REQUEST.get(host, now))
        wait = slot - now
        if wait > _MAX_THROTTLE_WAIT_SECONDS:
            raise TimeoutError(
                f"CoinDesk host throttle wait {wait:.3f}s exceeds bound"
            )
        if slot >= deadline:
            raise _DeadlineExceeded(
                "CoinDesk call deadline exceeded in host throttle"
            )
        _HOST_NEXT_REQUEST[host] = slot + _HOST_MIN_INTERVAL_SECONDS

    if wait:
        _wait_or_cancel(
            wait, deadline=deadline, cancel_event=cancel_event
        )


def _retry_after_seconds(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers else None
    if value is None:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            delay = retry_at.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(_MAX_RETRY_AFTER_SECONDS, max(0.0, delay))


def _backoff_seconds(attempt: int) -> float:
    return min(
        _MAX_BACKOFF_SECONDS,
        _BACKOFF_BASE_SECONDS * (2 ** attempt),
    )


def _http_get(
    url: str,
    *,
    params: dict | None = None,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """HTTP GET with bounded retries, host throttling, and cancellation."""
    deadline, cancel_event = _resolve_call_limits(deadline, cancel_event)
    _ensure_active(deadline=deadline, cancel_event=cancel_event)

    if params:
        qs = urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
        url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"

    headers = {"User-Agent": "TradingBot/2.0"}
    api_key = _get_api_key()
    if api_key:
        headers["authorization"] = f"Apikey {api_key}"

    request = urllib.request.Request(url, headers=headers)
    host = urlsplit(url).netloc.lower()
    attempts = max(1, int(_MAX_ATTEMPTS))

    for attempt in range(attempts):
        _acquire_host_slot(
            host, deadline=deadline, cancel_event=cancel_event
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _DeadlineExceeded("CoinDesk call deadline exceeded")
        request_timeout = min(_TIMEOUT, remaining)

        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw = response.read()
            _ensure_active(deadline=deadline, cancel_event=cancel_event)
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            retryable = error.code in {408, 425, 429, 500, 502, 503, 504}
            if not retryable or attempt + 1 >= attempts:
                raise
            delay = _retry_after_seconds(error)
            if delay is None:
                delay = _backoff_seconds(attempt)
        except _DeadlineExceeded:
            raise
        except CancelledError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt + 1 >= attempts:
                raise
            delay = _backoff_seconds(attempt)

        _wait_or_cancel(
            delay, deadline=deadline, cancel_event=cancel_event
        )

    raise RuntimeError("CoinDesk HTTP retry loop exhausted")


# ── Funding Rate ───────────────────────────────────────────────────────

def call_coindesk_fr_tick(
    *,
    market: str,
    instruments: str,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
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
        deadline=deadline,
        cancel_event=cancel_event,
    )


def call_coindesk_fr_ohlcv(
    *,
    market: str,
    instrument: str,
    frequency: str = "hours",
    aggregate: int = 8,
    limit: int = 21,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
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
        deadline=deadline,
        cancel_event=cancel_event,
    )


# ── Open Interest ──────────────────────────────────────────────────────

def call_coindesk_oi_tick(
    *,
    market: str,
    instruments: str,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
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
        deadline=deadline,
        cancel_event=cancel_event,
    )


def call_coindesk_oi_ohlcv(
    *,
    market: str,
    instrument: str,
    frequency: str = "hours",
    aggregate: int = 1,
    limit: int = 7,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
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
        deadline=deadline,
        cancel_event=cancel_event,
    )


# ── News ───────────────────────────────────────────────────────────────

def call_coindesk_news(
    *,
    limit: int = 50,
    lang: str = "EN",
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
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
        deadline=deadline,
        cancel_event=cancel_event,
    )
