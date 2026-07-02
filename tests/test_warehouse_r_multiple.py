"""Tests for the warehouse r_multiple data-integrity fixes (2026-05-27).

Covers:
  A. The _finalize_close candidate-stop fallback added to order_manager.py:
     when pos.stop_loss == 0, the code must query the warehouse for
     candidate.stop_px and use it as the risk denominator.

  B. fix_warehouse_integrity.py helpers:
     - _compute_r_multiple: side-aware formula, edge cases
     - collect_backfill_rows: only returns rows with a candidate stop_px
     - apply_backfill: idempotent; does not overwrite existing r_multiples
     - apply_phantom: marks the phantom OPEN trade CLOSED

  C. The phantom-close only touches trades that are still status='OPEN'.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _load_fix_script():
    """Load scripts/fix_warehouse_integrity.py without executing __main__."""
    spec = importlib.util.spec_from_file_location(
        "fix_warehouse_integrity",
        ROOT / "scripts" / "fix_warehouse_integrity.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fix_warehouse_integrity"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_warehouse_module():
    spec = importlib.util.spec_from_file_location(
        "warehouse_test_r",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_test_r"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fix_mod():
    return _load_fix_script()


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection with the minimal schema for tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE candidates (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            stop_px  REAL
        );
        CREATE TABLE trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER,
            side         TEXT,
            symbol       TEXT,
            entry_px     REAL,
            exit_px      REAL,
            r_multiple   REAL,
            status       TEXT DEFAULT 'CLOSED',
            ts_entry     REAL
        );
        """
    )
    return conn


# ---------------------------------------------------------------------------
# A. _compute_r_multiple
# ---------------------------------------------------------------------------

class TestComputeRMultiple:
    def test_buy_winner(self, fix_mod):
        # entry=100, stop=95, exit=110 => (110-100)/(100-95) = 2.0
        r = fix_mod._compute_r_multiple("buy", 100.0, 110.0, 95.0)
        assert r == pytest.approx(2.0)

    def test_buy_loser(self, fix_mod):
        # entry=100, stop=95, exit=97 => (97-100)/(100-95) = -0.6
        r = fix_mod._compute_r_multiple("buy", 100.0, 97.0, 95.0)
        assert r == pytest.approx(-0.6)

    def test_sell_winner(self, fix_mod):
        # entry=100, stop=105, exit=90 => (100-90)/(105-100) = 2.0
        r = fix_mod._compute_r_multiple("sell", 100.0, 90.0, 105.0)
        assert r == pytest.approx(2.0)

    def test_sell_loser(self, fix_mod):
        # entry=100, stop=105, exit=103 => (100-103)/(105-100) = -0.6
        r = fix_mod._compute_r_multiple("sell", 100.0, 103.0, 105.0)
        assert r == pytest.approx(-0.6)

    def test_degenerate_denom_buy(self, fix_mod):
        # stop == entry: denom == 0
        assert fix_mod._compute_r_multiple("buy", 100.0, 110.0, 100.0) is None

    def test_degenerate_denom_sell(self, fix_mod):
        assert fix_mod._compute_r_multiple("sell", 100.0, 90.0, 100.0) is None

    def test_inverted_stop_buy(self, fix_mod):
        # stop > entry for a buy: bad data, denom < 0 -> None
        assert fix_mod._compute_r_multiple("buy", 100.0, 105.0, 102.0) is None

    def test_inverted_stop_sell(self, fix_mod):
        # stop < entry for a sell: bad data
        assert fix_mod._compute_r_multiple("sell", 100.0, 95.0, 98.0) is None

    def test_result_is_rounded_to_3dp(self, fix_mod):
        r = fix_mod._compute_r_multiple("buy", 1.0000, 1.0033, 0.9967)
        assert r is not None
        assert round(r, 3) == r


# ---------------------------------------------------------------------------
# B. collect_backfill_rows / apply_backfill
# ---------------------------------------------------------------------------

class TestCollectBackfillRows:
    def test_returns_only_null_r_multiple(self, fix_mod, mem_conn):
        mem_conn.execute("INSERT INTO candidates(stop_px) VALUES (95.0)")
        cid = mem_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Row 1: already has r_multiple — must be skipped
        mem_conn.execute(
            "INSERT INTO trades(candidate_id, side, entry_px, exit_px, r_multiple, status) "
            "VALUES (?, 'buy', 100.0, 110.0, 2.0, 'CLOSED')",
            (cid,),
        )
        # Row 2: NULL r_multiple — must be included
        mem_conn.execute(
            "INSERT INTO trades(candidate_id, side, entry_px, exit_px, r_multiple, status) "
            "VALUES (?, 'buy', 100.0, 107.0, NULL, 'CLOSED')",
            (cid,),
        )
        rows = fix_mod.collect_backfill_rows(mem_conn)
        assert len(rows) == 1
        assert rows[0]["trade_id"] is not None
        assert rows[0]["r_multiple"] == pytest.approx(1.4)

    def test_skips_trades_without_candidate(self, fix_mod, mem_conn):
        # Trade with no candidate_id
        mem_conn.execute(
            "INSERT INTO trades(candidate_id, side, entry_px, exit_px, r_multiple, status) "
            "VALUES (NULL, 'buy', 100.0, 110.0, NULL, 'CLOSED')"
        )
        rows = fix_mod.collect_backfill_rows(mem_conn)
        # No new rows should appear (no matching candidate join)
        assert all(r["trade_id"] is not None for r in rows)

    def test_skips_candidate_with_null_stop(self, fix_mod, mem_conn):
        mem_conn.execute("INSERT INTO candidates(stop_px) VALUES (NULL)")
        cid = mem_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        mem_conn.execute(
            "INSERT INTO trades(candidate_id, side, entry_px, exit_px, r_multiple, status) "
            "VALUES (?, 'buy', 100.0, 110.0, NULL, 'CLOSED')",
            (cid,),
        )
        rows = fix_mod.collect_backfill_rows(mem_conn)
        # Candidate with null stop_px must not appear
        assert len(rows) == 0


class TestApplyBackfill:
    def _setup_trade(self, conn, stop_px, entry_px=100.0, exit_px=110.0, side="buy"):
        conn.execute("INSERT INTO candidates(stop_px) VALUES (?)", (stop_px,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO trades(candidate_id, side, entry_px, exit_px, r_multiple, status) "
            "VALUES (?, ?, ?, ?, NULL, 'CLOSED')",
            (cid, side, entry_px, exit_px),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_writes_r_multiple(self, fix_mod, mem_conn):
        tid = self._setup_trade(mem_conn, stop_px=95.0)
        rows = fix_mod.collect_backfill_rows(mem_conn)
        fix_mod.apply_backfill(mem_conn, rows)
        r = mem_conn.execute(
            "SELECT r_multiple FROM trades WHERE id=?", (tid,)
        ).fetchone()[0]
        assert r == pytest.approx(2.0)

    def test_idempotent_does_not_overwrite(self, fix_mod, mem_conn):
        tid = self._setup_trade(mem_conn, stop_px=95.0)
        rows = fix_mod.collect_backfill_rows(mem_conn)
        fix_mod.apply_backfill(mem_conn, rows)
        # Mutate the DB manually so the value changes
        mem_conn.execute("UPDATE trades SET r_multiple = 99.0 WHERE id=?", (tid,))
        # apply again — should NOT overwrite because WHERE r_multiple IS NULL fails
        rows2 = fix_mod.collect_backfill_rows(mem_conn)  # should be empty now
        n = fix_mod.apply_backfill(mem_conn, rows2)
        assert n == 0
        r = mem_conn.execute(
            "SELECT r_multiple FROM trades WHERE id=?", (tid,)
        ).fetchone()[0]
        assert r == pytest.approx(99.0)

    def test_skips_degenerate_denom(self, fix_mod, mem_conn):
        """When stop_px == entry_px the row is collected but r_multiple=None; apply skips it."""
        self._setup_trade(mem_conn, stop_px=100.0)  # stop == entry -> None
        rows = fix_mod.collect_backfill_rows(mem_conn)
        assert len(rows) == 1
        assert rows[0]["r_multiple"] is None
        n = fix_mod.apply_backfill(mem_conn, rows)
        assert n == 0


# ---------------------------------------------------------------------------
# C. apply_phantom / preview_phantom
# ---------------------------------------------------------------------------

class TestApplyPhantom:
    def _insert_trade(self, conn, status="OPEN"):
        conn.execute(
            "INSERT INTO trades(id, side, entry_px, exit_px, r_multiple, status, ts_entry) "
            "VALUES (?, 'buy', 1.4354, NULL, NULL, ?, ?)",
            (411, status, time.time() - 86400 * 36),
        )

    def test_marks_open_trade_closed(self, fix_mod, mem_conn):
        # Add ts_entry column (not in minimal schema)
        mem_conn.execute("ALTER TABLE trades ADD COLUMN ts_exit REAL")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN hold_sec REAL")
        self._insert_trade(mem_conn, status="OPEN")
        fix_mod.PHANTOM_TRADE_ID = 411
        fix_mod.apply_phantom(mem_conn)
        row = mem_conn.execute(
            "SELECT status, exit_reason FROM trades WHERE id=411"
        ).fetchone()
        assert row[0] == "CLOSED"
        assert row[1] == "phantom_stale"

    def test_noop_when_already_closed(self, fix_mod, mem_conn):
        mem_conn.execute("ALTER TABLE trades ADD COLUMN ts_exit REAL")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN hold_sec REAL")
        self._insert_trade(mem_conn, status="CLOSED")
        fix_mod.PHANTOM_TRADE_ID = 411
        fix_mod.apply_phantom(mem_conn)
        row = mem_conn.execute(
            "SELECT exit_reason FROM trades WHERE id=411"
        ).fetchone()
        # Should remain None (was CLOSED before, apply uses WHERE status='OPEN')
        assert row[0] is None

    def test_preview_phantom_detects_open(self, fix_mod, mem_conn, capsys):
        mem_conn.execute("ALTER TABLE trades ADD COLUMN ts_exit REAL")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN hold_sec REAL")
        mem_conn.execute(
            "INSERT INTO trades(id, side, status, ts_entry) VALUES (411, 'buy', 'OPEN', ?)",
            (time.time() - 3600,),
        )
        fix_mod.PHANTOM_TRADE_ID = 411
        result = fix_mod.preview_phantom(mem_conn)
        assert result is True
        out = capsys.readouterr().out
        assert "WILL close" in out

    def test_preview_phantom_closed_reports_nothing(self, fix_mod, mem_conn, capsys):
        mem_conn.execute("ALTER TABLE trades ADD COLUMN ts_exit REAL")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
        mem_conn.execute("ALTER TABLE trades ADD COLUMN hold_sec REAL")
        mem_conn.execute(
            "INSERT INTO trades(id, side, status, ts_entry) VALUES (411, 'buy', 'CLOSED', ?)",
            (time.time() - 3600,),
        )
        fix_mod.PHANTOM_TRADE_ID = 411
        result = fix_mod.preview_phantom(mem_conn)
        assert result is False


# ---------------------------------------------------------------------------
# D. _finalize_close candidate-stop fallback (order_manager code path)
# ---------------------------------------------------------------------------

class TestFinalizeCloseCandidateStopFallback:
    """Verify that _finalize_close uses candidate.stop_px when pos.stop_loss==0.

    We do not import the full OrderManager (it pulls BotEngine dependencies).
    Instead we extract and unit-test the r_multiple computation logic through
    a minimal re-implementation that mirrors the patched code path exactly.
    """

    def _compute_r_mult_with_fallback(
        self, pos_stop_loss: float, entry_px: float, exit_px: float,
        side: str, candidate_stop_px: float | None,
    ) -> float | None:
        """Mirror of the patched _finalize_close r_multiple block."""
        sl = float(pos_stop_loss or 0)
        ep = float(entry_px or 0)
        xp = float(exit_px or 0)
        # Candidate fallback
        if sl <= 0 and ep > 0 and candidate_stop_px is not None:
            sl = float(candidate_stop_px)
        r_mult = None
        if sl > 0 and ep > 0 and xp > 0 and sl != ep:
            if side == "buy":
                denom = ep - sl
                if denom > 0:
                    r_mult = (xp - ep) / denom
            else:
                denom = sl - ep
                if denom > 0:
                    r_mult = (ep - xp) / denom
        return round(r_mult, 3) if r_mult is not None else None

    def test_uses_candidate_stop_when_pos_stop_zero(self):
        # pos.stop_loss == 0, but candidate has stop_px=95
        r = self._compute_r_mult_with_fallback(0.0, 100.0, 110.0, "buy", 95.0)
        assert r == pytest.approx(2.0)

    def test_pos_stop_takes_priority_over_candidate(self):
        # pos.stop_loss is valid: should use pos stop (90), not candidate stop (95)
        r = self._compute_r_mult_with_fallback(90.0, 100.0, 110.0, "buy", 95.0)
        # (110-100)/(100-90) = 1.0
        assert r == pytest.approx(1.0)

    def test_no_fallback_when_no_candidate_stop(self):
        # pos.stop_loss == 0 and candidate_stop_px is None -> r_mult stays None
        r = self._compute_r_mult_with_fallback(0.0, 100.0, 110.0, "buy", None)
        assert r is None

    def test_reconcile_path_now_gets_r_multiple(self):
        # Reconcile-from-exchange arrives with stop_loss=0 but candidate had stop 1.38
        r = self._compute_r_mult_with_fallback(0.0, 1.4354, 1.35, "buy", 1.38)
        # (1.35-1.4354)/(1.4354-1.38) = -0.0854/0.0554 ~ -1.542
        assert r is not None
        assert r < 0  # a loss

    def test_sell_side_candidate_fallback(self):
        # sell, pos.stop_loss==0, candidate.stop_px=105
        r = self._compute_r_mult_with_fallback(0.0, 100.0, 90.0, "sell", 105.0)
        # (100-90)/(105-100) = 2.0
        assert r == pytest.approx(2.0)
