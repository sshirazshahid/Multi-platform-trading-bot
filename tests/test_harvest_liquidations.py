"""Unit tests for liquidation harvester helpers (no live WS)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "harvest_liquidations.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("harvest_liquidations", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def liq():
    return _load_mod()


def test_parse_force_order_sell_long_liq(liq):
    msg = {
        "e": "forceOrder",
        "E": 1568014460893,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "q": "0.014",
            "p": "9910",
            "ap": "9910",
            "T": 1568014460893,
        },
    }
    parsed = liq._parse_force_order(msg)
    assert parsed == ("BTCUSDT", "SELL", 0.014, 9910.0, 1568014460893.0)


def test_parse_force_order_rejects_incomplete(liq):
    assert liq._parse_force_order({"o": {"s": "ETHUSDT", "S": "BUY"}}) is None


def test_buckets_flush_completed_writes_history(liq, tmp_path, monkeypatch):
    hist = tmp_path / "liquidations_history.jsonl"
    monkeypatch.setattr(liq, "HIST", hist)
    b = liq._Buckets()
    b.add(1_000_000, "BTCUSDT", "SELL", 100.0)
    b.add(1_000_000, "ETHUSDT", "BUY", 50.0)
    # current hour = later → flush the earlier bucket
    n = b.flush_completed(1_003_600)
    assert n == 3  # BTCUSDT + ETHUSDT + ALL
    lines = hist.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert b.data == {}


def test_ws_url_uses_market_path(liq):
    """Regression: legacy /ws/ retires silently (0 events); forceOrder is /market/ws/."""
    assert "/market/ws/" in liq.WS_URL
    assert "!forceOrder@arr" in liq.WS_URL
    assert liq.WS_PING_INTERVAL is None
    assert liq.WS_PING_TIMEOUT is None
    assert liq.STALE_CONNECT_SEC >= 60.0


def test_ws_client_pings_disabled(liq):
    """Regression: client keepalive ping_interval must stay None (Binance host)."""
    assert liq.WS_PING_INTERVAL is None
    assert liq.WS_PING_TIMEOUT is None
    assert liq.STALE_CONNECT_SEC >= 60.0
