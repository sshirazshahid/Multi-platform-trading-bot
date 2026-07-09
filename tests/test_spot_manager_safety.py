"""Regressions for two SpotPortfolioManager safety defects (audit 2026-07-07).

3a. Peak-drawdown reset was committed inside ``evaluate_holding`` — i.e. at the
    moment the SCALE_OUT was *recommended*, before the caller had actually sold.
    A skipped/failed/recommendation-only SCALE_OUT then silently ate the
    drawdown trigger (the peak snapped to price, so the next rule never fired).
    The peak must only reset AFTER a confirmed execution.

3b. ``scan_holdings`` overwrote ``self._holdings`` wholesale, so a transient
    fetch error on one venue wiped that venue's known holdings. It must
    fail-hold: keep prior holdings on error, rebuild only on a successful fetch.
"""
from __future__ import annotations

import config
from core.spot_manager import HoldingInfo, SpotPortfolioManager


def _mgr(tmp_path):
    mgr = SpotPortfolioManager(exchanges={}, risk_manager=None, mcp_brain=None)
    mgr._state_file = tmp_path / "spot_portfolio.json"
    return mgr


def test_scale_out_does_not_reset_peak_at_evaluation_time(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SPOT_STRATEGY", {
        "enabled": True, "min_position_usd": 50.0,
        "drawdown_half_pct": 0.25, "drawdown_full_pct": 0.40,
    })
    mgr = _mgr(tmp_path)
    # peak 100, price 70 -> 30% drawdown: SCALE_OUT (>=25%, <40%).
    mgr._holdings = {"binance": {"SOL": HoldingInfo(
        coin="SOL", exchange="binance", balance=1.0, avg_cost=100.0,
        current_price=70.0, unrealized_pnl=-30.0, unrealized_pnl_pct=-0.30,
        peak_price=100.0, last_updated=0.0,
    )}}

    result = mgr.evaluate_holding("SOL", "binance")
    assert result["action"] == "SCALE_OUT"
    # BUG WAS HERE: peak must NOT be reset just because we recommended a trim.
    assert mgr._holdings["binance"]["SOL"].peak_price == 100.0

    # Only after a confirmed execution does the peak reset.
    assert mgr.reset_peak("SOL", "binance") is True
    assert mgr._holdings["binance"]["SOL"].peak_price == 70.0


def test_scan_holdings_fail_hold_on_transient_error(tmp_path):
    class _BadExchange:
        _connected = True

        def fetch_balance(self, market_type):
            raise RuntimeError("transient venue error")

    mgr = _mgr(tmp_path)
    mgr._exchanges = {"binance": _BadExchange()}
    prior = HoldingInfo(
        coin="BTC", exchange="binance", balance=0.1, avg_cost=50000.0,
        current_price=48000.0, unrealized_pnl=-200.0, unrealized_pnl_pct=-0.04,
        peak_price=52000.0, last_updated=0.0,
    )
    mgr._holdings = {"binance": {"BTC": prior}}

    results = mgr.scan_holdings()
    # Fail-hold: the venue's prior holdings survive a transient fetch error.
    assert "binance" in results
    assert results["binance"]["BTC"].peak_price == 52000.0
    assert mgr._holdings["binance"]["BTC"].peak_price == 52000.0
