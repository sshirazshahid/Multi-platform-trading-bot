"""Phase 1.2: backfill attribution for existing trades.

Tests run against a temp warehouse — the production warehouse is untouched.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path):
    wh_mod = _load_module("warehouse_bf", "core/warehouse.py")
    attr_mod = _load_module("attribution_bf", "core/attribution.py")
    bf_mod = _load_module("backfill_bf", "scripts/backfill_attribution.py")
    wh = wh_mod.Warehouse(path=tmp_path / "bf.sqlite")
    return wh, attr_mod, bf_mod


def _seed_trade(wh, *, trade_id_check=None, entry_px, exit_px, size, side,
                fee, slippage=0.0, ts_entry=1000.0, ts_exit=2000.0,
                exchange="binance", symbol="BTC/USDT:USDT"):
    """Insert an OPEN trade then close it; return the trade_id."""
    tid = wh.record_trade_open(
        exchange=exchange, symbol=symbol, side=side,
        ts_entry=ts_entry, entry_px=entry_px, size=size, leverage=3,
        fee=fee, slippage=slippage, mode="CONTROLLED_LIVE", mcp_score=70.0,
    )
    sign = 1 if side in ("buy", "long") else -1
    gross = (exit_px - entry_px) * size * sign
    realized = gross - fee
    wh.record_trade_close(
        trade_id=tid, ts_exit=ts_exit, exit_px=exit_px,
        realized_pnl=realized, exit_reason="TP", fee=fee, slippage=slippage,
    )
    return tid


def test_backfill_inserts_one_row_per_closed_trade(env):
    wh, _attr, bf = env
    t1 = _seed_trade(wh, entry_px=100.0, exit_px=102.0, size=1.0,
                     side="buy", fee=0.04)
    t2 = _seed_trade(wh, entry_px=200.0, exit_px=195.0, size=2.0,
                     side="sell", fee=0.08, ts_entry=1100.0, ts_exit=1500.0,
                     symbol="ETH/USDT:USDT")
    n = bf.backfill(warehouse=wh, commit=True)
    assert n == 2

    rows = wh.query("SELECT trade_id, alpha, fees, realized_pnl FROM attribution ORDER BY trade_id")
    assert len(rows) == 2
    by_id = {r["trade_id"]: r for r in rows}
    # Long: alpha = (102-100)*1 = 2.0
    assert by_id[t1]["alpha"] == pytest.approx(2.0, abs=1e-9)
    assert by_id[t1]["fees"] == pytest.approx(0.04, abs=1e-9)
    # Short on ETH: alpha = (200-195)*2 = 10.0 (sign=-1 applied on (exit-entry))
    assert by_id[t2]["alpha"] == pytest.approx(10.0, abs=1e-9)


def test_backfill_dry_run_writes_nothing(env):
    wh, _attr, bf = env
    _seed_trade(wh, entry_px=100.0, exit_px=101.0, size=1.0,
                side="buy", fee=0.02)
    n = bf.backfill(warehouse=wh, commit=False)
    assert n == 1  # would-have-processed count
    rows = wh.query("SELECT COUNT(*) c FROM attribution")
    assert rows[0]["c"] == 0


def test_backfill_skips_already_attributed(env):
    """Re-running backfill must skip trades already in `attribution`."""
    wh, attr, bf = env
    tid = _seed_trade(wh, entry_px=100.0, exit_px=101.0, size=1.0,
                      side="buy", fee=0.02)
    n1 = bf.backfill(warehouse=wh, commit=True)
    assert n1 == 1
    n2 = bf.backfill(warehouse=wh, commit=True)
    assert n2 == 0, "second run should find no un-attributed trades"


def test_backfill_skips_open_trades(env):
    wh, _attr, bf = env
    # Open trade, never closed
    wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        ts_entry=1000.0, entry_px=100.0, size=1.0, leverage=3,
        fee=0.02, mode="CONTROLLED_LIVE",
    )
    # Closed trade
    _seed_trade(wh, entry_px=100.0, exit_px=101.0, size=1.0,
                side="buy", fee=0.02, ts_entry=1100.0, ts_exit=1200.0)
    n = bf.backfill(warehouse=wh, commit=True)
    assert n == 1, "open trade must be skipped"


def test_backfill_invariant_holds_after_backfill(env):
    """For each backfilled trade: alpha - spread - slippage - funding - fees
    should match stored realized_pnl (within rounding)."""
    wh, _attr, bf = env
    _seed_trade(wh, entry_px=100.0, exit_px=102.5, size=1.5, side="buy",
                fee=0.10, slippage=0.05)
    bf.backfill(warehouse=wh, commit=True)
    row = wh.query(
        "SELECT a.*, t.realized_pnl AS stored_pnl FROM attribution a "
        "JOIN trades t ON t.id = a.trade_id"
    )[0]
    invariant = (row["alpha"] - row["spread"] - row["slippage"]
                 - row["funding"] - row["fees"])
    assert invariant == pytest.approx(row["realized_pnl"], abs=1e-6)
    assert row["realized_pnl"] == pytest.approx(row["stored_pnl"], abs=1e-6)


def test_backfill_handles_short(env):
    wh, _attr, bf = env
    # Short 100->95: profitable
    tid = _seed_trade(wh, entry_px=100.0, exit_px=95.0, size=2.0,
                      side="sell", fee=0.10)
    bf.backfill(warehouse=wh, commit=True)
    row = wh.query("SELECT * FROM attribution WHERE trade_id=?", (tid,))[0]
    # alpha = (95 - 100) * 2 * -1 = 10
    assert row["alpha"] == pytest.approx(10.0, abs=1e-9)
    assert row["realized_pnl"] == pytest.approx(10.0 - 0.10, abs=1e-9)


def test_backfill_limit(env):
    wh, _attr, bf = env
    for i in range(5):
        _seed_trade(wh, entry_px=100.0, exit_px=101.0, size=1.0,
                    side="buy", fee=0.02,
                    ts_entry=1000.0 + i, ts_exit=2000.0 + i,
                    symbol=f"X{i}/USDT")
    n = bf.backfill(warehouse=wh, commit=True, limit=3)
    assert n == 3
    rows = wh.query("SELECT COUNT(*) c FROM attribution")
    assert rows[0]["c"] == 3
