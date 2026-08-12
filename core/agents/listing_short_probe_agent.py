"""ListingShortProbeAgent — LOG-ONLY post-listing perp-short shadow probe.

Integrates the strategy pipeline's FIRST CONFIRMED_GO candidate:
capital-scaled post-listing perp short, rev3 (2026-07-09). See
_workspace/strategy_pipeline/02b_rev3_screener_listing_short.md (frozen
pre-registration, GO @7d,30d) and 03_rev3_audit_findings.md (honesty-auditor
CONFIRMED_GO, log-only, with binding conditions B1-B6).

HYPOTHESIS (unchanged from the screen): a freshly-listed USDT perp shorted at
its day-1 close decays after the day-1 pump and, sized at the charter caps
(3% notional per listing, 12% concurrent), earns a positive after-ALL-cost
short return while keeping the account drawdown inside the capital-preservation
bound. This probe measures that FORWARD, live, at zero capital risk.

READING THE RESULTS — the TP-probe precedent (core/agents/tp_probe_agent.py):
a raw hit-rate / win-rate is a geometry artifact until it is placed NEXT TO the
resolved after-cost net. Never read a listing-short win-rate without the
resolved shadow_outcomes.net_pnl AND the realized funding (this row's
realized_funding_rate_sum). The concurrent-MTM drawdown (auditor B1) — not the
per-trade realized cumsum — is the risk number the promotion gate must gate on.

LOG-ONLY (charter, non-negotiable): this class holds ONLY read-only providers
(markets / market-data / OHLCV / balance) plus the warehouse. It has no
reference to any order path and is structurally incapable of placing an order.
Promotion beyond shadow happens ONLY via core/promotion_gate.py on resolved
outcomes AND an explicit owner decision — never here.

Realized after-cost economics are produced by the vetted keystone resolver
(core/shadow_resolver.py) off the shadow_decisions rows this probe writes
(side='sell', no SL/TP, held to the horizon bar). This module performs NO PnL
math on outcomes — custom PnL math in a probe is a bug. It only logs the extra
binding-condition evidence the resolver does not capture: the per-bar intra-hold
MTM path, the concurrent account-MTM drawdown, the day-1 execution realism, and
the pre-specified discriminating score.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from core.agents.probe_common import (
    FUNDING_SETTLE_S,
    _pos_float,
    concurrent_account_mtm,
    ensure_schema,
    insert_row,  # standalone SKIP/MTM rows (an ENTRY uses write_entry_pair)
    unrealized_short_return,
    write_entry_pair,
)

# ── Frozen rev3 constants (pre-registration §Sizing / §Universe / §Horizons) ──
STAKE_FRAC = 0.03                 # 3% of account per listing short (CLAUDE.md §2)
MAX_CONCURRENT_EXPOSURE = 0.12    # 12% total concurrent listing exposure cap
MAX_CONCURRENT = int(round(MAX_CONCURRENT_EXPOSURE / STAKE_FRAC))  # -> 4
HORIZONS_D = (7, 30)              # 90d is INSUFFICIENT (audit) -> not probed
HOUR_S = 3600
DAY_S = 24 * HOUR_S
ENTRY_DELAY_S = DAY_S             # entry at the day-1 close (screen: first_ts + DAY)
MAX_ENTRY_WAIT_S = 3 * DAY_S      # give up waiting for day-1 data after 3d -> SKIP_NO_DATA
TIMEFRAME = "1h"
DEFAULT_STATE_PATH = "data/shadow_listing_state.json"  # gitignored (/data/)

# Equity/commodity + non-ASCII exclusion. MIRROR of research/screen_listing_short.py
# (rev2 audit B1). Kept in sync by tests/test_listing_short_probe.py::
# test_is_crypto_base_mirrors_screen — do NOT edit one without the other.
EQUITY_COMMODITY_BASES = frozenset(
    {"AAPL", "AMZN", "CL", "COIN", "COPPER", "MSFT", "MSTR", "TSLA", "XAG", "XAU"}
)

# ── Discriminating score (binding condition B5) ──────────────────────────────
# PRE-SPECIFIED BEFORE any outcome data exists. The AUC>=0.60 promotion gate is
# un-computable without a per-decision score; this is that score. Monotone
# INCREASING in (a) the first-24h pump magnitude the short will fade, and (b) the
# funding accruing to the short (short receives + / pays - funding). Higher score
# => larger predicted after-funding short decay. It is NOT tuned to outcomes
# (none exist yet) — the AUC gate later tests whether it actually ranks resolved
# winners above losers. tanh bounds the pump term so a single +290% outlier
# cannot dominate the ranking.
SCORE_PUMP_SCALE = 0.50           # ~50% first-day pump -> ~unit pump contribution
SCORE_FUNDING_WEIGHT = 10.0       # one 8h funding rate weighted ~10x its raw value


# ── Scope gate: venue-metadata TradFi exclusion (2026-07-28) ─────────────────
# ADDITIVE to is_crypto_base above, never a replacement: that static mirror is
# frozen with the screen and could not anticipate binance listing tokenized
# stocks/ETFs. All 25 events this probe has ever observed were such tokens
# (info.underlyingType EQUITY x19 / HK_EQUITY x6) and were logged with the
# WRONG reason (SKIP_UNSHORTABLE). They are outside the crypto-only rev3
# universe and now get their own reason, SKIP_NOT_CRYPTO.
#
# The field checks mirror core/pair_discovery._is_tradfi_market. They are
# duplicated rather than imported ON PURPOSE: this log-only lane must not take
# a runtime dependency on the live universe module's private API.
TRADFI_UNDERLYING_TYPES = frozenset(
    {"COMMODITY", "EQUITY", "HK_EQUITY", "STOCK", "TRADFI"}
)


def is_crypto_base(base: str) -> bool:
    """True iff ``base`` is a crypto ticker (ASCII, not a tokenized
    equity/commodity perp). Mirror of research.screen_listing_short.is_crypto_base."""
    return bool(base) and base.isascii() and base.upper() not in EQUITY_COMMODITY_BASES


def is_tradfi_market(meta) -> bool:
    """True iff the VENUE's own market metadata marks this contract as TradFi
    (tokenized equity / commodity RWA).

    Defensive by construction: anything that is not a mapping, and any market
    whose metadata simply omits ``underlyingType``, is NOT tradfi — a genuine
    crypto perp on a venue that publishes no such field must never be excluded
    here (that would re-brick the lane). Absence of evidence is not evidence."""
    if not isinstance(meta, Mapping):
        return False
    info = meta.get("info")
    if not isinstance(info, Mapping):
        info = {}
    if "tradifi" in str(info.get("contractType") or "").lower():
        return True  # binance's own spelling: contractType=TRADIFI_PERPETUAL
    raw_type = info.get("underlyingType") or meta.get("underlyingType")
    if str(raw_type or "").strip().upper() in TRADFI_UNDERLYING_TYPES:
        return True
    subtypes = info.get("underlyingSubType")
    if not isinstance(subtypes, (list, tuple)):
        subtypes = [subtypes]
    return any("tradfi" in str(s or "").lower() for s in subtypes)


# ── Shortability classifier (2026-07-28 unbrick) ─────────────────────────────
# The question this answers is "is this perp LIVE and TRADEABLE, so a short
# could be opened?" — not "did the ticker endpoint carry a book?". The previous
# test (`active and bid and ask`) could never be true on the probed venue:
# binance USDT-M ccxt fetch_ticker returns bid=None/ask=None for EVERY symbol
# (its 24hr-ticker endpoint carries no book; bookTicker/order-book would), so
# the probe logged 25/25 SKIP_UNSHORTABLE with 0 ENTER in 19 days.
#
# bid/ask stay AUTHORITATIVE when present. Only when the provider omits them do
# we fall back to venue-published evidence that the market is live: active
# market + a positive last price + positive quote volume. An inactive market,
# a market with no price, or a market with zero traded volume is still NOT
# shortable. The evidence path used is recorded per row (shortable_basis) so a
# future screen can condition on it.
SHORTABLE_BASIS_BOOK = "bid_ask"        # both sides of the book present
SHORTABLE_BASIS_TRADED = "last_volume"  # no book published; live + traded
UNSHORTABLE_INACTIVE = "inactive"       # venue says the market is not active
UNSHORTABLE_NO_QUOTE = "no_quote"       # no book AND no live price/volume


def classify_shortability(md) -> tuple:
    """(shortable, basis) for a market-data dict. See the block comment above."""
    if not isinstance(md, Mapping):
        md = {}
    if not bool(md.get("active", True)):
        return False, UNSHORTABLE_INACTIVE
    bid = _pos_float(md.get("bid"))
    ask = _pos_float(md.get("ask"))
    if bid and ask:
        return True, SHORTABLE_BASIS_BOOK
    if _pos_float(md.get("last")) and _pos_float(md.get("quoteVolume")):
        return True, SHORTABLE_BASIS_TRADED
    return False, UNSHORTABLE_NO_QUOTE


def day1_spread_bps(md) -> Optional[float]:
    """Day-1 book spread in bps, or None when it is UNKNOWN (no book published,
    or a crossed book). Never fabricate 0.0: a zero spread would pass any
    "spread must be tight" quality gate trivially — a fail-OPEN worse than the
    brick. Unknown is recorded as NULL and must be treated as unknown."""
    if not isinstance(md, Mapping):
        return None
    bid = _pos_float(md.get("bid"))
    ask = _pos_float(md.get("ask"))
    if not (bid and ask) or ask < bid:
        return None
    return (ask - bid) / ((ask + bid) / 2.0) * 1e4


# ── Default venue-metadata provider ──────────────────────────────────────────
# The engine's market-data provider (core/bot_engine._listing_market_data)
# returns only bid/ask/last/quoteVolume/funding_rate — it drops ccxt's `info`,
# so underlyingType is unreachable through it. Rather than reach into the
# engine, the probe resolves metadata itself with a PUBLIC, READ-ONLY
# load_markets(), cached per venue and refreshed once on a symbol miss (a
# brand-new listing is absent from a stale snapshot). Called at most once per
# new-listing decision (~1.3/day observed), never on a quiet tick.
# Returns the market mapping, or None when metadata is UNAVAILABLE — which the
# caller treats as fail-CLOSED (SKIP_NO_METADATA), not as "crypto, proceed".
_CCXT_VENUE_CLASSES = {"binance": "binanceusdm"}
_MARKETS_CACHE: dict = {}          # venue -> (fetched_ts, markets)
_MARKETS_ATTEMPT_TS: dict = {}     # venue -> last load attempt ts (success OR failure)
MARKETS_CACHE_TTL_S = 6 * HOUR_S
MARKETS_NEG_TTL_S = 15 * 60        # negative cache: don't hammer a down venue


def _load_venue_markets(venue: str) -> Optional[dict]:
    try:
        import ccxt

        cls = getattr(ccxt, _CCXT_VENUE_CLASSES.get(venue, venue))
        client = cls({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        return client.load_markets() or None   # public endpoint; no keys, no orders
    except Exception as e:
        logger.warning(f"[ListingProbe] venue metadata load failed ({venue}): {e}")
        return None


def venue_market_meta(venue: str, symbol: str, now: Optional[float] = None) -> Optional[dict]:
    """Cached read-only market metadata for ``symbol``; None if unavailable.

    Negative caching (F3b, 2026-07-29): every load_markets attempt — success
    or failure — is timestamped per venue, and no re-fetch happens within
    MARKETS_NEG_TTL_S. A persistent metadata outage previously re-issued a
    FULL load_markets() on every probe tick for up to ~4 days per stuck
    listing (ENTRY_DELAY_S + MAX_ENTRY_WAIT_S). A brand-new listing absent
    from a stale snapshot is now refreshed at most once per NEG-TTL window
    instead of every call — the entry window is measured in days, so a
    <=15-minute metadata delay cannot burn a listing. Fail direction is
    unchanged: unavailable metadata returns None (caller SKIP_NO_METADATA,
    fail-closed)."""
    ts = float(now if now is not None else time.time())
    cached = _MARKETS_CACHE.get(venue)
    markets = cached[1] if cached else None
    if markets is not None and symbol in markets:
        if ts - cached[0] < MARKETS_CACHE_TTL_S:
            return markets[symbol]
    last_attempt = _MARKETS_ATTEMPT_TS.get(venue)
    if last_attempt is not None and 0 <= ts - last_attempt < MARKETS_NEG_TTL_S:
        # within the negative-cache window: serve what we have, never re-fetch
        if markets and symbol in markets:
            return markets[symbol]   # stale-but-present beats hammering the venue
        return None
    _MARKETS_ATTEMPT_TS[venue] = ts
    fresh = _load_venue_markets(venue)
    if fresh is not None:
        _MARKETS_CACHE[venue] = (ts, fresh)
        markets = fresh
    if markets and symbol in markets:
        return markets[symbol]
    return None


def _base_of(symbol: str) -> str:
    """Base ticker of a unified perp symbol: 'SOMI/USDT:USDT' -> 'SOMI'."""
    return symbol.split("/")[0].split("-")[0]


def listing_short_score(pump_pct: float, funding_rate: float) -> float:
    """Pre-specified discriminating score (B5). See module header for rationale."""
    try:
        pump = float(pump_pct)
        fund = float(funding_rate)
    except (TypeError, ValueError):
        return 0.0
    return math.tanh(pump / SCORE_PUMP_SCALE) + SCORE_FUNDING_WEIGHT * fund


def compute_pump_pct(highs, listing_px: float) -> float:
    """First-hours pump magnitude: (max first-24h high - listing price) / listing.

    >= 0 for a genuine pump; the magnitude the short is betting will decay."""
    vals = [float(h) for h in (highs or []) if h is not None]
    if not vals or not listing_px or listing_px <= 0:
        return 0.0
    return (max(vals) - float(listing_px)) / float(listing_px)


# unrealized_short_return / concurrent_account_mtm / _pos_float moved to
# probe_common (shared with the unlock probe) and are re-exported above.


# shadow_decisions.model_version for this probe — importable (e.g. by
# scripts/gate_status.py) so a probe rename can never silently desync a reporter.
LISTING_SHORT_MODEL_VERSION = "listing_short_probe_v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_listing_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    base                       TEXT,
    horizon_days               INTEGER,
    decision                   TEXT,   -- ENTER | SKIP_CAP | SKIP_UNSHORTABLE
                                       -- | SKIP_NO_FUNDING | SKIP_NO_DATA
                                       -- | SKIP_NOT_CRYPTO  (venue says tradfi)
                                       -- | SKIP_NO_METADATA (scope unknowable)
    detected_ts                INTEGER,
    entry_ts                   INTEGER,
    entry_px                   REAL,
    listing_px                 REAL,
    stake_frac                 REAL,
    notional_usd               REAL,
    day1_spread_bps            REAL,   -- NULL when the venue publishes no book
    day1_funding_rate          REAL,
    shortable                  INTEGER,
    shortable_basis            TEXT,   -- which evidence decided shortable:
                                       -- bid_ask | last_volume | inactive
                                       -- | no_quote | NULL (not reached)
    quote_volume_usd           REAL,
    pump_pct                   REAL,
    score                      REAL,
    realized_funding_rate_sum  REAL,
    last_funding_bucket        INTEGER,
    concurrent_open_at_entry   INTEGER,
    created_ts                 INTEGER
);
CREATE INDEX IF NOT EXISTS idx_listing_probe_horizon
    ON shadow_listing_probe(horizon_days, decision);

CREATE TABLE IF NOT EXISTS shadow_listing_mtm (
    proposal_id           TEXT,
    bar_ts                INTEGER,
    mark_px               REAL,
    unrealized_short_ret  REAL,
    PRIMARY KEY (proposal_id, bar_ts)
);

CREATE TABLE IF NOT EXISTS shadow_listing_concurrent (
    horizon_days   INTEGER,
    snapshot_ts    INTEGER,
    n_open         INTEGER,
    account_mtm    REAL,
    peak_equity    REAL,
    max_drawdown   REAL,
    PRIMARY KEY (horizon_days, snapshot_ts)
);
"""


class ListingShortProbeAgent:
    """Market-wide new-listing detector + log-only short proposer. See header."""

    name = "ListingShortProbeAgent"
    model_version = LISTING_SHORT_MODEL_VERSION

    def __init__(
        self,
        *,
        warehouse,
        markets_provider: Callable[[], list],
        market_data_provider: Callable[[str], dict],
        ohlcv_provider: Callable[[str, str, int], list],
        account_balance_provider: Callable[[], float],
        market_meta_provider: Optional[Callable[[str], Optional[dict]]] = None,
        now_fn: Callable[[], float] = time.time,
        state_path: Optional[str] = None,
        venue: str = "binance",
    ):
        self._wh = warehouse
        self._markets = markets_provider
        self._market_data = market_data_provider
        self._ohlcv = ohlcv_provider
        self._balance = account_balance_provider
        self._now = now_fn
        self._venue = venue
        # Read-only venue metadata for the scope gate. Injectable (tests, and a
        # future engine-side provider); the default resolves lazily on the
        # first new-listing decision — never at construction time.
        self._market_meta = market_meta_provider or (
            lambda sym: venue_market_meta(self._venue, sym)
        )
        self._state_path = Path(state_path or DEFAULT_STATE_PATH)
        # F3a (2026-07-29): column migration is DEFERRED to the first tick.
        # ALTERing the LIVE warehouse at construction time under bot_engine's
        # fail-open _build_probe meant a transient DB error (e.g. "database is
        # locked" at boot, plausible with the live writer + hourly funnel task)
        # silently removed the whole listing lane for the boot with only a
        # WARNING. A migration failure now degrades loudly-but-alive and is
        # retried each tick until it succeeds.
        self._migrated = False
        self._migrate_warned = False
        self._ensure_schema()
        self._state = self._load_state()

    # ── schema / state ───────────────────────────────────────────────────
    def _ensure_schema(self) -> None:
        ensure_schema(self._wh, _SCHEMA)

    def _try_migrate(self) -> bool:
        """Run the deferred column migration; True once it has succeeded.
        On failure the probe stays constructed and idle, retrying next tick —
        never silently removed from the lane (the silent-idle class)."""
        if self._migrated:
            return True
        try:
            self._migrate_columns()
            self._migrated = True
            return True
        except Exception as e:
            if not self._migrate_warned:
                logger.error(
                    "[ListingProbe] column migration FAILED — probe idle until "
                    f"it succeeds (retrying each tick): {e}")
                self._migrate_warned = True
            else:
                logger.debug(f"[ListingProbe] column migration still failing: {e}")
            return False

    def _migrate_columns(self) -> None:
        """Additive, idempotent column migration. CREATE TABLE IF NOT EXISTS
        cannot widen the LIVE table (it already holds this probe's history), so
        columns added after the table's creation are ALTERed in once. Existing
        rows keep NULL — an honest "not recorded", never a back-filled guess."""
        conn = self._wh._conn()
        have = {r[1] for r in conn.execute(
            "PRAGMA table_info(shadow_listing_probe)").fetchall()}
        for col, decl in (("shortable_basis", "TEXT"),):
            if col not in have:
                conn.execute(
                    f"ALTER TABLE shadow_listing_probe ADD COLUMN {col} {decl}")
        conn.commit()

    def _load_state(self) -> dict:
        try:
            if self._state_path.exists():
                st = json.loads(self._state_path.read_text(encoding="utf-8"))
                st.setdefault("seeded", False)
                st.setdefault("known", [])
                st.setdefault("pending", {})
                return st
        except (OSError, ValueError) as e:
            logger.warning(f"[ListingProbe] state load failed ({e}); reseeding")
        return {"seeded": False, "known": [], "pending": {}}

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as e:
            logger.warning(f"[ListingProbe] state save failed: {e}")

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: detect new listings, enter ready ones, update MTM.
        Returns a small stats dict. NEVER raises into the caller."""
        stats = {"detected": 0, "entered": 0, "skipped": 0, "mtm_rows": 0}
        if not self._try_migrate():
            return stats   # loud, alive, retried next tick — no rows until then
        now = int(self._now())
        try:
            universe = self._current_universe()
        except Exception as e:
            logger.debug(f"[ListingProbe] universe error: {e}")
            universe = set()
        if universe:
            stats["detected"] = self._detect(universe, now)
        try:
            self._reclassify_legacy_unshortable_tradfi(now)
        except Exception as e:
            logger.debug(f"[ListingProbe] reclassify error: {e}")
        try:
            e, s = self._enter_ready(now)
            stats["entered"], stats["skipped"] = e, s
        except Exception as e:  # noqa: F841
            logger.debug(f"[ListingProbe] enter error: {e}")
        try:
            stats["mtm_rows"] = self._monitor_open_shorts(now)
        except Exception as e:
            logger.debug(f"[ListingProbe] monitor error: {e}")
        return stats

    # ── detection ────────────────────────────────────────────────────────
    def _current_universe(self) -> set:
        syms = self._markets() or []
        return {s for s in syms if is_crypto_base(_base_of(s))}

    def _detect(self, universe: set, now: int) -> int:
        if not self._state.get("seeded"):
            # First run: establish the baseline. We can only shadow-probe
            # listings that appear AFTER this point — proposing on the whole
            # existing universe would be dishonest (they are not new).
            self._state = {"seeded": True, "known": sorted(universe), "pending": {}}
            self._save_state()
            return 0
        known = set(self._state["known"])
        new = sorted(universe - known)
        if not new:
            return 0
        detected = 0
        for sym in new:
            # Scope at detection (2026-07-29): TradFi must never enter the
            # day-1 wait queue. Waiting then logging SKIP_UNSHORTABLE (pre-fix)
            # or even SKIP_NOT_CRYPTO after a day wastes the lane's triage
            # signal — every observed Binance "listing" in the starved window
            # was TRADIFI_PERPETUAL. Crypto (or unknown meta) still pend.
            meta = self._safe_market_meta(sym)
            if meta is not None and is_tradfi_market(meta):
                self._log_skip(sym, "SKIP_NOT_CRYPTO", now, detected_ts=now)
                self._state["pending"][sym] = {
                    "first_seen_ts": now, "entered": True,
                }
                continue
            self._state["pending"][sym] = {"first_seen_ts": now, "entered": False}
            detected += 1
        self._state["known"] = sorted(known | universe)
        self._save_state()
        return detected

    def _reclassify_legacy_unshortable_tradfi(self, now: int) -> None:
        """One-shot honesty fix for the starved window.

        Pre-unbrick rows logged SKIP_UNSHORTABLE because bid/ask were None on
        every Binance USDT-M ticker — including tokenized equities that are
        OUT OF the crypto rev3 universe. Those mislabels keep mission-control
        in STARVED/ALARM (SKIP_UNSHORTABLE is deliberately not a scope reason).
        Relabel them to SKIP_NOT_CRYPTO when venue metadata confirms TradFi.
        Idempotent via state flag; never invents TradFi when meta is missing.
        """
        if self._state.get("reclassified_tradfi_skips_v1"):
            return
        if not self._state.get("seeded"):
            return
        try:
            rows = self._wh.query(
                "SELECT DISTINCT symbol FROM shadow_listing_probe "
                "WHERE decision='SKIP_UNSHORTABLE' AND "
                "(shortable_basis IS NULL OR shortable_basis='')"
            ) or []
            if not rows:
                # Do NOT set the flag on an empty pass — a later insert (or the
                # live warehouse's pre-existing rows arriving after seed) must
                # still be eligible for one honesty pass.
                return
            n = 0
            for r in rows:
                sym = str(r.get("symbol") or "")
                if not sym:
                    continue
                meta = self._safe_market_meta(sym)
                if meta is None or not is_tradfi_market(meta):
                    continue
                conn = self._wh._conn()
                conn.execute(
                    "UPDATE shadow_listing_probe SET decision='SKIP_NOT_CRYPTO' "
                    "WHERE symbol=? AND decision='SKIP_UNSHORTABLE' AND "
                    "(shortable_basis IS NULL OR shortable_basis='')",
                    (sym,),
                )
                conn.commit()
                n += 1
            self._state["reclassified_tradfi_skips_v1"] = True
            self._save_state()
            if n:
                logger.info(
                    f"[ListingProbe] reclassified {n} legacy SKIP_UNSHORTABLE "
                    f"→ SKIP_NOT_CRYPTO (TradFi mislabel; ts={now})"
                )
        except Exception as e:
            logger.warning(f"[ListingProbe] legacy TradFi reclassify deferred: {e}")

    # ── entry ────────────────────────────────────────────────────────────
    def _enter_ready(self, now: int) -> tuple:
        # chronological: oldest first_seen first (ties by symbol) so the
        # concurrency skip is purely time-ordered (no cherry-picking, B3).
        ready = sorted(
            (
                (sym, d)
                for sym, d in self._state["pending"].items()
                if not d.get("entered") and now - int(d["first_seen_ts"]) >= ENTRY_DELAY_S
            ),
            key=lambda kv: (int(kv[1]["first_seen_ts"]), kv[0]),
        )
        entered = skipped = 0
        for sym, d in ready:
            e, s = self._try_enter(sym, d, now)
            entered += e
            skipped += s
        if ready:
            self._save_state()
        return entered, skipped

    def _try_enter(self, sym: str, d: dict, now: int) -> tuple:
        first_seen = int(d["first_seen_ts"])
        candles = self._ohlcv(sym, TIMEFRAME, first_seen * 1000) or []
        if not candles:
            if now - first_seen > ENTRY_DELAY_S + MAX_ENTRY_WAIT_S:
                self._log_skip(sym, "SKIP_NO_DATA", now, detected_ts=first_seen)
                d["entered"] = True
                return 0, 1
            return 0, 0  # retry next tick

        listing_ts = int(candles[0][0]) // 1000
        listing_px = float(candles[0][4])
        entry_target = listing_ts + ENTRY_DELAY_S
        entry_bar = next((c for c in candles if int(c[0]) // 1000 >= entry_target), None)
        if entry_bar is None:
            if now - first_seen > ENTRY_DELAY_S + MAX_ENTRY_WAIT_S:
                self._log_skip(sym, "SKIP_NO_DATA", now, detected_ts=first_seen)
                d["entered"] = True
                return 0, 1
            return 0, 0  # day-1 close not yet formed
        entry_ts = int(entry_bar[0]) // 1000
        entry_px = float(entry_bar[4])
        highs = [c[2] for c in candles if listing_ts <= int(c[0]) // 1000 <= entry_ts]
        pump_pct = compute_pump_pct(highs, listing_px)

        # ── Scope gate (runs BEFORE shortability so an out-of-scope token can
        # never be mislabeled "unshortable"). ADDITIVE to is_crypto_base.
        meta = self._safe_market_meta(sym)
        if meta is None:
            # Scope is UNKNOWABLE — fail CLOSED, never "crypto, proceed". A
            # TRANSIENT outage must not burn a genuine listing, so retry while
            # the entry window is open (same idiom as SKIP_NO_DATA above; the
            # entry bar comes from the candles, so timing is unaffected) and
            # log the terminal skip only once the window expires.
            if now - first_seen > ENTRY_DELAY_S + MAX_ENTRY_WAIT_S:
                self._log_skip(sym, "SKIP_NO_METADATA", now, detected_ts=first_seen,
                               entry_ts=entry_ts, entry_px=entry_px,
                               listing_px=listing_px, pump_pct=pump_pct)
                d["entered"] = True
                return 0, 1
            return 0, 0

        # F3c (2026-07-29): consume the pending listing ONLY at the points
        # where a terminal row has been written. Setting entered=True before
        # the market-data call meant a raising provider consumed the listing
        # with NO row written — silent evidence loss (latent: the engine-side
        # provider fail-opens to {}). A raising provider now leaves the
        # listing pending for retry on the next tick.
        if is_tradfi_market(meta):
            self._log_skip(sym, "SKIP_NOT_CRYPTO", now, detected_ts=first_seen,
                           entry_ts=entry_ts, entry_px=entry_px,
                           listing_px=listing_px, pump_pct=pump_pct)
            d["entered"] = True
            return 0, 1

        md = self._market_data(sym) or {}
        shortable, basis = classify_shortability(md)
        funding = md.get("funding_rate")
        qv = _pos_float(md.get("quoteVolume")) or 0.0
        spread_bps = day1_spread_bps(md)   # None (NULL) when the book is absent

        if not shortable:
            self._log_skip(sym, "SKIP_UNSHORTABLE", now, detected_ts=first_seen,
                           entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
                           pump_pct=pump_pct, day1_spread_bps=spread_bps,
                           quote_volume_usd=qv, shortable=0, shortable_basis=basis)
            d["entered"] = True
            return 0, 1
        if funding is None:
            # never guess funding — the short's dominant cost (screen: charged, not modelled)
            self._log_skip(sym, "SKIP_NO_FUNDING", now, detected_ts=first_seen,
                           entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
                           pump_pct=pump_pct, day1_spread_bps=spread_bps,
                           quote_volume_usd=qv, shortable=1, shortable_basis=basis)
            d["entered"] = True
            return 0, 1

        funding = float(funding)
        score = listing_short_score(pump_pct, funding)
        notional = STAKE_FRAC * float(self._balance() or 0.0)

        entered = skipped = 0
        for H in HORIZONS_D:
            open_now = self._open_count(H, now)
            common = dict(
                symbol=sym, base=_base_of(sym), horizon_days=H, detected_ts=first_seen,
                entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
                stake_frac=STAKE_FRAC, notional_usd=notional, day1_spread_bps=spread_bps,
                day1_funding_rate=funding, shortable=1, shortable_basis=basis,
                quote_volume_usd=qv, pump_pct=pump_pct, score=score,
                concurrent_open_at_entry=open_now,
            )
            if open_now >= MAX_CONCURRENT:
                pid = f"ls-{uuid.uuid4().hex[:10]}"
                self._write_probe_row(proposal_id=pid, decision="SKIP_CAP", now=now, **common)
                skipped += 1
                continue
            pid = f"ls-{uuid.uuid4().hex[:10]}"
            # ENTER writes the decision + probe rows ATOMICALLY (a split write
            # can orphan the decision — see probe_common.write_entry_pair).
            self._write_probe_row(
                proposal_id=pid, decision="ENTER", now=now,
                decision_row=self._decision_row(pid, sym, entry_ts, entry_px, H, notional),
                **common,
            )
            entered += 1
        d["entered"] = True   # terminal rows written for every horizon
        return entered, skipped

    def _open_count(self, horizon_days: int, now: int) -> int:
        rows = self._wh.query(
            "SELECT COUNT(*) AS n FROM shadow_listing_probe "
            "WHERE horizon_days=? AND decision='ENTER' AND (entry_ts + ?) > ?",
            (horizon_days, horizon_days * DAY_S, now),
        )
        return int(rows[0]["n"]) if rows else 0

    # ── monitoring: per-bar MTM path + concurrent drawdown ────────────────
    def _monitor_open_shorts(self, now: int) -> int:
        rows = self._wh.query(
            "SELECT proposal_id, symbol, horizon_days, entry_ts, entry_px, "
            "last_funding_bucket, realized_funding_rate_sum "
            "FROM shadow_listing_probe WHERE decision='ENTER'"
        )
        written = 0
        touched: set = set()
        for r in rows:
            pid = r["proposal_id"]
            sym = r["symbol"]
            H = int(r["horizon_days"])
            entry_ts = int(r["entry_ts"])
            entry_px = float(r["entry_px"])
            end_ts = entry_ts + H * DAY_S
            if now < entry_ts:
                continue
            last = self._wh.query(
                "SELECT MAX(bar_ts) AS m FROM shadow_listing_mtm WHERE proposal_id=?", (pid,)
            )
            since = (int(last[0]["m"]) + 1) if last and last[0]["m"] is not None else entry_ts
            cap = min(now, end_ts)
            if since <= cap:
                candles = self._ohlcv(sym, TIMEFRAME, since * 1000) or []
                for c in candles:
                    bar_ts = int(c[0]) // 1000
                    if bar_ts < entry_ts or bar_ts > end_ts:
                        continue
                    if bar_ts + HOUR_S > now:
                        continue  # forming/unclosed bar — no repaint
                    mark = float(c[4])
                    ur = unrealized_short_return(entry_px, mark)
                    self._wh._conn().execute(
                        "INSERT OR REPLACE INTO shadow_listing_mtm "
                        "(proposal_id, bar_ts, mark_px, unrealized_short_ret) VALUES (?,?,?,?)",
                        (pid, bar_ts, mark, ur),
                    )
                    written += 1
            self._accrue_funding(r, sym, now)
            touched.add(H)
        if written or touched:
            self._wh._conn().commit()
        for H in touched:
            self._log_concurrent(H, now)
        return written

    def _accrue_funding(self, row: dict, sym: str, now: int) -> None:
        md = self._market_data(sym) or {}
        fr = md.get("funding_rate")
        if fr is None:
            return
        bucket = now // FUNDING_SETTLE_S
        if row["last_funding_bucket"] is not None and int(row["last_funding_bucket"]) == bucket:
            return  # this 8h settlement already booked
        new_sum = float(row["realized_funding_rate_sum"] or 0.0) + float(fr)
        self._wh._conn().execute(
            "UPDATE shadow_listing_probe SET realized_funding_rate_sum=?, "
            "last_funding_bucket=? WHERE proposal_id=?",
            (new_sum, int(bucket), row["proposal_id"]),
        )

    def _log_concurrent(self, horizon_days: int, now: int) -> None:
        rows = self._wh.query(
            "SELECT m.proposal_id AS pid, m.bar_ts AS bar_ts, "
            "m.unrealized_short_ret AS ur "
            "FROM shadow_listing_mtm m JOIN shadow_listing_probe p "
            "ON p.proposal_id=m.proposal_id "
            "WHERE p.horizon_days=? AND p.decision='ENTER'",
            (horizon_days,),
        )
        if not rows:
            return
        bars_by_pos: dict = {}
        for r in rows:
            bars_by_pos.setdefault(r["pid"], []).append((int(r["bar_ts"]), float(r["ur"])))
        series, max_dd = concurrent_account_mtm(bars_by_pos, STAKE_FRAC)
        cur_mtm = series[-1][1] if series else 0.0
        peak_equity = max([1.0] + [1.0 + m for _, m in series]) if series else 1.0
        n_open = self._open_count(horizon_days, now)
        self._wh._conn().execute(
            "INSERT OR REPLACE INTO shadow_listing_concurrent "
            "(horizon_days, snapshot_ts, n_open, account_mtm, peak_equity, max_drawdown) "
            "VALUES (?,?,?,?,?,?)",
            (horizon_days, now, n_open, cur_mtm, peak_equity, max_dd),
        )
        self._wh._conn().commit()

    # ── warehouse writes ─────────────────────────────────────────────────
    def _decision_row(self, pid, sym, entry_ts, entry_px, horizon_days,
                      notional) -> dict:
        """The shadow_decisions kwargs for the keystone resolver, which replays
        the row into shadow_outcomes. side='sell', no SL/TP (naked, held to the
        horizon bar — auditor B2 unlevered-3%-notional, no-SL variant). sim_pnl
        is left NULL: this probe does no PnL projection; the resolver owns the
        after-cost net. Built here, written by _write_probe_row's atomic pair."""
        return dict(
            ts=entry_ts, model_version=self.model_version, symbol=sym,
            side="sell", agent_id=self.name, proposal_id=pid, notional=notional,
            entry_px=entry_px,
            sl_px=0.0,   # no stop: sl>0 checks in the resolver never fire
            tp_px=0.0,   # no target: runs to the horizon bar (time exit)
            venue=self._venue, timeframe=TIMEFRAME,
            horizon_bars=int(horizon_days) * 24,
        )

    def _safe_market_meta(self, symbol: str) -> Optional[dict]:
        """Venue metadata for ``symbol``; None when UNAVAILABLE. Never raises —
        a metadata failure must degrade to an honest skip, not kill the tick."""
        try:
            meta = self._market_meta(symbol)
        except Exception as e:
            logger.debug(f"[ListingProbe] metadata {symbol} failed: {e}")
            return None
        return meta if isinstance(meta, Mapping) else None

    def _write_probe_row(self, *, proposal_id, decision, now, symbol, base,
                         horizon_days, detected_ts, entry_ts, entry_px, listing_px,
                         stake_frac, notional_usd, day1_spread_bps, day1_funding_rate,
                         shortable, quote_volume_usd, pump_pct, score,
                         concurrent_open_at_entry, shortable_basis=None,
                         decision_row=None) -> None:
        """Write the probe row. ``decision_row`` (ENTER only) pairs it with its
        shadow_decisions row in ONE transaction; the SKIP paths pass None —
        they have no decision row and no position."""
        row = {
            "proposal_id": proposal_id, "symbol": symbol, "base": base,
            "horizon_days": int(horizon_days), "decision": decision,
            "detected_ts": int(detected_ts), "entry_ts": int(entry_ts),
            "entry_px": float(entry_px), "listing_px": float(listing_px),
            "stake_frac": float(stake_frac), "notional_usd": float(notional_usd),
            # NULL-preserving: an unknown spread stays NULL, never 0.0
            "day1_spread_bps": (None if day1_spread_bps is None
                                else float(day1_spread_bps)),
            "day1_funding_rate": float(day1_funding_rate),
            "shortable": int(shortable),
            "shortable_basis": (None if shortable_basis is None
                                else str(shortable_basis)),
            "quote_volume_usd": float(quote_volume_usd),
            "pump_pct": float(pump_pct), "score": float(score),
            "realized_funding_rate_sum": 0.0, "last_funding_bucket": None,
            "concurrent_open_at_entry": int(concurrent_open_at_entry),
            "created_ts": int(now),
        }
        if decision_row is None:
            insert_row(self._wh, "shadow_listing_probe", row, replace=True)
        else:
            write_entry_pair(
                self._wh, decision=decision_row,
                probe_table="shadow_listing_probe", probe_row=row, replace=True,
            )

    def _log_skip(self, sym: str, decision: str, now: int, *, detected_ts,
                  entry_ts=0, entry_px=0.0, listing_px=0.0, pump_pct=0.0,
                  day1_spread_bps=None, quote_volume_usd=0.0, shortable=0,
                  shortable_basis=None) -> None:
        # A pre-entry skip (no shadow_decisions row, no position). horizon_days=0
        # so it never counts toward the concurrency cap or a horizon's stats.
        self._write_probe_row(
            proposal_id=f"ls-{uuid.uuid4().hex[:10]}", decision=decision, now=now,
            symbol=sym, base=_base_of(sym), horizon_days=0, detected_ts=detected_ts,
            entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
            stake_frac=STAKE_FRAC, notional_usd=0.0, day1_spread_bps=day1_spread_bps,
            day1_funding_rate=0.0, shortable=shortable, shortable_basis=shortable_basis,
            quote_volume_usd=quote_volume_usd, pump_pct=pump_pct, score=0.0,
            concurrent_open_at_entry=0,
        )
