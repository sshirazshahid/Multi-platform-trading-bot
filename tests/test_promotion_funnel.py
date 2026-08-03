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
    expected = len(pf.PROBE_LANES) + len(pf.UNLOCK_ARM_LANES)
    assert all(l.state == "ERROR" for l in lanes) and len(lanes) == expected


def test_bundle_mr_lanes_registered():
    """Owner-directed bundle-test MR probes (2026-07-19, NOT a pipeline GO):
    both arms are 4h, so each lane keys on its own DISTINCT agent_id."""
    assert pf.PROBE_LANES["zfade_4h_cfg365"] == ("ZfadeProbeAgent", "4h")
    assert pf.PROBE_LANES["rsi2_4h_cfg226"] == ("Rsi2TrackerProbeAgent", "4h")
    # tsmom×2 + breakout + bundle×2 + pullback; unlock arms live in UNLOCK_ARM_LANES
    assert len(pf.PROBE_LANES) == 6
    assert len(pf.UNLOCK_ARM_LANES) == 2


def test_bundle_mr_lanes_carry_universe_widened_stamp(tmp_path):
    """Owner-approved 2026-07-20 widening: both bundle-MR lanes' accrual
    cohorts changed universe mid-stream (frozen 5 -> spec-derived), so any
    future promotion dossier MUST disclose the widening moment via
    detail.universe_widened_utc (ISO UTC). Cohorts NOT wiped by design."""
    from datetime import datetime

    db, c = _mk_shadow_db(tmp_path)
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lanes = {l.lane: l for l in pf.probe_lane_states(ro, time.time())}
    for lane in ("zfade_4h_cfg365", "rsi2_4h_cfg226"):
        stamp = lanes[lane].detail.get("universe_widened_utc")
        assert stamp, f"{lane} missing universe_widened_utc"
        parsed = datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0  # explicit UTC
    # lanes whose accrual universe never changed are NOT stamped
    for lane in ("tsmom_20d_1h", "tsmom_20d_4h", "breakout_60d",
                 "unlock_short_w1", "unlock_short_w2"):
        assert "universe_widened_utc" not in lanes[lane].detail


def test_unlock_arms_are_separate_funnel_lanes():
    assert "unlock_short" not in pf.PROBE_LANES
    assert pf.UNLOCK_ARM_LANES["unlock_short_w1"][1] == "unlock_short_w1_v1"
    assert pf.UNLOCK_ARM_LANES["unlock_short_w2"][1] == "unlock_short_w2_v1"


def test_unlock_arm_lanes_count_by_model_version(tmp_path):
    import sqlite3
    import time

    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, ts REAL, agent_id TEXT,"
        " timeframe TEXT, proposal_id TEXT, label_status TEXT, p_win REAL, model_version TEXT)"
    )
    c.execute(
        "CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)"
    )
    now = time.time()
    # 2 proposals on W1, 0 on W2
    for i in range(2):
        pid = f"u1-{i}"
        c.execute(
            "INSERT INTO shadow_decisions "
            "(ts, agent_id, timeframe, proposal_id, label_status, p_win, model_version) "
            "VALUES (?,?,?,?,?,?,?)",
            (now, "UnlockShortProbeAgent", "1d", pid, "PENDING", 0.6,
             "unlock_short_w1_v1"),
        )
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lanes = {l.lane: l for l in pf.probe_lane_states(ro, now)}
    assert lanes["unlock_short_w1"].detail["proposals"] == 2
    assert lanes["unlock_short_w1"].state == "ACCRUING"
    assert lanes["unlock_short_w2"].detail["proposals"] == 0
    assert lanes["unlock_short_w2"].state == "IDLE"

    assert pf.classify_base("TZA") == "tokenized"     # leveraged-ETF explicit list
    assert pf.classify_base("SOXS") == "tokenized"
    assert pf.classify_base("NVDA") == "tokenized"    # static stock set
    assert pf.classify_base("XAU") == "tokenized"     # commodity set
    assert pf.classify_base("PEPE") == "unclassified"


def test_listing_lane_starved_when_all_recent_tokenized(tmp_path):
    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
              " shortable INTEGER, created_ts REAL)")
    c.execute("CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)")
    now = time.time()
    for i, b in enumerate(["TZA", "SOXS", "NVDA"]):
        c.execute("INSERT INTO shadow_listing_probe VALUES (?,?,?,?,?)",
                  (f"ls{i}", b, "SKIP_UNSHORTABLE", 0, now - i * 86400))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ls = pf.listing_lane_state(ro, now)
    assert ls.state == "STARVED"
    assert ls.detail["known_tokenized_listings_30d"] == 3
    assert ls.detail["unclassified_listings_30d"] == 0
    assert ls.detail["starvation_reason"] == "no_actionable_shortable_listing"


def test_listing_lane_idle_when_all_skip_not_crypto(tmp_path):
    """Unanimous TradFi scope skips are idle-by-market, not probe starvation."""
    import sqlite3
    import time

    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
        " shortable INTEGER, created_ts REAL)"
    )
    c.execute(
        "CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)"
    )
    now = time.time()
    for i, b in enumerate(["GS", "SNOW", "HK0700", "PYPL"]):
        c.execute(
            "INSERT INTO shadow_listing_probe VALUES (?,?,?,?,?)",
            (f"ls{i}", b, "SKIP_NOT_CRYPTO", 0, now - i * 3600),
        )
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ls = pf.listing_lane_state(ro, now)
    assert ls.state == "IDLE"
    assert ls.detail["actionable_proposals_30d"] == 0
    assert ls.detail["skip_not_crypto_30d"] == 4
    assert ls.detail.get("scope_only") is True
    assert ls.detail["starvation_reason"] == "all_listings_out_of_crypto_universe"


def test_listing_lane_still_starved_on_mixed_skips_without_enter(tmp_path):
    import sqlite3
    import time

    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
        " shortable INTEGER, created_ts REAL)"
    )
    c.execute(
        "CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)"
    )
    now = time.time()
    rows = [
        ("a", "PEPE", "SKIP_NOT_CRYPTO"),
        ("b", "WIF", "SKIP_UNSHORTABLE"),
        ("c", "BONK", "SKIP_NO_FUNDING"),
    ]
    for i, (pid, base, dec) in enumerate(rows):
        c.execute(
            "INSERT INTO shadow_listing_probe VALUES (?,?,?,?,?)",
            (pid, base, dec, 0, now - i * 3600),
        )
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ls = pf.listing_lane_state(ro, now)
    assert ls.state == "STARVED"
    assert ls.detail.get("scope_only") is not True
    assert ls.detail["starvation_reason"] == "no_actionable_shortable_listing"


def test_unlock_calendar_coverage_flags_short_horizon(tmp_path):
    cal = tmp_path / "unlock_calendar"
    cal.mkdir()
    now = time.time()
    (cal / "AAA.json").write_text(json.dumps(
        {"events": [{"ts": now + 10 * 86400}]}))          # only 10 days forward
    cov = pf.unlock_calendar_coverage(cal, now)
    assert cov["forward_days"] < 30 and cov["starved"] is True
    assert "--forward-days 60" in cov["backfill_cmd"]


def _write_gate_log(p, entries):
    p.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_f1_alert_needs_three_consecutive_positives(tmp_path):
    log = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    mk = lambda i, edge: {"ts": now - 600 + i * 60, "symbol": "XRP/USDT",  # noqa: E731
                          "venue": "bitget", "net_edge_bps": edge}
    _write_gate_log(log, [mk(0, 5.0), mk(1, 6.0)])          # only 2 consecutive
    assert pf.f1_lane_state(log, now).detail["alert"] is False
    _write_gate_log(log, [mk(0, 5.0), mk(1, 6.0), mk(2, 7.0)])
    st = pf.f1_lane_state(log, now)
    assert st.detail["alert"] is True and st.state == "ACCRUING"
    assert st.detail["top_edges"][0]["symbol"] == "XRP/USDT"


def test_f1_idle_and_error_paths(tmp_path):
    log = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    _write_gate_log(log, [{"ts": now, "symbol": "XRP/USDT", "venue": "bitget",
                           "net_edge_bps": -20.0}])
    st = pf.f1_lane_state(log, now)
    assert st.state == "IDLE" and st.detail["alert"] is False
    assert pf.f1_lane_state(tmp_path / "missing.jsonl", now).state == "ERROR"


def test_directional_lane_reads_current_profile(tmp_path):
    gp = tmp_path / "goal_progress.json"
    gp.write_text(json.dumps({"lanes": [
        {"lane": "current_profile_directional", "closed_outcomes": 3,
         "wins": 2, "win_rate": 0.667, "net_after_cost_pnl": 1.5,
         "profit_factor": 2.0, "expectancy_per_outcome": 0.5,
         "target_status": "INSUFFICIENT_SAMPLE", "profile": "STANDARD"}]}))
    st = pf.directional_paper_lane_state(gp)
    assert (st.resolved, st.wins, st.wr) == (3, 2, 0.667)
    assert st.state == "ACCRUING" and st.detail["net_after_cost_pnl"] == 1.5


def _outcomes(n_win, n_loss, p_hi=0.7, p_lo=0.3):
    """Wins carry high p_win, losses low — a discriminating score (AUC ~1)."""
    return ([{"net_pnl": 1.0, "p_win": p_hi}] * n_win
            + [{"net_pnl": -1.0, "p_win": p_lo}] * n_loss)


def test_gate_passes_on_strong_synthetic_lane():
    """F2 (2026-07-20 audit): run_gate hard-coded dsr ok:False and pbo
    ok:False, making passed=all(...) permanently False — the dossier leg
    could NEVER fire. Restored: dsr computed via the funnel's own _dsr proxy
    vs MIN_DSR; pbo stays not-computable on a single stream but is
    informational ok:True with a note. A strong 30-outcome lane (WR 0.80,
    AUC 1.0, net > 0) must pass."""
    res = pf.run_gate(_outcomes(24, 6))   # WR 0.80, AUC 1.0
    assert res["gates"]["oos_wr"]["ok"] and res["gates"]["auc"]["ok"]
    dsr = res["gates"]["dsr"]
    assert dsr["computable"] is True and dsr["value"] is not None
    assert dsr["ok"] is True and dsr["value"] >= pf.MIN_DSR
    pbo = res["gates"]["pbo"]
    assert pbo["ok"] is True and pbo["computable"] is False and pbo["note"]
    assert res["passed"] is True


def test_gate_fails_on_weak_lane():
    """Weak lane (WR 0.50, net 0) still fails — F2 must not soften the gate."""
    res = pf.run_gate(_outcomes(15, 15))
    assert res["passed"] is False
    assert res["gates"]["oos_wr"]["ok"] is False
    assert res["gates"]["net_after_cost_pnl"]["ok"] is False


def test_gate_fails_on_nondiscriminating_score():
    res = pf.run_gate(_outcomes(24, 6, p_hi=0.5, p_lo=0.5))  # AUC 0.5
    assert res["passed"] is False and res["gates"]["auc"]["ok"] is False


def test_gate_fails_below_wr_floor():
    res = pf.run_gate(_outcomes(15, 15))  # WR 0.50 < 0.55
    assert res["passed"] is False and res["gates"]["oos_wr"]["ok"] is False


def test_dossier_written_complete_and_idempotent(tmp_path):
    lane = pf.LaneState(lane="tsmom_20d_1h", state="GATE_READY", resolved=30, wins=20,
                        wr=0.667, floor_progress="30/30")
    gate = {"passed": True, "gates": {"oos_wr": {"value": 0.667, "threshold": 0.55, "ok": True}}}
    outcomes = [{"net_pnl": 1.0, "p_win": 0.7}] * 30
    d = pf.build_dossier(lane, gate, outcomes, tmp_path, "20260718")
    assert d is not None and (d / "evidence.md").exists() and (d / "evidence.json").exists()
    assert (d / "proposed_change.patch").exists()
    md = (d / "evidence.md").read_text(encoding="utf-8")
    assert "owner sign-off" in md.lower() and "0.667" in md
    assert pf.build_dossier(lane, gate, outcomes, tmp_path, "20260718") is None  # idempotent


def test_listing_short_gate_ready_can_stage_dossier(tmp_path, monkeypatch):
    """listing_short is outside PROBE_LANES but must still run gate+dossier."""
    import sqlite3
    import time
    from datetime import datetime, timezone

    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, "
        "decision TEXT, shortable INTEGER, created_ts REAL)"
    )
    c.execute(
        "CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)"
    )
    c.execute(
        "CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, ts REAL, agent_id TEXT,"
        " timeframe TEXT, proposal_id TEXT, label_status TEXT, p_win REAL)"
    )
    now = time.time()
    for i in range(30):
        pid = f"L{i}"
        c.execute(
            "INSERT INTO shadow_listing_probe VALUES (?,?,?,?,?)",
            (pid, "NEWCOIN", "SHORT", 1, now - 86400),
        )
        c.execute(
            "INSERT INTO shadow_outcomes VALUES (?,?,?)",
            (pid, 1.0, now - 1000),
        )
    c.commit()
    c.close()
    (tmp_path / "carry_gate_log.jsonl").write_text("")
    (tmp_path / "goal_progress.json").write_text(json.dumps({"lanes": []}))
    (tmp_path / "unlock_calendar").mkdir()
    paths = {
        "warehouse": db,
        "gate_log": tmp_path / "carry_gate_log.jsonl",
        "goal_json": tmp_path / "goal_progress.json",
        "cal_dir": tmp_path / "unlock_calendar",
        "funnel_json": tmp_path / "promotion_funnel.json",
        "dossier_dir": tmp_path / "dossiers",
        "journal_dir": tmp_path / "journal",
    }

    def _pass_gate(outcomes):
        return {"passed": True, "gates": {}}

    monkeypatch.setattr(pf, "run_gate", _pass_gate)
    doc = pf.compute_all(paths, now)
    listing = next(l for l in doc["lanes"] if l["lane"] == "listing_short")
    assert listing["state"] == "STAGED"
    assert list((tmp_path / "dossiers").glob("listing_short_*"))


def test_compute_all_and_journal_on_state_change(tmp_path):
    import sqlite3
    import time

    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, ts REAL, agent_id TEXT,"
              " timeframe TEXT, proposal_id TEXT, label_status TEXT)")
    c.execute("CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)")
    c.execute("CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
              " shortable INTEGER, created_ts REAL)")
    c.commit()
    (tmp_path / "carry_gate_log.jsonl").write_text("")
    (tmp_path / "goal_progress.json").write_text(json.dumps({"lanes": []}))
    (tmp_path / "unlock_calendar").mkdir()
    paths = {"warehouse": db, "gate_log": tmp_path / "carry_gate_log.jsonl",
             "goal_json": tmp_path / "goal_progress.json",
             "cal_dir": tmp_path / "unlock_calendar",
             "funnel_json": tmp_path / "promotion_funnel.json",
             "dossier_dir": tmp_path / "dossiers", "journal_dir": tmp_path / "journal"}
    now = time.time()
    doc1 = pf.compute_all(paths, now)
    assert {l["lane"] for l in doc1["lanes"]} >= {
        "tsmom_20d_1h", "listing_short", "f1_carry",
        "directional_paper_cohort", "unlock_short_w1", "unlock_short_w2",
    }
    pf.persist(doc1, paths)            # first run: journal written (all states new)
    files = list((tmp_path / "journal").glob("*.md"))
    assert len(files) == 1
    first = files[0].read_text(encoding="utf-8")
    doc2 = pf.compute_all(paths, now + 60)
    pf.persist(doc2, paths)            # no state change: journal unchanged
    assert files[0].read_text(encoding="utf-8") == first
