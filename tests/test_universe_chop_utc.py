"""The chop gate (Kaufman ER) must measure UTC days on EVERY venue.

2026-08-19 incident: 367 chop blocks in 6h across 3 symbols idled the bot
through a real +6% BTC breakout. Root cause: `_check_range_stability` fetched
venue "1d" candles, and bitget buckets its daily candles on UTC+8 boundaries
(last bar opens 16:00Z). Same asset, same instant, the ER read 0.079 on bitget
vs 0.167 (binance) / 0.167 (bybit) for ATOM — the bitget lane was measuring a
DIFFERENT quantity than the spec, lagging intraday moves by up to 8 hours. The
bot logged `chop:ER=0.09` while the true UTC-day ER was 0.17-0.45 (PASS).

Fix under test: the range-stability metrics are built from 1h candles —
absolute timestamps, identical on every venue — grouped into UTC days. The
formula, lookback (10d), and threshold (0.12 loosened / 0.20 default) are
UNCHANGED; only the day-slicing is made venue-independent. This is a
correctness fix, not a loosening: shifted-boundary ER mis-reads in BOTH
directions, so the fix also blocks things bitget's calendar wrongly passed.

Run: venv/Scripts/python.exe -m pytest tests/test_universe_chop_utc.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.pair_discovery import UniverseFilter  # noqa: E402

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
# Anchor on an exact UTC midnight.
T0 = 1_784_246_400_000 - (1_784_246_400_000 % DAY_MS)

# Daily closes for the 10 fully closed days D1..D10 (alternating wiggle), then
# a forming day whose latest hourly close is 106 (the "breakout").
CLOSED_DAILY = [100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0]
FORMING_CLOSE = 106.0
# Hand-computed spec ER over the 11 UTC-day closes [D1..D10, forming]:
#   net  = |106 - 100| = 6
#   path = 9 one-point wiggles + |106 - 101| = 14
EXPECTED_ER = 6.0 / 14.0


class FakeHourlyExchange:
    """Serves synthetic 1h candles; records every requested timeframe.

    Requests for venue-bucketed '1d' candles raise: after the fix no code
    path may consume venue daily buckets (that IS the bug)."""

    name = "Fakeget"

    def __init__(self, days_of_bars=None, forming_hours=18):
        closes = days_of_bars if days_of_bars is not None else CLOSED_DAILY
        self.requested: list = []
        self.bars: list = []
        t = T0
        for day_close in closes:               # complete days: 24 bars each
            for h in range(24):
                c = day_close if h == 23 else day_close - 0.25
                self.bars.append([t, c, c + 0.5, c - 0.5, c, 1000.0])
                t += HOUR_MS
        for h in range(forming_hours):         # the forming UTC day
            c = FORMING_CLOSE if h >= 14 else 100.5   # pump lands 14:00Z
            self.bars.append([t, c, c + 0.5, c - 0.5, c, 1000.0])
            t += HOUR_MS

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        self.requested.append(timeframe)
        assert timeframe != "1d", (
            "range stability must NOT consume venue-bucketed daily candles — "
            "bitget buckets them on UTC+8 and the ER mis-reads by hours"
        )
        return self.bars[-limit:]


def _uf(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE_FLOW_LOOSEN", {"enabled": False})
    return UniverseFilter()


def test_chop_er_is_built_from_utc_days_not_venue_days(monkeypatch):
    """THE regression: ER equals the hand-computed UTC-day value."""
    uf = _uf(monkeypatch)
    ex = FakeHourlyExchange()
    roc, er = uf._check_range_stability(ex, "ATOM/USDT", "futures")
    assert er == pytest.approx(EXPECTED_ER, abs=1e-9), (
        f"ER must be the UTC-day spec quantity {EXPECTED_ER:.4f}, got {er}"
    )
    assert "1h" in ex.requested


def test_forming_utc_day_is_included(monkeypatch):
    """The current partial UTC day contributes its LATEST hourly close —
    that responsiveness is exactly what the bitget bucketing destroyed."""
    uf = _uf(monkeypatch)
    ex = FakeHourlyExchange(forming_hours=15)   # breakout bar just landed
    _roc, er = uf._check_range_stability(ex, "ATOM/USDT", "futures")
    assert er == pytest.approx(EXPECTED_ER, abs=1e-9)


def test_range_of_change_uses_daily_highs_lows(monkeypatch):
    uf = _uf(monkeypatch)
    ex = FakeHourlyExchange()
    roc, _er = uf._check_range_stability(ex, "ATOM/USDT", "futures")
    # window high = 106.5 (pump bar high); window low = 99.25 (99.5 wiggle - 0.25)
    expected = (106.5 - 99.25) / 99.25
    assert roc == pytest.approx(expected, rel=1e-6)


def test_too_few_utc_days_fails_open(monkeypatch):
    """< lookback+1 UTC day groups -> (None, None) -> missing_range_data."""
    uf = _uf(monkeypatch)
    ex = FakeHourlyExchange(days_of_bars=CLOSED_DAILY[:5])
    assert uf._check_range_stability(ex, "ATOM/USDT", "futures") == (None, None)


def test_empty_fetch_fails_open(monkeypatch):
    uf = _uf(monkeypatch)

    class Empty:
        name = "Fakeget"

        def fetch_ohlcv(self, *a, **k):
            return []

    assert uf._check_range_stability(Empty(), "X/USDT", "futures") == (None, None)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
