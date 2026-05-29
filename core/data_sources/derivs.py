"""core/data_sources/derivs.py — derivatives-microstructure harvester.

Harvests, from Binance's FREE public futures-data endpoints (no API key):
  * global long/short ACCOUNT ratio        (crowd positioning)
  * taker buy/sell ratio                   (aggressive-flow / liquidation proxy)
  * open-interest history                  (capital backing the move)
  * funding-rate history                   (carry / crowding cost)

Two jobs:
  1. `snapshot(coins)` → a normalized per-coin dict (latest values + short-term
     change) suitable for a *future* shadow agent's ctx. Fail-open: any error
     yields a `stale=True` neutral dict, never raises.
  2. `append_history(path, snap)` → append one JSON line per coin to a durable
     store. REQUIRED for forward accumulation: the endpoints only retain ~21
     days, so a leakage-clean ≥N-month OOS falsification can only exist if we
     persist forward. Dedup is done at read time by (symbol, bar_ts).

This module is import-cheap and side-effect-free; it makes no network call until
`snapshot()` is invoked. It is NOT wired into the live order path or the live
scoring loop — it is run by `scripts/harvest_derivs.py` (schedulable) and is the
data substrate for a gated, falsification-first derivs agent built later.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

_BASE = "https://fapi.binance.com"
_TIMEOUT = 10


def _get(url: str) -> Any | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/2.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (public https)
            return json.loads(resp.read())
    except Exception:
        return None


def _neutral(stale: bool = True) -> dict[str, Any]:
    return {
        "lsr": None,
        "lsr_chg": None,
        "taker_ls": None,
        "oi": None,
        "oi_chg_pct": None,
        "funding": None,
        "bar_ts": None,
        "stale": stale,
    }


class DerivsHarvester:
    """Fetch + normalize Binance public derivatives microstructure."""

    def __init__(self, *, period: str = "1h", lookback: int = 24, cache_ttl: int = 120):
        self.period = period
        self.lookback = lookback  # bars back for the change calc
        self._cache_ttl = cache_ttl
        self._cache: dict[str, dict] = {}
        self._cache_time = 0.0

    # -- per-series fetchers (each fail-open to None) --------------------
    def _series(self, path: str, sym: str, key: str) -> list[tuple[int, float]] | None:
        url = f"{_BASE}/futures/data/{path}?symbol={sym}&period={self.period}&limit=500"
        data = _get(url)
        if not isinstance(data, list) or not data:
            return None
        out = []
        for d in data:
            try:
                out.append((int(d["timestamp"]), float(d[key])))
            except (KeyError, TypeError, ValueError):
                continue
        return out or None

    def _funding(self, sym: str) -> float | None:
        data = _get(f"{_BASE}/fapi/v1/fundingRate?symbol={sym}&limit=1")
        if isinstance(data, list) and data:
            try:
                return float(data[-1]["fundingRate"])
            except (KeyError, TypeError, ValueError):
                return None
        return None

    # -- public API -----------------------------------------------------
    def snapshot(self, coins: list[str], *, force: bool = False) -> dict[str, dict[str, Any]]:
        """Return {coin: normalized derivs dict}. Cached for cache_ttl seconds."""
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache
        result: dict[str, dict] = {}
        for coin in coins:
            sym = f"{coin.upper()}USDT"
            try:
                result[coin] = self._snapshot_one(sym)
            except Exception:
                result[coin] = _neutral(stale=True)
        self._cache, self._cache_time = result, now
        return result

    def _snapshot_one(self, sym: str) -> dict[str, Any]:
        lsr = self._series("globalLongShortAccountRatio", sym, "longShortRatio")
        taker = self._series("takerlongshortRatio", sym, "buySellRatio")
        oi = self._series("openInterestHist", sym, "sumOpenInterestValue")
        if not lsr and not oi:
            return _neutral(stale=True)
        snap = _neutral(stale=False)
        if lsr:
            snap["lsr"] = lsr[-1][1]
            if len(lsr) > self.lookback:
                snap["lsr_chg"] = lsr[-1][1] - lsr[-1 - self.lookback][1]
            snap["bar_ts"] = lsr[-1][0]
        if taker:
            snap["taker_ls"] = taker[-1][1]
        if oi:
            snap["oi"] = oi[-1][1]
            if len(oi) > self.lookback and oi[-1 - self.lookback][1]:
                snap["oi_chg_pct"] = (oi[-1][1] / oi[-1 - self.lookback][1] - 1.0) * 100
            snap["bar_ts"] = snap["bar_ts"] or oi[-1][0]
        snap["funding"] = self._funding(sym)
        return snap

    @staticmethod
    def append_history(
        path: str | Path, snap: dict[str, dict[str, Any]], *, fetched_at: float
    ) -> int:
        """Append one JSON line per non-stale coin. Returns rows written.

        Forward-accumulation store. A future probe reads this jsonl and dedups by
        (symbol, bar_ts) to reconstruct multi-month history beyond the API window.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with p.open("a", encoding="utf-8") as f:
            for coin, d in snap.items():
                if d.get("stale"):
                    continue
                row = {"fetched_at": fetched_at, "symbol": coin, **d}
                f.write(json.dumps(row) + "\n")
                written += 1
        return written
