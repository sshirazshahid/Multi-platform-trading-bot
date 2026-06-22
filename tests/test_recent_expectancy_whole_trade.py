"""Audit 2026-06-21 A2: recent_expectancy(whole_trade=...) — the entry gate can
read whole-trade PnL (runner realized_pnl + partial_realized_pnl) instead of the
runner leg alone. Default-OFF (byte-identical, runner-only). When ON it folds in
the partial leg, which makes expectancy LESS negative -> loosens the gate (owner-
gated: only sensible WITH a measured edge; harmful on a NO_EDGE bot).

Uses a throwaway tmp_path sqlite — no production data.
"""
from __future__ import annotations

import time

import core.warehouse as whmod


def _wh(tmp_path):
    return whmod.Warehouse(path=tmp_path / "t.sqlite")


def _closed(wh, symbol, realized, partial, i, side="buy"):
    ts = time.time() - 100 + i            # recent (well within the 30d window), unique
    tid = wh.record_trade_open(
        exchange="binance", symbol=symbol, side=side,
        ts_entry=ts, entry_px=100.0, size=1.0, mode="PAPER")
    wh.record_trade_close(
        trade_id=tid, ts_exit=ts + 60, exit_px=101.0,
        realized_pnl=realized, r_multiple=1.0, exit_reason="take_profit",
        partial_realized_pnl=partial)
    return tid


def test_off_is_runner_only(tmp_path):
    wh = _wh(tmp_path)
    for i in range(3):
        _closed(wh, "BTC/USDT", realized=1.0, partial=0.5, i=i)
    off = wh.recent_expectancy("BTC/USDT", side="buy", min_n=2, whole_trade=False)
    assert off is not None
    assert abs(off["mean"] - 1.0) < 1e-9       # runner leg only


def test_on_includes_partial(tmp_path):
    wh = _wh(tmp_path)
    for i in range(3):
        _closed(wh, "BTC/USDT", realized=1.0, partial=0.5, i=i)
    on = wh.recent_expectancy("BTC/USDT", side="buy", min_n=2, whole_trade=True)
    assert abs(on["mean"] - 1.5) < 1e-9        # runner + partial


def test_on_loosens_gate_sign_flip(tmp_path):
    # The owner-gated direction: runner-only is NEGATIVE (gate restrictive),
    # whole-trade is POSITIVE (gate permissive) — exactly why it's default-OFF.
    wh = _wh(tmp_path)
    for i in range(3):
        _closed(wh, "ETH/USDT", realized=-0.30, partial=0.50, i=i)
    off = wh.recent_expectancy("ETH/USDT", side="buy", min_n=2, whole_trade=False)
    on = wh.recent_expectancy("ETH/USDT", side="buy", min_n=2, whole_trade=True)
    assert off["mean"] < 0 < on["mean"]


def test_partial_null_coalesced_to_zero(tmp_path):
    wh = _wh(tmp_path)
    for i in range(3):
        _closed(wh, "SOL/USDT", realized=1.0, partial=None, i=i)
    on = wh.recent_expectancy("SOL/USDT", side="buy", min_n=2, whole_trade=True)
    assert abs(on["mean"] - 1.0) < 1e-9        # NULL partial -> COALESCE(...,0)
