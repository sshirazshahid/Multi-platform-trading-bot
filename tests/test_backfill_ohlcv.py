# tests/test_backfill_ohlcv.py
from __future__ import annotations

from scripts.backfill_ohlcv_history import make_fetcher


class _FakeCcxt:
    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        # 3 hourly bars starting at `since`
        base = int(since)
        step = 3600_000
        return [[base + i * step, 1.0, 2.0, 0.5, 1.5, 100.0] for i in range(3)]


class _FakeClient:
    def __init__(self):
        self.exchange = _FakeCcxt()

    def _futures_params(self):
        return {}


def test_fetcher_passes_since_and_returns_rows():
    fetcher = make_fetcher(_FakeClient(), market_type="spot")
    rows = fetcher("BTC/USDT", "1h", 1_700_000_000_000, 1500)
    assert len(rows) == 3
    assert rows[0][0] == 1_700_000_000_000  # since echoed as first ts (ms)
