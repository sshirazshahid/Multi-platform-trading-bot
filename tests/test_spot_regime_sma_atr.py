"""Rev5.2 S1 regime: SMA200 +/- ATR14 band with 3-close confirmation."""

from __future__ import annotations

import pytest

from core.spot_regime import (
    REGIME_DEFENSIVE,
    REGIME_NORMAL,
    S1_ATR_PERIOD,
    S1_BAND_ATR_MULT,
    S1_CONFIRM_CLOSES,
    S1_SMA_PERIOD,
    closed_daily_ohlc,
    daily_sma200_atr_regime,
)

DAY = 86400


def _ohlc(closes: list[float], *, start_ts: int = 1_700_000_000, band: float = 0.1):
    return [
        {"ts": start_ts + i * DAY, "high": c + band, "low": c - band, "close": c}
        for i, c in enumerate(closes)
    ]


def test_sma_atr_constants():
    assert S1_SMA_PERIOD == 200
    assert S1_ATR_PERIOD == 14
    assert S1_BAND_ATR_MULT == 0.5
    assert S1_CONFIRM_CLOSES == 3


def test_three_consecutive_closes_required_to_flip():
    base = [100.0] * 205
    prev = {
        "BTC": {"state": "above", "streak": 0, "streak_dir": None, "last_bar_ts": 204},
        "ETH": {"state": "above", "streak": 0, "streak_dir": None, "last_bar_ts": 204},
    }

    regime, states = daily_sma200_atr_regime(_ohlc(base + [80.0]), _ohlc(base + [80.0]),
                                             prev_states=prev)
    assert regime == REGIME_NORMAL
    assert states["BTC"]["state"] == "above"
    assert states["BTC"]["streak"] == 1
    assert states["BTC"]["streak_dir"] == "below"

    regime, states = daily_sma200_atr_regime(_ohlc(base + [80.0, 80.0]),
                                             _ohlc(base + [80.0, 80.0]),
                                             prev_states=states)
    assert regime == REGIME_NORMAL
    assert states["BTC"]["streak"] == 2

    regime, states = daily_sma200_atr_regime(_ohlc(base + [80.0, 80.0, 80.0]),
                                             _ohlc(base + [80.0, 80.0, 80.0]),
                                             prev_states=states)
    assert regime == REGIME_DEFENSIVE
    assert states["BTC"]["state"] == "below"
    assert states["BTC"]["streak"] == 0


def test_inside_band_resets_pending_streak_and_retains_state():
    base = [100.0] * 205
    prev = {
        "BTC": {"state": "above", "streak": 1, "streak_dir": "below", "last_bar_ts": 205},
        "ETH": {"state": "above", "streak": 1, "streak_dir": "below", "last_bar_ts": 205},
    }
    regime, states = daily_sma200_atr_regime(_ohlc(base + [100.0]), _ohlc(base + [100.0]),
                                             prev_states=prev)
    assert regime == REGIME_NORMAL
    assert states["BTC"]["state"] == "above"
    assert states["BTC"]["streak"] == 0
    assert states["BTC"]["streak_dir"] is None


def test_same_bar_is_idempotent():
    base = [100.0] * 205
    prev = {
        "BTC": {"state": "above", "streak": 0, "streak_dir": None, "last_bar_ts": 204},
        "ETH": {"state": "above", "streak": 0, "streak_dir": None, "last_bar_ts": 204},
    }
    _, states = daily_sma200_atr_regime(_ohlc(base + [80.0]), _ohlc(base + [80.0]),
                                        prev_states=prev)
    _, states2 = daily_sma200_atr_regime(_ohlc(base + [80.0]), _ohlc(base + [80.0]),
                                         prev_states=states)
    assert states2 == states


def test_closed_daily_ohlc_drops_in_progress_candle():
    t0 = 1_700_000_000 - (1_700_000_000 % DAY)
    candles = [
        [(t0 + i * DAY) * 1000, 99.0 + i, 105.0 + i, 95.0 + i, 100.0 + i, 10.0]
        for i in range(3)
    ]
    rows = closed_daily_ohlc(candles, now_ts=t0 + 2 * DAY + 3600)
    assert [r["close"] for r in rows] == [100.0, 101.0]
    rows = closed_daily_ohlc(candles, now_ts=t0 + 3 * DAY)
    assert [r["high"] for r in rows] == [105.0, 106.0, 107.0]


def test_short_history_raises():
    with pytest.raises(ValueError):
        daily_sma200_atr_regime(_ohlc([100.0] * 50), _ohlc([100.0] * 50))
