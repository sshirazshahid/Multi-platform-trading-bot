"""Task B.2 — shadow dashboard panel."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_panel_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_panel_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _seed(wh):
    now = int(time.time())
    conn = wh._conn()
    for i in range(8):
        conn.execute(
            "INSERT INTO shadow_decisions (ts, model_version, symbol, side, decision, "
            "p_win, sim_pnl, projected_pnl, agent_id) "
            "VALUES (?, 'v1', 'BTC/USDT:USDT', 'buy', 'ALLOW', 0.6, ?, ?, ?)",
            (now - 60, 0.10 if i % 2 == 0 else -0.05,
             2.0 if i % 2 == 0 else -1.0,
             "ScalpAgent" if i < 4 else "TrendAgent"),
        )
    conn.execute(
        "INSERT INTO shadow_decisions (ts, model_version, symbol, side, decision, "
        "p_win, sim_pnl, agent_id, vetoed_by, veto_reason) "
        "VALUES (?, 'v1', 'BTC/USDT:USDT', 'buy', 'VETO', 0.4, 0, 'ScalpAgent', 'RiskAgent', 'halt_active')",
        (now - 30,),
    )
    for i in range(3):
        conn.execute(
            "INSERT INTO trades (candidate_id, ts_entry, ts_exit, exchange, symbol, "
            "side, entry_px, exit_px, size, leverage, fee, realized_pnl, status, exit_reason) "
            "VALUES (NULL, ?, ?, 'binance', ?, 'buy', 100.0, ?, 1.0, 2, 0.06, ?, 'CLOSED', 'tp')",
            (now - 7200 - i*60, now - 3600 - i*60, f"L{i}/USDT:USDT",
             100.30 if i % 2 == 0 else 99.5, 0.30 if i % 2 == 0 else -0.50),
        )
    conn.commit()


def test_shadow_panel_data_structure(wh):
    _seed(wh)
    from core.agents.shadow_panel import shadow_panel_data
    d = shadow_panel_data(wh.path)
    assert "shadow_current" in d
    assert "shadow_alt" in d
    assert "live" in d
    assert "per_agent" in d
    assert d["shadow_current"]["n"] == 8
    assert d["live"]["n"] == 3
    assert d["vetoes"] == 1


def test_shadow_panel_per_agent_breakdown(wh):
    _seed(wh)
    from core.agents.shadow_panel import shadow_panel_data
    d = shadow_panel_data(wh.path)
    assert "ScalpAgent" in d["per_agent"]
    assert "TrendAgent" in d["per_agent"]
    # Veto row also goes to ScalpAgent's bucket — count handles ALLOW only via outer query? No, we filtered to ALLOW
    assert d["per_agent"]["ScalpAgent"]["n"] == 4


def test_render_shadow_panel_text(wh):
    _seed(wh)
    from core.agents.shadow_panel import render_shadow_panel
    txt = render_shadow_panel(wh.path)
    assert "Shadow vs Live" in txt
    assert "LIVE:" in txt
    assert "ScalpAgent" in txt
    assert "TrendAgent" in txt


def test_render_panel_empty_window(wh):
    from core.agents.shadow_panel import render_shadow_panel
    txt = render_shadow_panel(wh.path)
    assert "n=   0" in txt
