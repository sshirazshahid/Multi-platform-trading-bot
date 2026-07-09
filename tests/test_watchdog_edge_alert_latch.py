"""Regression: a failed notifier send must NOT latch a watchdog alert episode.

Pre-fix, ``HealthWatchdog._alert`` stamped ``last_alert[key]`` and persisted the
cooldown BEFORE attempting ``notifier.alert(...)``. One SMTP hiccup therefore
latched the episode permanently: the edge-triggered guard (``key in last_alert``)
then muted every subsequent tick, silencing a live safety ALERT. The latch must
happen only AFTER a successful send so the next tick retries on failure.
"""
from __future__ import annotations

from core import health_watchdog as hw


class _FlakyNotifier:
    def __init__(self):
        self.calls = 0
        self.fail = True

    def alert(self, message, *, title="Alert", context=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("smtp down")


def test_failed_send_leaves_episode_unlatched(monkeypatch, tmp_path):
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    notif = _FlakyNotifier()
    wd = hw.HealthWatchdog(bot_engine=None, notifier=notif)

    key = "sl_placement_failed"

    # 1) SMTP down: the alert is attempted but the episode is NOT latched.
    wd._edge_alert(key, is_bad=True, level="alert", message="naked position")
    assert notif.calls == 1
    assert key not in wd._state.last_alert  # not latched -> retriable

    # 2) Still down on the next tick: it retries (a phantom latch would mute it).
    wd._edge_alert(key, is_bad=True, level="alert", message="naked position")
    assert notif.calls == 2
    assert key not in wd._state.last_alert

    # 3) SMTP recovers: the send succeeds and NOW the episode latches.
    notif.fail = False
    wd._edge_alert(key, is_bad=True, level="alert", message="naked position")
    assert notif.calls == 3
    assert key in wd._state.last_alert

    # 4) Same still-true episode: edge-trigger + latch keep it muted.
    wd._edge_alert(key, is_bad=True, level="alert", message="naked position")
    assert notif.calls == 3
