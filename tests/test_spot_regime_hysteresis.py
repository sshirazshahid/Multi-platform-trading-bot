"""W4 — S1 whipsaw hysteresis band around the daily EMA200 (closes-only).

The pinned primitive ``daily_ema200_regime`` is untouched; the hysteresis
variant retains the previous above/below state inside a band of
``band_mult x mean(|close-to-close delta|)`` and falls back to the stateless
sign on cold start, making the first evaluation bit-identical to the primitive.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.spot_regime import (
    EMA_PERIOD,
    HYSTERESIS_BAND_LOOKBACK,
    HYSTERESIS_BAND_MULT,
    REGIME_DEFENSIVE,
    REGIME_NORMAL,
    daily_ema200_regime,
    daily_ema200_regime_hysteresis,
)

N = 250


# ── reference replicas (pin the formulas independently) ────────────────
def _ema_ref(closes, period=EMA_PERIOD):
    mult = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for v in closes[period:]:
        ema = (v - ema) * mult + ema
    return ema


def _band_ref(closes, mult=HYSTERESIS_BAND_MULT, lookback=HYSTERESIS_BAND_LOOKBACK):
    deltas = [abs(closes[i] - closes[i - 1]) for i in range(len(closes) - lookback, len(closes))]
    return mult * (sum(deltas) / lookback)


def _series(final: float) -> list:
    """Flat 100s, a 14-value 105/95 oscillating tail (band ~4.8), then ``final``."""
    return [100.0] * (N - 15) + [105.0, 95.0] * 7 + [final]


def test_constants():
    assert HYSTERESIS_BAND_MULT == 0.5
    assert HYSTERESIS_BAND_LOOKBACK == 14


def test_inside_band_retains_prev_state():
    closes = _series(100.0)
    ema, band = _ema_ref(closes), _band_ref(closes)
    assert abs(closes[-1] - ema) < band  # precondition: inside the band

    regime, states = daily_ema200_regime_hysteresis(
        closes, closes, prev_states={"BTC": "above", "ETH": "above"}
    )
    assert regime == REGIME_NORMAL
    assert states == {"BTC": "above", "ETH": "above"}

    regime, states = daily_ema200_regime_hysteresis(
        closes, closes, prev_states={"BTC": "below", "ETH": "below"}
    )
    assert regime == REGIME_DEFENSIVE
    assert states == {"BTC": "below", "ETH": "below"}


def test_crossing_beyond_band_flips():
    up = _series(120.0)
    assert up[-1] > _ema_ref(up) + _band_ref(up)  # precondition: beyond band
    regime, states = daily_ema200_regime_hysteresis(
        up, up, prev_states={"BTC": "below", "ETH": "below"}
    )
    assert regime == REGIME_NORMAL
    assert states == {"BTC": "above", "ETH": "above"}

    down = _series(80.0)
    assert down[-1] < _ema_ref(down) - _band_ref(down)
    regime, states = daily_ema200_regime_hysteresis(
        down, down, prev_states={"BTC": "above", "ETH": "above"}
    )
    assert regime == REGIME_DEFENSIVE
    assert states == {"BTC": "below", "ETH": "below"}


def test_cold_start_inside_band_matches_stateless():
    for final in (99.0, 100.0, 101.0):
        closes = _series(final)
        assert abs(closes[-1] - _ema_ref(closes)) < _band_ref(closes)
        stateless = daily_ema200_regime(closes, closes)
        for prev in (None, {}):  # missing key behaves like missing state
            regime, _ = daily_ema200_regime_hysteresis(closes, closes, prev_states=prev)
            assert regime == stateless


def test_band_value_pin():
    """Behavior transitions exactly at ema +/- band from the reference formula."""
    for final in (100.5, 104.0, 106.0, 110.0, 96.0, 90.0):
        closes = _series(final)
        ema, band = _ema_ref(closes), _band_ref(closes)
        if closes[-1] > ema + band:
            expected = "above"
        elif closes[-1] < ema - band:
            expected = "below"
        else:
            expected = "below"  # prev state below is retained inside the band
        _, states = daily_ema200_regime_hysteresis(
            closes, closes, prev_states={"BTC": "below", "ETH": "below"}
        )
        assert states["BTC"] == expected, f"final={final}"


def test_short_history_raises():
    with pytest.raises(ValueError):
        daily_ema200_regime_hysteresis([100.0] * 50, [100.0] * 50)


def test_compute_regimes_hysteresis_flips_less_than_stateless():
    """A series oscillating tightly around the EMA whipsaws the stateless
    regime every bar; the hysteresis replay flips strictly fewer times."""
    from scripts.spot_rotation_slice import compute_regimes

    block = [104.0, 96.0] + [100.5, 99.5] * 6  # spikes inflate the band to ~1.0
    path = [100.0] * 200 + block * 7  # 200 warmup + 98 oscillating bars
    n = len(path)
    closes = pd.DataFrame(
        {"BTC": pd.Series(path, dtype=float), "ETH": pd.Series(path, dtype=float)}
    )

    hyst = compute_regimes(closes)
    stateless = []
    for t in range(n):
        if t + 1 < 200:
            stateless.append(REGIME_DEFENSIVE)
            continue
        seg = list(np.asarray(path[: t + 1], dtype=float))
        stateless.append(daily_ema200_regime(seg, seg))

    def flips(regs):
        return sum(1 for a, b in zip(regs, regs[1:]) if a != b)

    assert flips(stateless) > 10  # the whipsaw actually bites
    assert flips(hyst) < flips(stateless)
