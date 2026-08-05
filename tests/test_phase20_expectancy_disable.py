"""Phase 20 — EXPECTANCY_FILTER disabled by default (2026-05-04).

SUPERSEDED by Phase 27 (2026-05-05). The Phase 20 disable was
correct for its moment — the BINARY filter at $0.05 floor locked the
bot out forever. Phase 27 replaces the binary block with a GRADUATED
size multiplier:

  - n < min_sample_size:    ×1.0 (insufficient data)
  - mean >= 0:              ×1.0 (positive EV)
  - -0.20 <= mean < 0:      ×0.75
  - -0.50 <= mean < -0.20:  ×0.50
  - mean < -0.50 (n>=5):    ×0.0  (catastrophic — BLOCK)

So Phase 27 IS enabled, but no longer locks symbols out forever —
only catastrophic mean<-$0.50 hard-blocks. Mild/moderate negative EV
just downsizes; the symbol can recover as evidence accumulates.

This file is retained as a historical pin for the supersession.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep


def test_phase20_disabled_was_superseded_by_phase27():
    """EXPECTANCY_FILTER is now enabled (Phase 27) — but as a graduated
    multiplier, not the binary block that Phase 20 disabled. The
    catastrophic floor (-$0.50) is preserved from Phase 20's safer
    re-enable target."""
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER["enabled"] is True, (
        "Phase 27 re-enabled the filter as a graduated multiplier"
    )
    # Phase 20's $-0.50 floor preserved (catastrophic-only)
    assert EXPECTANCY_FILTER["min_expected_dollar"] == -0.50
    assert EXPECTANCY_FILTER["min_expected_star"] == -0.50


def test_phase16_18_still_engage_after_supersession():
    """Sanity: Phase 16 ev_mult and Phase 18 cal_mult must still apply,
    they compose with the Phase 27 graduated multiplier."""
    from pathlib import Path
    src = bot_engine_source_for_grep()
    assert "self.risk._adaptive_size_multiplier()" in src
    assert "size_fraction *= _ev_mult" in src
    assert "self.order_mgr.calibrator.calibrate(" in src
    assert "size_fraction *= _cal_mult" in src
    # Plus the new Phase 27 multiplier
    assert "size_fraction *= _ev_symbol_mult" in src
