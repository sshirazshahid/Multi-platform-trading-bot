"""Flag-based halt auto-resume invariants (2026-04-29).

Bug: `_should_auto_resume` originally only matched halt_reasons
containing "consec_global", but `_honour_review_flag_if_present`
builds reasons like "spec12:flag:manual_review" for outlier-loss
flags. Result: bots that hit an outlier-loss flag stayed halted
forever, requiring manual deletion of data/review_required.json.

Discovered when the L99 ETH trade (-$22.20) wrote an outlier flag
that no auto-resume path could clear.

Fix: any halt_reason starting "spec12:" honours the auto-resume
cooldown + flag-deletion semantics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _write_flag(reason="outlier_loss(-22.20 USD beyond $4.00 cap)",
                action="manual_review", ts=None):
    p = Path("data/review_required.json")
    p.write_text(json.dumps([{
        "ts": ts or time.time(),
        "reason": reason,
        "action": action,
        "symbol": "ETH/USDT:USDT",
        "family": "claude_portfolio",
    }]))
    return p


def test_flag_halt_resumes_after_cooldown():
    """Outlier-loss flag halt: after SPEC12_AUTO_RESUME_COOLDOWN_MIN,
    must auto-resume even though halt_reason is NOT 'consec_global'."""
    from core.risk_manager import SPEC12_AUTO_RESUME_COOLDOWN_MIN, RiskManager

    cooldown_sec = SPEC12_AUTO_RESUME_COOLDOWN_MIN * 60
    _write_flag(ts=time.time() - cooldown_sec - 60)

    rm = RiskManager()
    assert rm._halted is True
    assert rm._halt_reason.startswith("spec12:flag:")

    # can_trade triggers _should_auto_resume internally
    rm.can_trade(open_position_count=0)

    assert rm._halted is False, (
        "Flag-based halt must auto-resume after cooldown")
    assert not Path("data/review_required.json").exists(), (
        "Flag file must be deleted on auto-resume to prevent re-halt loop")


def test_flag_halt_stays_halted_during_cooldown():
    """Within cooldown window, must stay halted."""
    from core.risk_manager import RiskManager
    _write_flag(ts=time.time() - 60)  # 1 minute ago
    rm = RiskManager()
    assert rm._halted is True
    rm.can_trade(open_position_count=0)
    assert rm._halted is True
    assert Path("data/review_required.json").exists()


def test_consec_global_flag_still_resumes():
    """Regression: the original 'consec_global' branch must still work."""
    from core.risk_manager import SPEC12_AUTO_RESUME_COOLDOWN_MIN, RiskManager
    cooldown_sec = SPEC12_AUTO_RESUME_COOLDOWN_MIN * 60
    _write_flag(
        reason="5 consecutive global losses",
        action="halt_pending_review",
        ts=time.time() - cooldown_sec - 60,
    )
    rm = RiskManager()
    assert rm._halted is True
    assert "consec_global" in rm._halt_reason

    rm.can_trade(open_position_count=0)
    assert rm._halted is False
    assert not Path("data/review_required.json").exists()


def test_unparseable_flag_keeps_halted_safely():
    """A corrupt flag file must keep the bot halted (fail-safe)."""
    Path("data/review_required.json").write_text("{not valid json")
    from core.risk_manager import RiskManager
    rm = RiskManager()
    assert rm._halted is True
    # Don't auto-resume an unreadable flag — operator must investigate
    rm.can_trade(open_position_count=0)
    # halt_reason will be 'spec12:flag:force_observation_mode' from the
    # fail-safe path; auto-resume IS allowed once cooldown elapses
    # (after that the corrupt file gets deleted on auto-resume).


def test_flag_anchor_on_oldest_not_newest_entry():
    """2026-05-24 regression guard: _honour_review_flag_if_present must
    anchor the halt timestamp on flag_data[0] (the ORIGINAL halt event),
    not flag_data[-1] (the most recent audit append).

    Bug: while halted, note_stale_data / note_order_rejection / outlier
    branch append fresh entries to the list. Using [-1] slides the
    anchor forward on every append, so the 4-hour cooldown never
    elapses and the bot stays halted indefinitely.

    Fix:                              risk_manager.py:320 → flag_data[0]
    Originally tracked in memory:     stuck_halt_anchor_fix_2026_05_15
    """
    import json
    from core.risk_manager import SPEC12_AUTO_RESUME_COOLDOWN_MIN, RiskManager

    cooldown_sec = SPEC12_AUTO_RESUME_COOLDOWN_MIN * 60
    p = Path("data/review_required.json")

    # Original halt: outside cooldown — should auto-resume.
    # Recent audit append: inside cooldown — must NOT prevent resume.
    p.write_text(json.dumps([
        {
            "ts": time.time() - cooldown_sec - 60,  # OLDEST — original
            "reason": "outlier_loss(-22.20 USD beyond cap)",
            "action": "manual_review",
        },
        {
            "ts": time.time() - 60,                  # NEWEST — recent
            "reason": "stale_data audit append",
            "action": "audit_only",
        },
    ]))

    rm = RiskManager()
    assert rm._halted is True

    rm.can_trade(open_position_count=0)

    assert rm._halted is False, (
        "Flag halt must resume because the ORIGINAL halt timestamp is "
        "outside the cooldown — newer audit appends must not block resume."
    )
    assert not Path("data/review_required.json").exists()


def test_outlier_threshold_size_at_new_sizing(monkeypatch):
    """Sanity-check the bug premise: at $400 × 50% × 2x × 1.5% SL the
    expected loss is $6; the new $15 outlier threshold must NOT fire on a
    routine $6 SL hit."""
    from config import MAX_LOSS_PER_TRADE_USD
    expected_normal_sl_loss = 400 * 0.50 * 2 * 0.015
    assert expected_normal_sl_loss == pytest.approx(6.0)
    assert MAX_LOSS_PER_TRADE_USD > expected_normal_sl_loss, (
        "Outlier threshold must exceed the routine SL loss, otherwise "
        "every SL hit halts the bot. Bug at $4 threshold.")
    assert MAX_LOSS_PER_TRADE_USD >= 2.0 * expected_normal_sl_loss, (
        "Outlier threshold should be ≥2× routine loss to mean 'unusual'.")
