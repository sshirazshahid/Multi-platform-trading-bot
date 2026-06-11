"""scripts/weekly_scorecard.py — experiment self-evaluation (2026-06-11).

Synthetic warehouse + registry; deterministic seeds; pinned --as-of.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import weekly_scorecard as ws  # noqa: E402

START = time.time() - 7 * 86400  # experiment activated 7 days ago


def _db(dirpath, pre_mean, post_mean, n_each=40):
    db = dirpath / "wh.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY, ts_entry REAL, ts_exit REAL,
            symbol TEXT, side TEXT, status TEXT, mode TEXT,
            realized_pnl REAL, partial_realized_pnl REAL,
            fee REAL, exit_reason TEXT)"""
    )
    rid = 0
    for window, mean in (("pre", pre_mean), ("post", post_mean)):
        for i in range(n_each):
            rid += 1
            # pre: inside [START-14d, START); post: inside [START, now)
            ts = (START - (i % 12 + 1) * 86400 if window == "pre"
                  else START + (i % 6) * 86400 + 3600)
            pnl = mean + (0.1 if i % 2 == 0 else -0.1)
            conn.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, ts, ts + 60, "BTC/USDT:USDT",
                 "sell" if i % 3 else "buy", "CLOSED", "PAPER",
                 pnl, 0.0, 0.02, "stop_loss" if i % 4 else "take_profit"))
    conn.commit()
    conn.close()
    return db


def _registry(dirpath, start_ts=START):
    reg = dirpath / "experiments.json"
    reg.write_text(json.dumps({"experiments": [{
        "id": "exp1", "label": "Test experiment", "start_ts": start_ts,
        "end_ts": None, "mode": "PAPER", "baseline_days": 14,
        "min_n": 20, "aux": "",
    }]}), encoding="utf-8")
    return reg


def test_improved_verdict_end_to_end(tmp_path):
    db = _db(tmp_path, pre_mean=-0.5, post_mean=+0.5)
    exps = ws.load_registry(_registry(tmp_path))
    r = ws.evaluate(exps[0], db, as_of=time.time(), seed=42)
    assert r["status"] == "EVALUATED"
    assert r["verdict"]["verdict"] == "IMPROVED"
    assert r["pre"]["n"] == 40 and r["post"]["n"] == 40
    # pre is all-losses (wr 0%): Wilson lower bound sits AT 0 — assert
    # the interval is ordered, bounded, and its upper bound is nonzero
    ci = r["pre"]["wr_ci95"]
    assert 0.0 <= ci[0] <= ci[1] <= 100.0 and ci[1] > 0.0


def test_worse_and_inconclusive(tmp_path):
    d1 = tmp_path / "w"
    d1.mkdir()
    worse = ws.evaluate(
        ws.load_registry(_registry(tmp_path))[0],
        _db(d1, pre_mean=+0.5, post_mean=-0.5),
        as_of=time.time(), seed=42)
    assert worse["verdict"]["verdict"] == "WORSE"
    d2 = tmp_path / "i"
    d2.mkdir()
    flat = ws.evaluate(
        ws.load_registry(_registry(tmp_path))[0],
        _db(d2, pre_mean=0.0, post_mean=0.01),
        as_of=time.time(), seed=42)
    assert flat["verdict"]["verdict"] == "INCONCLUSIVE"


def test_not_started_and_no_data(tmp_path):
    db = _db(tmp_path, -0.5, 0.5)
    not_started = ws.evaluate(
        ws.load_registry(_registry(tmp_path, start_ts=0))[0],
        db, as_of=time.time())
    assert not_started["status"] == "NOT_STARTED"
    empty_db = tmp_path / "empty.sqlite"
    sqlite3.connect(str(empty_db)).close()
    no_data = ws.evaluate(
        ws.load_registry(_registry(tmp_path))[0],
        empty_db, as_of=time.time())
    assert no_data["status"] == "NO_DATA"


def test_main_writes_reports_and_exits_zero(tmp_path):
    db = _db(tmp_path, -0.5, 0.5)
    reg = _registry(tmp_path)
    out = tmp_path / "reports"
    rc = ws.main(["--db", str(db), "--registry", str(reg),
                  "--out-dir", str(out), "--as-of", str(time.time()),
                  "--seed", "42"])
    assert rc == 0
    md = list(out.glob("scorecard_*.md"))
    js = list(out.glob("scorecard_*.json"))
    assert len(md) == 1 and len(js) == 1
    text = md[0].read_text(encoding="utf-8")
    assert "BUNDLE-level" in text          # honesty header
    assert "screening evidence" in text    # honesty header
    assert "modeled" in text               # contested cost-split label
    assert "VERDICT: IMPROVED" in text


def test_deterministic_across_runs(tmp_path):
    db = _db(tmp_path, -0.5, 0.5)
    exps = ws.load_registry(_registry(tmp_path))
    as_of = time.time()
    a = ws.evaluate(exps[0], db, as_of=as_of, seed=42)
    b = ws.evaluate(exps[0], db, as_of=as_of, seed=42)
    assert a["verdict"]["delta_ci"] == b["verdict"]["delta_ci"]


def test_ps1_wired():
    src = Path("scripts/retrain_weekly.ps1").read_text(encoding="utf-8")
    assert "weekly_scorecard" in src
