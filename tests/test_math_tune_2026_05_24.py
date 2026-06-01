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


def test_scalp_size_pct_v4_tune():
    """SCALP size_pct has two documented lifecycle values:
      - 0.35 = LIVE design target (v5 2026-05-28, size_pct 0.16 → 0.35).
        Sizing math @ $130 pocket: 35% × 3x × 0.8% SL = ~$1.09 per SL,
        35% × 3x × 1.3% TP = ~$1.77 per TP — the $1-2 USDT/trade target.
      - 0.06 = TEMPORARY PAPER overlay (2026-05-30 data-gathering pass;
        config comment: 'Revert to 0.35 before live'). All tiers were
        scaled down for capital-preservation while LIVE-in-PAPER.
    Pin accepts either documented value; rejects undocumented drift
    (e.g. a typo to 0.16). SL/TP/leverage stay fixed across both."""
    import config
    scalp = config.LEVERAGE_TIERS.get("SCALP")
    assert scalp is not None
    assert scalp["size_pct"] in (0.06, 0.35), (
        f"SCALP size_pct {scalp['size_pct']} not in the documented set "
        f"{{0.06 PAPER overlay (2026-05-30), 0.35 live target}}. "
        f"0.35 is the live $1-2/trade design; 0.06 is the temporary "
        f"capital-preservation overlay ('Revert to 0.35 before live')."
    )
    assert scalp["leverage"] == 3
    # 2026-05-28: SL/TP matched to SCALP_MODE values.
    assert scalp["tp_pct"] == 0.013
    assert scalp["sl_pct"] == 0.008


def test_scalp_tier_economics_break_even_math():
    """Spot-check the break-even math after the v5 tune.

    R = avg_win/avg_loss = TP/SL = 1.3/0.8 = 1.625:1.
    Break-even WR at R=1.625 is 1/(1+1.625) = 38.1%.
    Current realized 15-60m WR ~55-64% is well above break-even.
    At 55% WR: EV = 0.55 × 1.625 - 0.45 = +$0.44 per unit risked.

    This is a math contract — asserts the configured R:R so any
    future config edit that breaks SCALP economics fails loudly.
    """
    import config
    scalp = config.LEVERAGE_TIERS["SCALP"]
    configured_r = scalp["tp_pct"] / scalp["sl_pct"]
    assert abs(configured_r - 1.625) < 0.01, (
        f"SCALP configured R:R changed to {configured_r:.3f}; the math-tune "
        f"contract expects R=1.625 (TP 1.3% / SL 0.8%)."
    )
    break_even_wr = 1.0 / (1.0 + configured_r)
    assert break_even_wr < 0.40, (
        f"Break-even WR {break_even_wr:.4f} too high — SCALP needs WR under "
        f"40% to be positive-EV at configured R:R."
    )
