"""Guards for small-TP trade capture without self-blocking R:R mismatches."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def test_scalp_tier_rr_meets_global_minimum():
    from config import LEVERAGE_TIERS, RISK

    tier = LEVERAGE_TIERS["SCALP"]
    rr = tier["tp_pct"] / tier["sl_pct"]

    assert rr >= RISK["min_rr_ratio"]


def test_portfolio_prompt_no_stale_wide_target_policy():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    prompt_idx = src.index("_PORTFOLIO_SYSTEM_PROMPT")
    prompt_block = src[prompt_idx:prompt_idx + 2500]

    assert "Min 1.8:1 RR" not in prompt_block
    assert "TP>=5%" not in prompt_block
    assert "max 3x" not in prompt_block
    assert "FUTURES SCALP" in prompt_block
    assert '"sl_pct":1.5,"tp_pct":1.8' in prompt_block


def test_claude_futures_ingestion_normalizes_sl_and_tp_together():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    parse_idx = src.index("# Parse actions")
    parse_block = src[parse_idx:parse_idx + 6000]

    assert "_claude_tp_clamped = min(2.0, max(1.8, _raw_tp))" in parse_block
    assert "_max_sl_for_rr = min(1.6, _claude_tp_clamped / 1.2)" in parse_block
    assert '"sl_pct":      _claude_sl_clamped' in parse_block


def test_execute_open_falls_back_to_tier_shape_for_bad_action_rr():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    eo_idx = src.index("def _execute_open(self, action: dict)")
    eo_block = src[eo_idx:eo_idx + 50000]

    assert "action SL/TP R:R" in eo_block
    assert "using {tier_name} tier shape" in eo_block
    assert "_tier_rr >= min_rr" in eo_block


def test_recent_tape_summary_accepts_warehouse_dict_rows():
    from core.mcp_brain import MCPBrain

    brain = MCPBrain.__new__(MCPBrain)
    fake_wh = MagicMock()
    fake_wh.query = MagicMock(return_value=[
        {"realized_pnl": 0.5, "ts_entry": 1700000000},
        {"realized_pnl": -0.3, "ts_entry": 1700001000},
        {"realized_pnl": 0.2, "ts_entry": 1700002000},
    ])

    import core.warehouse as wh_mod

    orig = wh_mod.get_warehouse
    wh_mod.get_warehouse = MagicMock(return_value=fake_wh)
    try:
        out = brain._recent_tape_summary(["BTC"])
    finally:
        wh_mod.get_warehouse = orig

    assert len(out) == 1
    assert "BTC" in out[0]
    assert "n=3" in out[0]
    assert "WR=67%" in out[0]
