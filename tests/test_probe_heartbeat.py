"""Heartbeat diagnosability for the log-only shadow probes (2026-07-20).

Forensic finding: BreakoutProbeAgent and UnlockShortProbeAgent had ZERO
shadow_decisions rows ever, and nothing in the logs distinguished "wiring
broken" from "triggers legitimately never fired" (it was the latter — verified
against live bybit data and the unlock calendar). These tests pin the fix:
every probe tick emits exactly ONE heartbeat log line carrying the evaluation
counters and the last evaluated bar ts, so probe silence is diagnosable from
the boot log alone.

Run: venv/Scripts/python.exe -m pytest tests/test_probe_heartbeat.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOUR = 3600
DAY = 24 * HOUR


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_probe_heartbeat_test",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_probe_heartbeat_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


@pytest.fixture
def log_capture():
    msgs: list = []
    sink_id = logger.add(lambda m: msgs.append(str(m)), level="DEBUG")
    yield msgs
    logger.remove(sink_id)


def _candles(start_ts_s: int, closes, bar_s: int):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        ts_ms = (start_ts_s + i * bar_s) * 1000
        out.append([ts_ms, prev, c + 1.0, c - 1.0, c, 1000.0])
        prev = c
    return out


class _Providers:
    """Stub read-only providers (no write endpoint = the LOG-ONLY guarantee)."""

    def __init__(self, *, ohlcv=None, now=0):
        self._ohlcv = dict(ohlcv or {})  # {(symbol, tf): [candles...]}
        self.now = now

    def ohlcv(self, venue, symbol, timeframe, since_ms):
        rows = self._ohlcv.get((symbol, timeframe), [])
        return [r for r in rows if r[0] >= int(since_ms)]

    def market_data(self, venue, symbol):
        return {}

    def balance(self):
        return 10_000.0

    def now_fn(self):
        return self.now


def _heartbeats(msgs, tag):
    return [m for m in msgs if f"[{tag}] heartbeat:" in m]


# ── probe_tick heartbeat (tsmom / breakout skeleton) ─────────────────────────
def test_tsmom_tick_emits_one_heartbeat_line_per_cycle(wh, log_capture):
    from core.agents.tsmom_probe_agent import TsmomProbeAgent

    prov = _Providers(now=2_000_000_000)
    agent = TsmomProbeAgent(
        warehouse=wh,
        ohlcv_provider=prov.ohlcv,
        market_data_provider=prov.market_data,
        account_balance_provider=prov.balance,
        now_fn=prov.now_fn,
        symbols=("BTC/USDT:USDT",),
    )
    agent.tick()
    assert len(_heartbeats(log_capture, "TsmomProbe")) == 1
    agent.tick()
    assert len(_heartbeats(log_capture, "TsmomProbe")) == 2


def test_breakout_heartbeat_reports_counts_and_last_bar_ts(wh, log_capture):
    from core.agents.breakout_probe_agent import (
        ATR_LEN,
        BAR_S,
        WINDOW_BARS,
        BreakoutProbeAgent,
    )

    n = WINDOW_BARS + ATR_LEN + 8
    now = 2_000_000_000 // BAR_S * BAR_S  # on the 4h grid
    start = now - n * BAR_S
    # Flat tape: enough history, never a channel break — the silent case.
    candles = _candles(start, [100.0] * n, BAR_S)
    prov = _Providers(ohlcv={("BTC/USDT:USDT", "4h"): candles}, now=now)
    agent = BreakoutProbeAgent(
        warehouse=wh,
        ohlcv_provider=prov.ohlcv,
        market_data_provider=prov.market_data,
        account_balance_provider=prov.balance,
        now_fn=prov.now_fn,
        symbols=("BTC/USDT:USDT",),
    )
    agent.tick()
    beats = _heartbeats(log_capture, "BreakoutProbe")
    assert len(beats) == 1
    line = beats[0]
    # evaluation counters + the last evaluated bar ts make silence diagnosable
    assert "evaluated=1" in line
    assert "entered=0" in line
    last_closed = ((now // BAR_S) - 1) * BAR_S
    from core.agents.probe_common import iso_utc

    assert f"last_bar_ts={iso_utc(last_closed)}" in line


def test_probe_tick_heartbeat_accumulates_totals(wh, log_capture):
    from core.agents.tsmom_probe_agent import TsmomProbeAgent

    prov = _Providers(now=2_000_000_000)
    agent = TsmomProbeAgent(
        warehouse=wh,
        ohlcv_provider=prov.ohlcv,
        market_data_provider=prov.market_data,
        account_balance_provider=prov.balance,
        now_fn=prov.now_fn,
        symbols=("BTC/USDT:USDT",),
    )
    agent.tick()
    agent.tick()
    agent.tick()
    beats = _heartbeats(log_capture, "TsmomProbe")
    assert "ticks=3" in beats[-1]


# ── unlock probe heartbeat ───────────────────────────────────────────────────
def _unlock_doc(ts, ratio):
    return {
        "symbol": "ARB",
        "events": [{"ts": ts, "tokens": 1_000_000.0, "ratio": ratio}],
        "source": "test-calendar",
    }


def test_unlock_heartbeat_reports_calendar_horizon(wh, log_capture, tmp_path):
    from core.agents.probe_common import iso_utc
    from core.agents.unlock_short_probe_agent import UnlockShortProbeAgent

    now = 2_000_000_000
    future_t = now + 90 * DAY  # qualifying cliff far beyond both arm windows
    agent = UnlockShortProbeAgent(
        warehouse=wh,
        calendar_provider=lambda: [
            _unlock_doc(future_t, 0.12),  # qualifying (ratio >= 0.10)
            _unlock_doc(now + 5 * DAY, 0.05),  # below the frozen 10% floor
        ],
        now_fn=lambda: now,
        state_path=str(tmp_path / "state.json"),
    )
    agent.tick()
    beats = _heartbeats(log_capture, "UnlockProbe")
    assert len(beats) == 1
    line = beats[0]
    assert "docs=2" in line
    assert "future_qualifying=1" in line
    assert f"max_unlock_ts={iso_utc(future_t)}" in line


def test_unlock_heartbeat_once_per_tick(wh, log_capture, tmp_path):
    from core.agents.unlock_short_probe_agent import UnlockShortProbeAgent

    agent = UnlockShortProbeAgent(
        warehouse=wh,
        calendar_provider=lambda: [],
        now_fn=lambda: 2_000_000_000,
        state_path=str(tmp_path / "state.json"),
    )
    agent.tick()
    agent.tick()
    assert len(_heartbeats(log_capture, "UnlockProbe")) == 2
