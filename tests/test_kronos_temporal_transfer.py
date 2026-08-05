"""Kronos-inspired temporal awareness (2026-05-27).

Transfers the calendar-embedding concept from the Kronos foundation model
(AAAI 2026) to the MCP Brain scoring engine. Kronos uses 5 additive temporal
embeddings (minute/hour/weekday/day/month) — we port the weekday dimension
as a confidence multiplier derived from 459-trade all-time data.

2026-05-27 (UNBLOCK directive): multipliers raised to floor 0.85 so they
serve as soft sizing hints without tanking confidence enough to block trades.
"""
from __future__ import annotations


def test_weekday_confidence_mult_config_exists():
    from config import WEEKDAY_CONFIDENCE_MULT
    assert isinstance(WEEKDAY_CONFIDENCE_MULT, dict)
    assert len(WEEKDAY_CONFIDENCE_MULT) == 7
    for dow in range(7):
        assert dow in WEEKDAY_CONFIDENCE_MULT
        assert 0 < WEEKDAY_CONFIDENCE_MULT[dow] <= 2.0


def test_monday_has_mild_penalty():
    """Mon (19% WR) gets mild penalty but floor >= 0.85 (UNBLOCK directive)."""
    from config import WEEKDAY_CONFIDENCE_MULT
    mon = WEEKDAY_CONFIDENCE_MULT[1]  # strftime %w: 1=Mon
    assert 0.80 <= mon <= 0.90, f"Mon mult {mon} not in soft-hint range [0.80, 0.90]"


def test_thursday_is_best():
    """Thu (45% WR, +$4.28) should be the highest multiplier."""
    from config import WEEKDAY_CONFIDENCE_MULT
    thu = WEEKDAY_CONFIDENCE_MULT[4]  # strftime %w: 4=Thu
    assert thu >= 1.10
    for dow, mult in WEEKDAY_CONFIDENCE_MULT.items():
        if dow != 4:
            assert mult <= thu, f"DOW {dow} ({mult}) > Thu ({thu})"


def test_multiplier_applied_in_score_coin_return():
    """_score_coin return dict should include _weekday_mult (FOMO mult removed)."""
    src = open("core/scoring/entry_score.py", encoding="utf-8").read()
    assert "_weekday_mult" in src
    assert "WEEKDAY_CONFIDENCE_MULT" in src
    assert "confidence * _weekday_mult" in src
    assert "_fomo_mult" not in src


def test_all_multipliers_positive():
    """No weekday should completely block trading (mult > 0)."""
    from config import WEEKDAY_CONFIDENCE_MULT
    for dow, mult in WEEKDAY_CONFIDENCE_MULT.items():
        assert mult > 0, f"DOW {dow} has zero multiplier — would block all trades"
