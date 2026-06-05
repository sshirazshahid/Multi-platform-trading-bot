"""Ground-truth real-fee aggregation tests (mock fetcher — no live account access)."""
from __future__ import annotations

import pytest

from core.cost_groundtruth import _fill_fee, aggregate_real_fees


def test_fill_fee_shapes():
    assert _fill_fee({"fee": {"cost": 0.30}}) == pytest.approx(0.30)
    assert _fill_fee({"fee": {"cost": -0.30}}) == pytest.approx(0.30)  # abs
    assert _fill_fee({"fees": [{"cost": 0.10}, {"cost": 0.05}]}) == pytest.approx(0.15)
    assert _fill_fee({}) == 0.0


def test_aggregate_real_fees_window_and_totals():
    trades = [
        {"exchange": "binance", "symbol": "BTC/USDT", "ts_entry": 1000.0, "ts_exit": 2000.0, "fee": 0.5},
        {"exchange": "binance", "symbol": "BTC/USDT", "ts_entry": 3000.0, "ts_exit": 4000.0, "fee": 0.5},
        {"exchange": "bybit", "symbol": "ETH/USDT", "ts_entry": 1500.0, "ts_exit": 2500.0, "fee": 0.2},
    ]
    fills_map = {
        ("binance", "BTC/USDT"): [
            {"timestamp": 1_000_000, "fee": {"cost": 0.30}},   # in window
            {"timestamp": 3_500_000, "fee": {"cost": 0.40}},   # in window
            {"timestamp": 99_999_000, "fee": {"cost": 5.0}},   # OUT of window -> excluded
        ],
        ("bybit", "ETH/USDT"): [
            {"timestamp": 1_600_000, "fees": [{"cost": 0.10}, {"cost": 0.05}]},  # in window
        ],
    }

    def fetch(ex, sym, since_ms):
        return fills_map.get((ex, sym), [])

    r = aggregate_real_fees(trades, fetch, pad_s=3600.0)
    assert r["modeled_fee_total"] == pytest.approx(1.2)        # 0.5 + 0.5 + 0.2
    assert r["real_fee_total"] == pytest.approx(0.30 + 0.40 + 0.15)
    assert r["n_fills"] == 3                                    # the 99999s fill excluded
    assert r["n_pairs"] == 2
    assert r["by_exchange"]["binance"] == pytest.approx(0.70)
    assert r["by_exchange"]["bybit"] == pytest.approx(0.15)


def test_aggregate_handles_empty_and_missing_fills():
    trades = [{"exchange": "bitget", "symbol": "SOL/USDT", "ts_entry": 10.0, "ts_exit": 20.0, "fee": 0.1}]
    r = aggregate_real_fees(trades, lambda *a: [], pad_s=60.0)
    assert r["real_fee_total"] == 0.0
    assert r["modeled_fee_total"] == pytest.approx(0.1)
    assert r["n_fills"] == 0
