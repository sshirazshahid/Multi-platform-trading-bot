"""Phase 20 — EXPECTANCY_FILTER disabled by default (2026-05-04).

Live monitor evidence: between 08:34 UTC and 08:59 UTC, the bot
proposed BTC + ETH + DOGE entries on 4 portfolio cycles, executing
0/N each time. Investigation showed all blocked by:

  [Expectancy] BLOCKED BTC/USDT buy (non-STAR): recent mean
  $-0.467 < $0.05 floor (n=5, WR=20%)

Even at floor=$-0.30, BTC at -$0.467 still fails. The filter was
effectively a "permanently locked out" gate with no recovery path
(0 trades → mean never moves toward zero).

This contradicts UNBLOCK_ALL ("Don't block any trades. Engine should
decide.") and is the exact triple-veto pattern Phase 19 fixed for the
caution-symbol gate. Phase 16 adaptive sizing (rolling EV) + Phase 18
calibrator (soft multiplier) already handle low-EV setups organically.

Phase 20 fix:
  - EXPECTANCY_FILTER.enabled = False (was True)
  - min_expected_dollar = -0.50 (was 0.05) — catastrophic-only guard
    if someone flips enabled back to True
  - min_expected_star   = -0.50 (was 0.00)

Restore the gate by flipping enabled=True. The lowered floor stays
even when re-enabled, since $0.05 was demonstrated too aggressive.
"""
from __future__ import annotations

from pathlib import Path


def test_filter_disabled_in_config():
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER["enabled"] is False


def test_floor_lowered_to_catastrophic_only():
    """Even if re-enabled, the floor must NOT block at -$0.05 again."""
    from config import EXPECTANCY_FILTER as ef
    assert ef["min_expected_dollar"] == -0.50
    assert ef["min_expected_star"] == -0.50


def test_call_site_respects_disable_flag():
    """The call site at bot_engine.py:1870-1873 must guard the entire
    block behind EXPECTANCY_FILTER.enabled. With enabled=False, none
    of the warehouse query / floor comparison / BLOCKED log can fire."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Find the import + flag check
    block_start = src.index("from config import EXPECTANCY_FILTER as _EF")
    flag_check_idx = src.index('_EF.get("enabled"', block_start)
    blocked_log_idx = src.index("[Expectancy] BLOCKED", block_start)
    # The flag check must precede the BLOCKED log emission
    assert flag_check_idx < blocked_log_idx, \
        "expectancy guard flag check must precede BLOCKED log"


def test_disable_flag_short_circuits_warehouse_query():
    """When enabled=False, the recent_expectancy() warehouse query must
    NOT fire — that's what makes the disable cheap (no DB read per entry)."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Find the if-block for the filter
    block_start = src.index('if _EF.get("enabled"')
    block_end = src.index("# (b)", block_start) if "# (b)" in src[block_start:] \
                else block_start + 3000
    block = src[block_start:block_end]
    # The recent_expectancy call must be INSIDE the if-enabled block
    assert "recent_expectancy" in block, \
        "recent_expectancy must be inside the enabled-flag guard"


def test_phase16_18_still_engage_without_expectancy_filter():
    """Sanity: with the binary expectancy gate off, Phase 16 ev_mult and
    Phase 18 cal_mult must still apply — they were the rationale for
    removing the gate."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "self.risk._adaptive_size_multiplier()" in src
    assert "size_fraction *= _ev_mult" in src
    assert "self.order_mgr.calibrator.calibrate(" in src
    assert "size_fraction *= _cal_mult" in src
