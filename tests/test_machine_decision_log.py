from __future__ import annotations

import json


def test_machine_decision_log_writes_detector_diagnostics(tmp_path, monkeypatch):
    import core.machine_signal as ms

    log_path = tmp_path / "machine_decisions.jsonl"
    monkeypatch.setattr(ms, "MACHINE_DECISION_LOG", log_path)

    ms._append_machine_decision(
        {
            "decision_id": "d1",
            "symbol": "ADA/USDT:USDT",
            "side": "sell",
            "machine_score_raw": 0.28,
            "confidence": 0.50,
            "reason": "valuation=bear",
            "machine_diagnostics": {"detectors": {"valuation": -1}},
        }
    )

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["decision_id"] == "d1"
    assert rows[0]["diagnostics"]["detectors"]["valuation"] == -1


def test_machine_decision_log_records_close_type(tmp_path, monkeypatch):
    import core.machine_signal as ms

    log_path = tmp_path / "machine_decisions.jsonl"
    monkeypatch.setattr(ms, "MACHINE_DECISION_LOG", log_path)

    ms._append_machine_decision(
        {
            "type": "CLOSE",
            "decision_id": "c1",
            "symbol": "ADA/USDT:USDT",
            "side": "sell",
            "confidence": 0.70,
            "reason": "machine_time_stop",
        }
    )

    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["type"] == "close"
    assert row["reason"] == "machine_time_stop"
