#!/usr/bin/env python3
"""
FMP API Client for Macro Regime Detector

Provides rate-limited access to Financial Modeling Prep API endpoints
for macro regime detection analysis.

Features:
- Rate limiting (0.3s between requests)
- Automatic retry on 429 errors
- Session caching for duplicate requests
- Batch historical data support
- Treasury rates endpoint support
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

# Shared FMP key resolver: explicit arg -> os.environ -> repo-root .env.
_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if _SKILLS_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_SKILLS_DIR))
try:
    from _shared_fmp_yahoo_patch import resolve_fmp_key
except ImportError:  # pragma: no cover - path isolation in odd test layouts

    def resolve_fmp_key(api_key=None):  # type: ignore[misc]
        return api_key or os.getenv("FMP_API_KEY")


class FMPClient:
    """Client for Financial Modeling Prep API with rate limiting and caching"""

    BASE_URL = "https://financialmodelingprep.com/api/v3"
    STABLE_URL = "https://financialmodelingprep.com/stable"
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

    def _rate_limited_get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
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
                    return self._rate_limited_get(url, params)
                else:
                    print("ERROR: Daily API rate limit reached.", file=sys.stderr)
                    self.rate_limit_reached = True
                    return None
            else:
                print(
                    f"ERROR: API request failed: {response.status_code} - {response.text[:200]}",
                    file=sys.stderr,
                )
                return None
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Request exception: {e}", file=sys.stderr)
            return None

    def get_historical_prices(self, symbol: str, days: int = 600) -> Optional[dict]:
        """Fetch historical daily OHLCV data.

        Free FMP plans 402 most ETFs and rate-limit quickly, so prefer Yahoo
        chart data for ETF history; keep FMP only as a secondary source.
        """
        cache_key = f"prices_{symbol}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = None
        if self.yahoo_fallback:
            try:
                import sys
                from pathlib import Path

                shared = Path(__file__).resolve().parents[2] / "_shared_fmp_yahoo_patch.py"
                if str(shared.parent) not in sys.path:
                    sys.path.insert(0, str(shared.parent))
                from _shared_fmp_yahoo_patch import yahoo_historical

                data = yahoo_historical(symbol, days=days)
            except Exception as exc:
                print(f"WARN: Yahoo fallback failed for {symbol}: {exc}", file=sys.stderr)
                data = None

        if (not data or "historical" not in data) and not self.rate_limit_reached:
            url = f"{self.STABLE_URL}/historical-price-eod/full"
            raw = self._rate_limited_get(url, {"symbol": symbol})
            if isinstance(raw, list) and raw:
                data = {"symbol": symbol, "historical": raw[:days]}
            elif isinstance(raw, dict) and "historical" in raw:
                data = raw

        if data:
            self.cache[cache_key] = data
        return data

    def get_batch_historical(self, symbols: list[str], days: int = 600) -> dict[str, list[dict]]:
        """Fetch historical prices for multiple symbols"""
        results = {}
        for symbol in symbols:
            data = self.get_historical_prices(symbol, days=days)
            if data and "historical" in data:
                results[symbol] = data["historical"]
        return results

    def get_treasury_rates(self, days: int = 600) -> Optional[list[dict]]:
        """
        Fetch treasury rate data from FMP stable endpoint.

        Returns list of dicts with keys like 'date', 'year2', 'year10', etc.
        Most recent first.
        """
        cache_key = f"treasury_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        url = f"{self.STABLE_URL}/treasury-rates"
        params = {"limit": days}
        data = self._rate_limited_get(url, params)
        if data and isinstance(data, list):
            self.cache[cache_key] = data
            return data
        return None

    def get_api_stats(self) -> dict:
        return {
            "cache_entries": len(self.cache),
            "api_calls_made": self.api_calls_made,
            "rate_limit_reached": self.rate_limit_reached,
        }
