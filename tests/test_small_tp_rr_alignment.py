"""Guards for small-TP trade capture without self-blocking R:R mismatches."""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

from pathlib import Path


def test_scalp_tier_rr_meets_global_minimum():
    from config import LEVERAGE_TIERS, RISK

    tier = LEVERAGE_TIERS["SCALP"]
    rr = tier["tp_pct"] / tier["sl_pct"]

    assert rr >= RISK["min_rr_ratio"]


def test_execute_open_falls_back_to_tier_shape_for_bad_action_rr():
    src = bot_engine_source_for_grep()
    eo_idx = src.index("def _execute_open(self, action: dict)")
    eo_block = src[eo_idx:eo_idx + 50000]

    assert "action SL/TP R:R" in eo_block
    assert "using {tier_name} tier shape" in eo_block
    assert "_tier_rr >= min_rr" in eo_block
