from __future__ import annotations

from types import SimpleNamespace

from core.health_watchdog import HealthWatchdog
from utils import notifier as notifier_module


def _enabled_notifier(monkeypatch):
    monkeypatch.setattr(notifier_module, "GMAIL_SENDER", "sender@example.com")
    monkeypatch.setattr(notifier_module, "GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setattr(notifier_module, "GMAIL_RECIPIENT", "owner@example.com")
    return notifier_module.EmailNotifier()


def test_smtp_has_hard_timeout_and_failed_send_does_not_consume_cooldown(monkeypatch):
    notifier = _enabled_notifier(monkeypatch)
    calls = []

    class SMTP:
        def __init__(self, host, port, *, context, timeout):
            calls.append((host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, sender, password):
            raise TimeoutError("smtp unavailable")

    monkeypatch.setattr(notifier_module.smtplib, "SMTP_SSL", SMTP)

    assert notifier.send("health", "<p>test</p>") is False
    assert calls == [
        (notifier.SMTP_HOST, notifier.SMTP_PORT, notifier.SMTP_TIMEOUT_SECONDS)
    ]
    assert "health" not in notifier._last_send


def test_successful_send_records_cooldown(monkeypatch):
    notifier = _enabled_notifier(monkeypatch)

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, sender, password):
            pass

        def sendmail(self, sender, recipient, message):
            pass

    monkeypatch.setattr(notifier_module.smtplib, "SMTP_SSL", SMTP)

    assert notifier.send("health", "<p>test</p>") is True
    assert "health" in notifier._last_send
    assert notifier.send("health", "<p>deduped</p>") is True


def test_watchdog_does_not_latch_an_enabled_transport_failure(tmp_path, monkeypatch):
    class FailedNotifier:
        enabled = True

        def alert(self, *args, **kwargs):
            return False

    monkeypatch.setattr("core.health_watchdog.COOLDOWN_STATE_PATH", tmp_path / "cooldown.json")
    watchdog = HealthWatchdog(
        SimpleNamespace(),
        notifier=FailedNotifier(),
        warehouse_path=tmp_path / "warehouse.sqlite",
    )

    watchdog._alert("smtp_failure", "critical", "test")

    assert "smtp_failure" not in watchdog._state.last_alert

