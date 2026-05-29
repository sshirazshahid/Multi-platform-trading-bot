"""Phase 26A — winner age-lock (2026-05-05).

Trade audit on 267 closed trades found:

  Hold time vs net PnL bucket
  --------------------------------
  <10min     n=42  sum=$-2.11   wr=43%
  10-30min   n=18  sum=$-0.04   wr=61%
  30-60min   n=33  sum=+$1.79   wr=55%   ← sweet spot
  1-2h       n=58  sum=$-5.79   wr=38%
  2-4h       n=58  sum=$-18.74  wr=35%
  4h+        n=58  sum=$-41.71  wr=40%   ← 63% of total bleed

The 4h+ bucket alone accounts for 63% of total $-66 lifetime bleed.
Phase 14 AGE rules cut LOSERS at 1.25h, but WINNERS held >2h that
later reverse to a loss aren't protected. The default lock fraction
(65% on small wins) leaves 35% of peak profit on the table — enough
slack for a reversal to wipe the entire move.

Phase 26A: when a position has been open >120 min AND is in profit
(peak_pnl > 0), ratchet `_lock_fraction` to 0.95. Any small adverse
move locks in 95% of peak gain. Targets the structural "MCP monitor
exits on weakness with worse price" pattern (31 mcp_brain_close
losses for $-21 in the audit).

Doesn't touch entry logic, doesn't change activation, doesn't conflict
with Phase 14 AGE rules. Pure exit-policy improvement.
"""
from __future__ import annotations

import time as _t
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── Lock fraction returns 0.95 when position age >= 120min ───────────


def test_lock_fraction_takes_optional_position_arg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    # Backwards-compatible: no position arg -> default behaviour
    out = tsm._lock_fraction(0.02)
    assert 0.0 <= out <= 1.0
    # Default at small win = 0.65
    assert out == pytest.approx(0.65)


def test_lock_fraction_overrides_to_0_95_at_2h_in_profit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    pos = MagicMock()
    pos.open_time = _t.time() - 130 * 60  # 130 min ago = 2.17h
    out = tsm._lock_fraction(0.01, pos)
    assert out == 0.95, f"expected 95% lock at 130min in profit, got {out}"


def test_lock_fraction_no_override_under_2h(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    pos = MagicMock()
    pos.open_time = _t.time() - 110 * 60  # 110 min — under threshold
    out = tsm._lock_fraction(0.01, pos)
    assert out == pytest.approx(0.65), \
        f"expected default 65% lock under 2h, got {out}"


def test_lock_fraction_no_override_when_not_in_profit(tmp_path, monkeypatch):
    """If peak_pnl <= 0, the position has nothing locked — no point ratcheting."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    pos = MagicMock()
    pos.open_time = _t.time() - 130 * 60
    out = tsm._lock_fraction(0.0, pos)
    # Falls through to default — 65% for small peak
    assert out == pytest.approx(0.65)


def test_lock_fraction_handles_missing_open_time(tmp_path, monkeypatch):
    """No open_time on position object → fall through, no crash."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    pos = MagicMock(spec=[])  # no attrs
    # Should not raise
    out = tsm._lock_fraction(0.01, pos)
    assert 0.0 <= out <= 1.0


# ─── Call sites pass position ─────────────────────────────────────────


def test_call_sites_pass_position_to_lock_fraction():
    """Both BUY and SELL update paths now pass position so the age
    override can fire."""
    src = Path("core/trailing_stop_manager.py").read_text(encoding="utf-8")
    # Two call sites in update() — both must pass position
    occurrences = src.count("self._lock_fraction(peak_pnl, position)")
    assert occurrences == 2, \
        f"expected 2 call sites passing position, got {occurrences}"


def test_lock_fraction_threshold_constant_present():
    """The 120-min threshold is the documented design — encoded in source."""
    src = Path("core/trailing_stop_manager.py").read_text(encoding="utf-8")
    assert "age_min >= 120" in src
    assert "0.95" in src
    assert "Phase 26A" in src


# ─── Behavioural integration: full update() path with 2h+ position ────


def test_active_buy_at_2h_uses_95pct_lock(tmp_path, monkeypatch):
    """End-to-end: a BUY position activated 2h ago at +1% peak should
    have its lock_sl computed at 95% of peak_pnl, not the default 65%.
    Pre-warm tracking to simulate a position that already saw the peak."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    pos = MagicMock()
    pos.id = "test-buy-2h"
    pos.symbol = "ETH/USDT:USDT"
    pos.side = "buy"
    pos.entry_price = 100.0
    pos.market_type = "futures"
    pos.atr_pct = 0.0
    pos.open_time = _t.time() - 150 * 60  # 2.5h
    pos.stop_loss = 99.0
    # Pre-warm: position already peaked at +1% and is active
    tsm._tracking[pos.id] = {
        "peak": 101.0, "trough": 100.0,
        "active": True, "peak_pnl": 0.01,
        "symbol": pos.symbol, "breakeven": 100.10,
    }
    # Now process a tick at 100.99 — should NOT close (above 95% lock)
    closed, reason, new_sl = tsm.update(pos, 100.99)
    # 95% lock on +1% peak = 100 × (1 + 0.01 × 0.95) = 100.95
    expected_lock = 100.0 * (1 + 0.01 * 0.95)
    assert new_sl >= expected_lock - 0.001, \
        f"new_sl {new_sl} should be at least {expected_lock} (95% lock)"


def test_active_buy_under_2h_uses_default_lock(tmp_path, monkeypatch):
    """Under 2h, the same scenario should use 65% lock (default)."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.trailing_stop_manager import TrailingStopManager
    tsm = TrailingStopManager()
    pos = MagicMock()
    pos.id = "test-buy-1h"
    pos.symbol = "ETH/USDT:USDT"
    pos.side = "buy"
    pos.entry_price = 100.0
    pos.market_type = "futures"
    pos.atr_pct = 0.0
    pos.open_time = _t.time() - 60 * 60  # 1h
    pos.stop_loss = 99.0
    # Pre-warm tracking just like the 2h test for parity
    tsm._tracking[pos.id] = {
        "peak": 101.0, "trough": 100.0,
        "active": True, "peak_pnl": 0.01,
        "symbol": pos.symbol, "breakeven": 100.10,
    }
    closed, reason, new_sl = tsm.update(pos, 100.99)
    # SL should NOT have reached 100.95 (95% floor) since we're under 2h.
    # Default 65% lock = 100 × (1 + 0.01 × 0.65) = 100.65
    expected_95_lock = 100.0 * (1 + 0.01 * 0.95)  # 100.95
    assert new_sl < expected_95_lock, \
        f"new_sl {new_sl} should be below 95% floor under 2h"
