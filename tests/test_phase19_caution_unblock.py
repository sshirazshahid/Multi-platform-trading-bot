"""Phase 19 — caution-symbol/strategy gate config-flagged off (2026-05-04).

Ruflo audit found: bot_engine._execute_open had a hard `return False` for
any symbol with 30d WR < 35% unless confidence >= 0.90. This directly
contradicts the user's UNBLOCK_ALL directive ("Don't block any trades.
Engine should decide.") and double-vetoes against Phase 16 adaptive
sizing + Phase 18 calibrator, which already handle low-EV setups
organically (size down + pull confidence toward historical WR).

The audit was triggered by a real log line at 23:27:14 UTC May 3:
  [Claude] BLOCKED: BTC/USDT is caution symbol (<50% WR, conf=0.81 < 0.90)
BTC at 81% confidence with 4-condition MCP score, blocked because BTC's
30d aggregate WR < 35%.

Fix: two new RISK flags, both default False per UNBLOCK_ALL:
  - caution_symbol_block_enabled
  - caution_strategy_block_enabled
Set either to True to restore the historic gate. Also corrected the
stale log message "<50% WR" to "<35% WR" (actual threshold per
knowledge_model.py:284).
"""
from __future__ import annotations

from pathlib import Path


def test_risk_config_has_caution_flags_default_false():
    from config import RISK
    assert RISK.get("caution_symbol_block_enabled") is False, \
        "default must be False per UNBLOCK_ALL"
    assert RISK.get("caution_strategy_block_enabled") is False, \
        "default must be False per UNBLOCK_ALL"


def test_caution_symbol_block_wrapped_in_flag():
    """The hard `return False` for caution_symbol must be wrapped by the
    config flag — or the flag is decorative."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # The flag check must precede the is_caution_symbol() call
    flag_idx = src.index('RISK.get("caution_symbol_block_enabled"')
    is_caution_idx = src.index("_km.is_caution_symbol(symbol)")
    # Match the actual log-line, not the comment that mentions "<35% WR".
    return_false_idx = src.index("BLOCKED: {symbol} is caution symbol")
    assert flag_idx < is_caution_idx < return_false_idx, \
        "expected: RISK flag check → is_caution_symbol → return False"


def test_caution_strategy_block_wrapped_in_flag():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Flag must guard the strategy_name try block
    assert 'and RISK.get("caution_strategy_block_enabled"' in src


def test_stale_log_message_corrected():
    """The stale '<50% WR' log was incorrect per knowledge_model.py:284
    (actual threshold: wr < 35). Must be updated."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # The corrected message must be present
    assert "<35% WR" in src, "log message should reflect actual threshold"


def test_phase16_phase18_still_engage_without_caution_block():
    """Sanity: with caution block off, Phase 16 ev_mult and Phase 18
    calibrator multipliers must still apply — they were the rationale
    for removing the binary block."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Phase 16
    assert "self.risk._adaptive_size_multiplier()" in src
    assert "size_fraction *= _ev_mult" in src
    # Phase 18
    assert "self.order_mgr.calibrator.calibrate(" in src
    assert "size_fraction *= _cal_mult" in src
