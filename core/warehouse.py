"""
core/warehouse.py — Historical Trade + Candidate Warehouse

A SQLite-backed append-only store for every candidate setup the signal engine
produces (whether it was taken or skipped), every trade that executes, and
every ClaudeCode advisory review. This is the learning substrate: the
meta-filter, weekly diagnostics, and what-if reports all read from here.

Design decisions:
  * SQLite (stdlib only — no extra dependency). Single file at data/warehouse.sqlite.
  * One connection per thread via thread-local storage; WAL mode for read concurrency.
  * Append-only for candidates / claude_reviews; trades are inserted on open and
    updated on close (so one trade row has the full lifecycle).
  * features are stored as JSON text (sqlite has no dict type). Keeps schema
    stable even as features evolve.
  * idempotency key on trades: (exchange, symbol, ts_entry, side). A re-import
    that already saw this trade is a no-op.

Spec references:
  §4 Historical Data Warehouse — every trade + every candidate setup.
  §6 minimum trade fields, minimum environment fields, ClaudeCode review outputs.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from loguru import logger

# NOTE: relative-to-cwd is the repo-wide contract (tests isolate by chdir'ing
# to tmp). Standalone entrypoints must chdir to the repo root before touching
# the warehouse — see scripts/resolve_shadow_outcomes.py (2026-07-07: launched
# by Task Scheduler with cwd=System32, mkdir("data") crashed every hourly run).
WAREHOUSE_PATH = Path("data/warehouse.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    market_type     TEXT,
    side            TEXT,
    strategy_family TEXT,
    regime_label    TEXT,
    entry_px        REAL,
    stop_px         REAL,
    target_px       REAL,
    leverage        INTEGER,
    size_pct        REAL,
    confidence      REAL,
    decision        TEXT,       -- ALLOW | SKIP | REVIEW | TAKEN
    skip_reason     TEXT,
    features_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_ts     ON candidates(ts);
CREATE INDEX IF NOT EXISTS idx_candidates_symbol ON candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_candidates_decision ON candidates(decision);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    INTEGER,
    ts_entry        REAL    NOT NULL,
    ts_exit         REAL,
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    market_type     TEXT,
    side            TEXT    NOT NULL,
    strategy_family TEXT,
    entry_px        REAL,
    exit_px         REAL,
    size            REAL,
    leverage        INTEGER,
    fee             REAL,
    slippage        REAL,
    realized_pnl    REAL,
    r_multiple      REAL,
    hold_sec        REAL,
    status          TEXT,       -- OPEN | CLOSED
    exit_reason     TEXT,
    mode            TEXT,       -- OBSERVATION | PAPER | CONTROLLED_LIVE
    mcp_score       REAL        -- MCP Brain entry score (50-101); NULL for pre-column rows
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_key
    ON trades(exchange, symbol, ts_entry, side);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);

CREATE TABLE IF NOT EXISTS trade_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      INTEGER NOT NULL,
    event_type    TEXT    NOT NULL,
    event_ts      REAL    NOT NULL,
    exchange      TEXT,
    symbol        TEXT,
    side          TEXT,
    payload_json  TEXT,
    UNIQUE(trade_id, event_type, event_ts)
);
CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON trade_events(trade_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_events_close_once
    ON trade_events(trade_id)
    WHERE event_type='CLOSE';

CREATE TABLE IF NOT EXISTS claude_reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL    NOT NULL,
    ref_type          TEXT    NOT NULL,  -- 'trade' | 'candidate'
    ref_id            INTEGER,
    task              TEXT    NOT NULL,
    decision          TEXT,
    confidence        REAL,
    red_flags_json    TEXT,
    reason_labels_json TEXT,
    commentary        TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_ref ON claude_reviews(ref_type, ref_id);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON claude_reviews(task);

-- ── Quant rebuild tables (Phase 0.2+) ────────────────────────────────
-- features: deterministic feature snapshots, one row per (ts, symbol, tf, name)
CREATE TABLE IF NOT EXISTS features (
    ts            INTEGER NOT NULL,
    symbol        TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL,
    feature_name  TEXT    NOT NULL,
    value         REAL,
    PRIMARY KEY (ts, symbol, timeframe, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_features_symbol_ts ON features(symbol, ts);

-- predictions: every model inference. PK is composite to make replays idempotent.
-- candidate_id (Phase 13.5b) lets predictions JOIN cleanly to trades via the
-- candidates.id → trades.candidate_id chain. NULL on imported / pre-Phase-13.5
-- rows so existing data doesn't trip the constraint.
CREATE TABLE IF NOT EXISTS predictions (
    ts             INTEGER NOT NULL,
    model_version  TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    side           TEXT    NOT NULL,
    p_win          REAL    NOT NULL,
    raw_score      REAL,
    feature_hash   TEXT,
    candidate_id   INTEGER,
    PRIMARY KEY (ts, model_version, symbol, side)
);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_ts ON predictions(symbol, ts);
-- idx_predictions_candidate is created in __init__ AFTER the migration
-- ALTER TABLE adds the column on legacy databases. Doing it here would
-- fail when the column doesn't exist yet.

-- shadow_decisions: shadow runner's would-have decisions + sim PnL.
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER,
    model_version  TEXT,
    symbol         TEXT,
    side           TEXT,
    decision       TEXT,
    p_win          REAL,
    sim_pnl        REAL,
    sim_r_multiple REAL
);
CREATE INDEX IF NOT EXISTS idx_shadow_ts ON shadow_decisions(ts);

-- attribution: per-trade alpha decomposition. trade_id is the PK (one row per trade).
CREATE TABLE IF NOT EXISTS attribution (
    trade_id        INTEGER PRIMARY KEY,
    alpha           REAL,
    spread          REAL,
    slippage        REAL,
    funding         REAL,
    fees            REAL,
    realized_pnl    REAL,
    attributed_at   INTEGER
);

-- model_versions: training-run ledger. model_version is PK so refits don't overwrite.
CREATE TABLE IF NOT EXISTS model_versions (
    model_version       TEXT PRIMARY KEY,
    trained_at          INTEGER,
    train_window_start  INTEGER,
    train_window_end    INTEGER,
    oos_sharpe          REAL,
    oos_wr              REAL,
    deflated_sharpe     REAL,
    pbo                 REAL,
    artifact_path       TEXT
);

-- labels: triple-barrier labels per candidate. y in {0, 1}; rows with the
-- time-bar firing are dropped before insert (kept out of the table to keep
-- the training set balanced). market_type lets the trainer split futures vs
-- spot without re-deriving from the candidate.
CREATE TABLE IF NOT EXISTS labels (
    candidate_id       INTEGER PRIMARY KEY,
    ts                 REAL    NOT NULL,
    symbol             TEXT    NOT NULL,
    side               TEXT    NOT NULL,
    market_type        TEXT    NOT NULL,
    y                  INTEGER NOT NULL,
    exit_bars          INTEGER,
    label_horizon_bars INTEGER NOT NULL,
    sl_pct             REAL,
    tp_pct             REAL
);
CREATE INDEX IF NOT EXISTS idx_labels_market_ts ON labels(market_type, ts);
CREATE INDEX IF NOT EXISTS idx_labels_symbol    ON labels(symbol);

-- shadow_outcomes (Phase 0b): forward-RESOLVED, SL-first, after-cost result for
-- each shadow_decisions row. Keyed by proposal_id (1:1 with the decision). This
-- is the ONLY shadow PnL a promotion gate may read — shadow_decisions.sim_pnl is
-- a projected-at-TP diagnostic and must never feed a promotion decision.
CREATE TABLE IF NOT EXISTS shadow_outcomes (
    proposal_id   TEXT PRIMARY KEY,
    exit_px       REAL,
    exit_reason   TEXT,
    gross_pnl     REAL,
    net_pnl       REAL,
    fees          REAL,
    slippage      REAL,
    funding       REAL,
    mfe           REAL,
    mae           REAL,
    bars_held     INTEGER,
    r_multiple    REAL,
    resolved_ts   INTEGER,
    label_status  TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_outcomes_status
    ON shadow_outcomes(label_status);
"""


class Warehouse:
    """Thin SQLite wrapper. Safe to instantiate once per process."""

    _lock = threading.Lock()
    _tls = threading.local()

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else WAREHOUSE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._conn()
            conn.executescript(_SCHEMA)
            # Idempotent migration: databases created before mcp_score existed
            # need the column added. SQLite raises if the column already exists,
            # which we catch and ignore.
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN mcp_score REAL")
            except sqlite3.OperationalError:
                pass
            # Phase: ensemble model gate — record the model that approved the
            # entry so realized PnL can be attributed to a specific version.
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN model_version TEXT")
            except sqlite3.OperationalError:
                pass
            # Phase (2026-05-29): per-trade execution fill type
            # ('maker' | 'taker' | 'maker_partial') so the maker-only soak can be
            # evaluated on ground truth instead of aggregate counters. NULL on
            # legacy rows and on paper (no real fill).
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN fill_type TEXT")
            except sqlite3.OperationalError:
                pass
            # C7 (tpbot retrofit 2026-07-08): REAL intra-trade extremes —
            # positive fractions of entry price, unleveraged (the
            # shadow_outcomes convention). NULL on legacy rows and until the
            # 10s monitor observes the first tick. DistFitSL prefers these
            # over its documented exit-price proxies.
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN mfe REAL")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN mae REAL")
            except sqlite3.OperationalError:
                pass
            # C10 (tpbot retrofit 2026-07-08): limit-TP counterfactual
            # measurement on shadow outcomes (touched-vs-filled honesty gate
            # for any future maker-TP experiment). NULL on legacy rows.
            for _ddl in (
                "ALTER TABLE shadow_outcomes ADD COLUMN ltp_touched INTEGER",
                "ALTER TABLE shadow_outcomes ADD COLUMN ltp_filled INTEGER",
                "ALTER TABLE shadow_outcomes ADD COLUMN ltp_exit_reason TEXT",
            ):
                try:
                    conn.execute(_ddl)
                except sqlite3.OperationalError:
                    pass
            # Phase (audit 2026-06-04): entry-time stop price so r_multiple is
            # re-derivable and never contaminated by a trailed/breakeven-moved
            # stop. NULL on legacy rows and on reconcile/stale closes (no entry
            # stop context). See reports/tp_booking_codepath_audit_2026-06-04.md.
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN entry_stop_px REAL")
            except sqlite3.OperationalError:
                pass
            # Phase (audit 2026-06-04): partial-TP completeness. realized_pnl
            # stays the RUNNER leg (it feeds the live recent_expectancy entry
            # gate — must not shift), and the taken fraction's net PnL is booked
            # here. Whole-trade $ = realized_pnl + partial_realized_pnl. NULL /
            # 0 when no partial was taken.
            try:
                conn.execute(
                    "ALTER TABLE trades ADD COLUMN partial_realized_pnl REAL")
            except sqlite3.OperationalError:
                pass
            # Phase (2026-06-05): beta-adjusted alpha-vs-BTC label. Separates
            # genuine entry alpha from BTC market beta so learning can't mistake a
            # net-short book riding a BTC dump for edge. Two-phase: NULL at close,
            # filled by scripts/backfill_btc_alpha.py / the learning cycle from
            # cached BTC OHLCV. See core/btc_alpha.py.
            for _alpha_col in (
                "alpha_vs_btc REAL",
                "btc_ret_window REAL",
                "beta_used REAL",
            ):
                try:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {_alpha_col}")
                except sqlite3.OperationalError:
                    pass
            # Provenance bundle (2026-06-12): decision_id joins trades AND
            # candidates to mcp_decisions.jsonl rows; exit_decision_id
            # attributes the CLOSE decision. NULL is legitimate (deterministic
            # SL/TP/trailing exits, manual/ghost/reconciled rows — plan 0E).
            for _prov_col in ("decision_id TEXT", "exit_decision_id TEXT"):
                try:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {_prov_col}")
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("ALTER TABLE candidates ADD COLUMN decision_id TEXT")
            except sqlite3.OperationalError:
                pass
            # Phase 13.5b: predictions.candidate_id added for clean
            # predictions→trades join. NULL on legacy rows.
            try:
                conn.execute("ALTER TABLE predictions ADD COLUMN candidate_id INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_predictions_candidate "
                    "ON predictions(candidate_id)")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_events_close_once "
                    "ON trade_events(trade_id) WHERE event_type='CLOSE'"
                )
            except sqlite3.OperationalError:
                pass
            for col_def in (
                "agent_id TEXT",
                "proposal_id TEXT",
                "proposed_at INTEGER",
                "vetoed_by TEXT",
                "veto_reason TEXT",
                "projected_notional_current REAL",
                "projected_notional_alt REAL",
                "projected_pnl REAL",
                "projected_fee REAL",
                # Phase 0a — raw barriers so a shadow decision is forward-resolvable
                # by the 0b resolver (sim_pnl stays a projected-at-TP diagnostic).
                "entry_px REAL",
                "sl_px REAL",
                "tp_px REAL",
                "venue TEXT",
                "timeframe TEXT",
                "horizon_bars INTEGER",
                "label_status TEXT",
            ):
                try:
                    conn.execute(f"ALTER TABLE shadow_decisions ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass
            # Phase 0a — legacy rows (written before barriers existed) can never be
            # resolved forward; flag them so the 0b resolver + promotion gate skip
            # them. Idempotent: new PENDING rows carry entry_px and are untouched.
            try:
                conn.execute(
                    "UPDATE shadow_decisions SET label_status='UNRESOLVABLE' "
                    "WHERE label_status IS NULL AND entry_px IS NULL"
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()
        logger.info(f"[Warehouse] Ready at {self.path}")

    def _conn(self) -> sqlite3.Connection:
        """Thread-local connection. WAL mode so readers don't block writers."""
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._tls.conn = conn
        return conn

    # ── Candidates ────────────────────────────────────────────────────

    def record_candidate(
        self,
        *,
        exchange: str,
        symbol: str,
        market_type: str = "futures",
        side: str | None = None,
        strategy_family: str | None = None,
        regime_label: str | None = None,
        entry_px: float | None = None,
        stop_px: float | None = None,
        target_px: float | None = None,
        leverage: int | None = None,
        size_pct: float | None = None,
        confidence: float | None = None,
        decision: str = "SKIP",
        skip_reason: str | None = None,
        features: dict | None = None,
        ts: float | None = None,
    ) -> int:
        """Append a candidate row. Returns candidate_id."""
        row = (
            ts or time.time(), exchange, symbol, market_type, side,
            strategy_family, regime_label, entry_px, stop_px, target_px,
            leverage, size_pct, confidence, decision, skip_reason,
            json.dumps(features or {}, separators=(",", ":")),
        )
        try:
            cur = self._conn().execute(
                """INSERT INTO candidates(
                    ts, exchange, symbol, market_type, side,
                    strategy_family, regime_label, entry_px, stop_px, target_px,
                    leverage, size_pct, confidence, decision, skip_reason,
                    features_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            return int(cur.lastrowid)
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_candidate error: {e}")
            return -1

    # ── Trades ────────────────────────────────────────────────────────

    def record_trade_open(
        self,
        *,
        exchange: str,
        symbol: str,
        side: str,
        ts_entry: float,
        entry_px: float,
        size: float,
        leverage: int = 1,
        candidate_id: int | None = None,
        market_type: str = "futures",
        strategy_family: str | None = None,
        fee: float = 0.0,
        slippage: float = 0.0,
        mode: str = "PAPER",
        mcp_score: float | None = None,
        model_version: str | None = None,
        fill_type: str | None = None,
        decision_id: str | None = None,
    ) -> int:
        """Insert an OPEN trade row. Idempotent on (exchange, symbol, ts_entry, side)."""
        try:
            cur = self._conn().execute(
                """INSERT OR IGNORE INTO trades(
                    candidate_id, ts_entry, exchange, symbol, market_type,
                    side, strategy_family, entry_px, size, leverage,
                    fee, slippage, status, mode, mcp_score, model_version,
                    fill_type, decision_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, ts_entry, exchange, symbol, market_type,
                 side, strategy_family, entry_px, size, leverage,
                 fee, slippage, "OPEN", mode, mcp_score, model_version,
                 fill_type, decision_id),
            )
            if cur.rowcount > 0:
                trade_id = int(cur.lastrowid)
                self._record_trade_event(
                    trade_id=trade_id,
                    event_type="OPEN",
                    event_ts=ts_entry,
                    payload={
                        "exchange": exchange,
                        "symbol": symbol,
                        "side": side,
                        "entry_px": entry_px,
                        "size": size,
                        "mode": mode,
                    },
                )
                return trade_id
            # Already inserted — look it up
            got = self._conn().execute(
                "SELECT id FROM trades WHERE exchange=? AND symbol=? AND ts_entry=? AND side=?",
                (exchange, symbol, ts_entry, side),
            ).fetchone()
            return int(got["id"]) if got else -1
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_trade_open error: {e}")
            return -1

    def update_trade_extremes(
        self,
        *,
        exchange: str,
        symbol: str,
        side: str,
        ts_entry: float,
        mfe: float,
        mae: float,
    ) -> bool:
        """Persist running intra-trade extremes on the OPEN row (C7).

        ``mfe``/``mae`` are positive fractions of entry price (unleveraged).
        Resolved by the same idempotency key as the close path; missing row
        -> False no-op. Best-effort: never raises."""
        try:
            tid = self.trade_id_by_key(
                exchange=exchange, symbol=symbol, ts_entry=ts_entry, side=side)
            if tid is None:
                return False
            self._conn().execute(
                "UPDATE trades SET mfe=?, mae=? WHERE id=?",
                (float(mfe), float(mae), tid))
            self._conn().commit()
            return True
        except Exception as e:
            logger.debug(f"[Warehouse] update_trade_extremes skipped: {e}")
            return False

    def record_lifecycle_event(
        self,
        *,
        exchange: str,
        symbol: str,
        side: str,
        ts_entry: float,
        event_type: str,
        payload: dict[str, Any] | None = None,
        event_ts: float | None = None,
    ) -> bool:
        """Append a mid-trade lifecycle row (C6, tpbot retrofit 2026-07-08).

        Until C6, trade_events held only OPEN/CLOSE endpoints — the
        signal->order->fill->TP->close chain had no intermediate audit rows
        (the Jun-4 TP-attribution-corruption class was unauditable after the
        fact). Event types written by the order manager: ORDER_PLACED (an
        SL/TP conditional landed on the venue), FILL (entry execution
        detail), PARTIAL_TP (partial take fired), SL_MOVE (breakeven/trail
        advancement).

        Resolves the OPEN trade row via ``trade_id_by_key`` — the same
        idempotency key the close path uses. Returns False (no-op) when the
        row cannot be found; dedup rides the existing
        UNIQUE(trade_id, event_type, event_ts) via INSERT OR IGNORE.
        Best-effort by contract: never raises.
        """
        try:
            tid = self.trade_id_by_key(
                exchange=exchange, symbol=symbol, ts_entry=ts_entry, side=side)
            if tid is None:
                return False
            self._record_trade_event(
                trade_id=tid,
                event_type=str(event_type),
                event_ts=float(event_ts) if event_ts is not None else time.time(),
                payload=payload,
            )
            self._conn().commit()
            return True
        except Exception as e:
            logger.debug(f"[Warehouse] record_lifecycle_event skipped: {e}")
            return False

    def _record_trade_event(
        self,
        *,
        trade_id: int,
        event_type: str,
        event_ts: float,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            row = self._conn().execute(
                "SELECT exchange, symbol, side FROM trades WHERE id=?",
                (trade_id,),
            ).fetchone()
            exchange = row["exchange"] if row else None
            symbol = row["symbol"] if row else None
            side = row["side"] if row else None
            self._conn().execute(
                """INSERT OR IGNORE INTO trade_events(
                    trade_id, event_type, event_ts, exchange, symbol, side, payload_json)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    trade_id,
                    event_type,
                    event_ts,
                    exchange,
                    symbol,
                    side,
                    json.dumps(payload or {}, separators=(",", ":")),
                ),
            )
        except sqlite3.Error as e:
            logger.debug(f"[Warehouse] record_trade_event skipped: {e}")

    def record_trade_close(
        self,
        *,
        trade_id: int,
        ts_exit: float,
        exit_px: float,
        realized_pnl: float,
        r_multiple: float | None = None,
        hold_sec: float | None = None,
        exit_reason: str = "",
        fee: float | None = None,
        slippage: float | None = None,
        entry_stop_px: float | None = None,
        partial_realized_pnl: float | None = None,
        exit_decision_id: str | None = None,
    ) -> None:
        """Update a trade row to CLOSED.

        Bug 2C — the trades schema stores `realized_pnl` (dollar) only; there
        is no separate pct column. Callers that hit a reconciled_no_context
        path (e.g. `position_tracker.reconcile_closed_pnl` with no entry/size
        to derive pct) must still pass the trusted dollar `realized_pnl`
        through. Downstream consumers (`knowledge_model`, `kelly_sizer`) are
        responsible for skipping rows where `pnl_pct is None` from
        WR/expectancy aggregations while keeping the dollar in ledger totals.
        """
        try:
            # Compute hold_sec if not provided
            if hold_sec is None:
                row = self._conn().execute(
                    "SELECT ts_entry FROM trades WHERE id=?", (trade_id,),
                ).fetchone()
                if row:
                    hold_sec = ts_exit - float(row["ts_entry"])
            self._conn().execute(
                """UPDATE trades SET
                    ts_exit=?, exit_px=?, realized_pnl=?, r_multiple=?,
                    hold_sec=?, exit_reason=?, status='CLOSED',
                    fee=COALESCE(?, fee), slippage=COALESCE(?, slippage),
                    entry_stop_px=COALESCE(?, entry_stop_px),
                    partial_realized_pnl=COALESCE(?, partial_realized_pnl),
                    exit_decision_id=COALESCE(?, exit_decision_id)
                WHERE id=?""",
                (ts_exit, exit_px, realized_pnl, r_multiple,
                 hold_sec, exit_reason, fee, slippage, entry_stop_px,
                 partial_realized_pnl, exit_decision_id, trade_id),
            )
            self._record_trade_event(
                trade_id=trade_id,
                event_type="CLOSE",
                event_ts=ts_exit,
                payload={
                    "exit_px": exit_px,
                    "realized_pnl": realized_pnl,
                    "r_multiple": r_multiple,
                    "exit_reason": exit_reason,
                },
            )
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_trade_close error: {e}")

    def recent_expectancy(
        self, symbol: str, *,
        side: str | None = None,
        days: int = 30,
        min_n: int = 5,
        max_rows: int = 50,
        whole_trade: bool = False,
    ) -> dict | None:
        """Per-symbol realised expectancy from the last `days` of CLOSED trades.

        Returns dict with keys {n, mean, win_rate, sum} or None when fewer
        than `min_n` rows exist (insufficient evidence — caller falls
        through and lets the trade through; we don't block on noise).

        2026-05-01: powers the per-trade expectancy filter
        (Tier 1.2 from the predictive-strategy stack). Used in
        bot_engine._execute_open — skip trades whose cell shows
        expected dollar PnL below 2x round-trip fee.

        Side filter: when `side` is passed, restricts to matching trades
        only (longs vs shorts have very different distributions). When
        None, aggregates across both sides.
        """
        try:
            since = int(__import__("time").time() - days * 86400)
            where = ["status='CLOSED'", "symbol=?", "ts_entry>=?"]
            params: list = [symbol, since]
            if side:
                where.append("side=?")
                params.append(side)
            params.append(int(max_rows))
            # A2 (audit 2026-06-21): when whole_trade, measure expectancy on
            # whole-trade PnL (runner realized_pnl + partial_realized_pnl) instead
            # of the runner leg alone, which under-counts partial-taken winners.
            # ⚠ Owner-gated (EXPECTANCY_FILTER.whole_trade, default-OFF): folding
            # partials in makes per-symbol expectancy LESS negative -> the gate
            # ALLOWS MORE trades; on a NO_EDGE bot that means more negative-EV
            # trades, so it must NOT be enabled without edge. Default OFF =>
            # byte-identical (runner-only realized_pnl).
            _pnl_col = ("realized_pnl + COALESCE(partial_realized_pnl, 0)"
                        if whole_trade else "realized_pnl")
            sql = (
                f"SELECT {_pnl_col} AS pnl FROM trades "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY ts_entry DESC LIMIT ?"
            )
            rows = self._conn().execute(sql, tuple(params)).fetchall()
            pnls = [
                float(r["pnl"]) for r in rows
                if r["pnl"] is not None
            ]
            n = len(pnls)
            if n < min_n:
                return None
            out = {
                "n": n,
                "mean": sum(pnls) / n,
                "sum": sum(pnls),
                "win_rate": sum(1 for p in pnls if p > 0) / n,
            }
            # 2026-06-11: informational 95% t-CI on the mean — makes
            # noise-vs-signal visible in [EV-CI] logs. Tier thresholds
            # stay point-estimate (owner rule); closed form, O(n), no rng.
            try:
                from core.stats_inference import mean_ci
                out["mean_ci95"] = list(mean_ci(pnls)[1:])
            except Exception:
                pass
            return out
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] recent_expectancy error: {e}")
            return None

    def trade_id_by_key(
        self, *, exchange: str, symbol: str, ts_entry: float, side: str,
        tolerance_sec: float = 2.0,
    ) -> int | None:
        """Look up an OPEN trade row by its idempotency key.

        2026-04-30 fix: previously this did exact-float equality on
        `ts_entry`. The caller (`order_manager._finalize_close`) passes the
        Position object's `open_time` — which is a different float source
        than what was originally written to the warehouse (see
        `record_trade_open`). They're typically the same value, but
        sub-second drift, json-roundtrip truncation, or even microsecond
        precision differences silently caused the lookup to miss → the
        warehouse update was skipped → the trade row stayed
        `status='OPEN'` forever despite a real close having fired.

        Symptom: dashboard "Best PnL" stat omitted closed trades whose
        warehouse row was orphaned. As of this fix's discovery, 5 DOGE
        trades + 17 others were stuck OPEN in the warehouse.

        Fix:
          1. Try exact match first (preserves the prior behaviour for
             callers whose ts_entry is bit-for-bit identical).
          2. Fall back to a `±tolerance_sec` window, picking the OPEN
             row closest in time to the requested ts_entry.
          3. Restrict to `status='OPEN'` — never silently overwrite a
             previously-closed trade with new close data.
        """
        try:
            # Step 1: exact match, OPEN-only.
            row = self._conn().execute(
                "SELECT id FROM trades "
                "WHERE exchange=? AND symbol=? AND ts_entry=? AND side=? "
                "AND status='OPEN' "
                "ORDER BY id DESC LIMIT 1",
                (exchange, symbol, ts_entry, side),
            ).fetchone()
            if row:
                return int(row["id"])
            # Step 2: tolerance window, pick the closest OPEN match.
            tol = float(tolerance_sec)
            if tol <= 0:
                return None
            row = self._conn().execute(
                "SELECT id, ts_entry FROM trades "
                "WHERE exchange=? AND symbol=? AND side=? AND status='OPEN' "
                "AND ts_entry >= ? AND ts_entry <= ? "
                "ORDER BY ABS(ts_entry - ?) ASC, id DESC LIMIT 1",
                (exchange, symbol, side,
                 ts_entry - tol, ts_entry + tol, ts_entry),
            ).fetchone()
            return int(row["id"]) if row else None
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] trade_id_by_key error: {e}")
            return None

    # ── Claude reviews ────────────────────────────────────────────────

    def record_claude_review(
        self,
        *,
        ref_type: str,           # 'trade' or 'candidate'
        ref_id: int | None,
        task: str,               # historical_annotation | pre_trade_review | ...
        decision: str | None = None,
        confidence: float | None = None,
        red_flags: list | None = None,
        reason_labels: list | None = None,
        commentary: str = "",
        ts: float | None = None,
    ) -> int:
        try:
            cur = self._conn().execute(
                """INSERT INTO claude_reviews(
                    ts, ref_type, ref_id, task, decision, confidence,
                    red_flags_json, reason_labels_json, commentary)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (ts or time.time(), ref_type, ref_id, task, decision, confidence,
                 json.dumps(red_flags or []),
                 json.dumps(reason_labels or []),
                 commentary),
            )
            return int(cur.lastrowid)
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_claude_review error: {e}")
            return -1

    # ── Attribution (Phase 1) ─────────────────────────────────────────

    def record_attribution(
        self,
        *,
        trade_id: int,
        alpha: float,
        spread: float,
        slippage: float,
        funding: float,
        fees: float,
        realized_pnl: float,
        attributed_at: int | None = None,
    ) -> None:
        """Insert (or replace) per-trade attribution row.

        Keyed on trade_id (PK). Re-running attribution for the same trade
        replaces the prior row — useful when later analysis adds a slippage
        snapshot we didn't have at close time.
        """
        try:
            self._conn().execute(
                """INSERT OR REPLACE INTO attribution(
                    trade_id, alpha, spread, slippage, funding, fees,
                    realized_pnl, attributed_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (int(trade_id), float(alpha), float(spread), float(slippage),
                 float(funding), float(fees), float(realized_pnl),
                 int(attributed_at) if attributed_at is not None else int(time.time())),
            )
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_attribution error: {e}")

    def update_btc_alpha(self, updates) -> int:
        """Batch-fill beta-adjusted alpha-vs-BTC labels onto closed trades.

        updates: iterable of (alpha_vs_btc, btc_ret_window, beta_used, trade_id).
        The backfill selects only rows where alpha_vs_btc IS NULL, so re-runs are
        idempotent. Returns the number of rows written. See core/btc_alpha.py.
        """
        rows = [
            (float(a), float(br), float(b), int(tid))
            for (a, br, b, tid) in updates
        ]
        if not rows:
            return 0
        try:
            conn = self._conn()
            conn.executemany(
                "UPDATE trades SET alpha_vs_btc=?, btc_ret_window=?, beta_used=? "
                "WHERE id=?",
                rows,
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] update_btc_alpha error: {e}")
            return 0
        return len(rows)

    # ── Labels (triple-barrier) ───────────────────────────────────────

    def record_label(
        self,
        *,
        candidate_id: int,
        ts: float,
        symbol: str,
        side: str,
        market_type: str,
        y: int,
        exit_bars: int | None,
        label_horizon_bars: int,
        sl_pct: float | None = None,
        tp_pct: float | None = None,
    ) -> None:
        """Idempotent insert of a triple-barrier label. PK is candidate_id —
        re-running the labeler with a different horizon is allowed and replaces
        the prior row for that candidate."""
        try:
            self._conn().execute(
                """INSERT OR REPLACE INTO labels(
                    candidate_id, ts, symbol, side, market_type, y, exit_bars,
                    label_horizon_bars, sl_pct, tp_pct)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (int(candidate_id), float(ts), str(symbol), str(side),
                 str(market_type), int(y),
                 int(exit_bars) if exit_bars is not None else None,
                 int(label_horizon_bars),
                 float(sl_pct) if sl_pct is not None else None,
                 float(tp_pct) if tp_pct is not None else None),
            )
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_label error: {e}")

    def record_model_version(
        self,
        *,
        model_version: str,
        trained_at: int,
        train_window_start: int,
        train_window_end: int,
        oos_sharpe: float,
        oos_wr: float,
        deflated_sharpe: float,
        pbo: float,
        artifact_path: str,
    ) -> None:
        try:
            self._conn().execute(
                """INSERT OR REPLACE INTO model_versions(
                    model_version, trained_at, train_window_start, train_window_end,
                    oos_sharpe, oos_wr, deflated_sharpe, pbo, artifact_path)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(model_version), int(trained_at),
                 int(train_window_start), int(train_window_end),
                 float(oos_sharpe), float(oos_wr),
                 float(deflated_sharpe), float(pbo), str(artifact_path)),
            )
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_model_version error: {e}")

    def record_carry_cycle(self, cyc: dict) -> None:
        """Book one F1 carry cycle row (Phase 3 paper runner; lazy migration).

        ``carry_cycles`` follows the shadow_outcomes convention: one RESOLVED
        (or FAILED) after-cost row per closed carry pair.
        """
        try:
            conn = self._conn()
            conn.execute(
                """CREATE TABLE IF NOT EXISTS carry_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, venue TEXT,
                    opened_ts REAL, resolved_ts REAL, notional REAL,
                    gross_funding REAL, basis_pnl REAL, fees REAL,
                    slippage REAL, net_pnl REAL,
                    settlements_held INTEGER, bars_held INTEGER,
                    exit_reason TEXT, label_status TEXT)"""
            )
            conn.execute(
                """INSERT INTO carry_cycles(
                    symbol, venue, opened_ts, resolved_ts, notional,
                    gross_funding, basis_pnl, fees, slippage, net_pnl,
                    settlements_held, bars_held, exit_reason, label_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(cyc.get("symbol")), str(cyc.get("venue")),
                 float(cyc.get("opened_ts") or 0), float(cyc.get("resolved_ts") or 0),
                 float(cyc.get("notional") or 0),
                 float(cyc.get("gross_funding") or 0), float(cyc.get("basis_pnl") or 0),
                 float(cyc.get("fees") or 0), float(cyc.get("slippage") or 0),
                 float(cyc.get("net_pnl") or 0),
                 int(cyc.get("settlements_held") or 0), int(cyc.get("bars_held") or 0),
                 str(cyc.get("exit_reason")), str(cyc.get("label_status"))),
            )
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_carry_cycle error: {e}")

    # ── Query helpers ─────────────────────────────────────────────────

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        """Raw parameterised read. Returns list of dicts."""
        rows = self._conn().execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def latest_candidates(self, limit: int = 100) -> list[dict]:
        return self.query(
            "SELECT * FROM candidates ORDER BY ts DESC LIMIT ?", (limit,),
        )

    def trades_in_range(self, since_ts: float, until_ts: float | None = None) -> list[dict]:
        if until_ts is None:
            until_ts = time.time()
        return self.query(
            "SELECT * FROM trades WHERE ts_entry BETWEEN ? AND ? ORDER BY ts_entry",
            (since_ts, until_ts),
        )

    def unannotated_trades(self, limit: int = 20) -> list[dict]:
        """Trades with no historical_annotation claude review yet."""
        return self.query(
            """SELECT t.* FROM trades t
               LEFT JOIN claude_reviews r
                 ON r.ref_type='trade' AND r.ref_id=t.id AND r.task='historical_annotation'
               WHERE t.status='CLOSED' AND r.id IS NULL
               ORDER BY t.ts_entry DESC LIMIT ?""",
            (limit,),
        )

    def counts(self) -> dict:
        return {
            "candidates": self.query("SELECT COUNT(*) c FROM candidates")[0]["c"],
            "trades":     self.query("SELECT COUNT(*) c FROM trades")[0]["c"],
            "reviews":    self.query("SELECT COUNT(*) c FROM claude_reviews")[0]["c"],
        }


# Singleton for import convenience
_default: Warehouse | None = None


def get_warehouse() -> Warehouse:
    global _default
    if _default is None:
        _default = Warehouse()
    return _default
