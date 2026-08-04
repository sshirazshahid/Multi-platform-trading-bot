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


def test_model_starving_alerts_when_no_opens(tmp_path, monkeypatch):
    # 2026-07-28: this test used to write {"ts": ..., "type": "OPEN"} at the
    # TOP level of mcp_decisions.jsonl — a shape the bot never emits (real
    # top-level types are portfolio/rejection/position_monitor). The test and
    # the check agreed with each other and both disagreed with production, so
    # the permanent false alarm went unseen. The source of truth is now
    # positions.json/open_time; see tests/test_watchdog_model_starving.py.
    monkeypatch.setattr(
        hw.HealthWatchdog, "_expected_idle_under_strict_econ_gate",
        staticmethod(lambda: False),
    )
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=0.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"open": [], "closed": [
        {"symbol": "ETH/USDT:USDT",
         "open_time": time.time() - hw.MODEL_STARVE_HOURS * 3600 - 10}]}))
    monkeypatch.setattr(hw, "POSITIONS_PATH", pos)
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


class _DriftEngine:
    def __init__(self, drift_map):
        self._clock_drift_ms = drift_map


class _DriftWD:
    """Minimal stub exercising HealthWatchdog._check_clock_drift unbound."""

    def __init__(self, drift_map):
        self._engine = _DriftEngine(drift_map)
        self.calls = []

    def _edge_alert(self, key, is_bad, level, message,
                    context=None, *, grace_sec=0.0):
        self.calls.append((key, is_bad, message))


def _drift_thr():
    try:
        from config import CLOCK_DRIFT_ALERT_MS
        return float(CLOCK_DRIFT_ALERT_MS)
    except ImportError:
        return 500.0


def test_clock_drift_hysteresis_band_holds_episode_state():
    """A venue inside [0.8*thr, thr] neither alerts nor clears — the
    2026-08-02 flap re-fired 3 WARNs because hovering at ~thr cleared and
    re-armed the episode on alternate ticks."""
    from core.health_watchdog import HealthWatchdog
    thr = _drift_thr()
    wd = _DriftWD({"binance": thr * 1.2, "bybit": thr * 0.9})
    HealthWatchdog._check_clock_drift(wd)
    keys = [k for k, _b, _m in wd.calls]
    assert "clock_drift_binance" in keys
    assert "clock_drift_bybit" not in keys  # held, not cleared


def test_clock_drift_missing_sample_holds_episode_state():
    """None samples (slow-RTT discard) must not clear an episode latch."""
    from core.health_watchdog import HealthWatchdog
    wd = _DriftWD({"binance": None})
    HealthWatchdog._check_clock_drift(wd)
    assert wd.calls == []


def test_clock_drift_clear_below_band_rearms():
    from core.health_watchdog import HealthWatchdog
    thr = _drift_thr()
    wd = _DriftWD({"binance": thr * 0.5})
    HealthWatchdog._check_clock_drift(wd)
    assert wd.calls and wd.calls[0][0] == "clock_drift_binance"
    assert wd.calls[0][1] is False  # explicit clear re-arms the episode


def test_clock_drift_near_band_attribution_blames_local_clock():
    """Decaying local-clock episode: one venue above thr, the other at 0.92x
    thr. Old logic said 'suspect this venue, not the local clock' — actively
    wrong operator guidance during the measured 2026-08-02 episode."""
    from core.health_watchdog import HealthWatchdog
    thr = _drift_thr()
    wd = _DriftWD({"binance": thr * 1.04, "bybit": thr * 0.92})
    HealthWatchdog._check_clock_drift(wd)
    bad_msgs = [m for _k, b, m in wd.calls if b]
    assert bad_msgs and "drifted together" in bad_msgs[0]


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

def test_model_starving_ignores_the_decisions_log(tmp_path, monkeypatch):
    """Replaces the F6 ISO-timestamp test (2026-07-20), which is now moot: this
    check no longer parses mcp_decisions.jsonl at all, because its record shapes
    cannot answer 'did a position open'. Nested OPEN actions are PROPOSALS, and
    the sibling 'rejection' records include successful maker fills.

    This pins the replacement contract: a decisions log stuffed with OPEN-shaped
    records must NOT suppress a genuine starvation alert. Anyone reintroducing
    the old source has to fail this test first."""
    from datetime import datetime, timezone
    monkeypatch.setattr(
        hw.HealthWatchdog, "_expected_idle_under_strict_econ_gate",
        staticmethod(lambda: False),
    )
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=0.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    now_iso = datetime.now(timezone.utc).isoformat()
    (tmp_path / "mcp_decisions.jsonl").write_text(
        json.dumps({"ts": now_iso, "type": "OPEN"}) + "\n"
        + json.dumps({"ts": now_iso, "type": "portfolio", "decisions": {
            "actions": [{"type": "OPEN", "symbol": "ETH/USDT:USDT"}]}}) + "\n"
    )
    pos = tmp_path / "positions.json"          # no recent opens: genuinely starving
    pos.write_text(json.dumps({"open": [], "closed": []}))
    monkeypatch.setattr(hw, "POSITIONS_PATH", pos)
    wd.tick()
    assert any("model_gate_starving" in c["title"] for c in n.calls), (
        "the decisions log must not be consulted — positions.json is the source")


def test_model_starving_suppressed_by_recent_open(tmp_path, monkeypatch):
    """A RECENT position open must suppress the alert.

    This is THE test that should have caught the false alarm and did not: it
    previously wrote a recent top-level {"type": "OPEN"} record — a shape the
    bot never emits — so it passed against a check that could never see it.
    Now it uses positions.json, the shape the bot actually writes."""
    n = _FakeNotifier()
    risk = SimpleNamespace(daily_pnl=0.0)
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, risk_manager=risk,
                           warehouse_path=tmp_path / "wh.sqlite")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"open": [], "closed": [
        {"symbol": "AAVE/USDT:USDT", "open_time": time.time() - 1200}]}))
    monkeypatch.setattr(hw, "POSITIONS_PATH", pos)
    wd.tick()
    assert not any("model_gate_starving" in c["title"] for c in n.calls)


def _make_open_trade_wh(path: Path, rows: list[tuple]) -> None:
    """rows: (id, exchange, symbol, side, ts_entry)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, exchange TEXT, "
        "symbol TEXT, side TEXT, status TEXT, ts_entry REAL)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id, exchange, symbol, side, status, ts_entry) "
            "VALUES (?,?,?,?,?,?)",
            (r[0], r[1], r[2], r[3], "OPEN", r[4]),
        )
    conn.commit()
    conn.close()


def test_stuck_open_silent_when_still_in_tracker(tmp_path, monkeypatch):
    """Live tracker opens older than STUCK_OPEN_HOURS are holds, not orphans."""
    wh = tmp_path / "wh.sqlite"
    age = time.time() - (hw.STUCK_OPEN_HOURS + 2) * 3600
    _make_open_trade_wh(wh, [
        (1, "binance", "BNB/USDT:USDT", "buy", age),
    ])
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"open": [{
        "exchange": "Binance",
        "symbol": "BNB/USDT:USDT",
        "side": "buy",
        "open_time": age,
    }], "closed": []}))
    monkeypatch.setattr(hw, "POSITIONS_PATH", pos)
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, warehouse_path=wh)
    wd.tick()
    assert not any("stuck_open_positions" in c["title"] for c in n.calls)


def test_stuck_open_alerts_orphan_warehouse_row(tmp_path, monkeypatch):
    """Warehouse OPEN with no matching tracker open is a true bookkeeping leak."""
    wh = tmp_path / "wh.sqlite"
    age = time.time() - (hw.STUCK_OPEN_HOURS + 2) * 3600
    _make_open_trade_wh(wh, [
        (99, "binance", "ETH/USDT:USDT", "buy", age),
    ])
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"open": [], "closed": []}))
    monkeypatch.setattr(hw, "POSITIONS_PATH", pos)
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, warehouse_path=wh)
    wd.tick()
    assert any("stuck_open_positions" in c["title"] for c in n.calls)
    assert any("orphan" in c["message"].lower() for c in n.calls)


def test_stuck_open_edge_silent_while_orphan_persists(tmp_path, monkeypatch):
    """Orphan alert is edge-triggered — no hourly re-email for the same leak."""
    wh = tmp_path / "wh.sqlite"
    age = time.time() - (hw.STUCK_OPEN_HOURS + 2) * 3600
    _make_open_trade_wh(wh, [
        (99, "binance", "ETH/USDT:USDT", "buy", age),
    ])
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"open": [], "closed": []}))
    monkeypatch.setattr(hw, "POSITIONS_PATH", pos)
    n = _FakeNotifier()
    wd = hw.HealthWatchdog(_make_engine(), notifier=n, warehouse_path=wh)
    wd.tick()
    wd.tick()
    assert sum(1 for c in n.calls if "stuck_open_positions" in c["title"]) == 1
