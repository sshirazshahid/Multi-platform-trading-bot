"""core/data_sources/cross_venue.py — S4 cross-venue RV stack (data + sim only).

Today every derivs feed is 100% Binance (``core/data_sources/derivs.py``). S4
(cross-venue relative-value / funding-basis) needs synchronized Bybit + Bitget
microstructure too. This module supplies:

  1. ``BybitDerivsHarvester`` / ``BitgetDerivsHarvester`` — parse each venue's
     NATIVE public ticker shape into ONE normalized record
     ``{venue, symbol, bid, ask, mid, funding, oi, ts, stale}``. A ``_get`` seam
     keeps them hermetic (unit-tested on fixtures, no live call). Fail-open like
     the Binance harvester: any error yields a neutral ``stale=True`` record.
  2. ``cross_venue_quote_frame`` — align per-venue snapshots into a per-coin frame.
  3. ``spread_z`` / ``spread_z_frame`` — z-score the cross-venue mid spread vs a
     trailing window (the RV signal substrate).
  4. ``round_trip_cost_frame`` / ``two_leg_round_trip_cost`` — per-venue and
     two-leg round-trip cost, reusing ``core.cost_model`` (no flat-bp guess).
  5. ``Book`` + ``TwoBookSimulator`` — a two-book sim with per-venue partial-fill,
     latency, stale-feed rejection and explicit LEGGING risk (one leg fills, the
     other misses -> unhedged exposure). This is the honest reason S4 is hard.

S4 is marked ``INFEASIBLE-AT-$420`` (two-venue notional + maker rebates + legging
buffer dwarf a $420 book) and FORWARD-DATA-GATED (real Bybit/Bitget history must
be accumulated forward before any persistence claim). No module-level network
call; nothing here is wired to the live order path; no daemon is started.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from core import cost_model

_TIMEOUT = 10

# --- S4 gating markers (mirror S2 LIVE_FEASIBILITY / S5 STANDALONE flags) ----
S4_LIVE_FEASIBILITY = "INFEASIBLE-AT-$420"
S4_FORWARD_DATA_GATED = True
S4_ROLE = "research/PAPER cross-venue RV stack; forward-data-gated; not live-wired"


def refuse_live_at_420() -> None:
    """Hard guard: S4 must never drive a live two-venue execution at $420."""
    raise ValueError(
        "S4 cross-venue RV is live-INFEASIBLE at $420 (two-venue notional + "
        "legging buffer) and forward-data-gated; research/PAPER sim only."
    )


def _http_get(url: str) -> Any | None:
    """Default live fetch seam — overridden/monkeypatched in tests (no live call there)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/2.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read())
    except Exception:
        return None


def _neutral(venue: str, symbol: str) -> dict[str, Any]:
    return {
        "venue": venue,
        "symbol": symbol,
        "bid": None,
        "ask": None,
        "mid": None,
        "funding": None,
        "oi": None,
        "ts": None,
        "stale": True,
    }


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- harvesters
class _VenueHarvester:
    """Common cache + snapshot loop; subclasses supply URL + parser."""

    venue = "?"

    def __init__(self, *, cache_ttl: int = 120):
        self._cache_ttl = cache_ttl
        self._cache: dict[str, dict] = {}
        self._cache_time = 0.0

    def _get(self, url: str) -> Any | None:  # seam (monkeypatched in tests)
        return _http_get(url)

    def _url(self, coin: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _parse(self, coin: str, raw: Any) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def snapshot(self, coins: list[str], *, force: bool = False) -> dict[str, dict[str, Any]]:
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache
        out: dict[str, dict] = {}
        for coin in coins:
            try:
                raw = self._get(self._url(coin))
                out[coin] = self._parse(coin, raw)
            except Exception:
                out[coin] = _neutral(self.venue, coin)
        self._cache, self._cache_time = out, now
        return out


class BybitDerivsHarvester(_VenueHarvester):
    """Bybit v5 linear-perp public ticker (no key)."""

    venue = "bybit"
    _BASE = "https://api.bybit.com"

    def _url(self, coin: str) -> str:
        sym = f"{coin.upper()}USDT"
        return f"{self._BASE}/v5/market/tickers?category=linear&symbol={sym}"

    def _parse(self, coin: str, raw: Any) -> dict[str, Any]:
        try:
            row = (raw or {}).get("result", {}).get("list", [])[0]
        except (AttributeError, IndexError, TypeError):
            return _neutral(self.venue, coin)
        rec = _neutral(self.venue, coin)
        rec["symbol"] = row.get("symbol", f"{coin.upper()}USDT")
        rec["bid"] = _f(row.get("bid1Price"))
        rec["ask"] = _f(row.get("ask1Price"))
        rec["funding"] = _f(row.get("fundingRate"))
        rec["oi"] = _f(row.get("openInterestValue"))
        rec["ts"] = time.time()
        if rec["bid"] is not None and rec["ask"] is not None:
            rec["mid"] = (rec["bid"] + rec["ask"]) / 2.0
            rec["stale"] = False
        elif _f(row.get("lastPrice")) is not None:
            rec["mid"] = _f(row.get("lastPrice"))
            rec["stale"] = False
        return rec


class BitgetDerivsHarvester(_VenueHarvester):
    """Bitget v2 usdt-futures public ticker (no key)."""

    venue = "bitget"
    _BASE = "https://api.bitget.com"

    def _url(self, coin: str) -> str:
        sym = f"{coin.upper()}USDT"
        return (
            f"{self._BASE}/api/v2/mix/market/ticker"
            f"?symbol={sym}&productType=usdt-futures"
        )

    def _parse(self, coin: str, raw: Any) -> dict[str, Any]:
        try:
            row = (raw or {}).get("data", [])[0]
        except (AttributeError, IndexError, TypeError):
            return _neutral(self.venue, coin)
        rec = _neutral(self.venue, coin)
        rec["symbol"] = row.get("symbol", f"{coin.upper()}USDT")
        rec["bid"] = _f(row.get("bidPr"))
        rec["ask"] = _f(row.get("askPr"))
        rec["funding"] = _f(row.get("fundingRate"))
        rec["oi"] = _f(row.get("holdingAmount") or row.get("openInterest"))
        rec["ts"] = time.time()
        if rec["bid"] is not None and rec["ask"] is not None:
            rec["mid"] = (rec["bid"] + rec["ask"]) / 2.0
            rec["stale"] = False
        elif _f(row.get("lastPr")) is not None:
            rec["mid"] = _f(row.get("lastPr"))
            rec["stale"] = False
        return rec


# ----------------------------------------------------- cross-venue frames
def cross_venue_quote_frame(
    snaps: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Align ``{venue: {coin: rec}}`` into ``{coin: {venues, mids, fundings}}``.

    Only non-stale records contribute a mid. A coin present on >=1 venue appears.
    """
    coins: set[str] = set()
    for per_coin in snaps.values():
        coins.update(per_coin)
    frame: dict[str, dict[str, Any]] = {}
    for coin in sorted(coins):
        venues: dict[str, dict] = {}
        mids: dict[str, float] = {}
        fundings: dict[str, float] = {}
        for venue, per_coin in snaps.items():
            rec = per_coin.get(coin)
            if not rec:
                continue
            venues[venue] = rec
            if not rec.get("stale") and rec.get("mid") is not None:
                mids[venue] = float(rec["mid"])
            if rec.get("funding") is not None:
                fundings[venue] = float(rec["funding"])
        frame[coin] = {"venues": venues, "mids": mids, "fundings": fundings}
    return frame


def _spread_series(mid_a: list[float], mid_b: list[float]) -> list[float]:
    n = min(len(mid_a), len(mid_b))
    return [float(mid_a[i]) - float(mid_b[i]) for i in range(n)]


def spread_z(mid_a: list[float], mid_b: list[float], *, window: int = 30) -> float | None:
    """Z-score of the LATEST cross-venue mid spread vs the trailing ``window``.

    Returns None if fewer than 2 trailing points or zero trailing variance.
    """
    spreads = _spread_series(mid_a, mid_b)
    if len(spreads) < 2:
        return None
    latest = spreads[-1]
    trail = spreads[-1 - window : -1] if window > 0 else spreads[:-1]
    if len(trail) < 2:
        return None
    mean = sum(trail) / len(trail)
    var = sum((s - mean) ** 2 for s in trail) / (len(trail) - 1)
    if var <= 0:
        return None
    return (latest - mean) / (var ** 0.5)


def spread_z_frame(
    coin_series: dict[str, tuple[list[float], list[float]]], *, window: int = 30
) -> dict[str, float | None]:
    """Per-coin spread-z from ``{coin: (mid_a_series, mid_b_series)}``."""
    return {c: spread_z(a, b, window=window) for c, (a, b) in coin_series.items()}


def round_trip_cost_frame(
    venues: list[str],
    *,
    market_type: str = "futures",
    entry_liq: str = "taker",
    exit_liq: str = "taker",
    tier_mult: float = 1.0,
    slip_mult: float = 1.0,
) -> dict[str, float]:
    """Per-venue round-trip cost fraction (reuses ``cost_model.round_trip_cost``)."""
    return {
        v: cost_model.round_trip_cost(
            v,
            market_type=market_type,
            entry_liq=entry_liq,
            exit_liq=exit_liq,
            tier_mult=tier_mult,
            slip_mult=slip_mult,
        )
        for v in venues
    }


def two_leg_round_trip_cost(venue_a: str, venue_b: str, **kw: Any) -> float:
    """Total round-trip cost of a two-leg cross-venue trade = leg_a + leg_b."""
    frame = round_trip_cost_frame([venue_a, venue_b], **kw)
    return frame[venue_a] + frame[venue_b]


# ------------------------------------------------- two-book legging sim
@dataclass
class Book:
    """One venue order book snapshot at time ``ts``.

    ``asks``/``bids`` are ascending/descending ``(price, qty)`` level lists.
    """

    venue: str
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    ts: float = 0.0


def fill_book(
    book: Book,
    side: str,
    qty: float,
    *,
    now: float,
    max_age_sec: float,
) -> dict[str, Any]:
    """Walk ``book`` levels to fill ``qty`` of ``side`` (buy=>take asks, sell=>take bids).

    Rejects the fill if the book feed is older than ``max_age_sec`` (stale-feed
    gate). Returns ``{filled_qty, avg_px, reason}`` — partial fills carry the
    available quantity and a ``partial`` reason.
    """
    age = float(now) - float(book.ts)
    if age > float(max_age_sec):
        return {"filled_qty": 0.0, "avg_px": None,
                "reason": f"stale:{age:.1f}s>{max_age_sec:.1f}s"}
    levels = book.asks if side == "buy" else book.bids
    if not levels:
        return {"filled_qty": 0.0, "avg_px": None, "reason": "empty_book"}
    remaining = float(qty)
    notional = 0.0
    filled = 0.0
    for px, avail in levels:
        take = min(remaining, float(avail))
        notional += take * float(px)
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if filled <= 0:
        return {"filled_qty": 0.0, "avg_px": None, "reason": "no_liquidity"}
    avg_px = notional / filled
    reason = "filled" if remaining <= 1e-12 else "partial"
    return {"filled_qty": filled, "avg_px": avg_px, "reason": reason}


class TwoBookSimulator:
    """Cross-venue two-leg executor IN SIM with explicit legging risk.

    Models per-venue partial fills, a stale-feed rejection on either leg, and a
    per-leg latency offset (the leg's book is consumed at ``now + latency_sec``).
    The defining hazard: if one leg fills a different quantity than the other,
    the residual is UNHEDGED — this is why cross-venue RV is operationally hard.
    """

    def __init__(self, *, max_age_sec: float = 10.0, latency_sec: float = 0.0):
        self.max_age_sec = float(max_age_sec)
        self.latency_sec = float(latency_sec)

    def execute_two_leg(
        self,
        long_book: Book,
        short_book: Book,
        qty: float,
        *,
        now: float,
        latency_sec: float | None = None,
    ) -> dict[str, Any]:
        lat = self.latency_sec if latency_sec is None else float(latency_sec)
        # long leg buys on its venue; short leg sells on its venue
        long_leg = fill_book(long_book, "buy", qty, now=now + lat,
                             max_age_sec=self.max_age_sec)
        short_leg = fill_book(short_book, "sell", qty, now=now + lat,
                              max_age_sec=self.max_age_sec)
        lq = long_leg["filled_qty"]
        sq = short_leg["filled_qty"]
        unhedged = abs(lq - sq)
        return {
            "long_leg": long_leg,
            "short_leg": short_leg,
            "unhedged_qty": unhedged,
            "legging_risk": unhedged > 1e-12,
            "latency_sec": lat,
        }
