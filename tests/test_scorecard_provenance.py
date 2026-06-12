"""weekly_scorecard provenance-health line + streaming _calls_per_day (spec 19).

Synthetic tmp_path fixtures only; never touches data/.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import weekly_scorecard as ws  # noqa: E402

NOW = time.time()
T0 = NOW - 2 * 86400


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ── streaming _calls_per_day (E-11) ──────────────────────────────────


def test_calls_per_day_streams_multikb_lines(tmp_path):
    audit = tmp_path / "claude_audit"
    audit.mkdir()
    t0 = 1_750_000_000.0
    big = "x" * 4096  # multi-KB prompt/raw_response per the new audit schema
    f = audit / "calls_2026-06.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for i in range(620):
            fh.write(json.dumps({"ts": t0 + i, "prompt": big, "raw_response": big}) + "\n")
        fh.write("{not json}\n")  # ignored, no crash
        fh.write(json.dumps({"ts": t0 - 50}) + "\n")  # outside window
        iso = datetime.fromtimestamp(t0 + 700, tz=timezone.utc).isoformat()
        fh.write(json.dumps({"ts": iso}) + "\n")  # ISO ts still parsed
    assert f.stat().st_size > 5_000_000  # genuinely multi-MB
    rate = ws._calls_per_day(t0, t0 + 86400.0, audit_dir=audit)
    assert rate == 621.0  # 620 epoch + 1 ISO


def test_aux_claude_calls_wiring(tmp_path, monkeypatch):
    """aux_metrics('claude_calls') keeps working through the extracted helper."""
    monkeypatch.chdir(tmp_path)
    audit = tmp_path / "data" / "claude_audit"
    audit.mkdir(parents=True)
    _write_jsonl(
        audit / "calls_2026-06.jsonl", [{"ts": NOW - 86400}, {"ts": NOW - 3600}, {"ts": NOW - 1800}]
    )
    out = ws.aux_metrics(
        "claude_calls",
        [{"ts_entry": NOW - 86400}],
        [{"ts_entry": NOW - 3600}, {"ts_entry": NOW - 900}],
        1.0,
        1.0,
    )
    assert "aux_error" not in out
    assert isinstance(out["pre_calls_per_day"], float)
    assert isinstance(out["post_calls_per_day"], float)


# ── provenance-health line ───────────────────────────────────────────


_SCHEMA = (
    "CREATE TABLE trades ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts_entry REAL, ts_exit REAL,"
    " symbol TEXT, side TEXT, strategy_family TEXT, status TEXT,"
    " realized_pnl REAL, r_multiple REAL, decision_id TEXT, exit_decision_id TEXT)"
)


def _wh(dirpath, rows, name="wh.sqlite"):
    db = dirpath / name
    conn = sqlite3.connect(str(db))
    conn.execute(_SCHEMA)
    conn.executemany(
        "INSERT INTO trades (ts_entry, ts_exit, symbol, side, strategy_family,"
        " status, realized_pnl, r_multiple, decision_id, exit_decision_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def test_provenance_health_with_new_fields(tmp_path):
    _write_jsonl(
        tmp_path / "mcp_decisions.jsonl",
        [
            {
                "ts": T0,
                "type": "portfolio",
                "truncated": True,
                "decisions": {
                    "actions": [
                        {
                            "type": "OPEN",
                            "symbol": "BTC/USDT:USDT",
                            "decision_id": "p1",
                            "source": "claude",
                            "repaired": True,
                        },
                        {
                            "type": "OPEN",
                            "symbol": "ETH/USDT:USDT",
                            "decision_id": "p2",
                            "source": "algo",
                        },
                    ]
                },
            },
            {
                "ts": T0 + 60,
                "type": "rejection",
                "decision_id": "p1",
                "symbol": "BTC/USDT:USDT",
                "reason": "risk_gate",
                "stage": "bot_engine",
            },
        ],
    )
    db = _wh(
        tmp_path,
        [
            (
                T0 + 100,
                T0 + 200,
                "ETH/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                1.0,
                1.0,
                "p2",
                None,
            ),  # joined -> per-source
            (
                T0 + 300,
                T0 + 400,
                "SOL/USDT:USDT",
                "buy",
                "claude_portfolio",
                "CLOSED",
                -1.0,
                -1.0,
                None,
                None,
            ),  # TRUE order-side orphan
        ],
    )
    af = tmp_path / "claude_audit_failures.json"
    af.write_text(json.dumps({"count": 2, "last_ts": T0, "last_err": "oserror"}), encoding="utf-8")
    prov = ws.provenance_health(
        decisions_glob=str(tmp_path / "mcp_decisions*.jsonl"), warehouse=db, audit_failures=af
    )
    assert prov["audit_write_failures"] == 2
    assert prov["repaired"] == 1
    assert prov["truncated"] == 1
    assert prov["rejections"] == 1
    assert prov["true_orphans"] == 1  # p1 rejection-covered, p2 joined
    assert prov["per_source"]["algo"]["n"] == 1
    md = ws.render_markdown([], {"date": "2026-06-12", "provenance": prov})
    assert "Provenance health:" in md
    assert "audit-write failures: 2" in md
    assert "repaired: 1" in md and "truncated: 1" in md
    assert "rejections: 1" in md and "true orphans: 1" in md
    assert "algo" in md  # per-source joined line


def test_provenance_health_degrades_na(tmp_path):
    # pre-restart shaped rows: no decision_id/source/repaired fields anywhere
    _write_jsonl(
        tmp_path / "mcp_decisions.jsonl",
        [
            {
                "ts": T0,
                "type": "portfolio",
                "decisions": {"actions": [{"type": "OPEN", "symbol": "BTC/USDT:USDT"}]},
            },
        ],
    )
    prov = ws.provenance_health(
        decisions_glob=str(tmp_path / "mcp_decisions*.jsonl"),
        warehouse=tmp_path / "missing.sqlite",
        audit_failures=tmp_path / "missing.json",
    )
    assert prov["audit_write_failures"] == "n/a"
    assert prov["repaired"] == "n/a" and prov["truncated"] == "n/a"
    assert prov["rejections"] == "n/a" and prov["true_orphans"] == "n/a"
    md = ws.render_markdown([], {"date": "x", "provenance": prov})
    assert "Provenance health:" in md and "n/a" in md
    # meta without provenance -> no line (back-compat with old callers)
    assert "Provenance health:" not in ws.render_markdown([], {"date": "x"})


def test_scorecard_main_end_to_end_renders_line(tmp_path):
    reg = tmp_path / "experiments.json"
    reg.write_text(json.dumps({"experiments": []}), encoding="utf-8")
    outdir = tmp_path / "reports"
    rc = ws.main(
        [
            "--db",
            str(tmp_path / "wh.sqlite"),
            "--registry",
            str(reg),
            "--out-dir",
            str(outdir),
            "--as-of",
            str(NOW),
            "--decisions-glob",
            str(tmp_path / "none*.jsonl"),
            "--audit-failures",
            str(tmp_path / "missing.json"),
        ]
    )
    assert rc == 0
    md_files = list(outdir.glob("scorecard_*.md"))
    assert md_files
    assert "Provenance health:" in md_files[0].read_text(encoding="utf-8")
    js = json.loads(next(iter(outdir.glob("scorecard_*.json"))).read_text(encoding="utf-8"))
    assert "provenance" in js["meta"]
