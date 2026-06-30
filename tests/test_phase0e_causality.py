"""Phase 0e — verification & causality infra.

Covers the de-risking fixes that feed the 0b resolver:
  1. Wick SL/TP path provably uses the last CLOSED bar (forming-bar repaint
     immunity → PnL invariance when a forming bar is shifted).
  2. Funding accrual is settlement-aligned: a future settlement interval is
     never credited at time t (shifting settlement timestamps leaves PnL
     invariant to future intervals).
  3. Per-venue feed-freshness / fill-time staleness gate: a fill on a stale
     mark is rejected.
  4. Publish-lag causality for OI / taker-flow style feeds.
  5. compileall sweep script exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.feed_health import (
    FeedFreshness,
    is_source_available,
    stale_fill_reason,
)
from core.sim_execution import SimExecutionModel, last_closed_bar

ROOT = Path(__file__).resolve().parents[1]


class _FakeExchange:
    """Returns a fixed 1m candle list regardless of args."""

    def __init__(self, candles):
        self._candles = candles

    def fetch_ohlcv(self, symbol, timeframe, limit=2, market_type="futures"):
        return list(self._candles[-limit:])


# ── 1. closed-bar guard ────────────────────────────────────────────────

def test_last_closed_bar_drops_forming_bar():
    # bar A closes at 1_060_000ms, bar B (forming) closes at 1_120_000ms.
    candles = [
        [1_000_000, 10, 11, 9, 10, 100],   # closed
        [1_060_000, 10, 12, 8, 10, 100],   # forming at now=1080s
    ]
    c = last_closed_bar(candles, now=1080.0, timeframe_sec=60)
    assert c is not None
    assert c[0] == 1_000_000  # the closed bar, not the forming one


def test_wick_trigger_invariant_to_forming_bar_shift():
    sim = SimExecutionModel()
    # closed bar A wicks down to 8 (triggers SL at 8.5 for a long).
    closed = [1_000_000, 10, 11, 8, 10, 100]
    forming_a = [1_060_000, 10, 12, 9, 10, 100]
    forming_b = [1_060_000, 10, 99, -99, 10, 100]  # wildly different forming bar

    ex_a = _FakeExchange([closed, forming_a])
    ex_b = _FakeExchange([closed, forming_b])

    res_a = sim.check_wick_trigger(
        ex_a, "BTC/USDT", "futures", "buy", sl=8.5, tp=20.0, now=1080.0)
    res_b = sim.check_wick_trigger(
        ex_b, "BTC/USDT", "futures", "buy", sl=8.5, tp=20.0, now=1080.0)

    assert res_a == ("stop_loss", 8.5)
    assert res_a == res_b  # forming-bar shift does not change the trigger


# ── 2. funding settlement causality ────────────────────────────────────

class _Pos:
    market_type = "futures"
    side = "buy"
    size = 1.0


def test_funding_not_credited_before_settlement():
    sim = SimExecutionModel()
    pos = _Pos()
    # settlement in the future relative to now → no funding credited.
    future = sim.funding_payment_at(
        pos, mark_price=100.0, funding_rate=0.01,
        settlement_ts=2000.0, now=1000.0)
    assert future == 0.0


def test_funding_pnl_invariant_to_future_settlement_shift():
    sim = SimExecutionModel()
    pos = _Pos()
    base = sim.funding_payment_at(
        pos, 100.0, 0.01, settlement_ts=900.0, now=1000.0)
    # Pushing additional settlements further into the future must not change
    # the PnL realized at now=1000.
    for future_ts in (1001.0, 5000.0, 1e9):
        bumped = sim.funding_payment_at(
            pos, 100.0, 0.01, settlement_ts=future_ts, now=1000.0)
        assert bumped == 0.0
    assert base == pytest.approx(1.0)  # 1.0 * 100 * 0.01


# ── 3. stale-feed fill gate ────────────────────────────────────────────

def test_fill_rejected_on_stale_mark():
    snap = FeedFreshness(
        venue="binance", mark_ts=900.0, index_ts=999.0,
        funding_ts=999.0, book_ts=999.0)
    reason = stale_fill_reason(snap, now=1000.0, max_age_sec=10.0)
    assert reason is not None
    assert "mark" in reason


def test_fill_allowed_on_fresh_feeds():
    snap = FeedFreshness(
        venue="binance", mark_ts=999.0, index_ts=999.0,
        funding_ts=999.0, book_ts=999.0)
    assert stale_fill_reason(snap, now=1000.0, max_age_sec=10.0) is None


# ── 4. publish-lag causality ───────────────────────────────────────────

def test_publish_lag_blocks_future_observation():
    # An OI sample stamped at t=1000 with a 30s publish lag is NOT available
    # until t>=1030.
    assert not is_source_available(source_ts=1000.0, now=1020.0, publish_lag_sec=30.0)
    assert is_source_available(source_ts=1000.0, now=1030.0, publish_lag_sec=30.0)


# ── 5. compileall sweep ────────────────────────────────────────────────

def test_compile_sweep_script_exists():
    assert (ROOT / "scripts" / "compile_sweep.py").exists()
