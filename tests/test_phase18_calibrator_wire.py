"""Phase 18 — ProbabilityCalibrator wire-fix (2026-05-04).

Sister fix to Phase 17. Ruflo MCP-calibration audit found two
compounding dead-code bugs:

1. The calibrator's record() feed was broken — positions.json never
   persisted `confidence`, so calibrator.record() either was never
   called or always received 0. Result: calibrator never trained.

2. The calibrator's calibrate()/should_trade() output was unused —
   no live caller in mcp_brain.py / bot_engine.py / order_manager.py.
   Test suite itself flagged it: "no live caller of should_trade".

This phase wires both ends:

  Feed:    Position(confidence=mcp_score/100), persisted by tracker.
           order_manager._finalize_close calls calibrator.record() with
           the closed position's stored confidence + actual_win.

  Output:  bot_engine._execute_open calls calibrator.calibrate() on
           the raw entry confidence and applies a [0.7, 1.3] symmetric
           soft size multiplier when calibrated diverges from raw.
           Per UNBLOCK_ALL — never gates, never zeroes.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

import inspect
from pathlib import Path

# ─── Position dataclass has confidence ────────────────────────────────


def test_position_has_confidence_field():
    from core.position_tracker import Position
    p = Position(
        id="t1", exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        market_type="futures", strategy="claude_portfolio",
        entry_price=70000, size=0.001, stop_loss=69000, take_profit=72000,
    )
    assert hasattr(p, "confidence")
    assert p.confidence == 0.0


def test_position_confidence_round_trip():
    from core.position_tracker import Position
    p = Position(
        id="t1", exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        market_type="futures", strategy="claude_portfolio",
        entry_price=70000, size=0.001, stop_loss=69000, take_profit=72000,
        confidence=0.72,
    )
    assert p.confidence == 0.72


# ─── OrderManager builds Position with confidence ─────────────────────


def test_open_position_passes_mcp_score_as_confidence():
    """The Position constructor in order_manager.open_position must
    receive confidence = mcp_score / 100 when mcp_score is given."""
    from tests.order_manager_source import order_manager_impl_source

    src = order_manager_impl_source()
    # The Position() constructor must have confidence= argument
    pos_block = src[src.index("# Build Position first"):
                    src.index("# Build Position first") + 1500]
    assert "confidence=_conf_in" in pos_block
    # And _conf_in must be derived from mcp_score
    assert "_conf_in = mcp_score / 100.0" in pos_block


# ─── OrderManager has lazy calibrator + records on close ──────────────


def test_order_manager_has_calibrator_property():
    from core.order_manager import OrderManager
    assert hasattr(OrderManager, "calibrator")
    src = inspect.getsource(OrderManager.calibrator.fget)
    assert "ProbabilityCalibrator" in src


def test_finalize_close_calls_calibrator_record():
    from tests.order_manager_source import order_manager_impl_source

    src = order_manager_impl_source()
    fc_start = src.index("def _finalize_close(self, pos: Position")
    fc_end = src.index("def ", fc_start + 50)  # next def
    fc_block = src[fc_start:fc_end]
    # Must reference calibrator.record with predicted_conf + actual_win
    assert "self.calibrator.record(" in fc_block
    assert "predicted_conf=_conf" in fc_block
    assert "actual_win=is_win" in fc_block


def test_finalize_close_skips_zero_confidence():
    """Trades from non-Claude-portfolio paths have confidence=0 — must
    skip calibrator.record() to avoid poisoning the bucket with junk."""
    from tests.order_manager_source import order_manager_impl_source

    src = order_manager_impl_source()
    fc_start = src.index("def _finalize_close(self, pos: Position")
    fc_end = src.index("def ", fc_start + 50)
    fc_block = src[fc_start:fc_end]
    # The guard must be present
    assert "if _conf > 0 and self.calibrator is not None" in fc_block


# ─── BotEngine applies calibrator soft multiplier in _execute_open ────


def test_execute_open_calls_calibrator_calibrate():
    src = bot_engine_source_for_grep()
    assert "self.order_mgr.calibrator.calibrate(" in src
    # Must apply as a multiplier in [0.7, 1.3]
    assert "max(0.7, min(1.3," in src
    # Log line must mention the calibrator
    assert "(calibrator: raw=" in src


def test_calibrator_multiplier_runs_after_adaptive_sizing():
    """Order matters: adaptive sizing applies first, then calibrator
    soft multiplier on top. Both write to size_fraction before notional."""
    src = bot_engine_source_for_grep()
    ev_idx = src.index("size_fraction *= _ev_mult")
    cal_idx = src.index("size_fraction *= _cal_mult")
    notional_idx = src.index("notional = mtype_bal * size_fraction")
    assert ev_idx < cal_idx < notional_idx, \
        "expected order: adaptive sizing → calibrator multiplier → notional"


# ─── Calibrator integration — actually call record + calibrate ────────


def test_calibrator_record_and_calibrate_round_trip(tmp_path, monkeypatch):
    """Sanity: feeding outcomes via record() builds bucket counts that
    calibrate() reads back. This is the loop Phase 18 unblocks."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.probability_calibrator import ProbabilityCalibrator
    cal = ProbabilityCalibrator()

    # Simulate 30 trades at predicted_conf=0.7, but only 50% win
    # (overconfident — calibrator should pull this down toward 0.5)
    for i in range(30):
        cal.record(predicted_conf=0.70,
                   actual_win=(i % 2 == 0),
                   strategy="claude_portfolio")

    out = cal.calibrate(0.70, "claude_portfolio")
    # With 30 samples in 0.7 bucket and ~50% wins, calibrated should
    # be near 0.5 (within ±0.10)
    assert 0.40 <= out <= 0.60, \
        f"calibrator should pull overconfidence down, got {out}"
