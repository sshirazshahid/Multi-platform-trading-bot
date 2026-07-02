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


# ------------------------------------------------- funding metadata (Phase 1)
def _neutral_funding(venue: str, symbol: str) -> dict[str, Any]:
    return {
        "venue": venue,
        "symbol": symbol,
        "rate": None,
        "interval_hours": None,
        "next_funding_ts": None,
        "ts": None,
        "age_sec": float("inf"),
        "stale": True,
    }


def _ms_to_s(x: Any) -> float | None:
    v = _f(x)
    return v / 1000.0 if v else None


class _FundingMetaHarvester:
    """Per-venue funding INTERVAL + NEXT-FUNDING-TIME harvester (fail-open).

    Subclasses implement ``_fetch(coin)`` using the ``_get`` seam only; any
    error yields a neutral stale record (``age_sec=inf``) — never raises.
    """

    venue = "?"

    def __init__(self, *, cache_ttl: int = 300):
        self._cache_ttl = cache_ttl
        self._cache: dict[str, dict] = {}
        self._cache_time = 0.0

    def _get(self, url: str) -> Any | None:  # seam (stubbed in tests)
        return _http_get(url)

    def _fetch(self, coin: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def snapshot(self, coins: list[str], *, force: bool = False) -> dict[str, dict[str, Any]]:
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache
        out: dict[str, dict] = {}
        for coin in coins:
            try:
                out[coin] = self._fetch(coin)
            except Exception:
                out[coin] = _neutral_funding(self.venue, coin)
        self._cache, self._cache_time = out, now
        return out


class BinanceFundingMetaHarvester(_FundingMetaHarvester):
    """Binance USD-M: fundingInfo lists ONLY per-symbol ADJUSTED intervals;
    absent symbols default to 8h. Rate + nextFundingTime from premiumIndex."""

    venue = "binance"
    _BASE = "https://fapi.binance.com"
    _DEFAULT_INTERVAL_H = 8.0

    def _fetch(self, coin: str) -> dict[str, Any]:
        sym = f"{coin.upper()}USDT"
        rec = _neutral_funding(self.venue, sym)
        prem = self._get(f"{self._BASE}/fapi/v1/premiumIndex?symbol={sym}")
        if not isinstance(prem, dict):
            return rec
        rec["rate"] = _f(prem.get("lastFundingRate"))
        rec["next_funding_ts"] = _ms_to_s(prem.get("nextFundingTime"))
        interval = self._DEFAULT_INTERVAL_H
        info = self._get(f"{self._BASE}/fapi/v1/fundingInfo")
        if isinstance(info, list):
            for row in info:
                if isinstance(row, dict) and row.get("symbol") == sym:
                    ih = _f(row.get("fundingIntervalHours"))
                    if ih:
                        interval = ih
                    break
        rec["interval_hours"] = interval
        if rec["rate"] is not None:
            rec["ts"] = time.time()
            rec["age_sec"] = 0.0
            rec["stale"] = False
        return rec


class BybitFundingMetaHarvester(_FundingMetaHarvester):
    """Bybit v5 linear: instruments-info fundingInterval is MINUTES -> hours;
    fundingRate + nextFundingTime from tickers."""

    venue = "bybit"
    _BASE = "https://api.bybit.com"

    def _fetch(self, coin: str) -> dict[str, Any]:
        sym = f"{coin.upper()}USDT"
        rec = _neutral_funding(self.venue, sym)
        tick = self._get(f"{self._BASE}/v5/market/tickers?category=linear&symbol={sym}")
        try:
            trow = (tick or {}).get("result", {}).get("list", [])[0]
        except (AttributeError, IndexError, TypeError):
            return rec
        rec["rate"] = _f(trow.get("fundingRate"))
        rec["next_funding_ts"] = _ms_to_s(trow.get("nextFundingTime"))
        inst = self._get(
            f"{self._BASE}/v5/market/instruments-info?category=linear&symbol={sym}"
        )
        try:
            irow = (inst or {}).get("result", {}).get("list", [])[0]
            minutes = _f(irow.get("fundingInterval"))
            if minutes:
                rec["interval_hours"] = minutes / 60.0
        except (AttributeError, IndexError, TypeError):
            pass
        if rec["rate"] is not None:
            rec["ts"] = time.time()
            rec["age_sec"] = 0.0
            rec["stale"] = False
        return rec


class BitgetFundingMetaHarvester(_FundingMetaHarvester):
    """Bitget v2 usdt-futures current-fund-rate carries fundingRateInterval
    (hours, VARIABLE — never hardcode) + nextUpdate (ms)."""

    venue = "bitget"
    _BASE = "https://api.bitget.com"

    def _fetch(self, coin: str) -> dict[str, Any]:
        sym = f"{coin.upper()}USDT"
        rec = _neutral_funding(self.venue, sym)
        raw = self._get(
            f"{self._BASE}/api/v2/mix/market/current-fund-rate"
            f"?symbol={sym}&productType=usdt-futures"
        )
        try:
            row = (raw or {}).get("data", [])[0]
        except (AttributeError, IndexError, TypeError):
            return rec
        rec["rate"] = _f(row.get("fundingRate"))
        rec["interval_hours"] = _f(row.get("fundingRateInterval"))
        rec["next_funding_ts"] = _ms_to_s(row.get("nextUpdate"))
        if rec["rate"] is not None:
            rec["ts"] = time.time()
            rec["age_sec"] = 0.0
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


# --------------------------------------- F2 funding differential (Phase 7)
# F2 = cross-venue funding-spread carry: long the perp on the venue paying
# LESS funding per hour, short it on the venue paying MORE, collect the
# differential. Built DISABLED (promotion_status='disabled_until_f1_stable').
# PRE-FUNDED-VENUES ASSUMPTION: both venues already hold enough margin for
# their leg — no cross-venue transfer is modeled in the hold window (transfer
# latency/cost would otherwise dominate); this is documented in the F2 spec.
F2_MIN_NET_EDGE_BPS = 20.0
F2_COST_MULT_FLOOR = 4.0
# 1bp per side — the tier where maker routing materially changes a 4-leg
# cross-venue round trip (config.FEE bybit_futures_maker=0.0001 passes <=,
# generic futures_maker=0.0002 fails).
F2_MAX_MAKER_FEE_FRAC = 0.0001


def funding_spread_frame(
    snaps: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Interval-NORMALIZED cross-venue funding differential per coin.

    Consumes Phase-1 funding-meta snapshots ``{venue: {coin: rec}}`` where each
    rec carries ``rate`` (per settlement) + ``interval_hours``. Rates are
    normalized to bps PER HOUR (``rate / interval_hours * 1e4``) so an 8h venue
    and a 4h venue are comparable before differencing. Stale records or records
    without a usable interval are excluded; with <2 usable venues the spread is
    ``None`` and the row is flagged ``stale``.
    """
    coins: set[str] = set()
    for per_coin in snaps.values():
        coins.update(per_coin)
    frame: dict[str, dict[str, Any]] = {}
    for coin in sorted(coins):
        per_hour: dict[str, float] = {}
        for venue, per_coin in snaps.items():
            rec = per_coin.get(coin) or {}
            rate = _f(rec.get("rate"))
            interval = _f(rec.get("interval_hours"))
            if rec.get("stale") or rate is None or not interval:
                continue
            per_hour[venue] = rate / interval * 1e4
        row: dict[str, Any] = {
            "per_hour_bps": per_hour,
            "spread_bps_per_hour": None,
            "long_venue": None,
            "short_venue": None,
            "stale": True,
        }
        if len(per_hour) >= 2:
            long_v = min(per_hour, key=lambda v: per_hour[v])
            short_v = max(per_hour, key=lambda v: per_hour[v])
            row.update(
                spread_bps_per_hour=per_hour[short_v] - per_hour[long_v],
                long_venue=long_v,
                short_venue=short_v,
                stale=False,
            )
        frame[coin] = row
    return frame


def f2_entry_gate(
    *,
    spread_bps_per_hour: float | None,
    hold_hours: float,
    two_leg_cost_frac: float,
    slippage_bps: float = 5.0,
    latency_buffer_bps: float = 2.0,
    failed_leg_buffer_bps: float = 5.0,
    min_edge_bps: float = F2_MIN_NET_EDGE_BPS,
    cost_mult: float = F2_COST_MULT_FLOOR,
) -> dict[str, Any]:
    """F2 entry math (no orders): does the funding differential clear the floor?

    ``net_edge_bps = spread*hold - fees(both venues) - slippage - latency
    buffer - failed-leg buffer`` must be ``>= max(20bps, 4x total round-trip
    cost)``. Fees come in as ``two_leg_cost_frac`` (see
    ``two_leg_round_trip_cost``). Refuses (fail-closed) on a missing spread.
    """
    if spread_bps_per_hour is None:
        return {"enter": False, "net_edge_bps": None, "floor_bps": None,
                "reason": "missing spread (stale/insufficient venues)"}
    cost_bps = float(two_leg_cost_frac) * 1e4
    gross_bps = float(spread_bps_per_hour) * float(hold_hours)
    net = (gross_bps - cost_bps - float(slippage_bps)
           - float(latency_buffer_bps) - float(failed_leg_buffer_bps))
    floor = max(float(min_edge_bps), float(cost_mult) * cost_bps)
    return {
        "enter": net >= floor,
        "net_edge_bps": net,
        "gross_bps": gross_bps,
        "cost_bps": cost_bps,
        "floor_bps": floor,
        "reason": "ok" if net >= floor else f"net {net:.1f}bps < floor {floor:.1f}bps",
    }


def run_one_leg_failure_sims(
    *,
    n: int = 25,
    long_book: Book | None = None,
    short_book: Book | None = None,
    qty: float = 1.0,
    now: float = 100.0,
    max_age_sec: float = 10.0,
) -> list[dict[str, Any]]:
    """Run the one-leg-failure scenario ``n`` times through ``TwoBookSimulator``.

    Deterministic by construction (no randomness in the fill walk): every run
    must yield the SAME result — one fresh leg fills, the stale/timed-out leg
    rejects, leaving explicit unhedged exposure. Default books encode the
    canonical hazard: fresh long book, stale short book.
    """
    if long_book is None:
        long_book = Book(venue="binance",
                         bids=[(100.0, 5.0)], asks=[(100.1, 5.0)], ts=now)
    if short_book is None:
        short_book = Book(venue="bybit",
                          bids=[(100.0, 5.0)], asks=[(100.1, 5.0)],
                          ts=now - max_age_sec - 1.0)
    sim = TwoBookSimulator(max_age_sec=max_age_sec)
    return [sim.execute_two_leg(long_book, short_book, qty, now=now)
            for _ in range(int(n))]


def f2_activation_allowed(
    *,
    registry_path: Any = None,
    min_paper_days: float = 30.0,
    maker_fee_frac: float | None = None,
    max_maker_fee_frac: float = F2_MAX_MAKER_FEE_FRAC,
    taker_economics_acknowledged: bool = False,
) -> dict[str, Any]:
    """ACTIVATION LATCH: F2 may only activate on >=30d of CLEAN F1 paper evidence.

    Reads the F1 EvidenceRegistry row: requires ``oos_metrics.paper_span_days
    >= min_paper_days``, ``paper_cycles > 0`` and ZERO ``failed_leg_events``
    (no unresolved one-leg events). Fail-closed on any missing data.

    Fee-tier precondition (applied AFTER evidence checks pass): either the
    verified maker fee tier is <= ``max_maker_fee_frac``, or the operator
    acknowledges the taker/taker floor (``f2_entry_gate``'s floor already
    prices taker/taker two_leg_cost_frac at 4x). Defaults fail closed.
    """
    try:
        from core.decision.promotion_loop import ACTIVE_STRATEGIES_PATH, load_registry

        reg = load_registry(registry_path or ACTIVE_STRATEGIES_PATH)
        rec = (reg.get("strategies") or {}).get("F1") or {}
        m = rec.get("oos_metrics") or {}
        span = float(m.get("paper_span_days") or 0.0)
        cycles = int(m.get("paper_cycles") or 0)
        failed = int(m.get("failed_leg_events") or 0)
    except Exception:
        return {"allowed": False, "reason": "F1 evidence unreadable (fail-closed)"}
    if cycles <= 0 or span < float(min_paper_days):
        return {"allowed": False,
                "reason": f"F1 clean paper span {span:.1f}d/{cycles} cycles "
                          f"< required {min_paper_days:.0f}d"}
    if failed > 0:
        return {"allowed": False,
                "reason": f"{failed} unresolved one-leg event(s) in F1 evidence"}
    # Fee-tier precondition — evidence is clean; now require verified maker
    # economics OR an explicit taker/taker acknowledgement (fail-closed).
    if taker_economics_acknowledged:
        fee_note = "taker/taker floor acknowledged"
    elif maker_fee_frac is None:
        return {"allowed": False,
                "reason": "maker fee tier unverified and taker/taker floor "
                          "not acknowledged (fail-closed)"}
    elif float(maker_fee_frac) > float(max_maker_fee_frac):
        return {"allowed": False,
                "reason": f"maker fee {float(maker_fee_frac):.6f} > "
                          f"max {float(max_maker_fee_frac):.6f}"}
    else:
        fee_note = f"maker fee {float(maker_fee_frac):.6f} <= {float(max_maker_fee_frac):.6f}"
    return {"allowed": True,
            "reason": f"F1 evidence clean over {span:.1f}d ({cycles} cycles); {fee_note}"}


def build_f2_spec():
    """Declarative F2 spec — registered DISABLED until F1 is stable (latch above)."""
    from core.strategy_spec import StrategySpec

    return StrategySpec(
        id="F2_XVENUE_FUNDING_SPREAD",
        family="carry",
        market_type="perp_perp_cross_venue",
        venues=["binance", "bybit", "bitget"],
        symbols=["BTC/USDT", "ETH/USDT"],
        data_required=[
            "funding_rate", "funding_interval", "next_funding_ts",
            "perp_bbo_both_venues", "book_depth", "fees",
        ],
        entry_rules={
            "type": "cross_venue_funding_spread",
            "interval_normalized_per_hour": True,
            "min_net_edge_bps_floor": F2_MIN_NET_EDGE_BPS,
            "cost_mult_floor": F2_COST_MULT_FLOOR,
            "buffers": ["slippage", "latency", "failed_leg"],
            "pre_funded_venues_assumed": True,
            "atomic_two_leg_fill": True,
        },
        exit_rules={
            "spread_decay_below_floor": True,
            "one_leg_failure_block": True,
        },
        sizing={"per_venue_pre_funded_margin_required": True},
        risk_limits={
            "activation_latch": "f2_activation_allowed",
            "requires_f1_clean_paper_days": 30,
            "maker_fee_precondition_frac": F2_MAX_MAKER_FEE_FRAC,
            "taker_taker_floor_ack_alternative": True,
            "live_execution_notes": [
                "asymmetric maker/taker leg routing is a live-execution concern; "
                "the paper floor prices taker/taker both legs"
            ],
        },
        validation_status="untested",
        promotion_status="disabled_until_f1_stable",
    )


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
