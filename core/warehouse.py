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
from pathlib import Path
from typing import Any, Iterable

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
CREATE TABLE IF NOT EXISTS predictions (
    ts             INTEGER NOT NULL,
    model_version  TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    side           TEXT    NOT NULL,
    p_win          REAL    NOT NULL,
    raw_score      REAL,
    feature_hash   TEXT,
    PRIMARY KEY (ts, model_version, symbol, side)
);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_ts ON predictions(symbol, ts);

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
    ) -> int:
        """Insert an OPEN trade row. Idempotent on (exchange, symbol, ts_entry, side)."""
        try:
            cur = self._conn().execute(
                """INSERT OR IGNORE INTO trades(
                    candidate_id, ts_entry, exchange, symbol, market_type,
                    side, strategy_family, entry_px, size, leverage,
                    fee, slippage, status, mode, mcp_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, ts_entry, exchange, symbol, market_type,
                 side, strategy_family, entry_px, size, leverage,
                 fee, slippage, "OPEN", mode, mcp_score),
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
        """Update a trade row to CLOSED."""
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

    def trade_id_by_key(
        self, *, exchange: str, symbol: str, ts_entry: float, side: str,
    ) -> int | None:
        """Look up a trade row by its idempotency key. Returns id or None."""
        try:
            row = self._conn().execute(
                "SELECT id FROM trades WHERE exchange=? AND symbol=? AND ts_entry=? AND side=?",
                (exchange, symbol, ts_entry, side),
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
