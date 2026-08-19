"""UnlockShortProbeAgent — LOG-ONLY pre-unlock cliff-short shadow probe.

Integrates the strategy pipeline's SECOND CONFIRMED-GO candidate:
`pre_unlock_short_capital_scaled` (08b screen GO, 2026-07-11). See
_workspace/strategy_pipeline/08b_screen_preunlock_short.md (frozen
pre-registration + execution addendum) and 09_audit_candidate2_final.md
(honesty-auditor CONFIRMED, log-only, with binding conditions 1-6).

HYPOTHESIS (unchanged from the screen): shorting the perp of a token facing a
>=10%-of-mcap non-insider cliff unlock, entering at T-28d (W1) or T-14d (W2)
and exiting at the unlock timestamp T, sized 3% of account equity per event /
12% max concurrent (4 positions) UNLEVERED, earns a positive after-ALL-cost
short return. This probe measures that FORWARD, live, at zero capital risk.

READ THE FRAGILITY PROFILE BEFORE READING RESULTS (audit F1/F2, recorded in
the refuted-families-ledger): 100% of the backtested profit came from 2025-26
bear-tape events — 2023 was NET NEGATIVE in both arms — and the screen's
n=32/36 is really ~19/22 independent bets (SUI/GUN monthly-cliff
pseudo-replication). If a bull tape makes this probe bleed, that is the KNOWN
failure mode playing out. Never read a win-rate here without the resolved
shadow_outcomes.net_pnl AND realized funding (the TP-probe precedent:
core/agents/tp_probe_agent.py — hit-rates without after-cost net are geometry
artifacts). W2 is the robustness-primary arm (audit F3); W3 (T -> T+3d
post-unlock) failed its AUC gate (0.591 < 0.60) and is NOT implemented
(binding condition 4).

LOG-ONLY (charter, non-negotiable — binding condition 1): this class holds
ONLY read-only providers (calendar / perp lookup / market data / OHLCV /
balance) plus the warehouse. It has no reference to any order path and is
structurally incapable of placing an order. Promotion beyond shadow happens
ONLY via core/promotion_gate.py on >=30 RESOLVED forward events PER ARM plus
an explicit owner decision — never here. At ~1-3 qualifying events/month this
takes months; that is the design, not a defect.

Realized after-cost economics are produced by the vetted keystone resolver
(core/shadow_resolver.py) off the shadow_decisions rows this probe writes.
This module performs NO PnL math on outcomes — custom PnL math in a probe is
a bug. Each entered event writes TWO decision rows per arm:
  * raw   (sl_px=0):            hold to unlock T — the screened strategy;
  * "-sl8" (sl_px=entry*1.08):  the charter §2 8% Stop-Loss-Guardian
    counterfactual (binding condition 3) — the screen did NOT model the
    Guardian and whether it truncates the big losses or stops out winners on
    interim pumps is a live open question the resolver answers honestly.
The probe additionally logs the evidence the resolver does not capture
(binding condition 2): per-bar intra-hold MTM, realized per-8h funding, the
concurrent per-arm account-MTM drawdown, the as-of-signal calendar snapshot
(binding condition 5), and the frozen discriminating score at entry.
"""
from __future__ import annotations

import glob
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from core.agents.probe_common import (
    FUNDING_SETTLE_S,
    _pos_float,
    concurrent_account_mtm,
    ensure_schema,
    insert_row,  # standalone SKIP/MTM rows (an ENTRY uses write_entry_pair)
    iso_utc,
    unrealized_short_return,
    write_entry_pair,
)

# ── Frozen 08b constants (pre-registration + execution addendum) ─────────────
# MIRROR of research/screen_preunlock_short.py. Kept in sync by
# tests/test_unlock_short_probe.py::test_frozen_constants_mirror_screen —
# do NOT edit one without the other.
RATIO_MIN = 0.10                  # unlock tokens / documented circ(T) >= 10%
STAKE_FRAC = 0.03                 # 3% of account equity per event (CLAUDE.md §2)
MAX_CONCURRENT_EXPOSURE = 0.12    # 12% max concurrent gross exposure
MAX_CONCURRENT = int(round(MAX_CONCURRENT_EXPOSURE / STAKE_FRAC))  # -> 4, per arm
AUC_RATIO_SCALE = 0.20            # frozen pre-outcome (08b addendum §6)
AUC_FUNDING_W = 10.0

# W1/W2 only — W3 (T -> T+3d) failed its AUC gate and must NOT be probed
# (binding condition 4). arm -> entry offset before floor_day(T).
ARMS = {"W1": 28 * 24 * 3600, "W2": 14 * 24 * 3600}

# Per-arm shadow_decisions.model_version strings — importable (e.g. by
# scripts/gate_status.py) so a probe rename can never silently desync a reporter.
# The "-sl8" rows are the charter-§2 Guardian counterfactual, NOT the promoted arm.
UNLOCK_W1_MODEL_VERSION = "unlock_short_w1_v1"
UNLOCK_W2_MODEL_VERSION = "unlock_short_w2_v1"
ARM_MODEL_VERSIONS = {"W1": UNLOCK_W1_MODEL_VERSION, "W2": UNLOCK_W2_MODEL_VERSION}
ARM_SL8_MODEL_VERSIONS = {"W1": "unlock_short_w1_sl8_v1", "W2": "unlock_short_w2_sl8_v1"}

# Charter §2 Stop-Loss Guardian: close when value drops 8% below entry. For a
# SHORT that is price rising to entry * 1.08 — the counterfactual barrier.
SL_GUARDIAN_ADVERSE = 0.08

HOUR_S = 3600
DAY_S = 24 * HOUR_S
# The screen's frozen 12h bar tolerance (BAR_TOL): an entry window discovered
# more than 12h late is SKIP_LATE — never a retro-entry at a past price.
ENTRY_LATE_TOL_S = 12 * HOUR_S
TIMEFRAME = "1h"
# Keep finishing MTM backfill this long after unlock, then drop from monitoring.
MONITOR_GRACE_S = 7 * DAY_S
DEFAULT_STATE_PATH = "data/shadow_unlock_state.json"      # gitignored (/data/)
DEFAULT_CALENDAR_DIR = "data/unlock_calendar"


def floor_day(ts: int) -> int:
    """ts floored to 00:00 UTC (mirror of the screen's floor_day)."""
    return int(ts) // DAY_S * DAY_S


def arm_entry_target(unlock_ts: int, arm: str) -> int:
    """Frozen entry TARGET for an arm: floor_day(T) - 28d/14d (screen
    window_bounds W1/W2). Exit target is always the unlock timestamp T."""
    return floor_day(unlock_ts) - ARMS[arm]


def unlock_short_score(ratio: float, funding_entry: float) -> float:
    """Frozen pre-outcome discriminating score (08b addendum §6):
    tanh(ratio / 0.20) + 10 x funding print nearest entry.

    Byte-identical to research.screen_preunlock_short.auc_score. Audit F4:
    likely NON-discriminating in the current regime (2025+ AUC 0.246/0.459) —
    an honest FAIL of the AUC>=0.60 promotion gate is a legitimate NO-PROMOTE
    outcome. NEVER re-tune this after outcomes accumulate; a new score
    requires a new pre-registration."""
    try:
        return math.tanh(float(ratio) / AUC_RATIO_SCALE) + AUC_FUNDING_W * float(funding_entry)
    except (TypeError, ValueError):
        return 0.0


# load_calendar_docs mtime cache: calendar_dir -> (fingerprint, docs). The
# calendar changes only when the backfill script runs, but the probe reads the
# directory every ~300s tick — re-parse only when a file name or mtime changed.
_CALENDAR_CACHE: dict = {}


def load_calendar_docs(calendar_dir: str = DEFAULT_CALENDAR_DIR) -> list:
    """Parse every data/unlock_calendar/*.json doc (backfill script output).
    Unreadable files are skipped, never guessed. Results are cached per
    directory on a (filename, mtime) fingerprint; callers must treat the
    returned list as read-only."""
    files = sorted(glob.glob(os.path.join(calendar_dir, "*.json")))
    try:
        fingerprint = tuple((f, os.path.getmtime(f)) for f in files)
    except OSError:
        fingerprint = None  # racing a file change — fall through, don't cache
    cached = _CALENDAR_CACHE.get(calendar_dir)
    if fingerprint is not None and cached is not None and cached[0] == fingerprint:
        return cached[1]
    docs = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                docs.append(json.load(fh))
        except (OSError, ValueError):
            continue
    if fingerprint is not None:
        _CALENDAR_CACHE[calendar_dir] = (fingerprint, docs)
    return docs


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_unlock_probe (
    proposal_id                TEXT PRIMARY KEY,
    base                       TEXT,
    symbol                     TEXT,
    venue                      TEXT,
    arm                        TEXT,    -- 'W1' (T-28d) | 'W2' (T-14d); W3 never probed
    decision                   TEXT,    -- ENTER | SKIP_CAP | SKIP_NO_PERP
                                        -- | SKIP_UNSHORTABLE | SKIP_NO_FUNDING | SKIP_LATE
    unlock_ts                  INTEGER, -- ┐ as-of-SIGNAL-time calendar snapshot
    unlock_ratio               REAL,    -- │ (binding condition 5): written once,
    event_tokens               REAL,    -- │ never updated from a refreshed
    calendar_source            TEXT,    -- ┘ calendar (source incl. fetch time)
    signal_ts                  INTEGER,
    entry_ts                   INTEGER,
    entry_px                   REAL,
    stake_frac                 REAL,
    notional_usd               REAL,
    entry_spread_bps           REAL,
    funding_entry              REAL,
    quote_volume_usd           REAL,
    score                      REAL,    -- frozen: tanh(ratio/0.20) + 10*funding_entry
    realized_funding_rate_sum  REAL,    -- raw hold-to-T funding (8h buckets)
    sl_cf_funding_rate_sum     REAL,    -- frozen at the Guardian flag (condition 3)
    last_funding_bucket        INTEGER,
    concurrent_open_at_entry   INTEGER,
    sl_cf_hit_ts               INTEGER, -- first bar HIGH >= entry*1.08; NULL = never
    sl_cf_hit_px               REAL,    -- the Guardian trigger level entry*1.08
    created_ts                 INTEGER
);
CREATE INDEX IF NOT EXISTS idx_unlock_probe_arm
    ON shadow_unlock_probe(arm, decision);

CREATE TABLE IF NOT EXISTS shadow_unlock_mtm (
    proposal_id           TEXT,
    bar_ts                INTEGER,
    mark_px               REAL,
    unrealized_short_ret  REAL,
    PRIMARY KEY (proposal_id, bar_ts)
);

CREATE TABLE IF NOT EXISTS shadow_unlock_concurrent (
    arm            TEXT,
    snapshot_ts    INTEGER,
    n_open         INTEGER,
    account_mtm    REAL,
    peak_equity    REAL,
    max_drawdown   REAL,
    PRIMARY KEY (arm, snapshot_ts)
);
"""


class UnlockShortProbeAgent:
    """Unlock-calendar scanner + log-only pre-unlock short proposer. See header."""

    name = "UnlockShortProbeAgent"
    model_version = "unlock_short_probe_v1"   # per-arm versions on decision rows

    def __init__(
        self,
        *,
        warehouse,
        calendar_provider: Optional[Callable[[], list]] = None,
        perp_resolver: Callable[[str], Optional[tuple]] = lambda base: None,
        market_data_provider: Callable[[str, str], dict] = lambda v, s: {},
        ohlcv_provider: Callable[[str, str, str, int], list] = lambda v, s, tf, ms: [],
        account_balance_provider: Callable[[], float] = lambda: 0.0,
        now_fn: Callable[[], float] = time.time,
        state_path: Optional[str] = None,
        calendar_dir: str = DEFAULT_CALENDAR_DIR,
    ):
        self._wh = warehouse
        self._calendar = calendar_provider or (lambda: load_calendar_docs(calendar_dir))
        self._perp = perp_resolver
        self._market_data = market_data_provider
        self._ohlcv = ohlcv_provider
        self._balance = account_balance_provider
        self._now = now_fn
        self._state_path = Path(state_path or DEFAULT_STATE_PATH)
        self._ensure_schema()
        self._state = self._load_state()

    # ── schema / state ───────────────────────────────────────────────────
    def _ensure_schema(self) -> None:
        ensure_schema(self._wh, _SCHEMA)

    def _load_state(self) -> dict:
        try:
            if self._state_path.exists():
                st = json.loads(self._state_path.read_text(encoding="utf-8"))
                st.setdefault("signaled", [])
                return st
        except (OSError, ValueError) as e:
            logger.warning(f"[UnlockProbe] state load failed ({e}); starting fresh")
        return {"signaled": []}

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as e:
            logger.warning(f"[UnlockProbe] state save failed: {e}")

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: scan the calendar for arm entry windows, signal
        ready events, update MTM/funding/Guardian evidence on open holds.
        Returns a small stats dict. NEVER raises into the shadow lane."""
        stats = {"events_seen": 0, "entered": 0, "skipped": 0, "mtm_rows": 0}
        now = int(self._now())
        try:
            docs = self._calendar() or []
        except Exception as e:
            logger.debug(f"[UnlockProbe] calendar error: {e}")
            docs = []
        signals = self._collect_signals(docs, now)
        stats["events_seen"] = len(signals)
        dirty = False
        for sig in signals:
            try:
                e, s = self._signal(sig, now)
                stats["entered"] += e
                stats["skipped"] += s
                dirty = dirty or bool(e or s)
            except Exception as e:  # noqa: F841
                logger.debug(f"[UnlockProbe] signal {sig['base']} error: {e}")
        if dirty:
            self._save_state()
        try:
            stats["mtm_rows"] = self._monitor_open(now)
        except Exception as e:
            logger.debug(f"[UnlockProbe] monitor error: {e}")
        # ── Heartbeat (2026-07-20): probe silence must be diagnosable from ──
        # the boot log alone. All 402 rows to date are SKIP_LATE (historical
        # sweep) and the calendar's future events all sit below the frozen 10%
        # ratio floor — the horizon fields make that visible per tick.
        try:
            hb = getattr(self, "_hb_totals", None)
            if hb is None:
                hb = self._hb_totals = {"ticks": 0, "entered": 0, "skipped": 0}
            hb["ticks"] += 1
            hb["entered"] += stats["entered"]
            hb["skipped"] += stats["skipped"]
            future_qual, max_unlock = self._calendar_horizon(docs, now)
            logger.info(
                f"[UnlockProbe] heartbeat: docs={len(docs)} "
                f"events_pending={stats['events_seen']} "
                f"entered={stats['entered']} skipped={stats['skipped']} "
                f"mtm_rows={stats['mtm_rows']} totals(ticks={hb['ticks']} "
                f"entered={hb['entered']} skipped={hb['skipped']}) "
                f"future_qualifying={future_qual} "
                f"max_unlock_ts={iso_utc(max_unlock)}")
        except Exception as e:  # the lane never raises for diagnostics
            logger.debug(f"[UnlockProbe] heartbeat error: {e}")
        return stats

    @staticmethod
    def _calendar_horizon(docs: list, now: int) -> tuple:
        """(future qualifying events, newest unlock ts) across the calendar.
        'Qualifying' mirrors _collect_signals' frozen filters (tokens > 0,
        ratio >= RATIO_MIN) for events still in the future — the count that
        tells an operator whether silence means 'nothing to trade' or
        'calendar horizon empty/stale'."""
        n_qual = 0
        max_ts = None
        for doc in docs:
            for e in doc.get("events") or []:
                try:
                    ts = int(e["ts"])
                    tokens = float(e.get("tokens") or 0.0)
                    ratio = e.get("ratio")
                except (KeyError, TypeError, ValueError):
                    continue
                if max_ts is None or ts > max_ts:
                    max_ts = ts
                if ts > now and tokens > 0 and ratio is not None \
                        and float(ratio) >= RATIO_MIN:
                    n_qual += 1
        return n_qual, max_ts

    # ── signal collection ────────────────────────────────────────────────
    def _collect_signals(self, docs: list, now: int) -> list:
        """Un-signaled (event, arm) pairs whose entry window has OPENED,
        chronological by (entry_target, base, arm) so the concurrency skip is
        purely time-ordered (no cherry-picking — rev3 B3 precedent)."""
        signaled = set(self._state.get("signaled") or [])
        out = []
        for doc in docs:
            base = str(doc.get("symbol") or "")
            source = str(doc.get("source") or "")
            if not base:
                continue
            for e in doc.get("events") or []:
                try:
                    unlock_ts = int(e["ts"])
                    tokens = float(e.get("tokens") or 0.0)
                    ratio = e.get("ratio")
                except (KeyError, TypeError, ValueError):
                    continue
                if tokens <= 0 or ratio is None or float(ratio) < RATIO_MIN:
                    continue
                for arm in ARMS:
                    key = f"{base}|{unlock_ts}|{arm}"
                    if key in signaled:
                        continue
                    target = arm_entry_target(unlock_ts, arm)
                    if now < target:
                        continue  # window not open yet; future tick
                    out.append({
                        "key": key, "base": base, "arm": arm, "target": target,
                        "unlock_ts": unlock_ts, "tokens": tokens,
                        "ratio": float(ratio), "source": source,
                    })
        out.sort(key=lambda s: (s["target"], s["base"], s["arm"]))
        return out

    # ── signal execution ─────────────────────────────────────────────────
    def _signal(self, sig: dict, now: int) -> tuple:
        """Decide one (event, arm) signal. The calendar fields in `sig` are the
        AS-OF-SIGNAL-TIME snapshot (binding condition 5): they are written once
        here and never re-derived from a later calendar refresh."""
        key = sig["key"]
        if now - sig["target"] > ENTRY_LATE_TOL_S:
            # Window discovered too late (probe down / calendar landed late).
            # Never backfill an entry from the past — that is a retro-signal.
            self._log_skip(sig, "SKIP_LATE", now)
            self._mark(key)
            return 0, 1
        resolved = self._perp(sig["base"])
        if not resolved:
            self._log_skip(sig, "SKIP_NO_PERP", now)
            self._mark(key)
            return 0, 1
        venue, symbol = str(resolved[0]), str(resolved[1])
        md = self._market_data(venue, symbol) or {}
        # 2026-08-19 BRICK REPAIR. This used to require `bid and ask`. binance
        # USDT-M fetch_ticker returns bid=None/ask=None for EVERY symbol
        # (verified live, BTC included), and binance is FIRST in
        # _unlock_venue_order — so this lane could never enter a single event
        # at any event rate. Its "0 ENTER in 5.5 weeks" was a NON-measurement,
        # not a negative result: of the 3 in-window opportunities it ever had,
        # SIGN W2 and GRVT W2 both died here. The sibling listing probe hit the
        # identical brick and was repaired 2026-07-28; the fix was never
        # ported. Reuse that classifier rather than re-deriving it: a traded
        # price + quote volume is sufficient evidence of shortability, while
        # no-book AND no-trade still fails CLOSED.
        from core.agents.listing_short_probe_agent import (
            classify_shortability,
            day1_spread_bps,
        )

        bid = _pos_float(md.get("bid"))
        ask = _pos_float(md.get("ask"))
        shortable, _basis = classify_shortability(md)
        if not shortable:
            self._log_skip(sig, "SKIP_UNSHORTABLE", now, venue=venue, symbol=symbol)
            self._mark(key)
            return 0, 1
        funding = md.get("funding_rate")
        if funding is None:
            # never guess funding — the short's dominant cost (screen: charged in full)
            self._log_skip(sig, "SKIP_NO_FUNDING", now, venue=venue, symbol=symbol)
            self._mark(key)
            return 0, 1

        entry = self._last_closed_bar(venue, symbol, now)
        if entry is None:
            return 0, 0  # transient candle gap: retry next tick within the 12h tol
        entry_ts, entry_px = entry
        funding = float(funding)
        score = unlock_short_score(sig["ratio"], funding)
        # NULL, never 0.0, when the venue publishes no book (binance USDT-M):
        # a fabricated zero spread would pass any "spread must be tight" gate
        # trivially — a fail-OPEN worse than the brick this repair removed.
        spread_bps = day1_spread_bps(md)
        qv = _pos_float(md.get("quoteVolume")) or 0.0
        # Capital-scaling convention (audit §5): 3% of ACCOUNT equity,
        # UNLEVERED — no leverage multiplier exists anywhere in this probe.
        notional = STAKE_FRAC * float(self._balance() or 0.0)
        open_now = self._open_count(sig["arm"], now)
        common = dict(
            base=sig["base"], symbol=symbol, venue=venue, arm=sig["arm"],
            unlock_ts=sig["unlock_ts"], unlock_ratio=sig["ratio"],
            event_tokens=sig["tokens"], calendar_source=sig["source"],
            signal_ts=now, entry_ts=entry_ts, entry_px=entry_px,
            stake_frac=STAKE_FRAC, notional_usd=notional,
            entry_spread_bps=spread_bps, funding_entry=funding,
            quote_volume_usd=qv, score=score, concurrent_open_at_entry=open_now,
        )
        if open_now >= MAX_CONCURRENT:
            self._write_probe_row(
                proposal_id=f"us-{uuid.uuid4().hex[:10]}", decision="SKIP_CAP",
                now=now, **common)
            self._mark(key)
            return 0, 1

        pid = f"us-{uuid.uuid4().hex[:10]}"
        horizon_bars = max(1, (sig["unlock_ts"] - entry_ts) // HOUR_S)
        # All THREE rows land together or not at all: the raw arm's naked hold
        # to unlock T (the screened strategy), the charter-§2 8%-SL-Guardian
        # counterfactual (binding condition 3), and the probe row holding the
        # slot. A split write orphans a decision and desyncs the two arms.
        self._write_probe_row(
            proposal_id=pid, decision="ENTER", now=now,
            decision_rows=[
                self._decision_row(
                    pid, symbol, venue, entry_ts, entry_px, horizon_bars, notional,
                    sl_px=0.0, model_version=ARM_MODEL_VERSIONS[sig["arm"]]),
                self._decision_row(
                    f"{pid}-sl8", symbol, venue, entry_ts, entry_px, horizon_bars,
                    notional, sl_px=entry_px * (1.0 + SL_GUARDIAN_ADVERSE),
                    model_version=ARM_SL8_MODEL_VERSIONS[sig["arm"]]),
            ],
            **common,
        )
        self._mark(key)
        return 1, 0

    def _mark(self, key: str) -> None:
        signaled = self._state.setdefault("signaled", [])
        if key not in signaled:
            signaled.append(key)

    def _last_closed_bar(self, venue: str, symbol: str, now: int) -> Optional[tuple]:
        """(open_ts_s, close) of the most recent CLOSED 1h bar. None on a gap."""
        candles = self._ohlcv(venue, symbol, TIMEFRAME, (now - 48 * HOUR_S) * 1000) or []
        best = None
        for c in candles:
            try:
                bar_ts = int(c[0]) // 1000
                close = float(c[4])
            except (IndexError, TypeError, ValueError):
                continue
            if bar_ts + HOUR_S <= now and close > 0:
                if best is None or bar_ts > best[0]:
                    best = (bar_ts, close)
        return best

    def _open_count(self, arm: str, now: int) -> int:
        rows = self._wh.query(
            "SELECT COUNT(*) AS n FROM shadow_unlock_probe "
            "WHERE arm=? AND decision='ENTER' AND unlock_ts > ?",
            (arm, now),
        )
        return int(rows[0]["n"]) if rows else 0

    # ── monitoring: MTM path + Guardian flag + funding + concurrent DD ────
    def _monitor_open(self, now: int) -> int:
        rows = self._wh.query(
            "SELECT proposal_id, symbol, venue, arm, entry_ts, entry_px, unlock_ts, "
            "last_funding_bucket, realized_funding_rate_sum, sl_cf_funding_rate_sum, "
            "sl_cf_hit_ts FROM shadow_unlock_probe "
            "WHERE decision='ENTER' AND (unlock_ts + ?) > ?",
            (MONITOR_GRACE_S, now),
        )
        written = 0
        touched: set = set()
        conn = self._wh._conn()
        for r in rows:
            pid = r["proposal_id"]
            entry_ts = int(r["entry_ts"])
            entry_px = float(r["entry_px"])
            end_ts = int(r["unlock_ts"])
            if now < entry_ts:
                continue
            sl_hit_ts = r["sl_cf_hit_ts"]
            last = self._wh.query(
                "SELECT MAX(bar_ts) AS m FROM shadow_unlock_mtm WHERE proposal_id=?", (pid,)
            )
            since = (int(last[0]["m"]) + 1) if last and last[0]["m"] is not None else entry_ts
            cap = min(now, end_ts)
            if since <= cap:
                candles = self._ohlcv(r["venue"], r["symbol"], TIMEFRAME, since * 1000) or []
                sl_level = entry_px * (1.0 + SL_GUARDIAN_ADVERSE)
                for c in candles:
                    try:
                        bar_ts = int(c[0]) // 1000
                        high = float(c[2])
                        mark = float(c[4])
                    except (IndexError, TypeError, ValueError):
                        continue
                    if bar_ts < entry_ts or bar_ts > end_ts:
                        continue
                    if bar_ts + HOUR_S > now:
                        continue  # forming/unclosed bar — no repaint
                    conn.execute(
                        "INSERT OR REPLACE INTO shadow_unlock_mtm "
                        "(proposal_id, bar_ts, mark_px, unrealized_short_ret) "
                        "VALUES (?,?,?,?)",
                        (pid, bar_ts, mark, unrealized_short_return(entry_px, mark)),
                    )
                    written += 1
                    # Guardian counterfactual flag (condition 3): first bar whose
                    # HIGH breaches entry*1.08 — the same wick trigger the
                    # resolver's sl8 row uses, and how the live Guardian
                    # (continuous monitoring) would actually fire.
                    if sl_hit_ts is None and high >= sl_level:
                        sl_hit_ts = bar_ts
                        conn.execute(
                            "UPDATE shadow_unlock_probe SET sl_cf_hit_ts=?, "
                            "sl_cf_hit_px=? WHERE proposal_id=? AND sl_cf_hit_ts IS NULL",
                            (bar_ts, sl_level, pid),
                        )
            if now <= end_ts:
                self._accrue_funding(r, now, sl_flagged=sl_hit_ts is not None)
            touched.add(r["arm"])
        if written or touched:
            conn.commit()
        for arm in touched:
            self._log_concurrent(arm, now)
        return written

    def _accrue_funding(self, row: dict, now: int, *, sl_flagged: bool) -> None:
        """Book the current realized funding print once per 8h settlement
        bucket into the raw hold sum, and — until the Guardian flag — into the
        SL-counterfactual sum (frozen thereafter: a stopped position pays no
        further funding)."""
        md = self._market_data(row["venue"], row["symbol"]) or {}
        fr = md.get("funding_rate")
        if fr is None:
            return
        bucket = now // FUNDING_SETTLE_S
        if row["last_funding_bucket"] is not None and int(row["last_funding_bucket"]) == bucket:
            return  # this 8h settlement already booked
        raw_sum = float(row["realized_funding_rate_sum"] or 0.0) + float(fr)
        sl_sum = float(row["sl_cf_funding_rate_sum"] or 0.0)
        if not sl_flagged:
            sl_sum += float(fr)
        self._wh._conn().execute(
            "UPDATE shadow_unlock_probe SET realized_funding_rate_sum=?, "
            "sl_cf_funding_rate_sum=?, last_funding_bucket=? WHERE proposal_id=?",
            (raw_sum, sl_sum, int(bucket), row["proposal_id"]),
        )

    def _log_concurrent(self, arm: str, now: int) -> None:
        """Per-arm concurrent account-MTM snapshot (the risk number the audit's
        F10 says the promotion decision must look at — NOT the per-trade
        realized cumsum the screen's MC understated ~2x)."""
        rows = self._wh.query(
            "SELECT m.proposal_id AS pid, m.bar_ts AS bar_ts, "
            "m.unrealized_short_ret AS ur "
            "FROM shadow_unlock_mtm m JOIN shadow_unlock_probe p "
            "ON p.proposal_id=m.proposal_id "
            "WHERE p.arm=? AND p.decision='ENTER'",
            (arm,),
        )
        if not rows:
            return
        bars_by_pos: dict = {}
        for r in rows:
            bars_by_pos.setdefault(r["pid"], []).append((int(r["bar_ts"]), float(r["ur"])))
        series, max_dd = concurrent_account_mtm(bars_by_pos, STAKE_FRAC)
        cur_mtm = series[-1][1] if series else 0.0
        peak_equity = max([1.0] + [1.0 + m for _, m in series]) if series else 1.0
        self._wh._conn().execute(
            "INSERT OR REPLACE INTO shadow_unlock_concurrent "
            "(arm, snapshot_ts, n_open, account_mtm, peak_equity, max_drawdown) "
            "VALUES (?,?,?,?,?,?)",
            (arm, now, self._open_count(arm, now), cur_mtm, peak_equity, max_dd),
        )
        self._wh._conn().commit()

    # ── warehouse writes ─────────────────────────────────────────────────
    def _decision_row(self, pid, symbol, venue, entry_ts, entry_px,
                      horizon_bars, notional, *, sl_px, model_version) -> dict:
        """The shadow_decisions kwargs for the keystone resolver, which replays
        the row into shadow_outcomes. side='sell'; tp_px=0 always (exit is the
        time barrier at the SNAPSHOTTED unlock T via horizon_bars — a later
        calendar edit can never move a logged exit). sim_pnl stays NULL: this
        probe does no PnL projection; the resolver owns the after-cost net.
        Built here, written by _write_probe_row's atomic write."""
        return dict(
            ts=entry_ts, model_version=model_version, symbol=symbol,
            side="sell", agent_id=self.name, proposal_id=pid, notional=notional,
            entry_px=entry_px, sl_px=sl_px, tp_px=0.0, venue=venue,
            timeframe=TIMEFRAME, horizon_bars=horizon_bars,
        )

    def _write_probe_row(self, *, proposal_id, decision, now, base, symbol, venue,
                         arm, unlock_ts, unlock_ratio, event_tokens, calendar_source,
                         signal_ts, entry_ts, entry_px, stake_frac, notional_usd,
                         entry_spread_bps, funding_entry, quote_volume_usd, score,
                         concurrent_open_at_entry, decision_rows=None) -> None:
        """Write the probe row. ``decision_rows`` (ENTER only) pairs it with
        its shadow_decisions rows in ONE transaction; the SKIP paths pass None
        — they have no decision row and no position."""
        row = {
            "proposal_id": proposal_id, "base": base, "symbol": symbol,
            "venue": venue, "arm": arm, "decision": decision,
            "unlock_ts": int(unlock_ts), "unlock_ratio": float(unlock_ratio),
            "event_tokens": float(event_tokens), "calendar_source": calendar_source,
            "signal_ts": int(signal_ts), "entry_ts": int(entry_ts or 0),
            "entry_px": float(entry_px or 0.0), "stake_frac": float(stake_frac),
            "notional_usd": float(notional_usd),
            # NULL-tolerant (2026-08-19): the spread is genuinely UNKNOWN when
            # the venue publishes no book. The column is nullable REAL; storing
            # NULL keeps "unknown" distinguishable from a real 0.0 spread.
            "entry_spread_bps": (
                None if entry_spread_bps is None else float(entry_spread_bps)),
            "funding_entry": float(funding_entry),
            "quote_volume_usd": float(quote_volume_usd), "score": float(score),
            "realized_funding_rate_sum": 0.0, "sl_cf_funding_rate_sum": 0.0,
            "last_funding_bucket": None,
            "concurrent_open_at_entry": int(concurrent_open_at_entry),
            "sl_cf_hit_ts": None, "sl_cf_hit_px": None, "created_ts": int(now),
        }
        # Plain INSERT (condition 5): a probe row is written exactly once and
        # never replaced by a later signal or calendar refresh.
        if decision_rows is None:
            insert_row(self._wh, "shadow_unlock_probe", row)
        else:
            write_entry_pair(
                self._wh, decisions=decision_rows,
                probe_table="shadow_unlock_probe", probe_row=row,
            )

    def _log_skip(self, sig: dict, decision: str, now: int, *,
                  venue: str = "", symbol: str = "") -> None:
        """A pre-entry skip: as-of-signal calendar snapshot preserved, no
        shadow_decisions row, no position, never counts toward the cap."""
        self._write_probe_row(
            proposal_id=f"us-{uuid.uuid4().hex[:10]}", decision=decision, now=now,
            base=sig["base"], symbol=symbol, venue=venue, arm=sig["arm"],
            unlock_ts=sig["unlock_ts"], unlock_ratio=sig["ratio"],
            event_tokens=sig["tokens"], calendar_source=sig["source"],
            signal_ts=now, entry_ts=0, entry_px=0.0, stake_frac=STAKE_FRAC,
            notional_usd=0.0, entry_spread_bps=0.0, funding_entry=0.0,
            quote_volume_usd=0.0, score=0.0, concurrent_open_at_entry=0,
        )
