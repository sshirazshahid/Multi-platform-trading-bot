"""Risk-layer tests: CLAUDE.md section 2 guard + mandatory disaster stop (MUST-FIX #1)."""
from __future__ import annotations

from ntrader.backtests._run import run_strategy_a
from ntrader.strategies.pretrade_guard import check_order

EQ = 1000.0


def test_guard_allows_normal_small_order():
    r = check_order(equity=EQ, order_notional=50.0, existing_gross_notional=0.0, sl_pct=8.0, leverage=1)
    assert r.allowed, r.reason


def test_guard_rejects_risk_over_3pct():
    # 8% stop on $500 notional = $40 risk > 3% of $1000 ($30)
    r = check_order(equity=EQ, order_notional=500.0, existing_gross_notional=0.0, sl_pct=8.0, leverage=1)
    assert not r.allowed and "risk" in r.reason


def test_guard_rejects_gross_over_12pct_portfolio_wide():
    # existing 100 + new 50 = 150 > 12% of 1000 (120); risk ok so the gross rule must fire
    r = check_order(equity=EQ, order_notional=50.0, existing_gross_notional=100.0, sl_pct=8.0, leverage=1)
    assert not r.allowed and "gross exposure" in r.reason


def test_guard_rejects_missing_stop():
    r = check_order(equity=EQ, order_notional=50.0, existing_gross_notional=0.0, sl_pct=0.0, leverage=1)
    assert not r.allowed and "stop" in r.reason


def test_guard_rejects_stop_wider_than_8pct():
    r = check_order(equity=EQ, order_notional=50.0, existing_gross_notional=0.0, sl_pct=12.0, leverage=1)
    assert not r.allowed and "stop" in r.reason


def test_guard_rejects_leverage_over_cap():
    r = check_order(equity=EQ, order_notional=50.0, existing_gross_notional=0.0, sl_pct=8.0, leverage=3.0)
    assert not r.allowed and "leverage" in r.reason


def test_disaster_stop_placed_without_error():
    # MUST-FIX #1: enable_stop must place a stop_market without the prior Quantity TypeError.
    res = run_strategy_a(base="BTC", source="synth", n_bars=200, enable_stop=True)
    opens = [d for d in res["decision_bars"] if d[1] == "OPEN"]
    assert opens, "fixture produced no OPEN; cannot exercise stop placement"
    assert res["stop_orders"] >= 1, f"no stop order placed (stop_orders={res['stop_orders']})"
    assert isinstance(res["open_at_end"], int)
