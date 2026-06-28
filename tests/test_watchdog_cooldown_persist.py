"""Regression tests: HealthWatchdog cooldowns survive a process restart.

Bug (2026-06-28): cooldowns lived only in the in-memory WatchdogState, so every
bot restart constructed a fresh watchdog with an empty last_alert dict and
re-emailed every still-true sticky condition (model_pointer_invalid,
audit_not_ready, ...). The fix persists last_alert to disk and reloads it on
init, so a sticky alert stays muted until its cooldown genuinely elapses.
"""
import core.health_watchdog as hw
from core.health_watchdog import HealthWatchdog


class _DummyNotifier:
    def __init__(self):
        self.sent = []

    def alert(self, message, title=None, context=None):
        self.sent.append(title)


def test_cooldown_persists_across_restart(tmp_path, monkeypatch):
    state = tmp_path / "watchdog_cooldown_state.json"
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", state)

    # First boot: alert fires and persists its timestamp.
    n1 = _DummyNotifier()
    HealthWatchdog(bot_engine=None, notifier=n1)._alert(
        "audit_not_ready", "WARN", "verdict NOT_READY")
    assert n1.sent == ["[Watchdog/WARN] audit_not_ready"]
    assert state.exists()

    # Simulated restart: a brand-new instance loads the persisted cooldown and
    # must NOT re-fire the still-true sticky alert within the cooldown window.
    n2 = _DummyNotifier()
    HealthWatchdog(bot_engine=None, notifier=n2)._alert(
        "audit_not_ready", "WARN", "verdict NOT_READY")
    assert n2.sent == []


def test_cooldown_expires_normally(tmp_path, monkeypatch):
    state = tmp_path / "watchdog_cooldown_state.json"
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", state)

    n = _DummyNotifier()
    w = HealthWatchdog(bot_engine=None, notifier=n)
    w._alert("audit_not_ready", "WARN", "verdict NOT_READY")
    assert n.sent == ["[Watchdog/WARN] audit_not_ready"]

    # Force the persisted timestamp older than the cooldown -> should re-fire.
    cooldown = hw.COOLDOWN_SEC["audit_not_ready"]
    w._state.last_alert["audit_not_ready"] -= cooldown + 1
    w._alert("audit_not_ready", "WARN", "verdict NOT_READY")
    assert n.sent == ["[Watchdog/WARN] audit_not_ready"] * 2


def test_corrupt_state_file_does_not_break_startup(tmp_path, monkeypatch):
    state = tmp_path / "watchdog_cooldown_state.json"
    state.write_text("{ this is not valid json", encoding="utf-8")
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", state)

    w = HealthWatchdog(bot_engine=None, notifier=_DummyNotifier())
    assert w._state.last_alert == {}  # loaded gracefully, no crash
