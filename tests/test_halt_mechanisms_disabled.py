"""Tests for the 2026-05-19 halt-disable directive.

The user instructed: "Don't halt or pause when losing trades." This file
verifies that with HALT_MECHANISMS flags = False, none of the 7 mechanisms
fire — AND that flipping ONE flag back to True restores just that mechanism
(Test 8 — the critical anti-revert protection).
"""
from __future__ import annotations

import json

# Test 1 — pin all flags = False (catches accidental revert)

def test_halt_mechanisms_all_disabled_by_default():
    """All 7 mechanisms must be False in checked-in config."""
    from config import HALT_MECHANISMS
    expected_keys = {
        "daily_pnl_halt", "drawdown_halt", "spec12_streak_halt",
        "symbol_pause", "family_pause", "outlier_loss_flag",
        "auto_mutator_blacklist",
    }
    assert set(HALT_MECHANISMS.keys()) == expected_keys, \
        f"HALT_MECHANISMS keys drifted: {set(HALT_MECHANISMS.keys())}"
    for key, value in HALT_MECHANISMS.items():
        assert value is False, f"HALT_MECHANISMS[{key!r}] is {value}, expected False"


# Helper: seed risk_state and chdir so risk_manager writes into tmp dir

def _seed_risk_state(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rs = {
        "is_halted": False, "halt_reason": "", "halt_time": None,
        "daily_pnl": 0.0, "max_drawdown_pct": 0.0,
        "start_balance": 500.0, "peak_balance": 500.0,
        "trading_day": "2026-05-19",
        "trades_today": 0,
        "recent_results": [], "trade_history": [],
        "symbol_pauses": {}, "family_pauses": {},
        "global_streak": [], "timestamp": 0,
    }
    (tmp_path / "data" / "risk_state.json").write_text(json.dumps(rs))


# Test 2 — daily PnL halt does NOT fire when flag off

def test_daily_pnl_loss_does_not_halt_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)

    # Drive daily_pnl far below the 1% threshold ($5) via record_trade_pnl
    # which is the method that contains the daily-loss circuit-breaker (Site A).
    for _ in range(3):
        rm.record_trade_pnl(pnl=-3.0, balance=500.0, is_win=False, pnl_pct=-1.0)

    assert rm._halted is False, \
        f"Daily PnL halt fired when flag is off: halt_reason={rm._halt_reason}"
    assert not (tmp_path / "data" / "review_required.json").exists()


# Test 3 — drawdown halt does NOT fire when flag off

def test_drawdown_does_not_halt_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)
    rm._peak_balance = 500.0

    # 12% drawdown — well above the 8% threshold. Site B (drawdown halt)
    # lives inside record_trade_pnl; drive daily_pnl down to -$60 via one
    # loss to trip both effective_balance=440 and the drawdown calc.
    rm.record_trade_pnl(pnl=-60.0, balance=500.0, is_win=False, pnl_pct=-12.0)

    assert rm._halted is False, \
        f"Drawdown halt fired when flag is off: halt_reason={rm._halt_reason}"


# Test 4 — Spec §12 5-consec halt does NOT fire when flag off

def test_spec12_streak_does_not_halt_when_flag_off(tmp_path, monkeypatch):
    """Inverse of tests/test_spec12_post_sprint.py — with flag off, 5 losses must NOT halt."""
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)

    # 5 consecutive losses — Spec §12 trigger condition
    for i in range(5):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT", family="claude_portfolio",
            is_win=False, pnl_usd=-0.50, pnl_pct=-1.5, reason="stop_loss")

    assert rm._halted is False, \
        f"Spec §12 halt fired when flag is off: halt_reason={rm._halt_reason}"
    assert not (tmp_path / "data" / "review_required.json").exists(), \
        "review_required.json was written despite spec12_streak_halt=False"


# Test 5 — symbol pause does NOT set when flag off

def test_symbol_pause_not_set_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)

    sym = "ATOM/USDT:USDT"
    # 2 consecutive losses on the same symbol — symbol-pause trigger
    rm.record_trade_result(symbol=sym, family="claude_portfolio",
                            is_win=False, pnl_usd=-0.5, pnl_pct=-1.5, reason="stop_loss")
    rm.record_trade_result(symbol=sym, family="claude_portfolio",
                            is_win=False, pnl_usd=-0.5, pnl_pct=-1.5, reason="stop_loss")

    assert rm._symbol_pauses.get(sym, 0) == 0, \
        f"Symbol pause set when flag is off: {sym} until {rm._symbol_pauses.get(sym)}"


# Test 6 — family pause does NOT set when flag off

def test_family_pause_not_set_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)

    fam = "claude_portfolio"
    # 3 consecutive losses on the same family — family-pause trigger
    for i in range(3):
        rm.record_trade_result(symbol=f"X{i}/USDT:USDT", family=fam,
                                is_win=False, pnl_usd=-0.5, pnl_pct=-1.5, reason="stop_loss")

    assert rm._family_pauses.get(fam, 0) == 0, \
        f"Family pause set when flag is off: {fam} until {rm._family_pauses.get(fam)}"


# Test 7 — outlier-loss flag does NOT write when flag off

def test_outlier_loss_does_not_write_flag_when_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)

    # Single loss exceeding MAX_LOSS_PER_TRADE_USD = $15
    rm.record_trade_result(symbol="X/USDT:USDT", family="claude_portfolio",
                            is_win=False, pnl_usd=-20.0, pnl_pct=-4.0, reason="stop_loss")

    assert not (tmp_path / "data" / "review_required.json").exists(), \
        "Outlier-loss review_required.json written despite outlier_loss_flag=False"


# Test 8 — re-enabling one flag restores that mechanism (anti-revert proof)

def test_re_enabling_one_flag_restores_that_mechanism(tmp_path, monkeypatch):
    """Critical: flipping spec12_streak_halt back to True must restore the halt.

    Proves the gate is wired correctly and rollback works. If this test fails,
    the gate code path is wrong and the rollback procedure won't work.
    """
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    import config
    # Patch the dict in-place; the gate sites read from the live module
    monkeypatch.setitem(config.HALT_MECHANISMS, "spec12_streak_halt", True)

    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(500.0)

    # 5 consecutive losses with mechanism re-enabled — must trip
    for i in range(5):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT", family="claude_portfolio",
            is_win=False, pnl_usd=-0.50, pnl_pct=-1.5, reason="stop_loss")

    halted = bool(getattr(rm, "_halted", False))
    review_file_exists = (tmp_path / "data" / "review_required.json").exists()
    assert halted or review_file_exists, (
        "Spec §12 did NOT trip with spec12_streak_halt=True. "
        "Either the gate code path is wrong or record_trade_result API drifted. "
        f"is_halted={halted}, review_required exists={review_file_exists}"
    )
