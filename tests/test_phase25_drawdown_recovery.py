"""Phase 25 — drawdown halt recovery + stale-peak reset (2026-05-05).

User reported "HALTED again" 12min after restart. Root cause:

  - Peak balance was $653.56 (from a prior higher-equity session)
  - Current equity ~$568 (post 30-day bleed)
  - Drawdown calc: ($653 − $568) / $653 = 13.0% from peak
  - max_drawdown_pct = 0.08 → trips drawdown halt
  - DRAWDOWN_HALT_COOLDOWN_MIN = 240 → 4h of forced downtime per trip
  - On resume, peak resets to current and the cycle repeats

The drawdown circuit-breaker was designed for sudden capital catastrophes
(flash crash, cascading SL fills) — NOT slow multi-week bleed. Two fixes:

(1) DRAWDOWN_HALT_COOLDOWN_MIN: 240 → 60. At a $568 account, 4h of
    downtime per drawdown trip is structurally too long. Spec §12 still
    independently catches real losing-streak halts at 240min.

(2) Stale-peak reset on restart. When _load_state restores peak from
    disk, mark it as stale. On the first note_balance_update, if the
    loaded peak is >10% above current effective balance, reset peak to
    current × 1.05. Prevents stale prior-session peaks from haunting
    every restart.
"""
from __future__ import annotations

from pathlib import Path


# ─── Cooldown constant reduction ──────────────────────────────────────


def test_drawdown_cooldown_reduced_to_60min():
    """Phase 25: 4h → 1h cooldown so slow-bleed halts don't dominate."""
    from core.risk_manager import DRAWDOWN_HALT_COOLDOWN_MIN
    assert DRAWDOWN_HALT_COOLDOWN_MIN == 60, (
        f"Phase 25 set this to 60 (was 240); now {DRAWDOWN_HALT_COOLDOWN_MIN}"
    )


def test_spec12_cooldown_unchanged_at_240():
    """Spec §12 (5-consec-loss) cooldown stays at 4h — that's the real
    losing-streak halt and needs the regime-shift breathing room."""
    from core.risk_manager import SPEC12_AUTO_RESUME_COOLDOWN_MIN
    assert SPEC12_AUTO_RESUME_COOLDOWN_MIN == 240


# ─── Stale-peak reset logic ───────────────────────────────────────────


def test_stale_peak_flag_attribute_exists(tmp_path, monkeypatch):
    """RiskManager.__init__ initializes _peak_stale_flag = False."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    assert hasattr(r, "_peak_stale_flag")
    assert r._peak_stale_flag is False


def test_load_state_marks_peak_stale_on_same_day_restart(tmp_path, monkeypatch):
    """When _load_state finds a peak_balance > 0 on a same-day restart,
    flag it as stale so the next balance update can verify."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    # Write a same-day risk_state with peak
    import json
    from datetime import date
    state = {
        "is_halted": False, "halt_reason": "", "halt_time": 0,
        "daily_pnl": 0.0, "start_balance": 100.0,
        "peak_balance": 200.0,  # higher than current
        "trading_day": date.today().isoformat(),
        "trades_today": 0, "recent_results": [],
        "trade_history": [], "symbol_pauses": {},
        "family_pauses": {}, "global_streak": [],
        "timestamp": 0,
    }
    Path("data/risk_state.json").write_text(json.dumps(state))
    from core.risk_manager import RiskManager
    r = RiskManager()
    assert r._peak_stale_flag is True


def test_stale_peak_resets_when_loaded_peak_is_high(tmp_path, monkeypatch):
    """First note_balance_update with low current vs high loaded peak →
    peak is reset to current × 1.05."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._peak_balance = 1000.0
    r._peak_stale_flag = True
    r._start_balance = 800.0
    r._daily_pnl = 0.0
    # current effective = 800.0; peak 1000.0 / 800.0 = 1.25 > 1.10 → reset
    r.record_trade_pnl(0.0, 800.0, is_win=True, pnl_pct=0.0)
    # Reset to 800 × 1.05 = 840.0
    assert r._peak_balance == 840.0
    assert r._peak_stale_flag is False


def test_stale_peak_NOT_reset_when_within_10pct(tmp_path, monkeypatch):
    """If loaded peak is within 10% of current, it's still relevant —
    don't reset (the drawdown guard should still apply)."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._peak_balance = 850.0  # 6.25% above current 800
    r._peak_stale_flag = True
    r._start_balance = 800.0
    r._daily_pnl = 0.0
    r.record_trade_pnl(0.0, 800.0, is_win=True, pnl_pct=0.0)
    # 850 / 800 = 1.0625 < 1.10 → NO reset
    assert r._peak_balance == 850.0
    # Flag still cleared (one-shot)
    assert r._peak_stale_flag is False


def test_stale_peak_flag_one_shot(tmp_path, monkeypatch):
    """Flag clears after first update — second update doesn't re-check."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._peak_balance = 1000.0
    r._peak_stale_flag = True
    r._start_balance = 800.0
    r._daily_pnl = 0.0
    r.record_trade_pnl(0.0, 800.0, is_win=True, pnl_pct=0.0)
    # Now manually inflate peak again
    r._peak_balance = 1000.0
    # Second update should NOT touch peak (flag is False)
    r.record_trade_pnl(0.0, 800.0, is_win=True, pnl_pct=0.0)
    assert r._peak_balance == 1000.0


def test_no_stale_check_on_fresh_init(tmp_path, monkeypatch):
    """Fresh init (no state file) → flag is False, no reset."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    assert r._peak_stale_flag is False
    r._start_balance = 800.0
    r._daily_pnl = 0.0
    r.record_trade_pnl(0.0, 800.0, is_win=True, pnl_pct=0.0)
    # Without flag set, peak builds normally from current balance
    # (effective_balance = 800, so peak becomes 800 since fresh)
    assert r._peak_balance == 800.0


# ─── Source-level invariant: existing auto-resume path preserved ──────


def test_auto_resume_path_for_drawdown_still_present():
    """Phase 25 must NOT delete the existing drawdown auto-resume branch."""
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    assert '"drawdown" in self._halt_reason' in src
    assert "DRAWDOWN_HALT_COOLDOWN_MIN" in src


def test_phase25_log_marker_present():
    """The phase 25 log message helps operators see when peak resets."""
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    assert "Phase 25: stale peak" in src
