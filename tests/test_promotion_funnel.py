"""Promotion funnel tests — synthetic stores only, no production data."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import promotion_funnel as pf  # noqa: E402


def test_lane_state_serializes_with_all_fields():
    ls = pf.LaneState(lane="x", state="ACCRUING", resolved=5, wins=3, wr=0.6,
                      floor_progress="5/30", accrual_rate_7d=1.2, eta_days=20.8,
                      detail={"k": "v"})
    d = ls.to_dict()
    assert d["lane"] == "x" and d["floor_progress"] == "5/30" and d["detail"] == {"k": "v"}


def test_atomic_write_json_replaces_not_partial(tmp_path):
    p = tmp_path / "out.json"
    pf.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert not (tmp_path / "out.json.tmp").exists()


def test_zero_live_path_imports():
    """Funnel import purity, checked in a FRESH interpreter: the test session
    itself loads banned modules (tests/conftest.py autouse fixtures import
    core.order_manager / core.kill_switch), so inspecting this process's
    sys.modules would test the harness, not the funnel."""
    banned = ("core.bot_engine", "core.order_manager", "exchanges", "config", "ccxt")
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r});\n"
        "import scripts.promotion_funnel\n"
        f"banned = {banned!r}\n"
        "loaded = set(sys.modules)\n"
        "bad = [b for b in banned\n"
        "       if any(m == b or m.startswith(b + '.') for m in loaded)]\n"
        "assert not bad, f'funnel pulled {bad}'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"funnel import purity failed:\n{res.stderr}"


import sqlite3  # noqa: E402
import time  # noqa: E402


def _mk_shadow_db(tmp_path):
    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, ts REAL, agent_id TEXT,"
              " timeframe TEXT, proposal_id TEXT, label_status TEXT)")
    c.execute("CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)")
    return db, c


def test_probe_lane_counts_resolved_and_wins_by_arm(tmp_path):
    db, c = _mk_shadow_db(tmp_path)
    now = time.time()
    for i in range(4):  # 4 resolved 1h tsmom, 3 wins, spread over last 7d (rate>0)
        c.execute("INSERT INTO shadow_decisions (ts, agent_id, timeframe, proposal_id, label_status)"
                  " VALUES (?,?,?,?,?)", (now - i * 86400, "TsmomProbeAgent", "1h", f"p{i}", "RESOLVED"))
        c.execute("INSERT INTO shadow_outcomes VALUES (?,?,?)",
                  (f"p{i}", 1.0 if i < 3 else -1.0, now - i * 86400))
    c.execute("INSERT INTO shadow_decisions (ts, agent_id, timeframe, proposal_id, label_status)"
              " VALUES (?,?,?,?,?)", (now, "TsmomProbeAgent", "4h", "p9", "PENDING"))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lanes = {l.lane: l for l in pf.probe_lane_states(ro, now)}
    l1 = lanes["tsmom_20d_1h"]
    assert (l1.resolved, l1.wins, l1.state) == (4, 3, "ACCRUING")
    assert l1.floor_progress == "4/30" and l1.accrual_rate_7d > 0 and l1.eta_days is not None
    assert lanes["tsmom_20d_4h"].resolved == 0
    assert lanes["breakout_60d"].state == "IDLE"  # zero proposals ever


def test_probe_lane_gate_ready_at_floor(tmp_path):
    db, c = _mk_shadow_db(tmp_path)
    now = time.time()
    for i in range(30):
        c.execute("INSERT INTO shadow_decisions (ts, agent_id, timeframe, proposal_id, label_status)"
                  " VALUES (?,?,?,?,?)", (now - i * 3600, "BreakoutProbeAgent", "4h", f"b{i}", "RESOLVED"))
        c.execute("INSERT INTO shadow_outcomes VALUES (?,?,?)", (f"b{i}", 1.0, now - i * 3600))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lanes = {l.lane: l for l in pf.probe_lane_states(ro, now)}
    assert lanes["breakout_60d"].state == "GATE_READY"


def test_probe_lane_error_isolated():
    ro = sqlite3.connect(":memory:")  # empty db: tables missing -> per-lane ERROR
    lanes = pf.probe_lane_states(ro, time.time())
    assert all(l.state == "ERROR" for l in lanes) and len(lanes) == len(pf.PROBE_LANES)


def test_classifier_tokenized_vs_crypto():
    assert pf.classify_base("TZA") == "tokenized"     # leveraged-ETF explicit list
    assert pf.classify_base("SOXS") == "tokenized"
    assert pf.classify_base("NVDA") == "tokenized"    # static stock set
    assert pf.classify_base("XAU") == "tokenized"     # commodity set
    assert pf.classify_base("PEPE") == "crypto"


def test_listing_lane_starved_when_all_recent_tokenized(tmp_path):
    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
              " shortable INTEGER, created_ts REAL)")
    now = time.time()
    for i, b in enumerate(["TZA", "SOXS", "NVDA"]):
        c.execute("INSERT INTO shadow_listing_probe VALUES (?,?,?,?,?)",
                  (f"ls{i}", b, "SKIP_UNSHORTABLE", 0, now - i * 86400))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ls = pf.listing_lane_state(ro, now)
    assert ls.state == "STARVED"
    assert ls.detail["crypto_native_listings_30d"] == 0
    assert ls.detail["tokenized_listings_30d"] == 3


def test_unlock_calendar_coverage_flags_short_horizon(tmp_path):
    cal = tmp_path / "unlock_calendar"
    cal.mkdir()
    now = time.time()
    (cal / "AAA.json").write_text(json.dumps(
        {"events": [{"ts": now + 10 * 86400}]}))          # only 10 days forward
    cov = pf.unlock_calendar_coverage(cal, now)
    assert cov["forward_days"] < 30 and cov["starved"] is True
    assert "--forward-days 60" in cov["backfill_cmd"]
