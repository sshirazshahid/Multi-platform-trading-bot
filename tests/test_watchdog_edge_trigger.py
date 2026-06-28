"""Edge-triggered / debounced watchdog alerting (2026-06-28).

Bug: persistent, known conditions (model_pointer_invalid, forward_feeds_stale,
audit_not_ready) re-emailed on every cooldown tick, and transient startup flaps
(a feed momentarily stale before its harvester warms up) fired immediately.

Fix: _edge_alert notifies ONCE when a condition becomes true, stays silent while
it persists, re-arms when it clears, and (with grace_sec) debounces transients.
Plus the model-pointer check is silent unless the model is on the live path
(SIGNAL_SOURCE=mcp).
"""
import core.health_watchdog as hw
import pytest
from core.health_watchdog import HealthWatchdog


class _DummyNotifier:
    def __init__(self):
        self.sent = []

    def alert(self, message, title=None, context=None):
        self.sent.append(title)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")


def _wd(n):
    return HealthWatchdog(bot_engine=None, notifier=n)


def test_edge_fires_once_then_silent():
    n = _DummyNotifier()
    w = _wd(n)
    for _ in range(4):
        w._edge_alert("k", True, "WARN", "bad")
    assert n.sent == ["[Watchdog/WARN] k"]


def test_edge_rearms_after_recovery():
    n = _DummyNotifier()
    w = _wd(n)
    w._edge_alert("k", True, "WARN", "bad")
    w._edge_alert("k", False, "WARN", "")  # recover -> re-arm
    w._edge_alert("k", True, "WARN", "bad")  # break again -> fire again
    assert n.sent == ["[Watchdog/WARN] k"] * 2


def test_grace_debounces_transient():
    n = _DummyNotifier()
    w = _wd(n)
    w._edge_alert("k", True, "WARN", "bad", grace_sec=600)  # within grace
    assert n.sent == []
    w._edge_alert("k", False, "WARN", "")  # recovered before grace -> still silent
    assert n.sent == []


def test_grace_alerts_when_sustained():
    n = _DummyNotifier()
    w = _wd(n)
    w._edge_alert("k", True, "WARN", "bad", grace_sec=600)  # seeds first_seen
    w._first_seen["k"] -= 601  # simulate the condition persisting past the grace
    w._edge_alert("k", True, "WARN", "bad", grace_sec=600)
    assert n.sent == ["[Watchdog/WARN] k"]


def test_edge_silent_across_restart(tmp_path, monkeypatch):
    state = tmp_path / "cd.json"
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", state)
    n1 = _DummyNotifier()
    _wd(n1)._edge_alert("k", True, "WARN", "bad")
    assert n1.sent == ["[Watchdog/WARN] k"]
    n2 = _DummyNotifier()  # simulated restart loads persisted state
    _wd(n2)._edge_alert("k", True, "WARN", "bad")
    assert n2.sent == []


def test_model_pointer_silent_off_mcp_path(monkeypatch):
    import config
    monkeypatch.setattr(config, "SIGNAL_SOURCE", "machine", raising=False)
    n = _DummyNotifier()
    w = _wd(n)
    w._check_model_pointer_valid()
    assert n.sent == []
