"""Trigger-by-trigger tests for core.health_watchdog.

Each test fixtures one condition in isolation by patching the module-level
paths to tmp files, then asserts the notifier received the expected alert.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import health_watchdog as hw


@pytest.fixture(autouse=True)
def _isolate_cooldown_state(tmp_path, monkeypatch):
    """HealthWatchdog now persists/reloads cooldowns from COOLDOWN_STATE_PATH.
    Redirect it to a per-test tmp file so state never leaks across tests or
    into the real data/ directory."""
    monkeypatch.setattr(
        hw, "COOLDOWN_STATE_PATH", tmp_path / "watchdog_cooldown_state.json")


class _FakeNotifier:
    def __init__(self):
        self.calls: list[dict] = []

    def alert(self, message, *, title="Alert", context=None):
        self.calls.append({"message": message, "title": title, "context": context})


def _make_engine(halted=None):
    return SimpleNamespace(_exchange_halted=set(halted or []))


def _make_warehouse(path: Path, recent_pnls: list[float]) -> None:
    """Insert closed trades with given pnls (most recent first)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, ts_entry REAL, ts_exit REAL, "
        "exchange TEXT, symbol TEXT, status TEXT, realized_pnl REAL)"
    )
    now = time.time()
    for i, p in enumerate(recent_pnls):
        ts = now - i * 60
        conn.execute(
            "INSERT INTO trades (ts_entry, ts_exit, exchange, symbol, status, realized_pnl) "
            "VALUES (?,?,?,?,?,?)",
            (ts - 60, ts, "test", "BTC/USDT", "CLOSED", p),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_paths(tmp_path, monkeypatch):
    """Redirect module-level paths into tmp_path for every test."""
    monkeypatch.setattr(hw, "HEARTBEAT_PATH",   tmp_path / "heartbeat.json")
    monkeypatch.setattr(hw, "REVIEW_FLAG_PATH", tmp_path / "review_required.json")
    monkeypatch.setattr(hw, "POST_MORTEM_PATH", tmp_path / "post_mortem.json")
    monkeypatch.setattr(hw, "DECISIONS_PATH",   tmp_path / "mcp_decisions.jsonl")
    monkeypatch.setattr(hw, "WAREHOUSE_PATH",   tmp_path / "wh.sqlite")
    monkeypatch.setattr(hw, "CARRY_HEARTBEAT_PATH",
                        tmp_path / "carry_heartbeat.json")
    yield


def test_review_flag_alerts(tmp_path):
    flag = tmp_path / "review_required.json"
    flag.write_text(json.dumps({"reason": "test", "action": "obs", "ts": "now"}))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert any("spec12_review_required" in c["title"] for c in n.calls)


def test_review_flag_clears_when_file_removed(tmp_path):
    flag = tmp_path / "review_required.json"
    flag.write_text("{}")
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    flag.unlink()
    flag.write_text("{}")
    wd._state.last_alert.pop("spec12_review_required", None)
    wd.tick()
    titles = [c["title"] for c in n.calls]
    assert sum(1 for t in titles if "spec12_review_required" in t) >= 2


def test_heartbeat_stale_alerts(tmp_path):
    hb = tmp_path / "heartbeat.json"
    hb.write_text("{}")
    old = time.time() - hw.HEARTBEAT_STALE_SEC - 10
    import os
    os.utime(hb, (old, old))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert any("heartbeat_stale" in c["title"] for c in n.calls)


def test_heartbeat_fresh_no_alert(tmp_path):
    hb = tmp_path / "heartbeat.json"
    hb.write_text("{}")
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert not any("heartbeat_stale" in c["title"] for c in n.calls)


def test_exchange_halted_persistence(tmp_path, monkeypatch):
    """First tick records first_seen; second tick after threshold fires."""
    monkeypatch.setattr(hw, "EXCHANGE_HALT_SEC", 1)
    n = _FakeNotifier()
    eng = _make_engine(halted=["binance"])
    wd = hw.HealthWatchdog(eng, notifier=n, warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert not any("exchange_halted" in c["title"] for c in n.calls)
    time.sleep(1.1)
    wd.tick()
    assert any("exchange_halted" in c["title"] for c in n.calls)


def test_sl_placement_failed_alerts(tmp_path):
    pm = tmp_path / "post_mortem.json"
    pm.write_text(json.dumps({"analyses": [{
        "timestamp": time.time(),
        "symbol":    "ATOM/USDT:USDT",
        "exchange":  "binance",
        "side":      "buy",
        "pnl_pct":   -5.5,
        "leverage":  3,
        "close_reason": "sl_placement_failed",
    }]}))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert any("sl_placement_failed" in c["title"] for c in n.calls)


def test_sl_placement_below_threshold_no_alert(tmp_path):
    pm = tmp_path / "post_mortem.json"
    pm.write_text(json.dumps({"analyses": [{
        "timestamp": time.time(),
        "pnl_pct": -1.0, "close_reason": "sl_placement_failed",
    }]}))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert not any("sl_placement_failed" in c["title"] for c in n.calls)


def test_loss_streak_alerts(tmp_path):
    db = tmp_path / "wh.sqlite"
    _make_warehouse(db, [-0.5, -1.0, -2.0, -3.0])
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, warehouse_path=db)
    wd.tick()
    assert any("loss_streak" in c["title"] for c in n.calls)


def test_loss_streak_broken_by_winner_no_alert(tmp_path):
    db = tmp_path / "wh.sqlite"
    _make_warehouse(db, [-0.5, +0.5, -1.0, -2.0])
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, warehouse_path=db)
    wd.tick()
    assert not any("loss_streak" in c["title"] for c in n.calls)


def test_model_starving_alerts_when_no_opens(tmp_path):
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=0.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    (tmp_path / "mcp_decisions.jsonl").write_text(
        json.dumps({"ts": time.time() - hw.MODEL_STARVE_HOURS * 3600 - 10,
                    "type": "OPEN"}) + "\n"
    )
    wd.tick()
    assert any("model_gate_starving" in c["title"] for c in n.calls)


def test_model_starving_silent_when_in_drawdown(tmp_path):
    """Don't nag operator when bot is genuinely in drawdown."""
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=-5.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert not any("model_gate_starving" in c["title"] for c in n.calls)


# ── W6: carry-runner heartbeat staleness (edge-triggered) ─────────────

def _carry_alerts(n):
    return [c for c in n.calls if "carry_heartbeat_stale" in c["title"]]


def test_carry_heartbeat_missing_no_alert(tmp_path):
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert _carry_alerts(n) == []  # "carry never ran" is not an alert


def test_carry_heartbeat_fresh_no_alert(tmp_path):
    (tmp_path / "carry_heartbeat.json").write_text("{}")
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert _carry_alerts(n) == []


def test_carry_heartbeat_stale_fires_once_then_silent(tmp_path):
    import os
    hb = tmp_path / "carry_heartbeat.json"
    hb.write_text("{}")
    old = time.time() - 2 * 3600  # 2h > 1h threshold
    os.utime(hb, (old, old))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    alerts = _carry_alerts(n)
    assert len(alerts) == 1
    assert "WARN" in alerts[0]["title"]
    wd.tick()
    assert len(_carry_alerts(n)) == 1  # edge-triggered: silent while sticky


def test_carry_heartbeat_refresh_rearms_then_fires_again(tmp_path):
    import os
    hb = tmp_path / "carry_heartbeat.json"
    hb.write_text("{}")
    old = time.time() - 2 * 3600
    os.utime(hb, (old, old))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert len(_carry_alerts(n)) == 1
    hb.write_text("{}")  # runner wrote again -> condition clears -> re-arm
    wd.tick()
    assert len(_carry_alerts(n)) == 1
    os.utime(hb, (old, old))  # goes stale again
    wd.tick()
    assert len(_carry_alerts(n)) == 2


# ── Rev 5.2: carry reduce-only recovery latched (edge-triggered) ──────

def _recovery_alerts(n):
    return [c for c in n.calls if "carry_recovery_active" in c["title"]]


def _write_carry_hb(tmp_path, recovery_active):
    (tmp_path / "carry_heartbeat.json").write_text(
        json.dumps({"ts": time.time(),
                    "summary": {"recovery_active": recovery_active}}))


def test_carry_recovery_missing_file_silent(tmp_path):
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert _recovery_alerts(n) == []


def test_carry_recovery_missing_key_silent(tmp_path):
    (tmp_path / "carry_heartbeat.json").write_text(
        json.dumps({"ts": time.time(), "summary": {"opened": 0}}))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    assert _recovery_alerts(n) == []


def test_carry_recovery_fires_once_then_rearms_on_clear(tmp_path):
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    _write_carry_hb(tmp_path, True)
    wd.tick()
    alerts = _recovery_alerts(n)
    assert len(alerts) == 1
    assert "ALERT" in alerts[0]["title"]
    assert "--clear-recovery" in alerts[0]["message"]
    wd.tick()
    assert len(_recovery_alerts(n)) == 1  # edge-triggered: silent while sticky
    _write_carry_hb(tmp_path, False)      # operator cleared -> re-arm
    wd.tick()
    assert len(_recovery_alerts(n)) == 1
    _write_carry_hb(tmp_path, True)       # latched again -> fires again
    wd.tick()
    assert len(_recovery_alerts(n)) == 2


def test_cooldown_suppresses_repeat(tmp_path):
    flag = tmp_path / "review_required.json"
    flag.write_text("{}")
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n,
                           warehouse_path=tmp_path / "wh.sqlite")
    wd.tick()
    wd.tick()
    wd.tick()
    review_alerts = [c for c in n.calls if "spec12_review_required" in c["title"]]
    assert len(review_alerts) == 1


# ------------------------------------------------ carry sample milestones
def _carry_state_file(tmp_path, n_resolved, wins):
    cycles = [{"label_status": "RESOLVED", "net_pnl": 1.0 if i < wins else -1.0}
              for i in range(n_resolved)]
    p = tmp_path / "carry_positions.json"
    p.write_text(json.dumps({"positions": {}, "blocks": {}, "cycles": cycles}),
                 encoding="utf-8")
    return p


def test_carry_milestone_announces_measured_wr_once(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "CARRY_STATE_PATH", _carry_state_file(tmp_path, 1, 1))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n)
    wd._check_carry_sample_milestones()
    assert len(n.calls) == 1
    msg = n.calls[0]["message"]
    assert "milestone 1" in msg and "1 resolved cycles" in msg and "100%" in msg
    wd._check_carry_sample_milestones()  # same count: no re-announce
    assert len(n.calls) == 1


def test_carry_milestone_ten_reports_sub_bar_wr(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "CARRY_STATE_PATH", _carry_state_file(tmp_path, 10, 7))
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n)
    wd._check_carry_sample_milestones()
    assert len(n.calls) == 1
    msg = n.calls[0]["message"]
    assert "milestone 10" in msg and "70%" in msg and "7W/3L" in msg
    assert n.calls[0]["context"]["win_rate_pct"] == 70.0


def test_carry_milestone_silent_without_file_or_cycles(tmp_path, monkeypatch):
    n = _FakeNotifier()
    monkeypatch.setattr(hw, "CARRY_STATE_PATH", tmp_path / "missing.json")
    hw.HealthWatchdog(_make_engine(), notifier=n)._check_carry_sample_milestones()
    monkeypatch.setattr(hw, "CARRY_STATE_PATH", _carry_state_file(tmp_path, 0, 0))
    hw.HealthWatchdog(_make_engine(), notifier=n)._check_carry_sample_milestones()
    assert n.calls == []


# ── F6 (2026-07-20 deep audit): ISO timestamps in mcp_decisions.jsonl ────────
# The log now carries ISO 'ts' strings ("2026-07-20T01:13:35.644077+00:00");
# float(rec["ts"]) raised on every line and the swallow-all except returned
# early, so the 6h zero-OPENs starvation alert could NEVER fire.

def test_model_starving_alert_fires_with_iso_timestamps(tmp_path):
    from datetime import datetime, timezone
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=0.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    old_iso = datetime.fromtimestamp(
        time.time() - hw.MODEL_STARVE_HOURS * 3600 - 10, tz=timezone.utc
    ).isoformat()
    (tmp_path / "mcp_decisions.jsonl").write_text(
        json.dumps({"ts": old_iso, "type": "OPEN"}) + "\n"
        + json.dumps({"ts": "not-a-timestamp", "type": "OPEN"}) + "\n"  # tolerated
    )
    wd.tick()
    assert any("model_gate_starving" in c["title"] for c in n.calls), (
        "6h-starvation alert must fire on an ISO-timestamped log")


def test_model_starving_suppressed_by_recent_iso_open(tmp_path):
    """A RECENT ISO-format OPEN must be recognized (parsed, not skipped)."""
    from datetime import datetime, timezone
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=0.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    recent_iso = datetime.now(timezone.utc).isoformat()
    (tmp_path / "mcp_decisions.jsonl").write_text(
        json.dumps({"ts": recent_iso, "type": "OPEN"}) + "\n")
    wd.tick()
    assert not any("model_gate_starving" in c["title"] for c in n.calls)
