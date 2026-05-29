"""Task B.1 — multi-agent promotion gate."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_pg_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_pg_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _seed_shadow(wh, n_shadow, win_pct, sym_offset=0):
    now = int(time.time())
    conn = wh._conn()
    for i in range(n_shadow):
        pnl = 0.20 if i < int(n_shadow * win_pct) else -0.15
        conn.execute(
            "INSERT INTO shadow_decisions (ts, model_version, symbol, side, decision, "
            "p_win, sim_pnl, sim_r_multiple, agent_id, projected_fee, "
            "projected_notional_current, projected_notional_alt, projected_pnl) "
            "VALUES (?, 'v1', ?, 'buy', 'ALLOW', 0.6, ?, 0.0, 'ScalpAgent', 0.05, 8.0, 200.0, ?)",
            (now - 3600 - i, f"S{sym_offset+i}/USDT:USDT", pnl, pnl * 25),
        )
    conn.commit()


def _seed_live(wh, n_live, win_pct, sym_offset=0):
    now = int(time.time())
    conn = wh._conn()
    for i in range(n_live):
        pnl = 0.30 if i < int(n_live * win_pct) else -0.40
        conn.execute(
            "INSERT INTO trades (candidate_id, ts_entry, ts_exit, exchange, symbol, "
            "side, entry_px, exit_px, size, leverage, fee, realized_pnl, status, exit_reason) "
            "VALUES (NULL, ?, ?, 'binance', ?, 'buy', 100.0, ?, 1.0, 2, 0.06, ?, 'CLOSED', 'tp')",
            (now - 7200 - i*120, now - 3600 - i*120,
             f"L{sym_offset+i}/USDT:USDT",
             100.30 if pnl > 0 else 99.60, pnl),
        )
    conn.commit()


def test_promotion_gate_insufficient_decisions_holds(wh):
    from core.multi_agent_promotion_gate import (
        MAVerdict,
        evaluate_multi_agent_shadow,
    )
    _seed_shadow(wh, 50, 0.60)
    _seed_live(wh, 30, 0.40)
    ev = evaluate_multi_agent_shadow(wh.path)
    assert ev.verdict == MAVerdict.HOLD
    assert "insufficient_shadow_decisions" in ev.reason


def test_promotion_gate_low_wr_holds(wh):
    from core.multi_agent_promotion_gate import (
        MAVerdict,
        evaluate_multi_agent_shadow,
    )
    _seed_shadow(wh, 120, 0.30)  # WR 30% < 40% floor
    _seed_live(wh, 50, 0.40)
    ev = evaluate_multi_agent_shadow(wh.path)
    assert ev.verdict == MAVerdict.HOLD
    assert "shadow_wr_low" in ev.reason


def test_promotion_gate_promotes_when_all_pass(wh):
    from core.multi_agent_promotion_gate import (
        MACriteria,
        MAVerdict,
        evaluate_multi_agent_shadow,
    )
    # Shadow strongly outperforms live
    _seed_shadow(wh, 150, 0.70)
    _seed_live(wh, 50, 0.30)
    ev = evaluate_multi_agent_shadow(
        wh.path, MACriteria(fee_burn_max_x=10.0),  # don't fail on fees here
    )
    assert ev.verdict == MAVerdict.PROMOTE
    assert ev.reason == "all_criteria_passed"


def test_promotion_gate_rejects_catastrophic(wh):
    from core.multi_agent_promotion_gate import (
        MAVerdict,
        evaluate_multi_agent_shadow,
    )
    _seed_shadow(wh, 120, 0.10)  # 10% WR — catastrophic
    _seed_live(wh, 50, 0.40)
    ev = evaluate_multi_agent_shadow(wh.path)
    assert ev.verdict == MAVerdict.REJECT
    assert "catastrophic" in ev.reason


def test_promotion_gate_writes_log(wh, tmp_path):
    from core.multi_agent_promotion_gate import evaluate_multi_agent_shadow
    _seed_shadow(wh, 50, 0.60)
    _seed_live(wh, 30, 0.40)
    log = tmp_path / "promotion_log.jsonl"
    evaluate_multi_agent_shadow(wh.path, log_path=log)
    assert log.exists()
    assert "verdict" in log.read_text()


def test_write_split_flag(tmp_path):
    from core.multi_agent_promotion_gate import write_split_flag
    p = tmp_path / "ab.json"
    write_split_flag(0.10, path=p)
    import json
    j = json.loads(p.read_text())
    assert j["pct_to_shadow"] == 0.10
    write_split_flag(2.0, path=p)
    assert json.loads(p.read_text())["pct_to_shadow"] == 1.0  # clamped
