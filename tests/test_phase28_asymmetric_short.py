"""Phase 28 — asymmetric SHORT-side risk reduction (2026-05-05).

Trade audit on 267 closed trades:

  SHORT (102 trades): 37.3% WR, $-52.12 sum  ← 79% of total bleed
  BUY   (165 trades): 44.8% WR, $-14.48 sum

SHORT side is structurally bleeding. The pre-existing ShortGate
(rolling SELL WR < 45% blocks entries) catches systemic regime shifts
but doesn't cap per-trade damage when shorts ARE allowed. Phase 27
per-(symbol, side) EV gate handles symbol-specific bad cells. Phase
28 completes the picture: when a SHORT does pass all gates, give it a
TIGHTER SL and SMALLER size — the same trade with reduced exposure.

Implementation:
  - SHORT sl_pct ×0.60  (e.g., 2.5% → 1.5%)
  - SHORT size_pct ×0.75
  - BUY trades unchanged

Net per-trade EV math (assume WR drops 4 points to 33% but per-loss
shrinks 40%):
  old: 0.37 × win − 0.63 × loss        ≈ −$0.51
  new: 0.33 × win − 0.67 × (loss×0.60) ≈ −$0.19
~$0.32/trade improvement at current SHORT volume → ~$5/30d.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

from pathlib import Path


def test_short_asymmetric_block_present_in_execute_open():
    src = bot_engine_source_for_grep()
    eo_idx = src.index("def _execute_open(self, action: dict)")
    eo_block = src[eo_idx:eo_idx + 50000]
    assert 'if side == "sell":' in eo_block, \
        "Phase 28 SHORT-side branch must exist in _execute_open"
    assert "Phase 28" in eo_block


def test_sl_multiplier_is_60_percent():
    src = bot_engine_source_for_grep()
    # The exact multiplier applied to sl_pct on the SHORT branch
    assert "sl_pct = sl_pct * 0.60" in src


def test_size_multiplier_is_75_percent():
    src = bot_engine_source_for_grep()
    assert "size_pct = size_pct * 0.75" in src


def test_short_block_runs_after_sl_tp_resolution():
    """The asymmetric block must run AFTER sl_pct/tp_pct are resolved
    from algo_sl/algo_tp or tier_params — otherwise we'd be modifying
    a stale value."""
    src = bot_engine_source_for_grep()
    sl_resolve_idx = src.index("sl_pct = tier_params[\"sl_pct\"] * 100.0")
    asym_idx = src.index('if side == "sell":', sl_resolve_idx)
    assert asym_idx > sl_resolve_idx, (
        "Phase 28 SHORT modifier must run after sl_pct resolution")


def test_short_block_runs_before_rr_validation():
    """The R:R validation runs AFTER our asymmetric tightening so the
    new (tighter SL, same TP) ratio is what gets validated."""
    src = bot_engine_source_for_grep()
    asym_idx = src.index('if side == "sell":')
    rr_idx = src.index("R:R validation", asym_idx)
    assert rr_idx > asym_idx, (
        "Phase 28 modifier must run before the R:R validation so the "
        "validated R:R reflects the tightened stop")


def test_log_marker_emitted_on_short():
    """Operators must see when the asymmetric reduction fires."""
    src = bot_engine_source_for_grep()
    assert "SHORT asymmetric" in src


def test_buy_path_unchanged():
    """BUY trades must NOT be affected — the branch is gated by
    `if side == \"sell\":` so the BUY path falls straight to R:R
    validation with original sl_pct / size_pct."""
    src = bot_engine_source_for_grep()
    asym_block_idx = src.index('if side == "sell":')
    block_end = src.index("# R:R validation", asym_block_idx)
    asym_block = src[asym_block_idx:block_end]
    # No mutation of sl_pct/size_pct outside the if-sell branch
    # within this localized region. Cheap proxy: the if block
    # contains the multipliers and the block ends before R:R validation.
    assert "sl_pct = sl_pct * 0.60" in asym_block
    assert "size_pct = size_pct * 0.75" in asym_block
    # No BUY-side counterpart
    assert 'if side == "buy":' not in asym_block


def test_phase28_complements_phase27_short_gate():
    """Phase 28 is per-trade risk reduction, NOT a gate. ShortGate
    (already in source) handles the entry blocking on regime drift."""
    src = bot_engine_source_for_grep()
    # ShortGate still present (the existing 45% WR rolling check)
    assert "ShortGate" in src
