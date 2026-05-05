"""Phase 34 — drawdown calc against realized-only peak (2026-05-05).

User flagged "HALTED again" at 17:13 today. Trace:
  17:13:56  DRAWDOWN 20.6% from peak $713.64 — Paused
  17:32:32  AUTO-RESUMED via WR-recovery path
  17:49     halted AGAIN — same phantom peak $713.64

Investigation: the realized starting balance was $568.32. Today's
realized daily_pnl was +$0.48. Realized capital: ~$568.80. But
peak_balance reached $713.64 — that's $145 above realized.

Source of the phantom: `update_current_balance` was bumping peak
from the wallet equity reading, which includes unrealized P&L from
open positions. A favorable intra-trade swing pushed peak past
realized capital. When unrealized reverted, drawdown computed
20.6% off the inflated peak even though no realized capital had
been lost.

`record_trade_pnl` already used realized-only:
  effective = start_balance + daily_pnl
  if effective > peak: peak = effective

Phase 34 makes this the ONLY peak-update path. update_current_balance
keeps its flake guards but no longer advances peak. Same fix in
set_start_balance for the same-day-restart branch.

Effect: drawdown is computed realized-vs-realized. Halts now reflect
actual money lost, not unrealized round-trips.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def test_update_current_balance_does_not_advance_peak():
    """The bug we're fixing: equity reading > peak should NOT advance
    peak via update_current_balance. Peak advances only via realized
    PnL bookings in record_trade_pnl."""
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._peak_balance = 100.0
    r._start_balance = 100.0
    # Pretend an open position briefly mark-to-market'd at +50 unrealized
    r.update_current_balance(150.0)
    # Peak must NOT have moved
    assert r._peak_balance == 100.0


def test_record_trade_pnl_does_advance_peak_on_win():
    """The realized path still works: a closed winning trade should
    advance peak by the booked PnL."""
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._start_balance = 100.0
    r._peak_balance = 100.0
    r._daily_pnl = 0.0
    # Book a +5 win — realized
    r.record_trade_pnl(5.0, 100.0, is_win=True, pnl_pct=0.05)
    # peak should now reflect realized: 100 + 5 = 105
    assert r._peak_balance == 105.0


def test_unrealized_reversal_does_not_trip_drawdown():
    """End-to-end: equity spikes to peak via update_current_balance,
    then reverses. With the old code the spike would have set peak
    to 200, then the revert to 110 would compute 45% drawdown. With
    Phase 34 the peak stays at start_balance (100), and current
    realized 100 + 0 = 100, drawdown = 0%."""
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._start_balance = 100.0
    r._peak_balance = 100.0
    r._daily_pnl = 0.0
    # Equity spikes (open position briefly profitable)
    r.update_current_balance(200.0)
    assert r._peak_balance == 100.0  # unchanged
    # Equity reverts
    r.update_current_balance(110.0)
    assert r._peak_balance == 100.0  # still unchanged
    # Now book a small realized loss
    r.record_trade_pnl(-1.0, 100.0, is_win=False, pnl_pct=-0.01)
    # Effective = 100 + -1 = 99. Peak = 100. DD = 1% — NOT halt-worthy
    # (max_drawdown_pct typically 8%).
    assert r._halted is False


def test_set_start_balance_same_day_does_not_inflate_peak():
    """Same-day restart with current balance > peak should NOT
    advance peak — that read could be inflated by open-position
    unrealized. Peak is realized-only."""
    from datetime import date
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._trading_day = date.today()
    r._peak_balance = 100.0
    r._start_balance = 0.0  # not yet set this session
    # Bot restarts and fetches balance: $150 (includes unrealized)
    r.set_start_balance(150.0)
    # Peak must NOT have been bumped to 150
    assert r._peak_balance == 100.0


def test_flake_guards_still_active_in_update_current_balance():
    """Phase 34 removed peak-bump but kept _last_balance_seen and the
    30%-drop guard. Verify."""
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._last_balance_seen = 100.0
    # 50% drop should be rejected (flake)
    r.update_current_balance(50.0)
    # _last_balance_seen still at 100 (drop rejected)
    assert r._last_balance_seen == 100.0


def test_phase34_marker_present():
    # Resolve absolute path so the autouse tmp_path fixture doesn't
    # break the file read.
    repo = Path(__file__).resolve().parents[1]
    src = (repo / "core" / "risk_manager.py").read_text(encoding="utf-8")
    assert "Phase 34" in src
    assert "peak_balance is NO LONGER bumped" in src
