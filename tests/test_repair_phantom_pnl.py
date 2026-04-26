"""Phase 3: scrub phantom-PnL rows from warehouse.

Tests run against a temp warehouse — the production warehouse is untouched.
"""
from __future__ import annotations

import importlib.util
import json
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
def env(tmp_path, monkeypatch):
    wh_mod = _load_module("warehouse_rp", "core/warehouse.py")
    rp_mod = _load_module("repair_rp", "scripts/repair_phantom_pnl.py")
    wh = wh_mod.Warehouse(path=tmp_path / "rp.sqlite")
    # CRITICAL: every test must isolate DATA_DIR so commit=True does NOT
    # touch the real ./data/kelly_stats.json. Tests that need to inspect
    # the kelly file simply use this same tmp_path / "data".
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rp_mod, "DATA_DIR", data_dir)
    return wh, rp_mod, tmp_path


def _seed_phantom(wh, *, ts_entry, ts_exit, realized_pnl, symbol="BTC/USDT:USDT"):
    """Insert a phantom-marker trade (entry_px=1.0, size=1.0) and close as loss."""
    tid = wh.record_trade_open(
        exchange="binance", symbol=symbol, side="buy",
        ts_entry=ts_entry, entry_px=1.0, size=1.0, leverage=3,
        fee=0.0, mode="CONTROLLED_LIVE",
    )
    wh.record_trade_close(
        trade_id=tid, ts_exit=ts_exit, exit_px=1.0,
        realized_pnl=realized_pnl, exit_reason="reconciled_no_context",
    )
    return tid


def _seed_clean(wh, *, ts_entry, ts_exit, entry_px, exit_px, size,
                realized_pnl, symbol="ETH/USDT:USDT", side="buy",
                exit_reason="TP"):
    """Insert a normal trade with real entry/exit prices."""
    tid = wh.record_trade_open(
        exchange="binance", symbol=symbol, side=side,
        ts_entry=ts_entry, entry_px=entry_px, size=size, leverage=3,
        fee=0.0, mode="CONTROLLED_LIVE",
    )
    wh.record_trade_close(
        trade_id=tid, ts_exit=ts_exit, exit_px=exit_px,
        realized_pnl=realized_pnl, exit_reason=exit_reason,
    )
    return tid


def test_repair_annotates_only_phantom_rows(env):
    wh, rp, _ = env
    p1 = _seed_phantom(wh, ts_entry=1000.0, ts_exit=1100.0, realized_pnl=-3.0,
                       symbol="A/USDT:USDT")
    p2 = _seed_phantom(wh, ts_entry=1200.0, ts_exit=1300.0, realized_pnl=-5.0,
                       symbol="B/USDT:USDT")
    c1 = _seed_clean(wh, ts_entry=1400.0, ts_exit=1500.0, entry_px=100.0,
                     exit_px=101.0, size=1.0, realized_pnl=1.0,
                     symbol="C/USDT:USDT")
    # Clean loss but with real prices — must NOT be repaired even though pnl < -2
    c2 = _seed_clean(wh, ts_entry=1600.0, ts_exit=1700.0, entry_px=200.0,
                     exit_px=190.0, size=1.0, realized_pnl=-10.0,
                     symbol="D/USDT:USDT", exit_reason="SL")
    # Tiny phantom-marker loss above threshold — must NOT be repaired (>-2)
    c3_id = wh.record_trade_open(
        exchange="binance", symbol="E/USDT:USDT", side="buy",
        ts_entry=1800.0, entry_px=1.0, size=1.0, leverage=3,
        fee=0.0, mode="CONTROLLED_LIVE",
    )
    wh.record_trade_close(
        trade_id=c3_id, ts_exit=1900.0, exit_px=1.0,
        realized_pnl=-0.5, exit_reason="reconciled_no_context",
    )

    n = rp.repair(warehouse=wh, commit=True, skip_learning_rerun=True)
    assert n == 2, "exactly 2 phantom rows below threshold should be repaired"

    rows = {r["id"]: r for r in wh.query(
        "SELECT id, exit_reason FROM trades ORDER BY id")}
    assert rows[p1]["exit_reason"] == "reconciled_no_context_repaired"
    assert rows[p2]["exit_reason"] == "reconciled_no_context_repaired"
    assert rows[c1]["exit_reason"] == "TP"
    assert rows[c2]["exit_reason"] == "SL"
    assert rows[c3_id]["exit_reason"] == "reconciled_no_context"


def test_repair_is_idempotent(env):
    wh, rp, _ = env
    _seed_phantom(wh, ts_entry=1000.0, ts_exit=1100.0, realized_pnl=-3.0,
                  symbol="A/USDT:USDT")
    _seed_phantom(wh, ts_entry=1200.0, ts_exit=1300.0, realized_pnl=-5.0,
                  symbol="B/USDT:USDT")

    n1 = rp.repair(warehouse=wh, commit=True, skip_learning_rerun=True)
    assert n1 == 2

    n2 = rp.repair(warehouse=wh, commit=True, skip_learning_rerun=True)
    assert n2 == 0, "second run on already-repaired warehouse should be a no-op"


def test_repair_dry_run_writes_nothing(env):
    wh, rp, _ = env
    p1 = _seed_phantom(wh, ts_entry=1000.0, ts_exit=1100.0, realized_pnl=-3.0,
                       symbol="A/USDT:USDT")

    n = rp.repair(warehouse=wh, commit=False, skip_learning_rerun=True)
    assert n == 1, "dry-run should still report would-have-repaired count"

    rows = wh.query("SELECT exit_reason FROM trades WHERE id=?", (p1,))
    assert rows[0]["exit_reason"] == "reconciled_no_context", \
        "dry-run must not mutate exit_reason"


def test_repair_limit(env):
    wh, rp, _ = env
    for i in range(5):
        _seed_phantom(wh, ts_entry=1000.0 + i, ts_exit=1100.0 + i,
                      realized_pnl=-3.0 - i, symbol=f"X{i}/USDT:USDT")
    n = rp.repair(warehouse=wh, commit=True, limit=3, skip_learning_rerun=True)
    assert n == 3
    repaired = wh.query(
        "SELECT COUNT(*) c FROM trades WHERE exit_reason='reconciled_no_context_repaired'"
    )
    assert repaired[0]["c"] == 3


def test_repair_backs_up_kelly_stats(env):
    wh, rp, tmp_path = env
    data_dir = tmp_path / "data"  # already isolated by fixture
    kelly = data_dir / "kelly_stats.json"
    kelly.write_text(json.dumps({"systematic": {"trades": 50}}))

    _seed_phantom(wh, ts_entry=1000.0, ts_exit=1100.0, realized_pnl=-3.0,
                  symbol="A/USDT:USDT")

    n = rp.repair(warehouse=wh, commit=True, skip_learning_rerun=True)
    assert n == 1

    # kelly_stats.json should now be {} and a backup must exist
    assert json.loads(kelly.read_text()) == {}
    backups = list(data_dir.glob("kelly_stats.bak.*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == {"systematic": {"trades": 50}}


def test_repair_dry_run_does_not_touch_kelly(env):
    wh, rp, tmp_path = env
    data_dir = tmp_path / "data"  # already isolated by fixture
    kelly = data_dir / "kelly_stats.json"
    original = {"systematic": {"trades": 50}}
    kelly.write_text(json.dumps(original))

    _seed_phantom(wh, ts_entry=1000.0, ts_exit=1100.0, realized_pnl=-3.0,
                  symbol="A/USDT:USDT")

    rp.repair(warehouse=wh, commit=False, skip_learning_rerun=True)

    assert json.loads(kelly.read_text()) == original
    assert list(data_dir.glob("kelly_stats.bak.*.json")) == []
