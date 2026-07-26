from __future__ import annotations

from core.mcp_brain import MCPBrain


NOW = 1_800_000_000.0
TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14_400}


def _rows(timeframe: str, *, forming_close: float = 10_000.0) -> list:
    tf_s = TF_SECONDS[timeframe]
    current_open = int(NOW // tf_s) * tf_s
    rows = []
    for i in range(250):
        ts_s = current_open - (249 - i) * tf_s
        close = 100.0 + i * 0.02
        if i == 249:
            close = forming_close
        open_px = 100.0 + i * 0.02
        rows.append(
            [ts_s * 1000, open_px, max(open_px, close) + 0.1,
             min(open_px, close) - 0.1, close, 1000.0 + i]
        )
    return rows


class _PartialVenue:
    name = "first"

    def fetch_ohlcv(self, symbol, timeframe, *, limit, market_type):
        if timeframe == "4h":
            return _rows(timeframe)
        return []


class _CompleteVenue:
    name = "second"

    def fetch_ohlcv(self, symbol, timeframe, *, limit, market_type):
        return _rows(timeframe)


def _brain() -> MCPBrain:
    brain = object.__new__(MCPBrain)
    brain._indicator_cache_time = 0.0
    brain._indicator_cache = {}
    brain._exchanges = {"first": _PartialVenue(), "second": _CompleteVenue()}
    return brain


def test_indicator_snapshot_uses_closed_bars_and_one_coherent_source(monkeypatch):
    monkeypatch.setattr("core.mcp_brain.time.time", lambda: NOW)

    result = _brain()._fetch_exchange_indicators(["BTC"])["BTC"]

    assert set(result) == {"15m", "1h", "4h"}
    assert {row["source_venue"] for row in result.values()} == {"second"}
    assert {row["source_market_type"] for row in result.values()} == {"futures"}
    assert {row["source_symbol"] for row in result.values()} == {"BTC/USDT:USDT"}
    for timeframe, row in result.items():
        assert row["candle_ts"] + TF_SECONDS[timeframe] * 1000 <= NOW * 1000
        assert row["price"] < 200.0, "forming-candle spike must not enter indicators"

