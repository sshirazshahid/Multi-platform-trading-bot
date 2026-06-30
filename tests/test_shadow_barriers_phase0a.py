"""Phase 0a — persist raw shadow barriers so a shadow decision is forward-resolvable.

Pins the schema + write path before the 0b resolver depends on it:
- shadow_decisions carries entry/sl/tp/venue/timeframe/horizon_bars + label_status
- new ALLOW rows are PENDING with non-null barriers
- veto rows are UNRESOLVABLE (never executed -> nothing to resolve)
- legacy rows (written before barriers existed) are backfilled UNRESOLVABLE on migration
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_phase0a", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_phase0a"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _columns(wh, table):
    return {r["name"] for r in wh.query(f"PRAGMA table_info({table})")}


def _proposal(side="buy"):
    from core.agents.base_agent import Proposal
    return Proposal(
        agent_id="ScalpAgent", symbol="BTC/USDT:USDT", side=side,
        entry=70000.0,
        sl=69650.0 if side == "buy" else 70350.0,
        tp=70525.0 if side == "buy" else 69475.0,
        confidence=0.6, reason="test",
        venue="binance", timeframe="1h", horizon_bars=24,
    )


def test_shadow_decisions_has_barrier_columns(wh):
    cols = _columns(wh, "shadow_decisions")
    required = {"entry_px", "sl_px", "tp_px", "venue", "timeframe",
                "horizon_bars", "label_status"}
    missing = required - cols
    assert not missing, f"shadow_decisions missing barrier columns: {missing}"


def test_legacy_columns_preserved(wh):
    cols = _columns(wh, "shadow_decisions")
    legacy = {"id", "ts", "model_version", "symbol", "side", "decision",
              "p_win", "sim_pnl", "sim_r_multiple", "proposal_id"}
    missing = legacy - cols
    assert not missing, f"legacy columns lost: {missing}"


def test_execute_shadow_persists_barriers_pending(wh):
    from core.agents.execution_agent import ExecutionAgent
    ea = ExecutionAgent(warehouse=wh)
    ea.execute_shadow(_proposal("buy"),
                      projected_notional_current=8.0, projected_notional_alt=200.0)
    rows = wh.query("SELECT * FROM shadow_decisions WHERE decision='ALLOW'")
    assert len(rows) == 1
    r = rows[0]
    assert r["entry_px"] == 70000.0
    assert r["sl_px"] == 69650.0
    assert r["tp_px"] == 70525.0
    assert r["venue"] == "binance"
    assert r["timeframe"] == "1h"
    assert r["horizon_bars"] == 24
    assert r["label_status"] == "PENDING"


def test_veto_row_is_unresolvable(wh):
    from core.agents.execution_agent import ExecutionAgent
    ea = ExecutionAgent(warehouse=wh)
    ea.log_veto(_proposal("buy"), vetoed_by="RiskAgent", reason="halt")
    rows = wh.query(
        "SELECT label_status FROM shadow_decisions WHERE decision='VETO'")
    assert len(rows) == 1
    assert rows[0]["label_status"] == "UNRESOLVABLE"


def test_legacy_rows_backfilled_unresolvable_on_migration(wh, tmp_path):
    # Simulate a legacy row: no barriers, no label_status.
    wh._conn().execute(
        "INSERT INTO shadow_decisions (ts, symbol, side, decision, sim_pnl) "
        "VALUES (?, ?, ?, ?, ?)", (1777712000, "ETH/USDT", "buy", "ALLOW", 1.0))
    wh._conn().commit()
    # Re-open the same DB -> migration + backfill runs again (must be idempotent).
    wh2 = _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")
    rows = wh2.query(
        "SELECT label_status FROM shadow_decisions WHERE symbol='ETH/USDT'")
    assert rows[0]["label_status"] == "UNRESOLVABLE"
