"""Path-dependent drawdown circuit-breaker (2026-06-28 deep-research risk gap).

Per-period loss caps miss slow-bleed clustering; this breaker refuses NEW entries
when equity is >X% below its running peak, with a hysteresis deadband and
restart-persistent state. Default OFF (opt-in via env). Fail-OPEN on bad data.
"""
import pytest

import core.drawdown_pause as dp_mod
from core.drawdown_pause import DrawdownPause

PEAK = 10_000.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dp_mod, "_STATE", tmp_path / "dd.json")
    for k in ("DRAWDOWN_PAUSE_ENABLED", "DRAWDOWN_MAX_PCT",
              "DRAWDOWN_HYSTERESIS_PCT", "DRAWDOWN_CLEAR_MIN"):
        monkeypatch.delenv(k, raising=False)


def _enable(monkeypatch, max_pct="0.15", hyst="0.05", clear="0"):
    monkeypatch.setenv("DRAWDOWN_PAUSE_ENABLED", "true")
    monkeypatch.setenv("DRAWDOWN_MAX_PCT", max_pct)
    monkeypatch.setenv("DRAWDOWN_HYSTERESIS_PCT", hyst)
    monkeypatch.setenv("DRAWDOWN_CLEAR_MIN", clear)


def test_disabled_by_default():
    paused, reason, _ = DrawdownPause().update_and_evaluate(PEAK, 5000.0, now=0.0)
    assert paused is False
    assert reason == "disabled"


def test_below_trip_not_paused(monkeypatch):
    _enable(monkeypatch)
    paused, _, info = DrawdownPause().update_and_evaluate(PEAK, 9_000.0, now=0.0)  # 10% dd
    assert paused is False
    assert info["drawdown_pct"] == 10.0


def test_trips_at_threshold(monkeypatch):
    _enable(monkeypatch)
    paused, reason, _ = DrawdownPause().update_and_evaluate(PEAK, 8_400.0, now=0.0)  # 16% dd
    assert paused is True
    assert "16.0%" in reason


def test_hysteresis_holds_inside_deadband(monkeypatch):
    _enable(monkeypatch)  # trip 15%, resume <=10%
    dp = DrawdownPause()
    assert dp.update_and_evaluate(PEAK, 8_400.0, now=0.0)[0] is True  # trip at 16%
    # recover to 12% dd — inside the deadband (between 10% and 15%) -> still paused
    assert dp.update_and_evaluate(PEAK, 8_800.0, now=60.0)[0] is True


def test_resumes_after_recovery(monkeypatch):
    _enable(monkeypatch)
    dp = DrawdownPause()
    assert dp.update_and_evaluate(PEAK, 8_400.0, now=0.0)[0] is True       # trip
    paused, reason, _ = dp.update_and_evaluate(PEAK, 9_100.0, now=60.0)    # 9% dd <= 10%
    assert paused is False
    assert "recovered" in reason


def test_clear_minutes_timer_blocks_early_resume(monkeypatch):
    _enable(monkeypatch, clear="30")
    dp = DrawdownPause()
    assert dp.update_and_evaluate(PEAK, 8_400.0, now=0.0)[0] is True
    # recovered (9% dd) but only 10 min elapsed -> still paused on the timer
    assert dp.update_and_evaluate(PEAK, 9_100.0, now=600.0)[0] is True
    # 31 min elapsed -> resume
    assert dp.update_and_evaluate(PEAK, 9_100.0, now=31 * 60.0)[0] is False


def test_fail_open_on_zero_peak(monkeypatch):
    _enable(monkeypatch)
    paused, reason, _ = DrawdownPause().update_and_evaluate(0.0, 5_000.0, now=0.0)
    assert paused is False
    assert reason == "no_data"


def test_state_persists_across_restart(monkeypatch):
    _enable(monkeypatch)
    DrawdownPause().update_and_evaluate(PEAK, 8_400.0, now=0.0)  # trip + persist
    # new instance (simulated restart) loads paused state and stays paused
    revived = DrawdownPause()
    assert revived._paused is True
    assert revived.update_and_evaluate(PEAK, 8_800.0, now=120.0)[0] is True  # still in deadband
