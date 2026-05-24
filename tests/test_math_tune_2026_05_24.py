"""SCALP math-tune pins (2026-05-24).

Locks the four config values changed in the math-tune bundle so they
can't silently regress. Each pin includes the math rationale.

Memory: feedback_scalp_mode_directive_2026_05_22 (small TPs target),
project_trailing_clips_winners_2026_04_21 (realized-R leak source).
"""
from __future__ import annotations


def test_auto_small_tp_disabled():
    """AUTO_SMALL_TP_ENABLED firing at 1% clipped wins at 56% of the
    configured 1.8% SCALP TP, producing realized R≈0.63 vs target
    R≈1.20. Must stay off until the realized-R recovers."""
    import config
    assert config.AUTO_SMALL_TP_ENABLED is False, (
        "AUTO_SMALL_TP_ENABLED clips SCALP winners at 1% (56% of "
        "configured 1.8% TP). Re-enabling without widening the TP "
        "first will re-introduce the 5× R-loss documented in "
        "project_trailing_clips_winners_2026_04_21."
    )


def test_age_aware_sl_min_profit_threshold():
    """AGE_AWARE_SL_MIN_PNL_FRAC at 0.0001 (any positive tick) was
    moving SL to breakeven on micro-profit, then getting wick-knocked
    at BE. Threshold raised to 0.5% so winners build a real cushion
    before SL ratchets to entry."""
    import config
    assert config.AGE_AWARE_SL_MIN_PNL_FRAC >= 0.005, (
        f"AGE_AWARE_SL_MIN_PNL_FRAC dropped below 0.005 (got "
        f"{config.AGE_AWARE_SL_MIN_PNL_FRAC}). At <0.5% profit the "
        f"breakeven SL move clips winners that haven't earned room to "
        f"survive normal wick noise."
    )
    # Sanity: max stays at 2% (trailing-stop boundary).
    assert config.AGE_AWARE_SL_MAX_PNL_FRAC == 0.02


def test_scalp_min_confidence_raised():
    """SCALP min_confidence 0.40 → 0.50. The 0.40-0.50 band was the
    anti-monotonic worst-WR cohort (~23-27% per gate_effectiveness §1
    score buckets 75-79 / 80-84). Tighter floor cuts that cohort."""
    import config
    scalp = config.LEVERAGE_TIERS.get("SCALP")
    assert scalp is not None, "SCALP tier missing (SCALP_TIER_ENABLED?)"
    assert scalp["min_confidence"] >= 0.50, (
        f"SCALP min_confidence dropped to {scalp['min_confidence']}; "
        f"the 0.40-0.50 band was the anti-monotonic worst WR cohort."
    )


def test_scalp_size_pct_reduced():
    """SCALP size_pct 0.10 → 0.075. Math-tune dampens bleed velocity
    by 25%. Stays in '1-2 USDT/trade' band on a $400 wallet:
    0.075 × 2x × 1.5% × 400 = $0.90 per SL hit."""
    import config
    scalp = config.LEVERAGE_TIERS.get("SCALP")
    assert scalp is not None
    assert scalp["size_pct"] == 0.075, (
        f"SCALP size_pct expected 0.075; got {scalp['size_pct']}. "
        f"This is the math-tune bleed-velocity damper."
    )
    # Sanity: tp/sl untouched (per scalp directive).
    assert scalp["tp_pct"] == 0.018
    assert scalp["sl_pct"] == 0.015


def test_scalp_tier_economics_break_even_math():
    """Spot-check the break-even math after the tune.

    R = avg_win/avg_loss after fix should approach configured R (1.2)
    once AUTO_SMALL_TP stops clipping. Break-even WR at R=1.2 is
    1/(1+1.2) = 45.5%. Current realized WR 44.8% sits just under this
    line — the audit-fix WR lift (1-3pp) + tighter min_confidence cohort
    selection should push WR above 45.5%, making PF > 1.0 plausible
    once realized R recovers.

    This is a math contract, not a guarantee — it asserts the
    configured R:R is what the tune intends, so any future config edit
    that breaks the SCALP economics fails loudly.
    """
    import config
    scalp = config.LEVERAGE_TIERS["SCALP"]
    configured_r = scalp["tp_pct"] / scalp["sl_pct"]
    assert configured_r == 1.2, (
        f"SCALP configured R:R changed to {configured_r}; the math-tune "
        f"contract expects R=1.2 (TP 1.8% / SL 1.5%)."
    )
    break_even_wr = 1.0 / (1.0 + configured_r)
    assert break_even_wr < 0.46, (
        f"Break-even WR {break_even_wr:.4f} too high — SCALP can't reach "
        f"profitability with current WR ~45%."
    )
