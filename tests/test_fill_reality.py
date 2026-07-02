"""W1 — fill_reality.perp_short_margin_buffer_x liquidation-realism tests (SIM).

Pins the isolated-short-perp margin-buffer model: 200x at 1x with mark==entry,
monotone decreasing in mark, clamped to 0 past equity exhaustion, and exactly
1.0x at the price given by fill_reality.liquidation_price.
"""

from __future__ import annotations

import pytest

from core.fill_reality import liquidation_price, perp_short_margin_buffer_x


def test_buffer_is_exactly_200x_at_1x_with_mark_equal_entry():
    out = perp_short_margin_buffer_x(entry_px=100.0, mark_px=100.0, leverage=1.0)
    assert out["buffer_x"] == 200.0  # (1/1 + 0) / 0.005 exactly
    assert out["equity_frac"] == pytest.approx(1.0)
    assert out["liq_px"] == pytest.approx(liquidation_price("short", 100.0, 1.0, mmr=0.005))


def test_buffer_monotone_decreasing_in_mark():
    marks = [90.0, 100.0, 120.0, 150.0, 199.0]
    buffers = [
        perp_short_margin_buffer_x(entry_px=100.0, mark_px=m, leverage=1.0)["buffer_x"]
        for m in marks
    ]
    assert buffers == sorted(buffers, reverse=True)
    assert buffers[0] > buffers[-1]


def test_buffer_clamps_to_zero_past_equity_exhaustion():
    # 1x short: equity is exhausted once mark >= 2x entry (before mmr).
    out = perp_short_margin_buffer_x(entry_px=100.0, mark_px=250.0, leverage=1.0)
    assert out["buffer_x"] == 0.0
    assert out["equity_frac"] < 0.0  # raw equity fraction is reported unclamped


@pytest.mark.parametrize("leverage", [1.0, 2.0, 5.0])
def test_buffer_is_exactly_one_at_liquidation_price(leverage):
    liq = liquidation_price("short", 100.0, leverage, mmr=0.005)
    out = perp_short_margin_buffer_x(entry_px=100.0, mark_px=liq, leverage=leverage)
    assert out["buffer_x"] == pytest.approx(1.0)
    assert out["liq_px"] == pytest.approx(liq)


@pytest.mark.parametrize(
    "kw",
    [
        dict(entry_px=0.0, mark_px=100.0, leverage=1.0),
        dict(entry_px=-1.0, mark_px=100.0, leverage=1.0),
        dict(entry_px=100.0, mark_px=0.0, leverage=1.0),
        dict(entry_px=100.0, mark_px=-5.0, leverage=1.0),
        dict(entry_px=100.0, mark_px=100.0, leverage=0.0),
        dict(entry_px=100.0, mark_px=100.0, leverage=-2.0),
    ],
)
def test_nonpositive_inputs_raise(kw):
    with pytest.raises(ValueError):
        perp_short_margin_buffer_x(**kw)
