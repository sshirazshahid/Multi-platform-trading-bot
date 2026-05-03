"""Task A.13 — Daily shadow-vs-live compare report."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_compare_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_compare_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _seed(wh):
    """Insert shadow rows + live trade rows for testing."""
    now = int(time.time())
    conn = wh._conn()
    for i in range(5):
        conn.execute(
            "INSERT INTO shadow_decisions (ts, model_version, symbol, side, decision, "
            "p_win, sim_pnl, sim_r_multiple, agent_id, projected_pnl, projected_fee, "
            "projected_notional_current, projected_notional_alt) "
            "VALUES (?, 'v1', 'BTC/USDT:USDT', 'buy', 'ALLOW', 0.6, ?, 0.0, 'ScalpAgent', ?, 0.05, 8.0, 200.0)",
            (now - 3600, 0.05 if i % 2 == 0 else -0.04, 1.20 if i % 2 == 0 else -0.50),
        )
    for i in range(3):
        conn.execute(
            "INSERT INTO trades (candidate_id, ts_entry, ts_exit, exchange, symbol, "
            "side, entry_px, exit_px, size, leverage, fee, realized_pnl, status, exit_reason) "
            "VALUES (NULL, ?, ?, 'binance', ?, 'buy', 100.0, ?, 1.0, 2, 0.06, ?, 'CLOSED', 'tp')",
            (now - 7200 - i * 60, now - 3600 - i * 60,
             f"BTC{i}/USDT:USDT",
             101.0 if i % 2 == 0 else 99.5,
             1.0 if i % 2 == 0 else -0.5),
        )
    conn.commit()


def test_compare_report_with_data(wh, tmp_path):
    _seed(wh)
    from scripts.shadow_vs_live_report import build_report
    md = build_report(wh.path, window_hours=24)
    assert "# Shadow vs Live" in md
    assert "## Live" in md
    assert "## Shadow" in md
    assert "ScalpAgent" in md


def test_compare_report_empty_window(wh, tmp_path):
    """Empty windows shouldn't crash."""
    from scripts.shadow_vs_live_report import build_report
    md = build_report(wh.path, window_hours=24)
    assert "# Shadow vs Live" in md


def test_write_report_creates_file(wh, tmp_path):
    _seed(wh)
    from scripts.shadow_vs_live_report import write_report
    out = write_report(wh.path, tmp_path / "reports", window_hours=24)
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "Shadow vs Live" in txt
