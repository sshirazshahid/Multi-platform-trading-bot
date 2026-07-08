"""C8 (tpbot retrofit): clock-drift sampling + watchdog alert.

Gap (survey 2026-07-08, lever 9): three-layer timestamp mitigation existed
(init-time load_time_difference, ccxt adjustForTimeDifference + fat
recvWindow, reactive -1021/10002 resync) but drift was only ever DETECTED
after a signed call already failed. The repo's own plan calls Windows clock
drift "the #1 silent killer" — yet none of health_watchdog's 14 checks
covered the clock.

New pieces:
  * core.bot_engine.sample_clock_drift_ms(exchange) — one-shot NTP-style
    offset sample: server_time - (t0+t1)/2 (first-order network-latency
    compensated); None when the client lacks fetch_time or the call fails
    (a missing sample must NEVER count as a health failure)
  * _check_exchange_health stores per-venue drift in engine._clock_drift_ms
    and warns on sustained drift (2 consecutive samples > threshold,
    mirroring the high-latency counter pattern)
  * heartbeat.json gains "clock_drift_ms" (source-scan pinned)
  * health_watchdog check #15: edge-triggered per-venue alert when
    |drift| > config.CLOCK_DRIFT_ALERT_MS (default 500)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import config
from core.bot_engine import sample_clock_drift_ms
from core.health_watchdog import HealthWatchdog


# ── the sampler ──────────────────────────────────────────────────────────────
def _ex_with_server_offset(offset_ms):
    import time as _t

    ex = MagicMock()
    ex.exchange.fetch_time.side_effect = lambda: (_t.time() * 1000.0) + offset_ms
    return ex


def test_sample_measures_positive_offset_within_tolerance():
    drift = sample_clock_drift_ms(_ex_with_server_offset(+2000.0))
    assert drift is not None
    assert abs(drift - 2000.0) < 250.0  # sample runtime noise tolerance


def test_sample_measures_negative_offset():
    drift = sample_clock_drift_ms(_ex_with_server_offset(-1500.0))
    assert drift is not None
    assert abs(drift + 1500.0) < 250.0


def test_sample_none_when_no_fetch_time():
    ex = MagicMock()
    ex.exchange = object()  # no fetch_time attribute
    assert sample_clock_drift_ms(ex) is None


def test_sample_none_on_error_or_zero():
    ex = MagicMock()
    ex.exchange.fetch_time.side_effect = RuntimeError("down")
    assert sample_clock_drift_ms(ex) is None
    ex2 = MagicMock()
    ex2.exchange.fetch_time.return_value = 0
    assert sample_clock_drift_ms(ex2) is None


def test_sample_none_when_no_raw_client():
    ex = MagicMock()
    ex.exchange = None
    assert sample_clock_drift_ms(ex) is None


# ── config flag ──────────────────────────────────────────────────────────────
def test_config_threshold_exists_default_500():
    assert getattr(config, "CLOCK_DRIFT_ALERT_MS", None) == 500


# ── watchdog check #15 ───────────────────────────────────────────────────────
def _watchdog(drift_map):
    engine = MagicMock()
    engine._clock_drift_ms = drift_map
    notifier = MagicMock()
    wd = HealthWatchdog.__new__(HealthWatchdog)
    wd._engine = engine
    wd._notifier = notifier
    wd._risk = None
    from core.health_watchdog import WatchdogState

    wd._state = WatchdogState()
    wd._first_seen = {}
    wd._persist_cooldowns = lambda: None  # no disk writes in tests
    return wd, notifier


def test_watchdog_alerts_on_drift_above_threshold():
    wd, notifier = _watchdog({"binance": 800.0})
    wd._check_clock_drift()
    assert notifier.alert.call_count == 1
    msg = notifier.alert.call_args[0][0]
    assert "binance" in msg and "800" in msg


def test_watchdog_silent_below_threshold():
    wd, notifier = _watchdog({"binance": 120.0, "bybit": -300.0})
    wd._check_clock_drift()
    notifier.alert.assert_not_called()


def test_watchdog_negative_drift_uses_absolute_value():
    wd, notifier = _watchdog({"bybit": -900.0})
    wd._check_clock_drift()
    assert notifier.alert.call_count == 1


def test_watchdog_edge_triggered_once_per_episode_and_rearms():
    wd, notifier = _watchdog({"binance": 800.0})
    wd._check_clock_drift()
    wd._check_clock_drift()  # still bad -> muted (edge-triggered)
    assert notifier.alert.call_count == 1
    wd._engine._clock_drift_ms = {"binance": 50.0}
    wd._check_clock_drift()  # clears -> re-arms
    wd._engine._clock_drift_ms = {"binance": 900.0}
    wd._check_clock_drift()  # new episode -> alerts again
    assert notifier.alert.call_count == 2


def test_watchdog_ignores_missing_or_none_samples():
    wd, notifier = _watchdog({"binance": None})
    wd._check_clock_drift()
    notifier.alert.assert_not_called()
    wd2, notifier2 = _watchdog({})
    wd2._check_clock_drift()
    notifier2.alert.assert_not_called()


def test_check_registered_in_tick():
    src = Path("core/health_watchdog.py").read_text(encoding="utf-8")
    i = src.index("def tick(")
    assert "self._check_clock_drift," in src[i : i + 1200], "clock-drift check must run from tick()"


# ── engine wiring pinned by source scan ──────────────────────────────────────
def test_heartbeat_carries_clock_drift_field():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i = src.index("def _write_heartbeat")
    assert '"clock_drift_ms"' in src[i : i + 3000], (
        "heartbeat.json must surface per-venue clock drift"
    )


def test_health_cycle_samples_drift():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i = src.index("def _check_exchange_health")
    assert "sample_clock_drift_ms" in src[i : i + 5000], (
        "_check_exchange_health must sample venue clock drift each cycle"
    )
