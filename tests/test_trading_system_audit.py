from __future__ import annotations

import json


def test_confluence_audit_flags_duplicate_rows(tmp_path, monkeypatch):
    import scripts.trading_system_audit as audit

    monkeypatch.setattr(audit, "ROOT", tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    csv_path = reports / "confluence_paper_trades.csv"
    csv_path.write_text(
        "\n".join(
            [
                "opened,closed,symbol,side,entry,exit,outcome,pnl_usd,balance",
                "2026-01-01,2026-01-02,BTC/USDT:USDT,buy,100,101,TP,1,5001",
                "2026-01-01,2026-01-02,BTC/USDT:USDT,buy,100,101,TP,1,5001",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    checks = []
    audit.audit_confluence_paper(checks)

    assert checks[0]["status"] == "FAIL"
    assert checks[0]["metrics"]["duplicate_rows_extra"] == 1


def test_logical_script_counts_collapse_venv_parent_child():
    import scripts.trading_system_audit as audit

    procs = [
        {
            "ProcessId": 10,
            "ParentProcessId": 1,
            "CommandLine": r"D:\repo\venv\Scripts\python.exe main.py",
        },
        {
            "ProcessId": 11,
            "ParentProcessId": 10,
            "CommandLine": r"C:\Python312\python.exe main.py",
        },
        {
            "ProcessId": 20,
            "ParentProcessId": 1,
            "CommandLine": r"D:\repo\venv\Scripts\python.exe scripts\harvest_l2.py",
        },
        {
            "ProcessId": 21,
            "ParentProcessId": 20,
            "CommandLine": r"C:\Python312\python.exe scripts\harvest_l2.py",
        },
    ]

    logical, raw = audit._logical_script_counts(procs)

    assert raw["main"] == 2
    assert logical["main"] == 1
    assert raw["l2"] == 2
    assert logical["l2"] == 1


def test_forward_feed_health_flags_stale_status(tmp_path):
    from core.feed_health import FORWARD_FEEDS, read_forward_feed_status, unhealthy_forward_feeds

    now = 10_000.0
    for spec in FORWARD_FEEDS:
        path = tmp_path.joinpath(*spec.status_path.split("\\"))
        path.parent.mkdir(parents=True, exist_ok=True)
        updated = now - (spec.max_age_sec + 1 if spec.name == "l2" else 10)
        path.write_text(
            json.dumps({"updated": updated, "connected": True, "total_polls": 1}),
            encoding="utf-8",
        )

    records = read_forward_feed_status(tmp_path, now=now)
    bad = unhealthy_forward_feeds(records)

    assert bad == ["l2"]


def test_forward_feed_audit_flags_missing_process(tmp_path, monkeypatch):
    import scripts.trading_system_audit as audit
    from core.feed_health import FORWARD_FEEDS

    now = 10_000.0
    for spec in FORWARD_FEEDS:
        path = tmp_path.joinpath(*spec.status_path.split("\\"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"updated": now, "connected": True, "total_polls": 1}),
            encoding="utf-8",
        )

    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(audit, "_windows_process_audit_supported", lambda: True)
    monkeypatch.setattr(
        audit,
        "_windows_python_processes",
        lambda: [
            {
                "ProcessId": 20,
                "ParentProcessId": 1,
                "CommandLine": r"D:\repo\venv\Scripts\python.exe scripts\harvest_l2.py",
            },
        ],
    )
    monkeypatch.setattr("core.feed_health.time.time", lambda: now)

    checks = []
    audit.audit_forward_feeds(checks)

    assert checks[0]["status"] == "FAIL"
    assert "liquidations" in checks[0]["metrics"]["unhealthy_feeds"]
