"""Warehouse round-trip tests — candidate + trade lifecycle, idempotency,
and Claude review linkage. Uses a temp DB so the production warehouse is
untouched.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse_module():
    """Load core/warehouse.py directly — bypass core/__init__.py which
    pulls in BotEngine → schedule → etc."""
    spec = importlib.util.spec_from_file_location(
        "warehouse_test_copy", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_test_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    mod = _load_warehouse_module()
    return mod.Warehouse(path=tmp_path / "t.sqlite")


def test_candidate_roundtrip(wh):
    cid = wh.record_candidate(
        exchange="binance", symbol="BTC/USDT", side="buy",
        strategy_family="systematic_v3_1", regime_label="trend",
        entry_px=70000.0, stop_px=69000.0, target_px=72000.0,
        leverage=5, size_pct=1.5, confidence=0.72,
        decision="ALLOW", features={"adx_4h": 28.5, "spread_pct": 0.02},
    )
    assert cid > 0
    rows = wh.query("SELECT * FROM candidates WHERE id=?", (cid,))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"
    assert rows[0]["decision"] == "ALLOW"


def test_trade_open_close_lifecycle(wh):
    cid = wh.record_candidate(
        exchange="binance", symbol="BTC/USDT", side="buy",
        decision="TAKEN", features={},
    )
    tid = wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=1000.0, entry_px=70000.0, size=0.01, leverage=5,
        candidate_id=cid, mode="PAPER",
    )
    assert tid > 0

    wh.record_trade_close(
        trade_id=tid, ts_exit=1600.0, exit_px=71000.0,
        realized_pnl=10.0, r_multiple=1.0, exit_reason="TP",
        fee=0.04, slippage=0.01,
    )

    rows = wh.query("SELECT * FROM trades WHERE id=?", (tid,))
    assert len(rows) == 1
    tr = rows[0]
    assert tr["status"] == "CLOSED"
    assert tr["exit_reason"] == "TP"
    assert tr["realized_pnl"] == 10.0
    assert tr["hold_sec"] == 600.0


def test_trade_open_idempotent(wh):
    """Re-inserting the same (exchange, symbol, ts_entry, side) is a no-op."""
    a = wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=42.0, entry_px=50000.0, size=0.1,
    )
    b = wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=42.0, entry_px=99999.0, size=0.9,  # different data, same key
    )
    assert a == b, "re-insert should return the existing trade id"
    rows = wh.query(
        "SELECT entry_px, size FROM trades WHERE exchange='binance' "
        "AND symbol='BTC/USDT' AND ts_entry=42.0",
    )
    assert len(rows) == 1, "dedup must not create a second row"
    # Original data is kept; UPDATE is NOT performed by record_trade_open.
    assert rows[0]["entry_px"] == 50000.0


def test_trade_id_by_key_lookup(wh):
    tid = wh.record_trade_open(
        exchange="bybit", symbol="ETH/USDT", side="sell",
        ts_entry=100.0, entry_px=3000.0, size=0.05,
    )
    got = wh.trade_id_by_key(
        exchange="bybit", symbol="ETH/USDT", ts_entry=100.0, side="sell",
    )
    assert got == tid
    assert wh.trade_id_by_key(
        exchange="bybit", symbol="ETH/USDT", ts_entry=999.0, side="sell",
    ) is None


def test_claude_review_link(wh):
    cid = wh.record_candidate(
        exchange="binance", symbol="BTC/USDT", side="buy",
        decision="REVIEW", features={},
    )
    rid = wh.record_claude_review(
        ref_type="candidate", ref_id=cid, task="pre_trade_review",
        decision="ALLOW", confidence=4.0,
        red_flags=["none"], commentary="ok",
    )
    assert rid > 0
    rows = wh.query(
        "SELECT * FROM claude_reviews WHERE ref_id=? AND task=?",
        (cid, "pre_trade_review"),
    )
    assert len(rows) == 1
    assert rows[0]["decision"] == "ALLOW"


def test_query_returns_dicts(wh):
    wh.record_candidate(exchange="x", symbol="X/USDT", side="buy",
                        decision="SKIP", features={})
    rows = wh.query("SELECT COUNT(*) AS n FROM candidates")
    assert rows[0]["n"] == 1


def test_mcp_score_persisted_on_open(wh):
    """Entry-time MCP Brain score must survive to the warehouse row.
    Without this, we can't measure score→outcome correlation after the fact."""
    tid = wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=500.0, entry_px=70000.0, size=0.01, leverage=5,
        mode="CONTROLLED_LIVE", mcp_score=78.5,
    )
    assert tid > 0
    rows = wh.query("SELECT mcp_score FROM trades WHERE id=?", (tid,))
    assert rows[0]["mcp_score"] == pytest.approx(78.5)


def test_mcp_score_optional(wh):
    """Legacy callers that don't pass mcp_score still succeed; column is NULL."""
    tid = wh.record_trade_open(
        exchange="bybit", symbol="ETH/USDT", side="sell",
        ts_entry=600.0, entry_px=3000.0, size=0.05,
    )
    assert tid > 0
    rows = wh.query("SELECT mcp_score FROM trades WHERE id=?", (tid,))
    assert rows[0]["mcp_score"] is None


def test_quant_tables_exist(wh):
    """Phase 0.2: features / predictions / shadow_decisions / attribution /
    model_versions must be created on init."""
    rows = wh.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )
    names = {r["name"] for r in rows}
    expected = {"features", "predictions", "shadow_decisions",
                "attribution", "model_versions"}
    missing = expected - names
    assert not missing, f"missing quant tables: {missing}"


def test_features_pk_enforced(wh):
    """Composite PK on features must reject duplicate (ts, symbol, tf, name)."""
    conn = wh._conn()
    conn.execute(
        "INSERT INTO features(ts, symbol, timeframe, feature_name, value) "
        "VALUES (?,?,?,?,?)", (100, "BTC/USDT", "1h", "rsi_14", 55.0))
    import sqlite3 as _sql
    with pytest.raises(_sql.IntegrityError):
        conn.execute(
            "INSERT INTO features(ts, symbol, timeframe, feature_name, value) "
            "VALUES (?,?,?,?,?)", (100, "BTC/USDT", "1h", "rsi_14", 60.0))


def test_predictions_pk_enforced(wh):
    """Idempotency: same (ts, model_version, symbol, side) rejected."""
    conn = wh._conn()
    conn.execute(
        "INSERT INTO predictions(ts, model_version, symbol, side, p_win) "
        "VALUES (?,?,?,?,?)", (200, "lr_v1", "BTC/USDT", "long", 0.62))
    import sqlite3 as _sql
    with pytest.raises(_sql.IntegrityError):
        conn.execute(
            "INSERT INTO predictions(ts, model_version, symbol, side, p_win) "
            "VALUES (?,?,?,?,?)", (200, "lr_v1", "BTC/USDT", "long", 0.71))


def test_attribution_one_row_per_trade(wh):
    """attribution.trade_id is the PK — one row per trade, exact decomposition."""
    conn = wh._conn()
    conn.execute(
        "INSERT INTO attribution(trade_id, alpha, spread, slippage, funding, "
        "fees, realized_pnl, attributed_at) VALUES (?,?,?,?,?,?,?,?)",
        (1, 2.0, 0.15, 0.0, 0.20, 0.06, 1.59, 1000))
    import sqlite3 as _sql
    with pytest.raises(_sql.IntegrityError):
        conn.execute(
            "INSERT INTO attribution(trade_id, alpha) VALUES (?,?)", (1, 99.0))


def test_model_versions_pk(wh):
    """model_version is PK — same version cannot be re-inserted."""
    conn = wh._conn()
    conn.execute(
        "INSERT INTO model_versions(model_version, trained_at, oos_sharpe) "
        "VALUES (?,?,?)", ("lr_v_20260426", 1234567890, 1.5))
    import sqlite3 as _sql
    with pytest.raises(_sql.IntegrityError):
        conn.execute(
            "INSERT INTO model_versions(model_version, trained_at, oos_sharpe) "
            "VALUES (?,?,?)", ("lr_v_20260426", 9999999999, 9.9))


def test_quant_indexes_exist(wh):
    """Indexes on (symbol, ts) for features and predictions must be present."""
    rows = wh.query(
        "SELECT name FROM sqlite_master WHERE type='index'",
    )
    names = {r["name"] for r in rows}
    assert "idx_features_symbol_ts" in names
    assert "idx_predictions_symbol_ts" in names


def test_mcp_score_column_added_to_legacy_db(tmp_path):
    """A warehouse.sqlite created before mcp_score existed should be migrated
    in-place on the next Warehouse() init, not crash.

    Simulates upgrade from prior schema by opening a fresh Warehouse, then
    physically dropping the column via table-rebuild (SQLite can't DROP COLUMN
    before 3.35), then re-opening — the idempotent ALTER must re-add it."""
    import sqlite3 as _sqlite
    mod = _load_warehouse_module()
    db = tmp_path / "legacy.sqlite"
    wh1 = mod.Warehouse(path=db)
    wh1.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=1.0, entry_px=70000.0, size=0.01,
    )
    # Drop the column by rebuilding the table without it.
    conn = _sqlite.connect(str(db))
    conn.executescript("""
        ALTER TABLE trades RENAME TO trades_old;
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER, ts_entry REAL NOT NULL, ts_exit REAL,
            exchange TEXT NOT NULL, symbol TEXT NOT NULL, market_type TEXT,
            side TEXT NOT NULL, strategy_family TEXT, entry_px REAL,
            exit_px REAL, size REAL, leverage INTEGER, fee REAL, slippage REAL,
            realized_pnl REAL, r_multiple REAL, hold_sec REAL,
            status TEXT, exit_reason TEXT, mode TEXT
        );
        INSERT INTO trades(id, candidate_id, ts_entry, ts_exit, exchange, symbol,
            market_type, side, strategy_family, entry_px, exit_px, size, leverage,
            fee, slippage, realized_pnl, r_multiple, hold_sec, status, exit_reason,
            mode)
        SELECT id, candidate_id, ts_entry, ts_exit, exchange, symbol,
            market_type, side, strategy_family, entry_px, exit_px, size, leverage,
            fee, slippage, realized_pnl, r_multiple, hold_sec, status, exit_reason,
            mode FROM trades_old;
        DROP TABLE trades_old;
    """)
    conn.commit()
    conn.close()
    cols_before = set()
    conn = _sqlite.connect(str(db))
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    conn.close()
    assert "mcp_score" not in cols_before, "setup: column should be absent"

    # Re-open via Warehouse() — the ALTER TABLE migration must add it back.
    wh2 = mod.Warehouse(path=db)
    cols_after = {r["name"] for r in wh2.query("PRAGMA table_info(trades)")}
    assert "mcp_score" in cols_after
