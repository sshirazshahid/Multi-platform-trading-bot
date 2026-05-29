"""Task A.1 — multi-agent shadow build.

The existing `shadow_decisions` table (Phase 0) had columns sufficient for a
single-decision shadow predictor. The multi-agent build needs additional
columns so each agent's proposal is fully attributable and the dual-notional
projection (current sizing vs alternative $200-notional sizing) can be
recorded for side-by-side economic comparison.

This test pins the schema additions before any code in core/agents/* is
written. If schema regresses, agent-layer tests break loudly here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse_module():
    """Load core/warehouse.py directly to avoid pulling BotEngine."""
    spec = importlib.util.spec_from_file_location(
        "warehouse_shadow_schema_copy", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_shadow_schema_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    mod = _load_warehouse_module()
    return mod.Warehouse(path=tmp_path / "t.sqlite")


def _columns(wh, table: str) -> set[str]:
    rows = wh.query(f"PRAGMA table_info({table})")
    return {r["name"] for r in rows}


def test_shadow_decisions_has_phase_a_columns(wh):
    """The multi-agent build needs per-agent attribution + dual-notional."""
    cols = _columns(wh, "shadow_decisions")
    required = {
        # per-agent attribution
        "agent_id",
        "proposal_id",
        "proposed_at",
        # central veto path
        "vetoed_by",
        "veto_reason",
        # dual-notional economic projection
        "projected_notional_current",
        "projected_notional_alt",
        "projected_pnl",
        "projected_fee",
    }
    missing = required - cols
    assert not missing, f"shadow_decisions missing columns: {missing}"


def test_shadow_decisions_legacy_columns_preserved(wh):
    """Migration must not drop the Phase 0 columns."""
    cols = _columns(wh, "shadow_decisions")
    legacy = {"id", "ts", "model_version", "symbol", "side",
              "decision", "p_win", "sim_pnl", "sim_r_multiple"}
    missing = legacy - cols
    assert not missing, f"legacy columns lost in migration: {missing}"


def test_shadow_decisions_index_on_ts(wh):
    """Existing idx_shadow_ts index must remain — fast time-windowed reads."""
    rows = wh.query(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='shadow_decisions'"
    )
    names = {r["name"] for r in rows}
    assert "idx_shadow_ts" in names


def test_shadow_decisions_insert_with_new_columns(wh):
    """Smoke: insert a row using all new columns; round-trip via query."""
    wh._conn().execute(
        """INSERT INTO shadow_decisions
           (ts, model_version, symbol, side, decision, p_win, sim_pnl, sim_r_multiple,
            agent_id, proposal_id, proposed_at, vetoed_by, veto_reason,
            projected_notional_current, projected_notional_alt,
            projected_pnl, projected_fee)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (1777712000, "v1", "BTC/USDT", "buy", "ALLOW", 0.62, 0.0, 0.0,
         "ScalpAgent", "p-001", 1777712000, None, None,
         8.0, 200.0, 1.20, 0.12),
    )
    wh._conn().commit()
    rows = wh.query(
        "SELECT agent_id, projected_notional_alt, projected_pnl FROM shadow_decisions"
    )
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "ScalpAgent"
    assert rows[0]["projected_notional_alt"] == 200.0
    assert rows[0]["projected_pnl"] == 1.20
