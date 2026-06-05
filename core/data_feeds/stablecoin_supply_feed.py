"""DefiLlama aggregate stablecoin supply — a market-wide liquidity-regime data source.

Genuinely-new (not on the bot's exploited list) + free + key-less + backfillable (~8.5y daily).
Hypothesis: aggregate stablecoin supply expansion = crypto "dry powder" (risk-on); a SLOW macro
regime overlay, NOT a per-bar trade signal. A time-series regime gate (next increment) tests
whether it predicts forward returns; the honest prior is reduced whipsaw, not alpha.

Pure parse + causal signal live here (unit-tested); the thin network fetch is used by the screen
script and kept out of the test suite. Source (confirmed free, no key, 2026-06-05 — ~3110 daily
records 2017-11-29 -> present, field totalCirculatingUSD.peggedUSD):
    https://stablecoins.llama.fi/stablecoincharts/all
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
_TIMEOUT = 30


def fetch_stablecoin_supply_records(url: str = _URL) -> list:
    """Network: raw DefiLlama daily records. Thin wrapper; not exercised by the test suite."""
    req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/2.0"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read())


def parse_supply_series(records) -> pd.Series:
    """Daily total USD-pegged stablecoin circulating supply, indexed by UTC date (ascending).

    Each record: {"date": "<unix-sec>", "totalCirculatingUSD": {"peggedUSD": <float>, ...}}.
    Records missing the pegged-USD value are skipped; duplicate dates keep the last.
    """
    out: dict = {}
    for rec in records or []:
        ts = rec.get("date")
        tc = rec.get("totalCirculatingUSD") or {}
        val = tc.get("peggedUSD") if isinstance(tc, dict) else None
        if ts is None or val is None:
            continue
        day = pd.Timestamp(int(float(ts)), unit="s", tz="UTC").normalize()
        out[day] = float(val)
    s = pd.Series(out, dtype=float).sort_index()
    return s[~s.index.duplicated(keep="last")]


def supply_growth_signal(supply: pd.Series, window: int = 30) -> pd.Series:
    """Causal regime signal: trailing ``window``-day fractional change in supply.

    Value at day t uses only supply[t] and supply[t-window] (both <= t) -> no look-ahead
    (verified by core.alpha_zoo.bias_checks). NaN for the first ``window`` days. This is the
    market-wide liquidity-regime variable a time-series gate then tests against forward returns.
    """
    s = pd.Series(supply, dtype=float).sort_index()
    return s.pct_change(window)
