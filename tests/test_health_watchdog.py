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
