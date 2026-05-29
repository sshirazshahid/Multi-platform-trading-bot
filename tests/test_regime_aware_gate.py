"""Phase 16 — regime-aware entry gate (2026-05-03).

Blocks entries when market regime contradicts the signal:
  - long  in TRENDING_DOWN regime → skip
  - short in TRENDING_UP regime   → skip
  - any signal in VOLATILE regime → skip
RANGING / TRENDING-aligned regime → allow (no change).
"""
from __future__ import annotations

from pathlib import Path


def test_regime_gate_present_in_bot_engine():
    """Source contains the regime-block branch in _execute_open."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "Regime-aware gate" in src
    assert "REGIME_VOLATILE" in src
    assert "REGIME_TRENDING_DOWN" in src
    assert "REGIME_TRENDING_UP" in src


def test_regime_gate_blocks_long_in_downtrend():
    """Source-level: the long-side block matches buy + TRENDING_DOWN."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "side == \"buy\" and regime == REGIME_TRENDING_DOWN" in src


def test_regime_gate_blocks_short_in_uptrend():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "side == \"sell\" and regime == REGIME_TRENDING_UP" in src


def test_regime_gate_blocks_volatile_for_any_side():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "regime == REGIME_VOLATILE" in src


def test_regime_gate_handles_detector_failure():
    """If detector raises, gate must default to ALLOW (don't block live trades)."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # The except branch must log + fall through (return False NOT triggered)
    assert "[Regime] check skipped" in src


def test_regime_gate_writes_skip_reason_to_warehouse():
    """When blocked, the candidate row gets decision=SKIP + reason."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "UPDATE candidates SET decision='SKIP'" in src
    assert "skip_reason=? WHERE id=?" in src


def test_regime_detector_lazy_init():
    """Detector instantiated lazily (first call) and cached on bot_engine."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_regime_detector" in src
    assert "MarketRegimeDetector()" in src


def test_regime_gate_runs_before_blacklist():
    """Source order: regime gate must precede the static blacklist check."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    regime_idx = src.index("Regime-aware gate")
    bl_idx = src.index("Symbol blacklist — evidence-based hard block")
    assert regime_idx < bl_idx


def test_market_regime_module_importable():
    from core.market_regime import (
        REGIME_RANGING,
        REGIME_TRENDING_DOWN,
        REGIME_TRENDING_UP,
        REGIME_VOLATILE,
        MarketRegimeDetector,
    )
    assert MarketRegimeDetector is not None
    assert REGIME_TRENDING_UP and REGIME_TRENDING_DOWN
    assert REGIME_VOLATILE and REGIME_RANGING
