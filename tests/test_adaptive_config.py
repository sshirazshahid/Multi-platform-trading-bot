from __future__ import annotations

import json
import time


def test_adaptive_machine_config_accepts_safe_paper_overrides():
    from core.adaptive_config import sanitize_adaptive_machine_payload

    result = sanitize_adaptive_machine_payload(
        {
            "enabled": True,
            "apply_scope": "paper",
            "expires_at": time.time() + 3600,
            "config": {
                "min_score": 0.4,
                "unconfirmed_min_score": 0.45,
                "rr": 2.2,
                "weights": {"ema_rsi": 0.32},
                "confirmation_detectors": ["ema_rsi"],
                "detector_params": {"ema_rsi": {"fast_ema": 9, "slow_ema": 21}},
            },
        }
    )

    assert result.applied is True
    assert result.overrides["weights"]["ema_rsi"] == 0.32
    assert result.overrides["confirmation_detectors"] == ("ema_rsi",)


def test_adaptive_machine_config_rejects_live_scope_and_unknown_keys():
    from core.adaptive_config import sanitize_adaptive_machine_payload

    live = sanitize_adaptive_machine_payload(
        {"apply_scope": "live", "config": {"min_score": 0.4}}
    )
    unknown = sanitize_adaptive_machine_payload(
        {"apply_scope": "paper", "config": {"default_leverage": 20}}
    )

    assert live.applied is False
    assert live.reason == "non_paper_scope_rejected"
    assert unknown.applied is False
    assert unknown.reason.startswith("invalid:")


def test_machine_signal_hot_reloads_adaptive_config(tmp_path, monkeypatch):
    import core.machine_signal as ms

    monkeypatch.setattr(ms, "MACHINE_DECISION_LOG", tmp_path / "decisions.jsonl")

    class FakeExchange:
        def __init__(self, rows):
            self.rows = rows

        def fetch_ohlcv(self, symbol, timeframe="15m", limit=100, market_type="futures"):
            return self.rows[-limit:]

        def fetch_ticker(self, symbol, market_type="futures"):
            return {"quoteVolume": 50_000_000.0, "bid": 100.99, "ask": 101.01}

    rows = []
    base_ts = 1_600_000_000_000
    for i in range(60):
        rows.append([base_ts + i * 60_000, 100.0, 100.5, 99.5, 100.0, 1000.0])
    rows[-1] = [rows[-1][0], 96.0, 102.0, 94.0, 101.0, 2000.0]

    config = {
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

    adaptive = tmp_path / "adaptive.json"
    adaptive.write_text(
        json.dumps(
            {
                "enabled": True,
                "apply_scope": "paper",
                "expires_at": time.time() + 3600,
                "config": {
                    "min_score": 0.25,
                    "unconfirmed_min_score": 0.40,
                    "confirmation_detectors": ["ema_rsi"],
                    "weights": {
                        "valuation": 0.0,
                        "harmonic": 0.0,
                        "stochastic": 0.0,
                        "ict": 0.28,
                        "asian_range": 0.0,
                        "dow_swing": 0.0,
                        "bb_squeeze": 0.0,
                        "ema_rsi": 0.16,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    sig = ms.MachineSignal(
        {"binance": FakeExchange(rows)},
        config={**config, "adaptive_config_path": str(adaptive)},
    )
    actions = sig.analyze_portfolio(["BTC"], [], {}, {}, [])

    assert actions == []
    assert sig.last_status[0]["score"] == 0.28


def test_machine_signal_reverts_expired_adaptive_config(tmp_path, monkeypatch):
    import core.machine_signal as ms

    monkeypatch.setattr(ms, "MACHINE_DECISION_LOG", tmp_path / "decisions.jsonl")

    class FakeExchange:
        def __init__(self, rows):
            self.rows = rows

        def fetch_ohlcv(self, symbol, timeframe="15m", limit=100, market_type="futures"):
            return self.rows[-limit:]

        def fetch_ticker(self, symbol, market_type="futures"):
            return {"quoteVolume": 50_000_000.0, "bid": 100.99, "ask": 101.01}

    rows = []
    base_ts = 1_600_000_000_000
    for i in range(60):
        rows.append([base_ts + i * 60_000, 100.0, 100.5, 99.5, 100.0, 1000.0])
    rows[-1] = [rows[-1][0], 96.0, 102.0, 94.0, 101.0, 2000.0]

    adaptive = tmp_path / "adaptive.json"
    adaptive.write_text(
        json.dumps(
            {
                "enabled": True,
                "apply_scope": "paper",
                "expires_at": time.time() - 1,
                "config": {"min_score": 0.99},
            }
        ),
        encoding="utf-8",
    )
    sig = ms.MachineSignal(
        {"binance": FakeExchange(rows)},
        config={
            "adaptive_config_path": str(adaptive),
            "universe": ["BTC"],
            "market_type": "futures",
            "timeframe": "15m",
            "lookback_candles": 80,
            "min_score": 0.10,
            "weights": {
                "valuation": 0.0,
                "harmonic": 0.0,
                "stochastic": 0.0,
                "ict": 1.0,
                "asian_range": 0.0,
                "dow_swing": 0.0,
                "bb_squeeze": 0.0,
            },
        },
    )
    actions = sig.analyze_portfolio(["BTC"], [], {}, {}, [])

    assert len(actions) == 1
    assert sig._adaptive_reason == "expired"
