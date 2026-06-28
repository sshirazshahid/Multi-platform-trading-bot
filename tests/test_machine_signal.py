from __future__ import annotations

import numpy as np
import pytest

from core.machine_signal import MachineSignal, is_machine_action, is_machine_position


@pytest.fixture(autouse=True)
def _isolate_machine_decision_log(tmp_path, monkeypatch):
    import core.machine_signal as ms

    monkeypatch.setattr(ms, "MACHINE_DECISION_LOG", tmp_path / "machine_decisions.jsonl")


class FakeExchange:
    def __init__(self, rows, ticker=None):
        self.rows = rows
        self.ticker = ticker or {
            "last": 101.0,
            "quoteVolume": 50_000_000.0,
            "bid": 100.99,
            "ask": 101.01,
        }

    def fetch_ohlcv(self, symbol, timeframe="15m", limit=100, market_type="futures"):
        return self.rows[-limit:]

    def fetch_ticker(self, symbol, market_type="futures"):
        return dict(self.ticker)


def _rows_from_close(closes):
    out = []
    base_ts = 1_600_000_000_000
    for i, c in enumerate(closes):
        out.append([base_ts + i * 60_000, c, c + 0.5, c - 0.5, c, 1000.0])
    return out


def _ict_sweep_rows():
    closes = [100.0] * 60
    rows = _rows_from_close(closes)
    # Liquidity sweep + bullish displacement on the final closed bar.
    rows[-1] = [rows[-1][0], 96.0, 102.0, 94.0, 101.0, 2000.0]
    return rows


def _config(**overrides):
    cfg = {
        "universe": ["BTC"],
        "market_type": "futures",
        "timeframe": "15m",
        "lookback_candles": 80,
        "min_score": 0.10,
        "rr": 1.8,
        "scalp_time_stop_bars": 4,
        "min_quote_volume": 1_000_000.0,
        "max_spread_bps": 20.0,
        "weights": {
            "valuation": 0.0,
            "harmonic": 0.0,
            "stochastic": 0.0,
            "ict": 1.0,
            "asian_range": 0.0,
            "dow_swing": 0.0,
            "bb_squeeze": 0.0,
        },
    }
    cfg.update(overrides)
    return cfg


def test_machine_signal_emits_open_action_contract():
    sig = MachineSignal({"binance": FakeExchange(_ict_sweep_rows())}, config=_config())
    actions = sig.analyze_portfolio(
        coins=["BTC"],
        open_positions=[],
        exchange_balances={},
        risk_envelope={},
        recent_trades=[],
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["type"] == "OPEN"
    assert action["source"] == "machine"
    assert action["strategy"] == "machine_strategy_engine"
    assert action["side"] == "buy"
    assert action["decision_id"]
    assert action["sl_pct"] > 0
    assert action["tp_pct"] > action["sl_pct"]
    assert action["mcp_score"] > 0
    assert is_machine_action(action)


def test_machine_signal_execution_confidence_floor_keeps_low_score_trade_executable():
    weights = {
        "valuation": 0.0,
        "harmonic": 0.0,
        "stochastic": 0.0,
        "ict": 0.28,
        "asian_range": 0.0,
        "dow_swing": 0.0,
        "bb_squeeze": 0.0,
    }
    sig = MachineSignal(
        {"binance": FakeExchange(_ict_sweep_rows())},
        config=_config(
            min_score=0.25,
            execution_confidence_floor=0.50,
            weights=weights,
        ),
    )
    actions = sig.analyze_portfolio(
        coins=["BTC"],
        open_positions=[],
        exchange_balances={},
        risk_envelope={},
        recent_trades=[],
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["machine_score"] == 28.0
    assert action["machine_score_raw"] == 0.28
    assert action["machine_confidence_raw"] == 0.28
    assert action["confidence"] == 0.50
    assert action["execution_confidence_floor"] == 0.50
    assert action["machine_diagnostics"]["execution_confidence"] == 0.50
    assert "exec_conf_floor=0.50" in action["reason"]


def test_machine_signal_liquidity_filter_excludes_low_volume():
    low_volume = {"last": 101.0, "quoteVolume": 10_000.0, "bid": 100.99, "ask": 101.01}
    sig = MachineSignal(
        {"binance": FakeExchange(_ict_sweep_rows(), ticker=low_volume)},
        config=_config(min_quote_volume=1_000_000.0),
    )
    actions = sig.analyze_portfolio(["BTC"], [], {}, {}, [])
    assert actions == []
    assert sig.last_status[0]["state"] == "no_data"


def test_machine_signal_does_not_open_when_symbol_already_occupied():
    sig = MachineSignal({"binance": FakeExchange(_ict_sweep_rows())}, config=_config())
    actions = sig.analyze_portfolio(
        ["BTC"],
        [{"symbol": "BTC/USDT:USDT", "side": "buy", "strategy": "other"}],
        {},
        {},
        [],
    )
    assert actions == []
    assert sig.last_status[0]["state"] == "occupied"


def test_machine_signal_time_stop_closes_own_position():
    sig = MachineSignal({"binance": FakeExchange(_ict_sweep_rows())}, config=_config())
    pos = {
        "id": "p1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "exchange": "binance",
        "market_type": "futures",
        "strategy": "machine",
        "age_min": 75,
    }
    actions = sig.analyze_portfolio(["BTC"], [pos], {}, {}, [])
    assert actions and actions[0]["type"] == "CLOSE"
    assert actions[0]["position_id"] == "p1"
    assert actions[0]["source"] == "machine"
    assert actions[0]["reason"] == "machine_time_stop"
    assert "age" in actions[0]["rationale"]
    assert is_machine_position(pos)


def test_machine_signal_no_false_open_on_flat_data():
    rows = _rows_from_close(np.full(80, 100.0))
    sig = MachineSignal({"binance": FakeExchange(rows)}, config=_config())
    actions = sig.analyze_portfolio(["BTC"], [], {}, {}, [])
    assert actions == []
