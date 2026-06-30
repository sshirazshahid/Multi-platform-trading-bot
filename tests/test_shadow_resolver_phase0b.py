"""Phase 0b — forward-resolved, SL-first, after-cost shadow outcomes.

Pins the keystone that replaces best-case-TP projected shadow PnL with
forward-resolved net outcomes, and proves the promotion read-sites now read
RESOLVED net (not TP-projected) PnL.

- resolver reproduces a known SL-first outcome on fixture candles
- resolver reproduces TP and time-barrier outcomes
- a wide-TP no-edge agent that scores fake-positive on projected sim_pnl
  RESOLVES to <=0 net and is REJECTED by both gate readers
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_phase0b", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_phase0b"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _bar(ts_ms, o, h, low, c):
    return [ts_ms, o, h, low, c, 0.0]


def test_shadow_outcomes_table_exists(wh):
    cols = {r["name"] for r in wh.query("PRAGMA table_info(shadow_outcomes)")}
    required = {"proposal_id", "exit_px", "exit_reason", "gross_pnl", "net_pnl",
                "fees", "slippage", "funding", "mfe", "mae", "bars_held",
                "resolved_ts", "label_status"}
    assert required - cols == set(), f"missing: {required - cols}"


def test_resolver_reproduces_sl_first(wh):
    from core.shadow_resolver import resolve_one
    row = {
        "side": "buy", "entry_px": 100.0, "sl_px": 95.0, "tp_px": 110.0,
        "horizon_bars": 5, "projected_notional_current": 100.0, "timeframe": "1h",
    }
    # First bar wicks BOTH barriers; SL-first tie-break must pick stop_loss.
    candles = [_bar(0, 100, 111, 94, 96)]
    out = resolve_one(row, candles)
    assert out["exit_reason"] == "stop_loss"
    assert out["net_pnl"] < 0
    assert out["bars_held"] == 1
    assert out["r_multiple"] < 0


def test_resolver_take_profit(wh):
    from core.shadow_resolver import resolve_one
    row = {
        "side": "buy", "entry_px": 100.0, "sl_px": 95.0, "tp_px": 110.0,
        "horizon_bars": 5, "projected_notional_current": 100.0,
    }
    candles = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 112, 100, 111)]
    out = resolve_one(row, candles)
    assert out["exit_reason"] == "take_profit"
    assert out["net_pnl"] > 0
    assert out["bars_held"] == 2


def test_resolver_time_barrier(wh):
    from core.shadow_resolver import resolve_one
    row = {
        "side": "buy", "entry_px": 100.0, "sl_px": 95.0, "tp_px": 110.0,
        "horizon_bars": 2, "projected_notional_current": 100.0,
    }
    candles = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 102, 98, 99),
               _bar(2, 100, 109, 96, 108)]  # 3rd bar beyond horizon -> ignored
    out = resolve_one(row, candles)
    assert out["exit_reason"] == "time"
    assert out["bars_held"] == 2


def test_wide_tp_no_edge_resolves_negative_and_is_rejected(wh, tmp_path):
    """An agent with a far-away TP scores fake-positive on projected sim_pnl but
    must resolve to <=0 net and be rejected by BOTH gate readers."""
    from core.agents.execution_agent import ExecutionAgent
    from core.shadow_resolver import resolve_pending

    ea = ExecutionAgent(warehouse=wh)
    from core.agents.base_agent import Proposal
    prop = Proposal(
        agent_id="WideTPAgent", symbol="BTC/USDT:USDT", side="buy",
        entry=100.0, sl=95.0, tp=200.0,  # absurd wide TP -> huge projected sim_pnl
        confidence=0.9, reason="no-edge", venue="binance", timeframe="1h",
        horizon_bars=5,
    )
    written = ea.execute_shadow(
        prop, projected_notional_current=100.0, projected_notional_alt=200.0)
    assert written["sim_pnl"] > 0  # projected-at-TP looks great

    # Reality: price never reaches 200; it wicks the stop.
    candles = [_bar(0, 100, 102, 94, 96)]
    resolve_pending(wh, fetch_candles=lambda r: candles)

    # shadow_decisions row flipped to RESOLVED, outcome is negative net.
    drow = wh.query(
        "SELECT label_status FROM shadow_decisions WHERE proposal_id=?",
        (prop.proposal_id,))[0]
    assert drow["label_status"] == "RESOLVED"
    orow = wh.query(
        "SELECT net_pnl FROM shadow_outcomes WHERE proposal_id=?",
        (prop.proposal_id,))[0]
    assert orow["net_pnl"] <= 0

    # Gate reader #1: multi_agent_promotion_gate reads resolved net, NOT promote.
    from core.multi_agent_promotion_gate import (
        MACriteria,
        MAVerdict,
        evaluate_multi_agent_shadow,
    )
    ev = evaluate_multi_agent_shadow(
        wh.path, MACriteria(min_decisions=1), now_ts=prop.ts + 10)
    assert ev.verdict != MAVerdict.PROMOTE
    assert ev.shadow_sum_pnl <= 0  # resolved net, not the positive projected sim_pnl

    # Gate reader #2: promotion_loop.read_shadow_returns reads resolved net.
    from core.decision.promotion_loop import read_shadow_returns
    returns = read_shadow_returns(db_path=str(wh.path))
    assert returns, "expected at least one resolved return"
    assert all(x <= 0 for x in returns)
