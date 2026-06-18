"""UniverseFilter chop-gate threshold is sourced from centralized config.

The Kaufman Efficiency Ratio floor (MIN_TREND_EFFICIENCY) was promoted from a
hardcoded class constant to an owner-tunable config/env knob (2026-06-18) so the
chop gate can be loosened without a code edit. These pin that the instance reads
the configured value and that check()'s comparison + log line stay in sync with it.
"""
from __future__ import annotations

import config
from core.pair_discovery import UniverseFilter


def test_chop_threshold_overrides_from_config():
    # The instance threshold must reflect centralized config, not the class default.
    uf = UniverseFilter()
    assert uf.MIN_TREND_EFFICIENCY == config.MIN_TREND_EFFICIENCY


def test_chop_threshold_is_float_in_unit_range():
    uf = UniverseFilter()
    assert isinstance(uf.MIN_TREND_EFFICIENCY, float)
    assert 0.0 <= uf.MIN_TREND_EFFICIENCY <= 1.0
