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
            ):
                try:
                    conn.execute(f"ALTER TABLE shadow_decisions ADD COLUMN {col_def}")
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
    ) -> int:
        """Insert an OPEN trade row. Idempotent on (exchange, symbol, ts_entry, side)."""
        try:
            cur = self._conn().execute(
                """INSERT OR IGNORE INTO trades(
                    candidate_id, ts_entry, exchange, symbol, market_type,
                    side, strategy_family, entry_px, size, leverage,
                    fee, slippage, status, mode, mcp_score, model_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, ts_entry, exchange, symbol, market_type,
                 side, strategy_family, entry_px, size, leverage,
                 fee, slippage, "OPEN", mode, mcp_score, model_version),
            )
            if cur.lastrowid:
                return int(cur.lastrowid)
            # Already inserted — look it up
            got = self._conn().execute(
                "SELECT id FROM trades WHERE exchange=? AND symbol=? AND ts_entry=? AND side=?",
                (exchange, symbol, ts_entry, side),
            ).fetchone()
            return int(got["id"]) if got else -1
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_trade_open error: {e}")
            return -1

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
                    fee=COALESCE(?, fee), slippage=COALESCE(?, slippage)
                WHERE id=?""",
                (ts_exit, exit_px, realized_pnl, r_multiple,
                 hold_sec, exit_reason, fee, slippage, trade_id),
            )
        except sqlite3.Error as e:
            logger.warning(f"[Warehouse] record_trade_close error: {e}")

    def recent_expectancy(
        self, symbol: str, *,
        side: str | None = None,
        days: int = 30,
        min_n: int = 5,
        max_rows: int = 50,
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
            sql = (
                "SELECT realized_pnl FROM trades "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY ts_entry DESC LIMIT ?"
            )
            rows = self._conn().execute(sql, tuple(params)).fetchall()
            pnls = [
                float(r["realized_pnl"]) for r in rows
                if r["realized_pnl"] is not None
            ]
            n = len(pnls)
            if n < min_n:
                return None
            return {
                "n": n,
                "mean": sum(pnls) / n,
                "sum": sum(pnls),
                "win_rate": sum(1 for p in pnls if p > 0) / n,
            }
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
