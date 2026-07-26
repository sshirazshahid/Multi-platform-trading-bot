#!/usr/bin/env python3
"""
FMP API Client for FTD Detector

Provides rate-limited access to Financial Modeling Prep API endpoints
for follow-through day detection analysis.

Features:
- Rate limiting (0.3s between requests)
- Automatic retry on 429 errors
- Session caching for duplicate requests
- Batch quote support for ETF baskets
"""

import os
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

# Shared EOD-bar shape check (stable list vs error / garbage payloads)
_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if _SKILLS_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_SKILLS_DIR))
try:
    from _shared_fmp_yahoo_patch import looks_like_eod_bar_list, resolve_fmp_key
except ImportError:  # pragma: no cover — path isolation in odd test layouts

    def resolve_fmp_key(api_key=None):  # type: ignore[misc]
        return api_key or os.getenv("FMP_API_KEY")

    def looks_like_eod_bar_list(data, *, sample_size: int = 5):  # type: ignore[misc]
        if not isinstance(data, list) or not data:
            return False
        for item in data[: min(sample_size, len(data))]:
            if not isinstance(item, dict) or not item.get("date"):
                return False
            if item.get("close") is None and item.get("adjClose") is None:
                return False
        return True


# --- FMP endpoint fallback: stable (new users) -> v3 (legacy users) ---


def _stable_quote_url(base, symbols_str, params):
    """stable/quote?symbol=^GSPC"""
    params["symbol"] = symbols_str
    return base, params


def _v3_quote_url(base, symbols_str, params):
    """api/v3/quote/^GSPC"""
    return f"{base}/{symbols_str}", params


def _stable_eod_url(base, symbols_str, params):
    """stable/historical-price-eod/full?symbol=SPY (current FMP free/stable)."""
    params["symbol"] = symbols_str
    return base, params


def _stable_hist_url(base, symbols_str, params):
    """stable/historical-price-full?symbol=^GSPC&timeseries=80"""
    params["symbol"] = symbols_str
    return base, params


def _v3_hist_url(base, symbols_str, params):
    """api/v3/historical-price-full/^GSPC?timeseries=80"""
    return f"{base}/{symbols_str}", params


_FMP_ENDPOINTS = {
    "quote": [
        ("https://financialmodelingprep.com/stable/quote", _stable_quote_url),
        ("https://financialmodelingprep.com/api/v3/quote", _v3_quote_url),
    ],
    "historical": [
        (
            "https://financialmodelingprep.com/stable/historical-price-eod/full",
            _stable_eod_url,
        ),
        ("https://financialmodelingprep.com/stable/historical-price-full", _stable_hist_url),
        ("https://financialmodelingprep.com/api/v3/historical-price-full", _v3_hist_url),
    ],
}


class FMPClient:
    """Client for Financial Modeling Prep API with rate limiting and caching"""

    BASE_URL = "https://financialmodelingprep.com/api/v3"
    RATE_LIMIT_DELAY = 0.3  # 300ms between requests

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = resolve_fmp_key(api_key)
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Set FMP_API_KEY environment variable "
                "or pass api_key parameter."
            )
        self.session = requests.Session()
        self.session.headers.update({"apikey": self.api_key})
        self.cache = {}
        self.last_call_time = 0
        self.rate_limit_reached = False
        self.retry_count = 0
        self.max_retries = 1
        self.api_calls_made = 0
        self.yahoo_fallback = True

    def _rate_limited_get(
        self, url: str, params: Optional[dict] = None, quiet: bool = False
    ) -> Optional[dict]:
        if self.rate_limit_reached:
            return None

        if params is None:
            params = {}
        params = dict(params)
        params.setdefault("apikey", self.api_key)

        elapsed = time.time() - self.last_call_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            response = self.session.get(url, params=params, timeout=30)
            self.last_call_time = time.time()
            self.api_calls_made += 1

            if response.status_code == 200:
                self.retry_count = 0
                return response.json()
            elif response.status_code == 429:
                self.retry_count += 1
                if self.retry_count <= self.max_retries:
                    print("WARNING: Rate limit exceeded. Waiting 60 seconds...", file=sys.stderr)
                    time.sleep(60)
                    return self._rate_limited_get(url, params, quiet=quiet)
                else:
                    print("ERROR: Daily API rate limit reached.", file=sys.stderr)
                    self.rate_limit_reached = True
                    return None
            else:
                if not quiet:
                    print(
                        f"ERROR: API request failed: {response.status_code} - {response.text[:200]}",
                        file=sys.stderr,
                    )
                return None
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Request exception: {e}", file=sys.stderr)
            return None

    def _request_with_fallback(self, endpoint_key, symbols_str, extra_params=None):
        """Try stable endpoint first, fall back to v3 for legacy users."""
        params = dict(extra_params) if extra_params else {}
        endpoints = _FMP_ENDPOINTS[endpoint_key]
        is_single = "," not in symbols_str

        for i, (base_url, url_builder) in enumerate(endpoints):
            url, final_params = url_builder(base_url, symbols_str, dict(params))
            is_last = i == len(endpoints) - 1
            data = self._rate_limited_get(url, final_params, quiet=not is_last)
            if not data:
                continue

            if endpoint_key == "quote":
                if not isinstance(data, list) or len(data) == 0:
                    continue
                # Single-symbol: verify returned symbol matches request
                if is_single and not any(
                    q.get("symbol", "").replace("-", ".") == symbols_str.replace("-", ".")
                    for q in data
                ):
                    continue

            if endpoint_key == "historical":
                if isinstance(data, list):
                    # Stable EOD is a bare bar list — only accept real bars so
                    # Error Message / numeric garbage falls through to next URL.
                    if not looks_like_eod_bar_list(data):
                        continue
                    limit = int((extra_params or {}).get("timeseries") or len(data))
                    return {
                        "symbol": symbols_str,
                        "historical": data[:limit],
                    }
                if not isinstance(data, dict):
                    continue
                if "historicalStockList" in data:
                    norm = symbols_str.replace("-", ".")
                    for entry in data["historicalStockList"]:
                        if entry.get("symbol", "").replace("-", ".") == norm:
                            hist = entry.get("historical", [])
                            if hist and not looks_like_eod_bar_list(hist):
                                break
                            return {
                                "symbol": entry.get("symbol"),
                                "historical": hist if isinstance(hist, list) else [],
                            }
                    continue
                elif "historical" not in data:
                    continue
                hist = data.get("historical")
                if hist is not None and not looks_like_eod_bar_list(hist):
                    continue
                # Single-symbol: verify returned symbol matches request
                if is_single and data.get("symbol"):
                    if data["symbol"].replace("-", ".") != symbols_str.replace("-", "."):
                        continue

            return data
        return None

    def get_quote(self, symbols: str) -> Optional[list[dict]]:
        """Fetch real-time quote data for one or more symbols (comma-separated)"""
        cache_key = f"quote_{symbols}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._request_with_fallback("quote", symbols)
        if data:
            self.cache[cache_key] = data
        return data

    def get_historical_prices(self, symbol: str, days: int = 365) -> Optional[dict]:
        """Fetch historical daily OHLCV data"""
        cache_key = f"prices_{symbol}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._request_with_fallback("historical", symbol, {"timeseries": days})
        if not data and self.yahoo_fallback:
            try:
                import sys
                from pathlib import Path

                shared = Path(__file__).resolve().parents[2] / "_shared_fmp_yahoo_patch.py"
                if str(shared.parent) not in sys.path:
                    sys.path.insert(0, str(shared.parent))
                from _shared_fmp_yahoo_patch import yahoo_historical

                data = yahoo_historical(symbol, days=days)
            except Exception:
                data = None
        if data:
            self.cache[cache_key] = data
        return data

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch quotes for a list of symbols, batching up to 5 per request"""
        results = {}
        # FMP supports comma-separated symbols in quote endpoint
        batch_size = 5
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            batch_str = ",".join(batch)
            quotes = self.get_quote(batch_str)
            if quotes:
                for q in quotes:
                    results[q["symbol"]] = q
        return results

    def get_batch_historical(self, symbols: list[str], days: int = 50) -> dict[str, list[dict]]:
        """Fetch historical prices for multiple symbols"""
        results = {}
        for symbol in symbols:
            data = self.get_historical_prices(symbol, days=days)
            if data and "historical" in data:
                results[symbol] = data["historical"]
        return results

    def calculate_ema(self, prices: list[float], period: int) -> float:
        """Calculate Exponential Moving Average from a list of prices (most recent first)"""
        if len(prices) < period:
            return sum(prices) / len(prices)

        prices_reversed = prices[::-1]
        sma = sum(prices_reversed[:period]) / period
        ema = sma
        k = 2 / (period + 1)
        for price in prices_reversed[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def calculate_sma(self, prices: list[float], period: int) -> float:
        """Calculate Simple Moving Average from a list of prices (most recent first)"""
        if len(prices) < period:
            return sum(prices) / len(prices)
        return sum(prices[:period]) / period

    def get_api_stats(self) -> dict:
        return {
            "cache_entries": len(self.cache),
            "api_calls_made": self.api_calls_made,
            "rate_limit_reached": self.rate_limit_reached,
        }
