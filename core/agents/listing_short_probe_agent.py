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

import bisect
import json
import math
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

# ── Frozen rev3 constants (pre-registration §Sizing / §Universe / §Horizons) ──
STAKE_FRAC = 0.03                 # 3% of account per listing short (CLAUDE.md §2)
MAX_CONCURRENT_EXPOSURE = 0.12    # 12% total concurrent listing exposure cap
MAX_CONCURRENT = int(round(MAX_CONCURRENT_EXPOSURE / STAKE_FRAC))  # -> 4
HORIZONS_D = (7, 30)              # 90d is INSUFFICIENT (audit) -> not probed
HOUR_S = 3600
DAY_S = 24 * HOUR_S
ENTRY_DELAY_S = DAY_S             # entry at the day-1 close (screen: first_ts + DAY)
MAX_ENTRY_WAIT_S = 3 * DAY_S      # give up waiting for day-1 data after 3d -> SKIP_NO_DATA
FUNDING_SETTLE_S = 8 * HOUR_S     # perp funding settles every 8h
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


def is_crypto_base(base: str) -> bool:
    """True iff ``base`` is a crypto ticker (ASCII, not a tokenized
    equity/commodity perp). Mirror of research.screen_listing_short.is_crypto_base."""
    return bool(base) and base.isascii() and base.upper() not in EQUITY_COMMODITY_BASES


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


def unrealized_short_return(entry_px: float, mark_px: float) -> float:
    """Unrealized return of a SHORT marked at ``mark_px``: (entry - mark) / entry.
    Positive when price falls (short in profit), negative on a pump against it."""
    if not entry_px or entry_px <= 0:
        return 0.0
    return (float(entry_px) - float(mark_px)) / float(entry_px)


def concurrent_account_mtm(bars_by_pos: dict, stake_frac: float = STAKE_FRAC):
    """Calendar-time concurrent account-MTM curve + max drawdown (auditor B1/B2).

    ``bars_by_pos`` maps proposal_id -> [(bar_ts, unrealized_short_ret), ...].
    At each calendar bar the account MTM is the sum, over positions OPEN at that
    bar (forward-filled from their last observed bar), of
    ``stake_frac * unrealized_short_ret`` — NOT the realized-return cumsum the
    frozen MC used (which understated the true drawdown ~2x). Drawdown is
    peak-to-trough of the equity curve (1 + MTM), with the pre-trade baseline
    equity 1.0 as the initial peak.

    Returns (series, max_drawdown) where series is [(bar_ts, account_mtm), ...].
    """
    # Per-position sorted bar timestamps + values, and active windows.
    prepared = {}
    all_ts: set = set()
    for pid, bars in bars_by_pos.items():
        if not bars:
            continue
        sb = sorted((int(t), float(v)) for t, v in bars)
        ts_list = [t for t, _ in sb]
        val_list = [v for _, v in sb]
        prepared[pid] = (ts_list, val_list, ts_list[0], ts_list[-1])
        all_ts.update(ts_list)
    if not all_ts:
        return [], 0.0

    series = []
    peak_equity = 1.0
    max_dd = 0.0
    for ts in sorted(all_ts):
        mtm = 0.0
        for ts_list, val_list, first_ts, last_ts in prepared.values():
            if ts < first_ts or ts > last_ts:
                continue  # position not open at this calendar bar
            idx = bisect.bisect_right(ts_list, ts) - 1  # last bar <= ts (forward-fill)
            if idx >= 0:
                mtm += stake_frac * val_list[idx]
        equity = 1.0 + mtm
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        max_dd = max(max_dd, dd)
        series.append((ts, mtm))
    return series, max_dd


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_listing_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    base                       TEXT,
    horizon_days               INTEGER,
    decision                   TEXT,   -- ENTER | SKIP_CAP | SKIP_UNSHORTABLE
                                       -- | SKIP_NO_FUNDING | SKIP_NO_DATA
    detected_ts                INTEGER,
    entry_ts                   INTEGER,
    entry_px                   REAL,
    listing_px                 REAL,
    stake_frac                 REAL,
    notional_usd               REAL,
    day1_spread_bps            REAL,
    day1_funding_rate          REAL,
    shortable                  INTEGER,
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
    model_version = "listing_short_probe_v1"

    def __init__(
        self,
        *,
        warehouse,
        markets_provider: Callable[[], list],
        market_data_provider: Callable[[str], dict],
        ohlcv_provider: Callable[[str, str, int], list],
        account_balance_provider: Callable[[], float],
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
        self._state_path = Path(state_path or DEFAULT_STATE_PATH)
        self._ensure_schema()
        self._state = self._load_state()

    # ── schema / state ───────────────────────────────────────────────────
    def _ensure_schema(self) -> None:
        conn = self._wh._conn()
        conn.executescript(_SCHEMA)
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
        now = int(self._now())
        try:
            universe = self._current_universe()
        except Exception as e:
            logger.debug(f"[ListingProbe] universe error: {e}")
            universe = set()
        if universe:
            stats["detected"] = self._detect(universe, now)
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
        for sym in new:
            self._state["pending"][sym] = {"first_seen_ts": now, "entered": False}
        self._state["known"] = sorted(known | universe)
        self._save_state()
        return len(new)

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

        md = self._market_data(sym) or {}
        bid = _pos_float(md.get("bid"))
        ask = _pos_float(md.get("ask"))
        active = bool(md.get("active", True))
        shortable = bool(active and bid and ask)
        funding = md.get("funding_rate")
        qv = _pos_float(md.get("quoteVolume")) or 0.0
        spread_bps = ((ask - bid) / ((ask + bid) / 2.0) * 1e4) if (bid and ask and ask >= bid) else 0.0

        d["entered"] = True  # decided this tick either way (skip or enter)

        if not shortable:
            self._log_skip(sym, "SKIP_UNSHORTABLE", now, detected_ts=first_seen,
                           entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
                           pump_pct=pump_pct, day1_spread_bps=spread_bps,
                           quote_volume_usd=qv, shortable=0)
            return 0, 1
        if funding is None:
            # never guess funding — the short's dominant cost (screen: charged, not modelled)
            self._log_skip(sym, "SKIP_NO_FUNDING", now, detected_ts=first_seen,
                           entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
                           pump_pct=pump_pct, day1_spread_bps=spread_bps,
                           quote_volume_usd=qv, shortable=1)
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
                day1_funding_rate=funding, shortable=1, quote_volume_usd=qv,
                pump_pct=pump_pct, score=score, concurrent_open_at_entry=open_now,
            )
            if open_now >= MAX_CONCURRENT:
                pid = f"ls-{uuid.uuid4().hex[:10]}"
                self._write_probe_row(proposal_id=pid, decision="SKIP_CAP", now=now, **common)
                skipped += 1
                continue
            pid = f"ls-{uuid.uuid4().hex[:10]}"
            self._write_decision_row(pid, sym, entry_ts, entry_px, H, notional, score)
            self._write_probe_row(proposal_id=pid, decision="ENTER", now=now, **common)
            entered += 1
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
    def _write_decision_row(self, pid, sym, entry_ts, entry_px, horizon_days,
                            notional, score) -> None:
        """One shadow_decisions row the keystone resolver replays into
        shadow_outcomes. side='sell', no SL/TP (naked, held to the horizon bar —
        auditor B2 unlevered-3%-notional, no-SL variant). sim_pnl is left NULL:
        this probe does no PnL projection; the resolver owns the after-cost net."""
        row = {
            "ts": int(entry_ts),
            "model_version": self.model_version,
            "symbol": sym,
            "side": "sell",
            "decision": "ALLOW",
            "p_win": None,
            "sim_pnl": None,
            "sim_r_multiple": None,
            "agent_id": self.name,
            "proposal_id": pid,
            "proposed_at": int(entry_ts),
            "vetoed_by": None,
            "veto_reason": None,
            "projected_notional_current": float(notional),
            "projected_notional_alt": float(notional),
            "projected_pnl": None,
            "projected_fee": None,
            "entry_px": float(entry_px),
            "sl_px": 0.0,   # no stop: sl>0 checks in the resolver never fire
            "tp_px": 0.0,   # no target: runs to the horizon bar (time exit)
            "venue": self._venue,
            "timeframe": TIMEFRAME,
            "horizon_bars": int(horizon_days) * 24,
            "label_status": "PENDING",
        }
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        conn = self._wh._conn()
        conn.execute(f"INSERT INTO shadow_decisions ({cols}) VALUES ({ph})", tuple(row.values()))
        conn.commit()

    def _write_probe_row(self, *, proposal_id, decision, now, symbol, base,
                         horizon_days, detected_ts, entry_ts, entry_px, listing_px,
                         stake_frac, notional_usd, day1_spread_bps, day1_funding_rate,
                         shortable, quote_volume_usd, pump_pct, score,
                         concurrent_open_at_entry) -> None:
        row = {
            "proposal_id": proposal_id, "symbol": symbol, "base": base,
            "horizon_days": int(horizon_days), "decision": decision,
            "detected_ts": int(detected_ts), "entry_ts": int(entry_ts),
            "entry_px": float(entry_px), "listing_px": float(listing_px),
            "stake_frac": float(stake_frac), "notional_usd": float(notional_usd),
            "day1_spread_bps": float(day1_spread_bps),
            "day1_funding_rate": float(day1_funding_rate),
            "shortable": int(shortable), "quote_volume_usd": float(quote_volume_usd),
            "pump_pct": float(pump_pct), "score": float(score),
            "realized_funding_rate_sum": 0.0, "last_funding_bucket": None,
            "concurrent_open_at_entry": int(concurrent_open_at_entry),
            "created_ts": int(now),
        }
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        conn = self._wh._conn()
        conn.execute(
            f"INSERT OR REPLACE INTO shadow_listing_probe ({cols}) VALUES ({ph})",
            tuple(row.values()),
        )
        conn.commit()

    def _log_skip(self, sym: str, decision: str, now: int, *, detected_ts,
                  entry_ts=0, entry_px=0.0, listing_px=0.0, pump_pct=0.0,
                  day1_spread_bps=0.0, quote_volume_usd=0.0, shortable=0) -> None:
        # A pre-entry skip (no shadow_decisions row, no position). horizon_days=0
        # so it never counts toward the concurrency cap or a horizon's stats.
        self._write_probe_row(
            proposal_id=f"ls-{uuid.uuid4().hex[:10]}", decision=decision, now=now,
            symbol=sym, base=_base_of(sym), horizon_days=0, detected_ts=detected_ts,
            entry_ts=entry_ts, entry_px=entry_px, listing_px=listing_px,
            stake_frac=STAKE_FRAC, notional_usd=0.0, day1_spread_bps=day1_spread_bps,
            day1_funding_rate=0.0, shortable=shortable, quote_volume_usd=quote_volume_usd,
            pump_pct=pump_pct, score=0.0, concurrent_open_at_entry=0,
        )


def _pos_float(v) -> Optional[float]:
    """Coerce to a positive float, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None
