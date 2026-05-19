"""Patch #3 tests — mcp_take_profit path amplification.

Spec: docs/superpowers/specs/2026-05-18-bleed-fix-sprint-and-rebuild-design.md §5.

Plan deviations (declared upfront):
  - Spec §5.3 references `position.tp_price`, `position.entry_atr`,
    `position.target_pnl_pct`. The actual Position dataclass
    (core/position_tracker.py:117) has `take_profit` and `entry_price`
    only — no `tp_price`, no `entry_atr`, no `target_pnl_pct`. The
    proximity function falls back to:
      tp_price        → pos.take_profit
      entry_atr proxy → abs(take_profit - entry_price)
      target_pnl_pct  → (take_profit - entry_price) / entry_price
                        (sign-flipped for shorts)
      ema_aligned     → 0.5 neutral (no ema_aligned helper in mcp_brain).
  - These match Task 1's pattern (document deviation, keep function
    functional rather than silently returning 0).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

import config
from core import order_manager as om_mod
from core.mcp_brain import score_take_profit_proximity


def _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
              current_px=None, unrealized_pnl_pct=None,
              grace_until=None):
    """Build a minimal mock Position with the attributes the proximity
    function and `_try_soft_close` read. MagicMock supports arbitrary
    attribute writes, mirroring how the real `@dataclass Position`
    accepts dynamic attrs (verified via Task 1's `_ghost_reroute_count`).
    """
    pos = MagicMock()
    pos.entry_price = entry_price
    pos.take_profit = take_profit
    pos.side = side
    pos.symbol = "ATOM/USDT:USDT"
    if current_px is not None:
        pos.current_px = current_px
    else:
        # MagicMock auto-attrs would be a MagicMock instance, not None.
        # Delete so `getattr(pos, "current_px", None)` returns None.
        del pos.current_px
    if unrealized_pnl_pct is not None:
        pos.unrealized_pnl_pct = unrealized_pnl_pct
    else:
        del pos.unrealized_pnl_pct
    if grace_until is not None:
        pos._mcp_tp_grace_until = grace_until
    else:
        del pos._mcp_tp_grace_until
    return pos


# ──────────────────────────── proximity tests ────────────────────────────

def test_proximity_returns_zero_when_no_tp_price():
    """Guard against div-by-zero / missing TP. Spec §5.6 test #8."""
    pos = _make_pos(take_profit=None, current_px=104.0,
                    unrealized_pnl_pct=0.04)
    assert score_take_profit_proximity(pos) == 0.0


def test_proximity_high_when_close_to_tp():
    """Long position at 104 with TP=105, entry=100. Should score ~0.79.

    Math: dist_score=0.9 (1 - 0.2/2), pnl_score=0.8 (0.04/0.05),
    momentum=0.5 (no ema_aligned helper). Total = 0.45+0.24+0.10 = 0.79.
    """
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04)
    score = score_take_profit_proximity(pos)
    assert 0.7 < score < 0.85, f"expected ~0.79, got {score}"


# ─────────────────────── _try_soft_close behavior ────────────────────────

@pytest.fixture(autouse=True)
def reset_amplify_config(monkeypatch):
    """Restore defaults for every test via monkeypatch (auto-rolled back)."""
    monkeypatch.setattr(config, "MCP_TP_AMPLIFY_ENABLED", True,
                        raising=False)
    monkeypatch.setattr(config, "MCP_TP_PROXIMITY_THRESHOLD", 0.7,
                        raising=False)
    monkeypatch.setattr(config, "MCP_TP_GRACE_SEC", 1800, raising=False)
    yield


def test_proximity_below_threshold_closes_normally(monkeypatch):
    """Threshold 0.9 (impossibly high) → STALE fires immediately."""
    monkeypatch.setattr(config, "MCP_TP_PROXIMITY_THRESHOLD", 0.99,
                        raising=False)
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04)
    om = MagicMock()
    closed = {"called": False, "reason": None}

    def _close(reason):
        closed["called"] = True
        closed["reason"] = reason
        return True

    result = om_mod._try_soft_close(om, pos, "STALE", proceed_fn=_close)
    assert closed["called"] is True
    assert closed["reason"] == "STALE"
    assert result is True


def test_proximity_above_threshold_first_call_defers(monkeypatch):
    """Low threshold (0.3) → defers, sets grace, no close."""
    monkeypatch.setattr(config, "MCP_TP_PROXIMITY_THRESHOLD", 0.3,
                        raising=False)
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04)
    om = MagicMock()
    closed = {"called": False}

    def _close(reason):
        closed["called"] = True
        return True

    before = time.time()
    result = om_mod._try_soft_close(om, pos, "STALE", proceed_fn=_close)
    after = time.time()
    assert closed["called"] is False
    assert result is None
    # Grace was set to roughly now + 1800
    grace = getattr(pos, "_mcp_tp_grace_until", 0)
    assert before + 1800 - 5 <= grace <= after + 1800 + 5


def test_within_grace_window_still_defers(monkeypatch):
    """Grace already set to future → still defers."""
    monkeypatch.setattr(config, "MCP_TP_PROXIMITY_THRESHOLD", 0.3,
                        raising=False)
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04,
                    grace_until=time.time() + 1000)
    om = MagicMock()
    closed = {"called": False}

    def _close(reason):
        closed["called"] = True
        return True

    result = om_mod._try_soft_close(om, pos, "STALE", proceed_fn=_close)
    assert closed["called"] is False
    assert result is None


def test_grace_expired_closes_with_post_grace_suffix(monkeypatch):
    """Grace expired → close fires with `STALE_post_grace`."""
    monkeypatch.setattr(config, "MCP_TP_PROXIMITY_THRESHOLD", 0.3,
                        raising=False)
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04,
                    grace_until=time.time() - 10)
    om = MagicMock()
    closed = {"called": False, "reason": None}

    def _close(reason):
        closed["called"] = True
        closed["reason"] = reason
        return True

    result = om_mod._try_soft_close(om, pos, "STALE", proceed_fn=_close)
    assert closed["called"] is True
    assert closed["reason"] == "STALE_post_grace"
    assert result is True


def test_flag_off_preserves_original_behavior(monkeypatch):
    """MCP_TP_AMPLIFY_ENABLED=False → close fires immediately."""
    monkeypatch.setattr(config, "MCP_TP_AMPLIFY_ENABLED", False,
                        raising=False)
    # Use proximity-high inputs; flag off must short-circuit regardless.
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04)
    om = MagicMock()
    closed = {"called": False, "reason": None}

    def _close(reason):
        closed["called"] = True
        closed["reason"] = reason
        return True

    result = om_mod._try_soft_close(om, pos, "STALE", proceed_fn=_close)
    assert closed["called"] is True
    assert closed["reason"] == "STALE"
    assert result is True


def test_sl_path_unaffected():
    """SL close path (`stop_loss`, `sl_failed`, `sl_placement_failed`)
    is NEVER gated — fires immediately even with high proximity.

    Spec §5.6 test #5: invariant — SL must always close.
    """
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04)
    om = MagicMock()
    for reason in ("stop_loss", "sl_failed", "sl_placement_failed"):
        closed = {"called": False, "reason": None}

        def _close(reason_arg, _c=closed):
            _c["called"] = True
            _c["reason"] = reason_arg
            return True

        result = om_mod._try_soft_close(om, pos, reason, proceed_fn=_close)
        assert closed["called"] is True, (
            f"SL path {reason!r} should fire immediately")
        assert closed["reason"] == reason
        assert result is True


# ─────────────────────── regression-protection tests ─────────────────────


def test_proximity_short_side_sign_correct():
    """SHORT mirror of `test_proximity_high_when_close_to_tp`.

    Regression protection: `score_take_profit_proximity` has explicit
    side-aware sign logic at mcp_brain.py:3526-3529. If a future refactor
    inverts the sign convention for shorts, this test catches it.

    Short setup mirrors the long around entry=100:
      LONG : entry=100, tp=105, current=104  → 80% of the way to TP
      SHORT: entry=100, tp=95,  current=96   → 80% of the way to TP

    Both should produce ~0.79 (within 0.05 of each other).
    """
    long_pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                         current_px=104.0, unrealized_pnl_pct=0.04)
    long_score = score_take_profit_proximity(long_pos)

    # For SHORT: price moved from 100 → 96 = +4% PnL (price fell, short wins)
    # upnl_frac is the absolute fraction gain, sign matches the long side.
    short_pos = _make_pos(entry_price=100.0, take_profit=95.0, side="sell",
                          current_px=96.0, unrealized_pnl_pct=0.04)
    short_score = score_take_profit_proximity(short_pos)

    assert abs(long_score - short_score) < 0.05, (
        f"SHORT side ({short_score:.3f}) should mirror LONG "
        f"({long_score:.3f}) within 0.05 — sign-flip regression?")
    # Both should be in the 'close to TP' band.
    assert 0.7 < short_score < 0.85, (
        f"SHORT score {short_score:.3f} outside expected ~0.79 band")


def test_post_grace_suffix_preserved_when_proximity_drops(monkeypatch):
    """Grace previously set, then proximity dropped below threshold.

    Without the fix at order_manager.py:2742, the close would fire with
    the bare `soft_reason` ("STALE"), making it invisible to
    sprint_kpi.py's `_post_grace` attribution counter. The fix detects
    `_mcp_tp_grace_until > 0` even on the below-threshold branch and
    emits the suffix.

    Setup: grace_until = (past) AND current proximity below threshold
    (high threshold 0.99 forces the < branch). This simulates a position
    that crossed the proximity bar earlier, then the market moved away.
    """
    # Force the proximity < threshold branch by setting threshold > any
    # achievable score for this synthetic position.
    monkeypatch.setattr(config, "MCP_TP_PROXIMITY_THRESHOLD", 0.99,
                        raising=False)
    pos = _make_pos(entry_price=100.0, take_profit=105.0, side="buy",
                    current_px=104.0, unrealized_pnl_pct=0.04,
                    grace_until=time.time() - 60)
    om = MagicMock()
    closed = {"called": False, "reason": None}

    def _close(reason):
        closed["called"] = True
        closed["reason"] = reason
        return True

    result = om_mod._try_soft_close(om, pos, "STALE", proceed_fn=_close)
    assert closed["called"] is True
    assert closed["reason"] == "STALE_post_grace", (
        f"Expected `_post_grace` suffix preserved when grace was set "
        f"and proximity then dropped — got {closed['reason']!r}")
    assert result is True
