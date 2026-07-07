from __future__ import annotations

import json


def test_self_healing_repairs_missing_feed_process(tmp_path, monkeypatch):
    import core.self_healing_supervisor as sh

    started = []
    monkeypatch.setattr(
        sh,
        "read_forward_feed_status",
        lambda root: [
            {
                "name": spec.name,
                "exists": True,
                "connected": True,
                "fresh": True,
                "error": None,
            }
            for spec in sh.FORWARD_FEEDS
        ],
    )
    monkeypatch.setattr(sh, "_powershell_process_snapshot", lambda: [])
    monkeypatch.setattr(
        sh,
        "_start_process",
        lambda script, cfg: started.append(script)
        or {"script": script, "started": True, "dry_run": True},
    )

    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping(
            {
                "dry_run": True,
                "state_path": tmp_path / "state.json",
                "report_dir": tmp_path / "reports",
                "repair_enabled": True,
                "retrain_enabled": False,
                "adapt_enabled": False,
                "min_interval_sec": 0,
            }
        )
    )
    report = sup.tick(force=True)

    assert report["verdict"] == "OK"
    assert any("harvest_l2.py" in s for s in started)
    assert any(a["type"] == "repair_feed" for a in report["actions"])


def test_self_healing_promotes_candidate_only_after_replay_gate(tmp_path, monkeypatch):
    import core.self_healing_supervisor as sh

    def fake_run(self, candidate):
        good = candidate["id"] == "strict_confirmed_rr22"
        return {
            "candidate": candidate,
            "path": str(tmp_path / f"{candidate['id']}.json"),
            "label": "unit",
            "metrics": {
                "summary": {
                    "n": 120 if good else 120,
                    "avg_ret": 0.001 if good else -0.001,
                    "profit_factor": 1.2 if good else 0.8,
                },
                "folds": {
                    "fold1": {"n": 40, "avg_ret": 0.001 if good else -0.001, "profit_factor": 1.1},
                    "fold2": {"n": 40, "avg_ret": 0.001 if good else -0.001, "profit_factor": 1.1},
                    "fold3": {"n": 40, "avg_ret": -0.001, "profit_factor": 0.9},
                },
            },
        }

    monkeypatch.setattr(sh.SelfHealingSupervisor, "_run_replay_candidate", fake_run)
    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping(
            {
                "dry_run": False,
                "state_path": tmp_path / "state.json",
                "report_dir": tmp_path / "reports",
                "adaptive_config_path": tmp_path / "adaptive.json",
                "repair_enabled": False,
                "retrain_enabled": False,
                "adapt_enabled": True,
                "min_interval_sec": 0,
                "adapt_cooldown_sec": 0,
                "min_trades": 100,
                "min_profit_factor": 1.05,
                "min_positive_folds": 2,
            }
        )
    )
    report = sup.tick(force=True)
    payload = json.loads((tmp_path / "adaptive.json").read_text(encoding="utf-8"))

    action = report["actions"][0]
    assert action["type"] == "adapt_strategy"
    assert action["promoted"] is True
    assert action["candidate_id"] == "strict_confirmed_rr22"
    assert payload["candidate_id"] == "strict_confirmed_rr22"
    assert payload["apply_scope"] == "paper"
