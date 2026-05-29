"""Phase 22 — regime VOLATILE soft multiplier instead of hard block (2026-05-04).

User flagged 1 open position despite multiple high-conviction proposals
(BTC@84% conf, ETH@76% conf) every 5-min cycle. Investigation showed
Phase 16's regime gate hard-blocked ~10 proposals/hour on
`regime:volatile` due to ATR%-relative-percentile classification —
BTC at 0.79% ATR was tagged vol_extreme because it sits in its 95th
percentile of last-200-bar ATR, even though 0.79% is low absolute.

Same anti-pattern as Phase 17/18/19/20 dead-vetoes: a hard `return False`
where Phase 16/18 soft-multipliers already exist for the same concern.

Phase 22 fix:
  - Counter-trend (long in TRENDING_DOWN, short in TRENDING_UP):
    KEEP hard block — stronger evidence of bad edge
  - Volatile: SOFT multiplier ×0.4 by default (configurable)

  Two RISK config flags:
    regime_volatile_block_enabled = False  (was effectively True)
    regime_volatile_size_mult     = 0.4
"""
from __future__ import annotations

from pathlib import Path


def test_risk_config_has_regime_flags():
    from config import RISK
    assert RISK.get("regime_volatile_block_enabled") is False
    assert RISK.get("regime_volatile_size_mult") == 0.4


def test_volatile_no_longer_hard_blocks():
    """The regime gate code at bot_engine.py must NOT unconditionally
    return False on REGIME_VOLATILE. Soft path must be reachable."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Find the regime gate
    gate_start = src.index("Phase 22): Regime-aware gate")
    gate_end = src.index("# (b) Spot", gate_start)
    gate_block = src[gate_start:gate_end]
    # Volatile branch must check the flag, not unconditionally _block
    assert 'RISK.get("regime_volatile_block_enabled"' in gate_block
    # Soft path must be present
    assert "_regime_size_mult = float(" in gate_block


def test_soft_mult_applied_to_size_fraction():
    """The soft multiplier must be applied AFTER Phase 17 ev_mult and
    Phase 18 cal_mult, BEFORE notional computation."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    ev_idx = src.index("size_fraction *= _ev_mult")
    cal_idx = src.index("size_fraction *= _cal_mult")
    regime_idx = src.index("size_fraction *= _regime_size_mult")
    notional_idx = src.index("notional = mtype_bal * size_fraction")
    assert ev_idx < cal_idx < regime_idx < notional_idx, \
        "expected order: ev → cal → regime → notional"


def test_counter_trend_still_hard_blocks():
    """Counter-trend (long/down or short/up) must remain hard block."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    gate_start = src.index("Phase 22): Regime-aware gate")
    gate_end = src.index("# (b) Spot", gate_start)
    gate_block = src[gate_start:gate_end]
    assert "regime:trending_down_blocks_long" in gate_block
    assert "regime:trending_up_blocks_short" in gate_block


def test_regime_size_mult_default_initialization():
    """`_regime_size_mult` must initialize to 1.0 BEFORE the regime gate,
    so the multiplier line never references an undefined variable when
    the regime detector errors out (e.g., exchange unavailable)."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    init_idx = src.index("_regime_size_mult = 1.0")
    gate_check_idx = src.index('if RISK.get("regime_volatile_block_enabled"')
    assert init_idx < gate_check_idx, \
        "_regime_size_mult must be initialized before regime check"


def test_regime_log_line_emitted_on_volatile_soft():
    """When volatile soft-multiplier engages, the log must say so."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Use ASCII substrings so test file encoding can't mismatch:
    assert "VOLATILE" in src
    assert "soft size" in src
    assert "regime soft-mult" in src
    assert "Phase 22" in src
