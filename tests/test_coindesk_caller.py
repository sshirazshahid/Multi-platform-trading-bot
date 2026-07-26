import threading
import time
import urllib.error
from concurrent.futures import CancelledError

import pytest

from core.data_feeds import _coindesk_caller as caller


class _Response:
    def __init__(self, payload=b'{"ok": true}'):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


@pytest.fixture(autouse=True)
def _reset_host_throttle():
    with caller._HOST_THROTTLE_LOCK:
        caller._HOST_NEXT_REQUEST.clear()
    yield
    with caller._HOST_THROTTLE_LOCK:
        caller._HOST_NEXT_REQUEST.clear()


def test_http_get_honors_retry_after(monkeypatch):
    responses = iter([
        urllib.error.HTTPError(
            "https://example.test/data",
            429,
            "rate limited",
            {"Retry-After": "0.25"},
            None,
        ),
        _Response(),
    ])
    waits = []

    def urlopen(request, timeout):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(caller, "_HOST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(caller.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        caller,
        "_wait_or_cancel",
        lambda delay, *, deadline, cancel_event: waits.append(delay),
    )

    result = caller._http_get(
        "https://example.test/data", deadline=time.monotonic() + 5
    )

    assert result == {"ok": True}
    assert waits == pytest.approx([0.25])


def test_http_get_uses_bounded_exponential_backoff(monkeypatch):
    responses = iter([
        urllib.error.URLError("temporary one"),
        urllib.error.URLError("temporary two"),
        _Response(),
    ])
    waits = []

    def urlopen(request, timeout):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(caller, "_HOST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(caller, "_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(caller, "_BACKOFF_BASE_SECONDS", 0.1)
    monkeypatch.setattr(caller, "_MAX_BACKOFF_SECONDS", 0.15)
    monkeypatch.setattr(caller.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        caller,
        "_wait_or_cancel",
        lambda delay, *, deadline, cancel_event: waits.append(delay),
    )

    result = caller._http_get(
        "https://example.test/data", deadline=time.monotonic() + 5
    )

    assert result == {"ok": True}
    assert waits == pytest.approx([0.1, 0.15])


def test_http_get_throttles_each_host_independently(monkeypatch):
    waits = []
    monkeypatch.setattr(caller, "_HOST_MIN_INTERVAL_SECONDS", 0.5)
    monkeypatch.setattr(caller.urllib.request, "urlopen", lambda request, timeout: _Response())
    monkeypatch.setattr(
        caller,
        "_wait_or_cancel",
        lambda delay, *, deadline, cancel_event: waits.append(delay),
    )

    deadline = time.monotonic() + 5
    caller._http_get("https://one.example/data", deadline=deadline)
    caller._http_get("https://two.example/data", deadline=deadline)
    caller._http_get("https://one.example/other", deadline=deadline)

    assert len(waits) == 1
    assert waits[0] == pytest.approx(0.5, abs=0.05)


def test_http_get_rejects_a_throttle_wait_over_the_bound(monkeypatch):
    monkeypatch.setattr(caller, "_HOST_MIN_INTERVAL_SECONDS", 1.0)
    monkeypatch.setattr(caller, "_MAX_THROTTLE_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(caller.urllib.request, "urlopen", lambda request, timeout: _Response())

    deadline = time.monotonic() + 5
    caller._http_get("https://example.test/first", deadline=deadline)

    with pytest.raises(TimeoutError, match="throttle"):
        caller._http_get("https://example.test/second", deadline=deadline)


def test_http_get_checks_cancellation_before_network(monkeypatch):
    cancel_event = threading.Event()
    cancel_event.set()
    called = False

    def urlopen(request, timeout):
        nonlocal called
        called = True
        return _Response()

    monkeypatch.setattr(caller.urllib.request, "urlopen", urlopen)

    with pytest.raises(CancelledError):
        caller._http_get(
            "https://example.test/data",
            deadline=time.monotonic() + 5,
            cancel_event=cancel_event,
        )

    assert called is False


def test_http_get_rejects_expired_deadline_before_network(monkeypatch):
    called = False

    def urlopen(request, timeout):
        nonlocal called
        called = True
        return _Response()

    monkeypatch.setattr(caller.urllib.request, "urlopen", urlopen)

    with pytest.raises(TimeoutError, match="deadline"):
        caller._http_get(
            "https://example.test/data", deadline=time.monotonic() - 1
        )

    assert called is False


def test_call_context_bounds_the_socket_timeout(monkeypatch):
    cancel_event = threading.Event()
    deadline = time.monotonic() + 0.5
    observed = {}

    def urlopen(request, timeout):
        observed["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(caller, "_HOST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(caller.urllib.request, "urlopen", urlopen)

    with caller.coindesk_call_context(
        deadline=deadline, cancel_event=cancel_event
    ):
        caller._http_get("https://example.test/data")

    assert 0 < observed["timeout"] <= 0.5


def test_cancellation_interrupts_retry_sequence(monkeypatch):
    cancel_event = threading.Event()

    def urlopen(request, timeout):
        cancel_event.set()
        raise urllib.error.URLError("temporary")

    monkeypatch.setattr(caller, "_HOST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(caller.urllib.request, "urlopen", urlopen)

    with pytest.raises(CancelledError):
        caller._http_get(
            "https://example.test/data",
            deadline=time.monotonic() + 5,
            cancel_event=cancel_event,
        )


def test_public_callers_accept_deadline_and_cancellation(monkeypatch):
    cancel_event = threading.Event()
    deadline = time.monotonic() + 5
    captured = {}

    def http_get(url, **kwargs):
        captured.update(kwargs)
        return {"Data": []}

    monkeypatch.setattr(caller, "_http_get", http_get)

    caller.call_coindesk_news(
        limit=1, deadline=deadline, cancel_event=cancel_event
    )

    assert captured["deadline"] == deadline
    assert captured["cancel_event"] is cancel_event
