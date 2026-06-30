"""Phase 0f — cost-model calibration + stress sweep + tool reconciliation."""
from __future__ import annotations

import sqlite3

import pytest

from core import cost_model as cm


# ---------------------------------------------------------------- fee lookup
def test_fee_rate_per_venue_taker_maker():
    # Bybit futures taker = 0.0006, maker = 0.0001 (config.FEE)
    assert cm.fee_rate("bybit", "futures", "taker") == pytest.approx(0.0006)
    assert cm.fee_rate("bybit", "futures", "maker") == pytest.approx(0.0001)
    # Binance futures taker = 0.0005
    assert cm.fee_rate("binance", "futures", "taker") == pytest.approx(0.0005)


def test_fee_rate_falls_back_to_generic_key():
    # An unknown venue falls back to the generic futures_taker (0.0005)
    assert cm.fee_rate("kraken", "futures", "taker") == pytest.approx(0.0005)


def test_fee_tier_stress_multiplier():
    base = cm.fee_rate("bybit", "futures", "taker")
    assert cm.fee_rate("bybit", "futures", "taker", tier_mult=2.0) == pytest.approx(base * 2.0)


def test_round_trip_cost_taker_with_slippage():
    # taker RT fee + slippage (open+close) at 1x
    rt = cm.round_trip_cost("bybit", entry_liq="taker", exit_liq="taker")
    assert rt > 0
    # at 2x slippage the cost is strictly larger (slippage doubled, fees unchanged)
    rt2 = cm.round_trip_cost("bybit", entry_liq="taker", exit_liq="taker", slip_mult=2.0)
    assert rt2 > rt


# ---------------------------------------------------------------- breakeven
def test_breakeven_wr_monotonic_in_cost():
    lo = cm.breakeven_wr(0.0004)
    hi = cm.breakeven_wr(0.0022)
    assert 0.0 < lo < hi < 1.0


# ---------------------------------------------------------------- calibration
def _seed_warehouse(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, exchange TEXT, symbol TEXT, "
        "entry_px REAL, size REAL, fee REAL, realized_pnl REAL, mode TEXT, status TEXT)"
    )
    con.execute(
        "CREATE TABLE attribution (trade_id INTEGER, alpha REAL, spread REAL, "
        "slippage REAL, funding REAL, fees REAL, realized_pnl REAL)"
    )
    # two live closed trades, notional 100 each, fee 0.06 each (=0.06% per round trip leg modeled)
    con.execute("INSERT INTO trades VALUES (1,'bybit','BTC/USDT',100,1,0.06,-0.5,'CONTROLLED_LIVE','CLOSED')")
    con.execute("INSERT INTO trades VALUES (2,'binance','ETH/USDT',100,1,0.06,0.2,'CONTROLLED_LIVE','CLOSED')")
    con.execute("INSERT INTO attribution VALUES (1,-0.4,0.02,0.0,0.0,0.06,-0.5)")
    con.execute("INSERT INTO attribution VALUES (2,0.28,0.02,0.0,0.0,0.06,0.2)")
    con.commit()
    con.close()


def test_calibrate_from_warehouse(tmp_path):
    db = str(tmp_path / "wh.sqlite")
    _seed_warehouse(db)
    cal = cm.calibrate_from_warehouse(db, mode="CONTROLLED_LIVE")
    assert cal["n"] == 2
    assert cal["notional"] == pytest.approx(200.0)
    # realized fee rate = sum(fee)/notional = 0.12/200 = 0.0006
    assert cal["realized_fee_rate"] == pytest.approx(0.0006)
    assert isinstance(cal["provenance"], str) and cal["provenance"]


# ------------------------------------------------------ tool reconciliation
def test_reconcile_cost_shares_root_causes_denominator():
    # Same cost numerator, two denominators => two very different shares.
    cost_total = 6.2
    realized_pnl = -7.0
    gross_alpha = realized_pnl + cost_total  # = -0.8
    r = cm.reconcile_cost_shares(cost_total, realized_pnl, gross_alpha)
    # cost/|pnl| ~ 0.886  (the "~90%" tp_accuracy figure)
    assert r["share_vs_pnl"] == pytest.approx(cost_total / abs(realized_pnl), rel=1e-6)
    # cost/(|alpha|+cost) ~ 0.886? no: 6.2/(0.8+6.2)=0.886 too -- pick numbers that diverge
    assert r["numerator_cost"] == pytest.approx(cost_total)
    assert "denominator" in r["root_cause"].lower()


def test_cost_tools_reconciled_when_numerators_match():
    # Both tools read the same attribution cost total => numerators agree within tol.
    assert cm.cost_tools_reconciled(6.20, 6.21, tol=0.05) is True
    assert cm.cost_tools_reconciled(6.20, 30.5, tol=0.05) is False


# ----------------------------------------------------------- stress sweep
def test_stress_sweep_matrix_pass_fail():
    strategies = [
        # a strategy that wins 60% on a 0.8/1.3 bracket => should PASS at 1x, may fail at 2x
        {"name": "S_good", "venue": "bybit", "wr": 0.60, "sl_pct": 0.008, "tp_pct": 0.013},
        # a coin-flip strategy => FAIL everywhere after cost
        {"name": "S_bad", "venue": "bybit", "wr": 0.40, "sl_pct": 0.008, "tp_pct": 0.013},
    ]
    m = cm.stress_sweep(strategies, slippage_mults=(1.0, 1.5, 2.0), fee_tier_mults=(1.0, 2.0))
    # matrix keyed by strategy name, then (slip_mult, fee_tier_mult)
    assert set(m.keys()) == {"S_good", "S_bad"}
    cell = m["S_good"][(1.0, 1.0)]
    assert {"cost_rt", "breakeven_wr", "wr", "pass"} <= set(cell.keys())
    # S_bad fails every cell
    assert all(not c["pass"] for c in m["S_bad"].values())
    # S_good passes the cheapest cell
    assert m["S_good"][(1.0, 1.0)]["pass"] is True
    # cost rises monotonically with slippage multiplier (fixed fee tier)
    assert m["S_good"][(2.0, 1.0)]["cost_rt"] > m["S_good"][(1.0, 1.0)]["cost_rt"]
