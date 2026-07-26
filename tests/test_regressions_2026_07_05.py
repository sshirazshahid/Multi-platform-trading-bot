"""Regression tests for the 2026-07-05 adversarial-review fixes.

1. Supervisor wedged-holder repair: an unhealthy feed whose process is still
   ALIVE holds the singleton lock (utils.process_lock), so a bare respawn used
   to exit instantly and the repair no-oped forever while reporting ok=True.
   The supervisor must now kill the wedged holder(s) before respawning.
2. Spot regime frozen-state: fabricated index timestamps (ts=0..n-1) from
   plain-close inputs made the same-bar guard fire on every fixed-length
   rolling window (prev_ts == last_ts == n-1), freezing the persisted state
   forever — a crash could never flip the regime to DEFENSIVE.
"""

from __future__ import annotations


def test_supervisor_kills_wedged_feed_holder_before_respawn(tmp_path, monkeypatch):
    import core.self_healing_supervisor as sh
    from core.feed_health import FORWARD_FEEDS
    from core.self_healing_policy import SelfHealingPolicy

    started: list[str] = []
    terminated: list[list[int]] = []

    # Feed 'l2' reports unhealthy (stale) while its process is still alive.
    monkeypatch.setattr(
        sh,
        "read_forward_feed_status",
        lambda root: [
            {"name": spec.name, "exists": True, "connected": True,
             "fresh": spec.name != "l2", "error": None}
            for spec in FORWARD_FEEDS
        ],
    )
    monkeypatch.setattr(sh, "unhealthy_forward_feeds", lambda records: ["l2"])
    fake_proc = {
        "ProcessId": 4242,
        "CommandLine": r"python scripts\harvest_l2.py",
        "WorkingDirectory": str(sh.ROOT),
    }
    monkeypatch.setattr(sh, "_process_snapshot", lambda: [fake_proc])
    monkeypatch.setattr(
        sh, "_logical_counts",
        lambda procs: {"l2": 1, "skew": 1, "tv": 1, "liquidations": 1},
    )
    monkeypatch.setattr(
        sh, "_terminate_feed_processes",
        lambda procs, feed, cfg: terminated.append(sh._pids_for_feed(procs, feed))
        or sh._pids_for_feed(procs, feed),
    )
    monkeypatch.setattr(
        sh, "_start_process",
        lambda script, cfg: started.append(script)
        or {"script": script, "started": True, "dry_run": True},
    )

    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping(
            {
                "dry_run": True,
                "repair_enabled": True,
                "retrain_enabled": False,
                "adapt_enabled": False,
                "min_interval_sec": 0,
            }
        ),
        policy=SelfHealingPolicy(sh.ROOT, runtime_root=tmp_path),
    )
    report = sup.tick(force=True)

    # The wedged holder was killed BEFORE the respawn, and the action says so.
    assert terminated == [[4242]]
    assert any("harvest_l2.py" in s for s in started)
    l2_actions = [
        a for a in report["actions"]
        if a.get("type") == "restart_feed" and a.get("target") == "l2"
        and not a.get("skipped")
    ]
    assert l2_actions, report["actions"]
    assert l2_actions[0].get("wedged_holder") is True
    assert l2_actions[0].get("killed_pids") == [4242]


def test_supervisor_pid_matching_is_script_scoped():
    import core.self_healing_supervisor as sh

    procs = [
        {
            "ProcessId": 1,
            "CommandLine": r"python scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {
            "ProcessId": 2,
            "CommandLine": r"python scripts\harvest_skew.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {"ProcessId": 3, "CommandLine": r"python main.py", "WorkingDirectory": str(sh.ROOT)},
        {
            "ProcessId": "bad",
            "CommandLine": r"python scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
    ]
    assert sh._pids_for_script(procs, "scripts/harvest_l2.py") == [1]
    assert sh._pids_for_script(procs, "scripts/harvest_tv.py") == []


def test_spot_regime_index_ts_never_freezes_persisted_state():
    from core.spot_regime import REGIME_DEFENSIVE, daily_sma200_atr_regime

    # 247 flat bars at 100, then 3 decisive closes at 50 — far below any
    # SMA200/ATR band. Fabricated INDEX timestamps (the plain-close coercion
    # shape): last_ts == 249 on every fixed-length window.
    rows = [
        {"ts": i, "high": float(v), "low": float(v), "close": float(v)}
        for i, v in enumerate([100.0] * 247 + [50.0] * 3)
    ]
    prev = {
        name: {"state": "above", "streak": 0, "streak_dir": None, "last_bar_ts": 249}
        for name in ("BTC", "ETH")
    }
    # Pre-fix: prev_ts == last_ts (249 == 249) short-circuited to the persisted
    # 'above' forever — the crash below the band could NEVER flip the regime.
    regime, states = daily_sma200_atr_regime(rows, rows, prev_states=prev)
    assert states["BTC"]["state"] == "below"
    assert states["ETH"]["state"] == "below"
    assert regime == REGIME_DEFENSIVE


def test_spot_regime_epoch_ts_same_bar_guard_still_holds():
    from core.spot_regime import daily_sma200_atr_regime

    # With GENUINE epoch timestamps, the same-bar guard must still be
    # idempotent: a second call on the same closed bar returns the persisted
    # state untouched.
    base = 1_751_500_800  # epoch seconds
    rows = [
        {"ts": base + i * 86_400, "high": float(v), "low": float(v), "close": float(v)}
        for i, v in enumerate([100.0] * 247 + [50.0] * 3)
    ]
    last_ts = rows[-1]["ts"]
    prev = {
        name: {"state": "above", "streak": 1, "streak_dir": "below", "last_bar_ts": last_ts}
        for name in ("BTC", "ETH")
    }
    _, states = daily_sma200_atr_regime(rows, rows, prev_states=prev)
    assert states["BTC"] == {
        "state": "above", "streak": 1, "streak_dir": "below", "last_bar_ts": last_ts,
    }
