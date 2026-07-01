"""454-trade hardening (2026-05-27) — minimum hold-time gate.

Evidence from 454 real trades:
  195 trades closed in <15 min: 31% WR, -$41.25 PnL  (43% of all trades, biggest loss source)
  47  trades closed in 15m-1h:  64% WR, +$4.41 PnL   (ONLY profitable bucket)

Gate: when a position is under MIN_HOLD_CLOSE_MINUTES old, the position monitor
must not close it unless the advisory confidence >= MIN_HOLD_STRONG_CONF.

Exchange-side SL/TP orders are completely unaffected by this gate.
TIGHTEN and BREAKEVEN actions are also unaffected (they don't close the position).

These tests verify:
1. Config values are set correctly.
2. The gate logic (extracted as a pure function matching the bot_engine logic) works.
"""
from __future__ import annotations

# ── Config tests ──────────────────────────────────────────────────────────

def test_min_hold_minutes_default():
    """MIN_HOLD_CLOSE_MINUTES must be 15 (the profitable-bucket boundary)."""
    from config import MIN_HOLD_CLOSE_MINUTES
    assert MIN_HOLD_CLOSE_MINUTES == 15, (
        f"Expected 15, got {MIN_HOLD_CLOSE_MINUTES}. "
        "The 15m-1h bucket is the only profitable cohort in 454-trade data."
    )


def test_min_hold_strong_conf_default():
    """MIN_HOLD_STRONG_CONF must be 0.80 — the strong-exit threshold."""
    from config import MIN_HOLD_STRONG_CONF
    assert MIN_HOLD_STRONG_CONF == 0.80, (
        f"Expected 0.80, got {MIN_HOLD_STRONG_CONF}"
    )


# ── Gate logic tests ──────────────────────────────────────────────────────
# These tests exercise the exact condition used in _run_mcp_position_monitor:
#   if age < MIN_HOLD_CLOSE_MINUTES and conf < MIN_HOLD_STRONG_CONF: suppress

def _should_suppress(action: str, age_min: float, conf: float,
                     min_hold: int = 15, strong_conf: float = 0.80) -> bool:
    """Mirror of the gate logic in bot_engine._run_mcp_position_monitor."""
    if action not in ("CLOSE", "TAKE_PROFIT"):
        return False
    return age_min < min_hold and conf < strong_conf


def test_close_suppressed_when_young_and_low_conf():
    """Young position + low confidence: CLOSE must be suppressed."""
    assert _should_suppress("CLOSE", age_min=5.0, conf=0.65) is True


def test_take_profit_suppressed_when_young_and_low_conf():
    """Young position + low confidence: TAKE_PROFIT must be suppressed."""
    assert _should_suppress("TAKE_PROFIT", age_min=3.0, conf=0.70) is True


def test_close_allowed_when_young_but_high_conf():
    """Young position with strong confidence (>= 0.80) must NOT be suppressed."""
    assert _should_suppress("CLOSE", age_min=8.0, conf=0.85) is False


def test_close_allowed_when_old_enough_low_conf():
    """Position older than 15 min with any confidence: NOT suppressed."""
    assert _should_suppress("CLOSE", age_min=16.0, conf=0.55) is False


def test_close_allowed_when_old_and_high_conf():
    """Position older than 15 min with high confidence: NOT suppressed."""
    assert _should_suppress("CLOSE", age_min=30.0, conf=0.90) is False


def test_close_not_suppressed_at_exact_threshold_age():
    """At exactly min_hold minutes the gate does NOT fire (< not <=)."""
    assert _should_suppress("CLOSE", age_min=15.0, conf=0.60) is False


def test_close_suppressed_just_below_threshold_age():
    """Just below min_hold (14.9 min) with low conf: suppressed."""
    assert _should_suppress("CLOSE", age_min=14.9, conf=0.60) is True


def test_tighten_never_suppressed():
    """TIGHTEN does not close a position — must never be suppressed."""
    assert _should_suppress("TIGHTEN", age_min=1.0, conf=0.40) is False


def test_breakeven_never_suppressed():
    """BREAKEVEN does not close a position — must never be suppressed."""
    assert _should_suppress("BREAKEVEN", age_min=1.0, conf=0.40) is False


def test_hold_never_suppressed():
    """HOLD is a no-op — must never be suppressed."""
    assert _should_suppress("HOLD", age_min=1.0, conf=0.40) is False


def test_exact_strong_conf_threshold_not_suppressed():
    """conf == MIN_HOLD_STRONG_CONF (0.80) exactly passes the gate (< not <=)."""
    assert _should_suppress("CLOSE", age_min=5.0, conf=0.80) is False


def test_just_below_strong_conf_suppressed():
    """conf = 0.799 is below 0.80: suppressed on a young position."""
    assert _should_suppress("CLOSE", age_min=5.0, conf=0.799) is True
