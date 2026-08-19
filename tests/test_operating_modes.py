"""Operating-mode tests: checklist sign-off gate for CONTROLLED_LIVE.

We test the gate in isolation (core/live_gate.py) so we don't have to
construct a full BotEngine. The gate is the load-bearing piece — every
other mode check is a simple branch on the config value.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.live_gate import (
    enforce_controlled_live_gate,
    enforce_live_runtime_invariants,
    is_checklist_signed,
    live_latch_permits_execution,
)


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# ── is_checklist_signed ───────────────────────────────────────────────

def test_missing_checklist_not_signed(tmp_path):
    ok, msg = is_checklist_signed(tmp_path / "nope.md")
    assert ok is False
    assert "not found" in msg


def test_unsigned_checklist_rejected(tmp_path):
    f = tmp_path / "c.md"
    _write(f, "# Checklist\n\nAll criteria TBD.\n")
    ok, msg = is_checklist_signed(f)
    assert ok is False
    assert "no valid" in msg.lower() or "refusing" in msg.lower()


def test_example_comment_signature_does_not_count(tmp_path):
    """The shipped template has a commented-out example signature —
    it must NOT be read as a valid sign-off."""
    f = tmp_path / "c.md"
    _write(f, "# Checklist\n\n<!-- Signed-By: Placeholder 2026-12-31 -->\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


def test_unclosed_html_comment_signature_does_not_count(tmp_path):
    """A missing --> used to match Signed-By and count as live sign-off."""
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, f"# Checklist\n\n<!-- Signed-By: Owner {today}\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


def test_future_date_rejected(tmp_path):
    f = tmp_path / "c.md"
    future = (date.today() + timedelta(days=7)).isoformat()
    _write(f, f"# Checklist\n\nSigned-By: Owner {future}\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


def test_valid_signature_accepted(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, f"# Checklist\n\nSigned-By: Real Owner {today}\n")
    ok, msg = is_checklist_signed(f)
    assert ok is True
    assert "Real Owner" in msg


def test_unchecked_acceptance_item_rejects_signature(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(
        f,
        "# Checklist\n\n"
        "- [x] Tests passing\n"
        "- [ ] Positive paper expectancy\n\n"
        f"Signed-By: Real Owner {today}\n",
    )
    ok, msg = is_checklist_signed(f)
    assert ok is False
    assert "unchecked" in msg


def test_stale_signature_rejected(tmp_path):
    f = tmp_path / "c.md"
    stale = (date.today() - timedelta(days=31)).isoformat()
    _write(f, f"# Checklist\n\nSigned-By: Old Owner {stale}\n")
    ok, msg = is_checklist_signed(f)
    assert ok is False
    assert "older than" in msg


def test_latest_signature_wins(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, (
        "# Checklist\n"
        "Signed-By: Old Owner 2020-01-01\n"
        f"Signed-By: Current Owner {today}\n"
    ))
    ok, msg = is_checklist_signed(f)
    assert ok is True
    assert "Current Owner" in msg


def test_malformed_signature_ignored(tmp_path):
    f = tmp_path / "c.md"
    _write(f, "Signed-By: Missing Date\nSigned-By: Only 2026\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


# ── enforce_controlled_live_gate ──────────────────────────────────────

def test_enforce_observation_never_raises(tmp_path):
    """OBSERVATION mode: gate is a no-op even without a checklist."""
    enforce_controlled_live_gate("OBSERVATION", path=tmp_path / "absent.md")


def test_enforce_paper_never_raises(tmp_path):
    enforce_controlled_live_gate("PAPER", path=tmp_path / "absent.md")


def test_enforce_controlled_live_unsigned_raises(tmp_path):
    f = tmp_path / "c.md"
    _write(f, "# unsigned\n")
    with pytest.raises(SystemExit) as exc:
        enforce_controlled_live_gate("CONTROLLED_LIVE", path=f)
    assert "REFUSING TO START" in str(exc.value)


def test_enforce_controlled_live_signed_passes(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, f"Signed-By: Owner {today}\n")
    enforce_controlled_live_gate("CONTROLLED_LIVE", path=f)  # no raise


def test_enforce_controlled_live_requires_env_latch(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, f"Signed-By: Owner {today}\n")
    with pytest.raises(SystemExit) as exc:
        enforce_controlled_live_gate(
            "CONTROLLED_LIVE",
            path=f,
            controlled_live_enabled=False,
        )
    assert "CONTROLLED_LIVE_ENABLED" in str(exc.value)


# ── live_latch_permits_execution (Latch 2: CONTROLLED_LIVE_ENABLED env) ──
# Latch 1 (checklist) is covered above. Latch 2 is the second latch the bot
# requires before placing REAL orders: OPERATING_MODE=CONTROLLED_LIVE alone is
# not enough — the CONTROLLED_LIVE_ENABLED env flag must also be true. These
# tests pin the double-latch so neither half can silently arm live trading.

def test_latch_blocks_controlled_live_without_env():
    """The halt case: CONTROLLED_LIVE but env latch off → execution refused."""
    assert live_latch_permits_execution("CONTROLLED_LIVE", False) is False


def test_latch_arms_controlled_live_with_env():
    assert live_latch_permits_execution("CONTROLLED_LIVE", True) is True


def test_latch_paper_unaffected():
    """PAPER places paper orders regardless of the live env flag."""
    assert live_latch_permits_execution("PAPER", False) is True
    assert live_latch_permits_execution("PAPER", True) is True


def test_latch_is_case_insensitive():
    """Mode comparison must not be defeated by casing."""
    assert live_latch_permits_execution("controlled_live", False) is False


def test_latch_observation_permitted_here():
    """OBSERVATION is blocked upstream (separate gate); this latch alone
    does not block it — documents the division of responsibility."""
    assert live_latch_permits_execution("OBSERVATION", False) is True


def test_bot_engine_fails_closed_when_live_startup_gate_errors(monkeypatch):
    """An unexpected gate failure must never be downgraded during live startup."""
    import config
    import core.bot_engine as bot_engine
    import core.live_gate as live_gate

    monkeypatch.setattr(bot_engine, "DRY_RUN", False)
    monkeypatch.setattr(config, "OPERATING_MODE", "CONTROLLED_LIVE")
    monkeypatch.setattr(config, "CONTROLLED_LIVE_ENABLED", True)

    def broken_gate(*args, **kwargs):
        raise RuntimeError("readiness database unavailable")

    monkeypatch.setattr(live_gate, "enforce_controlled_live_gate", broken_gate)

    engine = object.__new__(bot_engine.BotEngine)
    with pytest.raises(SystemExit, match="live safety checks errored"):
        engine.run()


def _safe_live_config(**overrides):
    values = {
        "SL_FAIL_EMERGENCY_CLOSE_ENABLED": True,
        "OHLCV_VALIDATION_ENABLED": True,
        "PER_POSITION_LOCK_ENABLED": True,
        "MAX_PORTFOLIO_EXPOSURE_PCT": 2.0,
        "MAX_AGGREGATE_OPEN_RISK_PCT": 0.001,
        "STRESSED_EXIT_COST_FRAC": 0.002,
        "EXECUTION_BOOK_MAX_AGE_SEC": 5.0,
        "MAX_ENTRY_SLIPPAGE_BPS": 30.0,
        "DAILY_LOSS_BREAKER": {"enabled": True, "max_loss_pct": 0.02},
        "RISK": {"futures_max_leverage": 1.0},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_live_runtime_invariants_accept_conservative_config():
    enforce_live_runtime_invariants(
        "CONTROLLED_LIVE", config_module=_safe_live_config()
    )


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"SL_FAIL_EMERGENCY_CLOSE_ENABLED": False}, "emergency close"),
        ({"OHLCV_VALIDATION_ENABLED": False}, "OHLCV validation"),
        ({"PER_POSITION_LOCK_ENABLED": False}, "per-position lock"),
        ({"MAX_PORTFOLIO_EXPOSURE_PCT": 0.0}, "portfolio exposure cap"),
        ({"MAX_PORTFOLIO_EXPOSURE_PCT": 12.0}, "portfolio exposure cap"),
        ({"MAX_AGGREGATE_OPEN_RISK_PCT": 0.0}, "aggregate open-risk cap"),
        ({"STRESSED_EXIT_COST_FRAC": 0.0}, "stressed exit-cost"),
        ({"EXECUTION_BOOK_MAX_AGE_SEC": 30.0}, "execution-book age"),
        ({"MAX_ENTRY_SLIPPAGE_BPS": 100.0}, "entry-slippage cap"),
        ({"DAILY_LOSS_BREAKER": {"enabled": False}}, "daily-loss breaker"),
        ({"RISK": {"futures_max_leverage": 3.0}}, "leverage cap"),
    ],
)
def test_live_runtime_invariants_reject_disabled_or_loosened_rails(override, reason):
    with pytest.raises(SystemExit, match=reason):
        enforce_live_runtime_invariants(
            "CONTROLLED_LIVE", config_module=_safe_live_config(**override)
        )


def test_live_runtime_invariants_are_dormant_in_paper():
    enforce_live_runtime_invariants(
        "PAPER", config_module=_safe_live_config(SL_FAIL_EMERGENCY_CLOSE_ENABLED=False)
    )
