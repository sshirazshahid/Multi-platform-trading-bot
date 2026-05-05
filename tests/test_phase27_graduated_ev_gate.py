"""Phase 27 — graduated EV-based 'test before trade' (2026-05-05).

User directive: "Scan 24/7. No emotions. No bias. Just data. Test
before it trades."

Phase 20 disabled the EXPECTANCY_FILTER because its BINARY veto locked
symbols out forever (chicken-and-egg: 0 trades → mean never moves).
Phase 27 re-enables it as a GRADUATED multiplier:

  - n < min_sample_size:        1.0  (insufficient data, allow)
  - mean >= 0:                  1.0  (positive EV — full size)
  - -0.20 <= mean < 0:          0.75 (mildly negative — downsize)
  - -0.50 <= mean < -0.20:      0.50 (moderately negative — half size)
  - mean < -0.50 (n>=5):        0.0  (catastrophic — HARD BLOCK)

Whitelisted symbols bypass entirely.

Pure data: queries warehouse for realized PnL on (symbol, side) over
last 30 days. No curated lists. Self-correcting: as a symbol's EV
improves, the multiplier auto-restores. Composes multiplicatively
with Phase 17 (rolling-50 EV), Phase 18 (calibrator), Phase 22
(regime), Phase 26A (winner age-lock at exit).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── Config — Phase 27 enabled with graduated semantics ───────────────


def test_expectancy_filter_enabled_phase27():
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER.get("enabled") is True


def test_expectancy_filter_min_sample_size_5():
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER.get("min_sample_size") == 5


def test_expectancy_filter_lookback_30days():
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER.get("lookback_days") == 30


# ─── _ev_per_symbol_multiplier tier behaviour ────────────────────────


def _stub_engine_with_expectancy(exp_dict, monkeypatch):
    """Build a minimal stub of BotEngine.* needed for the helper test.
    Patches the warehouse query to return the supplied expectancy dict.
    """
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)  # skip __init__

    # patch warehouse.recent_expectancy
    fake_wh = MagicMock()
    fake_wh.recent_expectancy = MagicMock(return_value=exp_dict)
    fake_get_warehouse = MagicMock(return_value=fake_wh)
    monkeypatch.setattr(
        "core.warehouse.get_warehouse", fake_get_warehouse, raising=False)
    return eng


def test_ev_mult_full_size_when_positive_ev(monkeypatch):
    eng = _stub_engine_with_expectancy(
        {"mean": 0.50, "n": 20, "win_rate": 0.6}, monkeypatch)
    mult, reason = eng._ev_per_symbol_multiplier("ATOM/USDT:USDT", "buy")
    assert mult == 1.0


def test_ev_mult_full_size_when_insufficient_data(monkeypatch):
    """recent_expectancy returns None when n<min_sample_size."""
    eng = _stub_engine_with_expectancy(None, monkeypatch)
    mult, reason = eng._ev_per_symbol_multiplier("NEW/USDT:USDT", "buy")
    assert mult == 1.0


def test_ev_mult_075_when_mildly_negative(monkeypatch):
    """-0.20 <= mean < 0 → ×0.75"""
    eng = _stub_engine_with_expectancy(
        {"mean": -0.10, "n": 12, "win_rate": 0.45}, monkeypatch)
    mult, reason = eng._ev_per_symbol_multiplier("ETH/USDT:USDT", "buy")
    assert mult == 0.75
    assert "ev_neg_mild" in reason


def test_ev_mult_050_when_moderately_negative(monkeypatch):
    """-0.50 <= mean < -0.20 → ×0.5"""
    eng = _stub_engine_with_expectancy(
        {"mean": -0.30, "n": 15, "win_rate": 0.40}, monkeypatch)
    # XRP isn't in the whitelist — use it instead of DOGE
    mult, reason = eng._ev_per_symbol_multiplier("XRP/USDT:USDT", "sell")
    assert mult == 0.5
    assert "ev_neg_med" in reason


def test_ev_mult_zero_when_catastrophic(monkeypatch):
    """mean < -0.50 with n>=5 → 0.0 (BLOCK)"""
    eng = _stub_engine_with_expectancy(
        {"mean": -0.80, "n": 8, "win_rate": 0.25}, monkeypatch)
    mult, reason = eng._ev_per_symbol_multiplier("BAD/USDT:USDT", "sell")
    assert mult == 0.0
    assert "catastrophic" in reason


def test_ev_mult_does_not_block_on_low_sample_catastrophic(monkeypatch):
    """If n<5 we don't have enough evidence to call it catastrophic.
    With only 3 trades at mean=-1.00, that's noise — let it through."""
    eng = _stub_engine_with_expectancy(
        {"mean": -1.00, "n": 3, "win_rate": 0.0}, monkeypatch)
    mult, reason = eng._ev_per_symbol_multiplier("THIN/USDT:USDT", "buy")
    # mean<-0.50 but n<5 → falls to mild/medium tier or default
    # Since recent_expectancy returns the dict regardless, the >= 5 guard
    # is only on the catastrophic block. Our function must NOT zero on n<5.
    assert mult > 0.0


def test_ev_mult_whitelisted_bypasses(monkeypatch):
    """Whitelist symbols return 1.0 regardless of EV.

    Phase 33 (2026-05-05) emptied the live whitelist per UNBLOCK_ALL.
    This test still validates the whitelist LOGIC works when populated —
    we monkey-patch a temporary entry to verify the bypass mechanism."""
    eng = _stub_engine_with_expectancy(
        {"mean": -2.00, "n": 30, "win_rate": 0.0}, monkeypatch)
    monkeypatch.setattr(
        "config.EXPECTANCY_FILTER",
        {
            "enabled": True,
            "min_expected_dollar": -0.50,
            "min_expected_star": -0.50,
            "lookback_days": 30,
            "min_sample_size": 5,
            "whitelist": {"DOGE/USDT:USDT", "DOGE/USDT"},
        },
        raising=False,
    )
    mult, reason = eng._ev_per_symbol_multiplier("DOGE/USDT:USDT", "buy")
    assert mult == 1.0
    assert "whitelist" in reason.lower()


def test_ev_mult_disabled_returns_full_size(monkeypatch):
    """If EXPECTANCY_FILTER.enabled=False, always full size."""
    monkeypatch.setattr("config.EXPECTANCY_FILTER",
                        {"enabled": False}, raising=False)
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    mult, reason = eng._ev_per_symbol_multiplier("ANY/USDT:USDT", "buy")
    assert mult == 1.0


# ─── Source-level invariants on the wire-up ──────────────────────────


def test_ev_check_runs_in_execute_open():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_ev_per_symbol_multiplier" in src
    assert "_ev_symbol_mult" in src


def test_ev_block_path_short_circuits():
    """Catastrophic (mult==0) path must short-circuit with return False."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "if _ev_symbol_mult <= 0.0:" in src
    # Within close proximity, return False must follow (1500 chars covers
    # the warning log + warehouse SKIP record + return)
    idx = src.index("if _ev_symbol_mult <= 0.0:")
    block = src[idx:idx + 1500]
    assert "return False" in block


def test_ev_multiplier_applied_in_size_chain():
    """The graduated tier (0.5, 0.75) must multiply size_fraction."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "size_fraction *= _ev_symbol_mult" in src


def test_ev_multiplier_runs_before_regime_in_chain():
    """Order: ev → cal → ev_symbol → regime → notional"""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    cal_idx = src.index("size_fraction *= _cal_mult")
    ev_idx = src.index("size_fraction *= _ev_symbol_mult")
    regime_idx = src.index("size_fraction *= _regime_size_mult")
    notional_idx = src.index("notional = mtype_bal * size_fraction")
    assert cal_idx < ev_idx < regime_idx < notional_idx, (
        "expected order: cal_mult → ev_symbol_mult → regime → notional")


def test_phase27_log_marker_present():
    """A 'Phase 27' marker in source helps log readers identify the path."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "Phase 27" in src
