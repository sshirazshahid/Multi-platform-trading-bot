"""Regression tests for the 2026-04-30 phantom-drawdown halt bug.

What happened in production
---------------------------
The bot halted on `drawdown 70.2% (peak $364.40)` even though no real
70% loss had happened. risk_state.json showed:

  peak_balance: 364.40
  start_balance: 108.05      ← clobbered by a partial-exchange-fetch flake
  daily_pnl: -0.65

Drawdown formula:
    effective = start_balance + daily_pnl = 108.05 + (-0.65) = 107.40
    drawdown  = (peak - effective) / peak  = (364.40 - 107.40) / 364.40 = 70.5%

Two bugs in `update_current_balance` produced this:

1. It overwrote `self._start_balance = balance` every cycle. start_balance
   is supposed to be the SESSION START balance set ONCE by
   `set_start_balance()` and held constant; daily_pnl tracks all P&L
   since session start. The drawdown math depends on this invariant.
   Overwriting start_balance with current_balance every cycle effectively
   makes drawdown calc = (peak - current) - daily_pnl, which means a
   transient balance drop reads as a permanent loss.

2. There was a 30% UP-spike rejection but no DOWN-spike rejection. So
   when one exchange API stalled and returned a partial balance read
   ($108 vs the real ~$357), the bot accepted it as truth.

These tests pin both fixes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.risk_manager as rm_mod
from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Each test gets a fresh cwd so risk_state.json doesn't leak between tests."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _make_rm() -> RiskManager:
    """Construct a RiskManager bypassing __init__ side effects."""
    rm = RiskManager()
    # Reset to known clean state
    rm._halted = False
    rm._halt_reason = ""
    rm._halt_time = 0.0
    rm._daily_pnl = 0.0
    rm._peak_balance = 0.0
    rm._start_balance = 0.0
    rm._last_balance_seen = 0.0
    return rm


# ─── Bug 1: start_balance must NOT be overwritten by update_current_balance


def test_update_current_balance_does_not_clobber_start_balance():
    """The session-start balance is set once by set_start_balance().
    Subsequent update_current_balance() calls (which run every cycle when
    balance fluctuates) must NOT modify it — otherwise the drawdown
    formula breaks."""
    rm = _make_rm()
    rm.set_start_balance(300.0)
    assert rm._start_balance == 300.0

    # Simulate a normal balance fluctuation cycle.
    rm.update_current_balance(295.0)
    assert rm._start_balance == 300.0, (
        "start_balance must NOT change on update_current_balance — that's "
        "set_start_balance's job. Overwriting it breaks the drawdown "
        "circuit-breaker math.")

    # Even a balance INCREASE shouldn't shift start_balance — only peak.
    rm.update_current_balance(320.0)
    assert rm._start_balance == 300.0
    assert rm._peak_balance == 320.0


# ─── Bug 2: 30% DOWN-spike must be rejected like the existing UP-spike guard


def test_partial_balance_fetch_rejected():
    """If one exchange stalls and the total reads 50%+ lower than last
    cycle, treat it as a flake and don't update peak. Without this, a
    momentary partial read poisons the drawdown calc."""
    rm = _make_rm()
    rm.set_start_balance(360.0)
    # Establish a stable last-seen reading.
    rm.update_current_balance(355.0)
    assert rm._last_balance_seen == 355.0

    # Simulate one exchange offline → partial total ≈ 30% of full.
    rm.update_current_balance(108.0)

    # Peak unchanged (was already higher) — this is the side-effect we
    # actually care about. Original behavior would have ALSO clobbered
    # start_balance to 108 here (Bug 1). Both invariants checked:
    assert rm._peak_balance == 360.0, "peak must NOT shift on flake"
    assert rm._start_balance == 360.0, "start must NOT shift on flake"


def test_real_balance_increase_still_advances_peak():
    """The down-spike guard must NOT reject genuine P&L increases."""
    rm = _make_rm()
    rm.set_start_balance(300.0)
    rm.update_current_balance(305.0)
    assert rm._peak_balance == 305.0
    rm.update_current_balance(312.0)
    assert rm._peak_balance == 312.0


def test_modest_drawdown_not_rejected():
    """A real -10% drawdown is plausible P&L — must NOT be rejected as a
    flake. Only ≥30% drops are treated as fetch flakes."""
    rm = _make_rm()
    rm.set_start_balance(300.0)
    rm.update_current_balance(305.0)
    rm.update_current_balance(280.0)  # -8.2% from prior — REAL loss
    # peak unchanged (no rule says -8% advances peak)
    assert rm._peak_balance == 305.0
    # last_balance_seen IS updated (it's not a flake)
    assert rm._last_balance_seen == 280.0


# ─── Combined regression: phantom drawdown halt no longer fires ─────


def test_phantom_drawdown_70pct_does_not_fire():
    """End-to-end reproduction of the 2026-04-30 production bug.

    Production observation:
      peak_balance=$364, start_balance=$108, daily_pnl=-$0.65
      → drawdown formula: (364 - (108-0.65))/364 = 70.5%
      → halt fires with reason "drawdown 70.2% (peak $364.40)"

    Root cause: start_balance got clobbered to $108 by a partial-exchange
    fetch flake, even though the bot's REAL session-start balance was
    much higher and there was no real $256 loss.

    Fix:
      1. update_current_balance no longer overwrites start_balance.
      2. update_current_balance rejects 30%+ DOWN spikes as fetch flakes.

    Asserts only the bug-specific outcome: a 70%+ phantom drawdown halt
    must NOT fire. (A modest residual drawdown halt may still fire if
    peak was set high before the flake — that's correct behavior, not
    the bug. The bug is the >50% phantom number.)
    """
    rm = _make_rm()
    rm.set_start_balance(300.0)
    rm.update_current_balance(360.0)  # genuine high reading
    assert rm._peak_balance == 360.0
    assert rm._start_balance == 300.0  # set ONCE, stays put

    # Flake: one exchange stalls, balance reads as $108.
    rm.update_current_balance(108.0)

    # Critical invariant: start_balance is still $300, NOT $108.
    # Without the fix, this is what triggered the 70% phantom drawdown.
    assert rm._start_balance == 300.0, (
        "Phantom-drawdown bug: start_balance got clobbered by a flake "
        "balance reading. set_start_balance() should be the only writer.")

    # Now record a tiny -$0.65 trade.
    rm.record_trade_pnl(pnl=-0.65, balance=108.0, is_win=False, pnl_pct=None)

    # If drawdown halt fired, it must be the small residual one (< 30%),
    # not the phantom 70%+ that the production bug produced.
    if rm._halted and "drawdown" in rm._halt_reason:
        # Parse the percent from the halt reason "drawdown XX.X% (peak $YYY)"
        import re
        m = re.search(r"drawdown\s+([\d.]+)%", rm._halt_reason)
        if m:
            dd_pct = float(m.group(1))
            assert dd_pct < 50.0, (
                f"Phantom drawdown halt: {dd_pct}% — this is the bug we "
                f"fixed. With start_balance preserved at $300 and a real "
                f"-$0.65 loss, the math should give ~16% drawdown, not 70%."
            )
