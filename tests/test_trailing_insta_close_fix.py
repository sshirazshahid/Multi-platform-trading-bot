"""Regression tests — 2026-07-07 trailing insta-close fix.

The Phase 15.3 age-aware activation drop (65min -> ~0 threshold) was
calibrated against a 75-min AGE_LIMIT that has been 4h (and loss-only) since
Phase 14. Result: any 65min+ position that had EVER ticked above entry
activated trailing on a ~0 threshold; the breakeven floor then placed the SL
above the current price and the SAME update market-closed the position at a
guaranteed small loss, booked as "trailing_stop". 30d warehouse: 161 of 258
trailing exits (6% win rate, -$52) were this mechanism — it pre-empted both
TP and SL on near-flat positions.

Fix: (1) the stale age tiers are removed from _adaptive_activation;
(2) activation additionally requires the CURRENT price to be past breakeven,
so a historical peak (persisted across a restart, or exposed by a live
threshold edit) can never arm an instant breakeven-floor close.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mgr(tmp_path, monkeypatch):
    import core.trailing_stop_manager as tsm
    monkeypatch.setattr(tsm, "PEAKS_FILE", tmp_path / "peaks.json")
    monkeypatch.setattr(tsm, "PARAMS_FILE", tmp_path / "params.json")
    m = tsm.TrailingStopManager()
    m.enabled = True
    m._tracking = {}
    return m


def _pos(entry=100.0, sl=97.0, side="buy", age_min=70.0):
    return SimpleNamespace(
        id="p1", symbol="TEST/USDT:USDT", side=side, entry_price=entry,
        stop_loss=sl, market_type="futures", atr_pct=0.0,
        open_time=time.time() - age_min * 60.0,
    )


def test_aged_flat_position_is_not_insta_closed(tmp_path, monkeypatch):
    """A 65min+ near-flat position (peaked +0.05% once, now slightly down)
    must NOT be market-closed by trailing — its real SL/TP stay in charge."""
    m = _mgr(tmp_path, monkeypatch)
    pos = _pos(age_min=70.0)
    closed, reason, _sl = m.update(pos, 100.05)   # tiny historical peak
    assert not closed, f"insta-closed on the peak update via {reason!r}"
    closed, reason, _sl = m.update(pos, 99.90)    # now slightly down
    assert not closed, f"aged flat position insta-closed via {reason!r}"


def test_activation_requires_price_above_breakeven(tmp_path, monkeypatch):
    """A threshold crossed by a HISTORICAL peak while the price sits below
    breakeven must not activate trailing (the be-floor would close it on the
    same update). Covers restart-persisted peaks and live threshold edits."""
    m = _mgr(tmp_path, monkeypatch)
    pos = _pos(age_min=10.0)
    # Simulate a persisted/historical peak >= threshold (e.g. after restart).
    m._tracking[pos.id] = {
        "peak": 102.0, "trough": 102.0, "active": False,
        "peak_pnl": 0.02, "symbol": pos.symbol, "breakeven": 100.2,
    }
    closed, _reason, _sl = m.update(pos, 99.8)  # price below breakeven now
    assert not closed
    assert not m._tracking[pos.id]["active"]


def test_real_time_activation_and_lock_still_work(tmp_path, monkeypatch):
    """Price rising through the threshold in real time still activates, and a
    later give-back still closes at a net-positive lock (designed behavior)."""
    m = _mgr(tmp_path, monkeypatch)
    pos = _pos(age_min=10.0)
    closed, _, _ = m.update(pos, 100.0)
    assert not closed
    closed, _, _ = m.update(pos, 102.5)   # +2.5% — above any adaptive threshold
    assert not closed
    assert m._tracking[pos.id]["active"]
    closed, reason, sl = m.update(pos, 100.0)  # full give-back
    assert closed and reason == "trailing_stop"
    assert sl > pos.entry_price  # breakeven guarantee held


def test_short_side_mirror_guard(tmp_path, monkeypatch):
    """Sell positions: no activation while price is above breakeven."""
    m = _mgr(tmp_path, monkeypatch)
    pos = _pos(entry=100.0, sl=103.0, side="sell", age_min=70.0)
    closed, reason, _sl = m.update(pos, 99.95)   # tiny favorable tick
    assert not closed
    closed, reason, _sl = m.update(pos, 100.10)  # back above entry
    assert not closed, f"aged flat short insta-closed via {reason!r}"
