"""Dashboard data-bridge: _get_btc_trend must persist data/btc_trend.json.

The dashboard reads data/btc_trend.json (dashboard.py:2024) for its BTC macro panel,
but nothing in the bot ever wrote that file -> the panel was permanently blank/default.
The bot computes the trend + EMA200 slope in _get_btc_trend but only cached it in memory.
Surfaced by the 2026-06-07 dashboard audit. Cosmetic panel only; no trade-logic impact.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.bot_engine import BotEngine


class _FakeEx:
    name = "binance"

    def fetch_ohlcv(self, symbol, tf, limit, market):
        # Flat series -> slope ~0 -> 'neutral'; deterministic, threshold-agnostic.
        return [[i, 100.0, 100.0, 100.0, 100.0, 1.0] for i in range(limit)]


def test_get_btc_trend_persists_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    eng = BotEngine.__new__(BotEngine)
    eng.active_exchanges = {"binance": _FakeEx()}
    eng._btc_trend_cached_at = 0  # bypass the 15-min cache

    trend = eng._get_btc_trend()

    p = Path("data/btc_trend.json")
    assert p.exists(), "data/btc_trend.json was not persisted"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert "ema200_slope" in d, "dashboard reads ema200_slope"
    assert "trend" in d and d["trend"] == trend, "persisted trend must match the returned trend"
    assert d["trend"] in ("bull", "bear", "neutral")
