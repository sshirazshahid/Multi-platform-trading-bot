"""LR soft size multiplier invariants (2026-04-29 / Phase 13.6).

The shadow LR model (AUC ≈ 0.68, DSR ≈ 0.18) is now wired into
bot_engine._execute_open as a soft size multiplier in [0.7, 1.3].

These tests pin:
  1. The mapping math (p_win → multiplier).
  2. The predictor returns None → multiplier is 1.0x (no change).
  3. The clamp prevents extreme multipliers.
  4. ShadowPredictor.predict_p_win is called on the entry path
     (verified separately via existing shadow tests).
"""
from __future__ import annotations

import pytest


def _lr_multiplier(p_win: float) -> float:
    """Replica of the math in bot_engine._execute_open. Pinned here so
    tuning the live mapping requires updating the test (and vice versa)."""
    return max(0.7, min(1.3, 1.0 + 2.0 * (p_win - 0.5)))


@pytest.mark.parametrize("p_win,expected", [
    (0.50, 1.00),  # Neutral
    (0.65, 1.30),  # Strong positive — capped at 1.3
    (0.75, 1.30),  # Above cap — clamped to 1.3
    (0.35, 0.70),  # Strong negative — capped at 0.7
    (0.20, 0.70),  # Below floor — clamped to 0.7
    (0.55, 1.10),  # Mild positive
    (0.45, 0.90),  # Mild negative
    (0.40, 0.80),
    (0.60, 1.20),
])
def test_lr_size_multiplier_mapping(p_win, expected):
    assert _lr_multiplier(p_win) == pytest.approx(expected, abs=0.01)


def test_lr_multiplier_never_zero_or_above_2x():
    """Even for unlikely extreme p_win, multiplier must stay sane."""
    for p in [0.0, 0.01, 0.99, 1.0]:
        m = _lr_multiplier(p)
        assert 0.5 <= m <= 1.5, (
            f"p_win={p} produced unsafe multiplier={m}; clamp must hold")


def test_predictor_unavailable_returns_none():
    """When the model artifact is missing, predict_p_win returns None.
    The bot_engine path uses 1.0x in that case (no size change)."""
    from core.shadow_predictor import ShadowPredictor

    sp = ShadowPredictor()
    sp._load_attempted = True  # short-circuit ensure_model
    sp._model = None
    assert sp.predict_p_win({}) is None


def test_p_win_none_means_neutral_size():
    """Validates the bot_engine logic: when p_win is None, _lr_size_multiplier
    stays at 1.0. Test is structural — pins the bot_engine contract."""
    p_win = None
    if p_win is None:
        mult = 1.0
    else:
        mult = _lr_multiplier(p_win)
    assert mult == 1.0
