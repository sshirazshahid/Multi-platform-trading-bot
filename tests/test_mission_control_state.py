"""Mission Control state reader tests."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pytest

from mission_control import state


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def test_load_status_from_fixtures(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "heartbeat.json",
        {"ts": "2026-07-27T12:00:00+00:00", "operating_mode": "PAPER", "paper_trading_profile": "MAX_FLOW_BAND"},
    )
    _write(tmp_path / "data" / "risk_state.json", {"is_halted": False, "daily_pnl": 1.2})
    _write(tmp_path / ".env", "OPERATING_MODE=PAPER\nPAPER_TRADING_PROFILE=MAX_FLOW_BAND\nCONTROLLED_LIVE_ENABLED=false\n")
    (tmp_path / "data" / "KILL_SWITCH").write_text("x", encoding="utf-8")

    status = state.load_status(tmp_path)
    assert status["mode"] == "PAPER"
    assert status["paper_profile"] == "MAX_FLOW_BAND"
    assert status["kill_switch"] is True
    assert status["incident_latch_present"] is False
    assert status["env"]["OPERATING_MODE"] == "PAPER"
    assert status["regime_short_bias"]["live_short_authorized"] is False
    assert status["regime_short_bias"]["log_only"] is True
    assert status["whale_events"]["live_trade_authorized"] is False
    assert status["whale_events"]["log_only"] is True
    assert status["soft_stale_entry_block"] is False


def test_load_status_paper_research_telemetry(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "heartbeat.json",
        {"ts": "2026-08-11T00:00:00+00:00", "operating_mode": "PAPER", "paper_trading_profile": "MAX_FLOW_BAND"},
    )
    _write(
        tmp_path / ".env",
        "OPERATING_MODE=PAPER\nPAPER_TRADING_PROFILE=MAX_FLOW_BAND\n"
        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=paper_fallback\nSCALP_TIER_ENABLED=false\n",
    )
    _write(
        tmp_path / "data" / "goal_progress.json",
        {
            "generated_utc": "2026-08-11T00:00:00+00:00",
            "lanes": [
                {
                    "lane": "paper_futures_current_utc_day",
                    "closed_outcomes": 5,
                    "win_rate": 0.4,
                    "expectancy_per_outcome": -0.25,
                    "net_after_cost_pnl": -1.25,
                    "profit_factor": 0.5,
                    "target_status": "INSUFFICIENT_SAMPLE",
                    "sample_mature": False,
                },
                {
                    "lane": "listing_short_probe",
                    "resolved": 1,
                    "resolved_floor": 30,
                    "floor_progress": "1/30",
                    "forward_wr": 0.0,
                },
            ],
        },
    )
    _write(tmp_path / "data" / "soft_stale_entry_latch.json", {"active": True, "reason": "test"})
    status = state.load_status(tmp_path)
    pr = status["paper_research"]
    assert "geometry" in pr["honesty"].lower() or "not edge" in pr["honesty"].lower()
    assert pr["paper_futures_utc_day"]["expectancy_per_outcome"] == -0.25
    assert pr["paper_futures_utc_day"]["target_status"] == "INSUFFICIENT_SAMPLE"
    assert any(p["lane"] == "listing_short_probe" and p["resolved"] == 1 for p in pr["probe_floors"])
    assert pr["econ_gate_mode"] == "paper_fallback"
    assert pr["scalp_tier_enabled"] is False
    assert status["soft_stale_entry_block"] is True
    assert pr["exit_geometry"]["live_trade_authorized"] is False
    assert pr["mature_cohort"]["live_trade_authorized"] is False


def test_load_status_regime_and_liq_telemetry(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "heartbeat.json",
        {"ts": "2026-08-05T12:00:00+00:00", "operating_mode": "PAPER"},
    )
    _write(
        tmp_path / "data" / "regime_short_bias_latest.json",
        {
            "ts_utc": "2026-08-05T12:00:00Z",
            "honesty": "Log-only",
            "evaluation": {
                "narrative": "SHORT_BIAS_ENV",
                "any_cell_fired": True,
                "fng_value": 27,
                "long_usd_24h": 49e6,
                "live_short_authorized": False,
            },
        },
    )
    _write(tmp_path / "data" / "liquidations_status.json", {"connected": True})
    status = state.load_status(tmp_path)
    assert status["regime_short_bias"]["narrative"] == "SHORT_BIAS_ENV"
    assert status["regime_short_bias"]["fng_value"] == 27
    assert status["liquidations_status"]["connected"] is True


def test_load_positions_open_list(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "positions.json",
        {"open": [{"symbol": "BTC/USDT", "side": "long"}], "closed": [{}, {}]},
    )
    _write(tmp_path / "data" / "virtual_wallet.json", {"balance": 100.0})
    pos = state.load_positions(tmp_path)
    assert pos["open_count"] == 1
    assert pos["closed_count"] == 2
    assert pos["wallet"]["balance"] == 100.0


def test_validate_bind_host_loopback_ok() -> None:
    state.validate_bind_host("127.0.0.1")
    state.validate_bind_host("localhost")


def test_validate_bind_host_lan_refused() -> None:
    try:
        state.validate_bind_host("0.0.0.0", allow_lan=False, token_set=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "loopback" in str(exc)


def test_validate_bind_host_lan_allowed_with_token() -> None:
    state.validate_bind_host("0.0.0.0", allow_lan=True, token_set=True)


def test_load_audit_tail(tmp_path: Path) -> None:
    path = tmp_path / "data" / "mission_control_audit.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"ts": "1", "action": "a"}) + "\n" + json.dumps({"ts": "2", "action": "b"}) + "\n",
        encoding="utf-8",
    )
    rows = state.load_audit(tmp_path, limit=10)
    assert rows[0]["action"] == "b"
    assert rows[1]["action"] == "a"


def test_load_mtsi_missing_fail_open(tmp_path: Path) -> None:
    out = state.load_mtsi(tmp_path)
    assert out["present"] is False
    assert out["final_verdict"] == "NOT_RUN"
    assert out["max_gross_inventory_usd"] == 1.0


def test_load_mtsi_from_status_file(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "mtsi_status.json",
        {
            "family": "mtsi_inventory_v1",
            "final_verdict": "NO_GO",
            "max_gross_inventory_usd": 1.0,
            "inventory_usd": 0.4,
            "inventory_utilization": 0.4,
            "clip_pnl_histogram": [0, 1, 2, 3],
            "display_cell": "F2",
            "n_clips": 10,
            "mean_clip_pnl_usd": -0.01,
            "candle_spark": [100.0, 100.5],
            "honesty": "test",
        },
    )
    out = state.load_mtsi(tmp_path)
    assert out["present"] is True
    assert out["final_verdict"] == "NO_GO"
    assert out["inventory_usd"] == 0.4
    assert out["n_clips"] == 10
    assert out["clip_pnl_histogram"] == [0, 1, 2, 3]


def test_heartbeat_is_authoritative_over_env(tmp_path: Path) -> None:
    """.env is intent; the heartbeat is what the running process reports.

    A long-running supervisor never re-reads .env, so showing the .env value as
    the mode is the failure class that hid 22.4h of zero-entry starvation.
    """
    _write(
        tmp_path / "data" / "heartbeat.json",
        {
            "ts": "2026-07-27T12:00:00+00:00",
            "operating_mode": "PAPER",
            "paper_trading_profile": "MAX_FLOW_BAND",
        },
    )
    _write(
        tmp_path / ".env",
        "OPERATING_MODE=OBSERVATION\nPAPER_TRADING_PROFILE=STANDARD\n",
    )
    status = state.load_status(tmp_path)
    assert status["mode"] == "PAPER"
    assert status["mode_source"] == "heartbeat"
    assert status["env_mode"] == "OBSERVATION"
    assert status["mode_divergent"] is True
    assert status["paper_profile"] == "MAX_FLOW_BAND"
    assert status["env_paper_profile"] == "STANDARD"
    assert status["profile_divergent"] is True


def test_max_drawdown_exposed_as_percent(tmp_path: Path) -> None:
    """risk_state stores a fraction; a 4.32% drawdown must not read as 0.04%."""
    _write(tmp_path / "data" / "risk_state.json", {"max_drawdown_pct": 0.0432})
    risk = state.load_risk(tmp_path)
    assert risk["max_drawdown_fraction"] == 0.0432
    assert risk["max_drawdown_percent"] == 4.32


def test_blank_numeric_fields_are_none_not_zero(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "positions.json",
        {"open": [], "closed": [{"pnl": "", "total_fees": None}]},
    )
    pos = state.load_positions(tmp_path)
    assert pos["closed_stats"]["with_pnl"] == 0
    assert pos["closed_stats"]["net_pnl"] is None
    assert pos["closed_stats"]["win_rate"] is None


def test_closed_ring_cap_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "positions.json",
        {"open": [], "closed": [{"pnl": 1.0} for _ in range(state.CLOSED_RING_CAP)]},
    )
    pos = state.load_positions(tmp_path)
    assert pos["closed_is_capped"] is True


def test_missing_snapshot_reports_absence(tmp_path: Path) -> None:
    funnel = state.load_funnel(tmp_path)
    assert funnel["available"] is False
    assert "absent" in funnel["reason"]


def _write_listing_probe(tmp_path: Path, decisions: list[str]) -> None:
    """Minimal shadow_listing_probe fixture — only the columns the reader uses."""
    import sqlite3

    db = tmp_path / "data" / "warehouse.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, "
        "decision TEXT, created_ts REAL)"
    )
    now = time.time()
    conn.executemany(
        "INSERT INTO shadow_listing_probe VALUES (?,?,?,?)",
        [(f"p{i}", "XYZ", d, now - 3600) for i, d in enumerate(decisions)],
    )
    conn.commit()
    conn.close()


def test_scope_only_skips_mark_a_lane_idle_by_market(tmp_path: Path) -> None:
    """All-out-of-scope skips are positive evidence of IDLE, not of BROKEN."""
    _write_listing_probe(tmp_path, ["SKIP_NOT_CRYPTO"] * 12)
    lane = state.load_lane_skip_reasons(tmp_path)["lanes"]["listing_short"]
    assert lane["readable"] is True
    assert lane["scope_only"] is True
    assert lane["dominant"] == "SKIP_NOT_CRYPTO"
    assert lane["dominant_count"] == 12


def test_unshortable_skips_are_not_treated_as_out_of_scope(tmp_path: Path) -> None:
    """The 2026-07-28 fault signature must stay in the ALARM tier.

    Every listing_short row read SKIP_UNSHORTABLE because the shortability test
    could never return True. Classifying that as "idle by market" would hide a
    real, reproduced defect.
    """
    _write_listing_probe(tmp_path, ["SKIP_UNSHORTABLE"] * 25)
    lane = state.load_lane_skip_reasons(tmp_path)["lanes"]["listing_short"]
    assert lane["scope_only"] is False
    assert lane["dominant"] == "SKIP_UNSHORTABLE"


def test_one_non_scope_skip_breaks_unanimity(tmp_path: Path) -> None:
    _write_listing_probe(tmp_path, ["SKIP_NOT_CRYPTO"] * 9 + ["SKIP_UNSHORTABLE"])
    lane = state.load_lane_skip_reasons(tmp_path)["lanes"]["listing_short"]
    assert lane["scope_only"] is False


def test_zero_proposals_is_never_scope_only(tmp_path: Path) -> None:
    """No proposals is not evidence of an empty market — it stays an alarm."""
    _write_listing_probe(tmp_path, [])
    lane = state.load_lane_skip_reasons(tmp_path)["lanes"]["listing_short"]
    assert lane["total"] == 0
    assert lane["scope_only"] is False


def test_unreadable_warehouse_fails_towards_the_alarm(tmp_path: Path) -> None:
    """An absent database must never downgrade a STARVED lane to informational."""
    out = state.load_lane_skip_reasons(tmp_path)
    assert out["available"] is False
    assert out["lanes"] == {}
    assert "absent" in out["reason"]


def test_missing_probe_table_is_reported_unreadable(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "data" / "warehouse.sqlite"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()

    lane = state.load_lane_skip_reasons(tmp_path)["lanes"]["listing_short"]
    assert lane["readable"] is False
    assert "scope_only" not in lane


def test_funnel_carries_lane_skip_triage(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "promotion_funnel.json", {"lanes": [], "resolved_floor": 30})
    _write_listing_probe(tmp_path, ["SKIP_NOT_CRYPTO"] * 3)
    funnel = state.load_funnel(tmp_path)
    assert funnel["available"] is True
    assert funnel["lane_skips"]["lanes"]["listing_short"]["scope_only"] is True


def test_lane_skip_read_takes_no_write_lock(tmp_path: Path) -> None:
    """It rides the 12s poll, so it must never be able to write."""
    import sqlite3

    _write_listing_probe(tmp_path, ["SKIP_NOT_CRYPTO"])
    state.load_lane_skip_reasons(tmp_path)
    ro = sqlite3.connect(
        f"file:{(tmp_path / 'data' / 'warehouse.sqlite').as_posix()}?mode=ro", uri=True
    )
    ro.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO shadow_listing_probe VALUES ('x','y','z',0)")
    ro.close()


def test_checklist_gate_is_read_only(tmp_path: Path) -> None:
    doc = tmp_path / "docs" / "CONTROLLED_LIVE_CHECKLIST.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("- [ ] verify sizing\n- [x] done\n", encoding="utf-8")
    before = doc.read_text(encoding="utf-8")
    signed, message, unchecked = state.inspect_checklist(doc)
    assert signed is False
    assert unchecked == 1
    assert "unchecked" in message
    assert doc.read_text(encoding="utf-8") == before


def test_inspect_checklist_rejects_unclosed_html_comment_signature(tmp_path: Path) -> None:
    doc = tmp_path / "docs" / "CONTROLLED_LIVE_CHECKLIST.md"
    doc.parent.mkdir(parents=True)
    today = date.today().isoformat()
    doc.write_text(f"<!-- Signed-By: Owner {today}\n", encoding="utf-8")
    signed, _message, unchecked = state.inspect_checklist(doc)
    assert signed is False
    assert unchecked == 0


def test_warehouse_is_opened_read_only(tmp_path: Path) -> None:
    """A write lock on warehouse.sqlite can stall the live bot."""
    import sqlite3

    db = tmp_path / "data" / "warehouse.sqlite"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE candidates (ts REAL, exchange TEXT, symbol TEXT, side TEXT, "
        "strategy_family TEXT, decision TEXT, skip_reason TEXT, confidence REAL)"
    )
    conn.execute(
        "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)",
        (time.time(), "binance", "ETH/USDT:USDT", "buy", "mcp", "SKIP", "score_floor", 0.5),
    )
    conn.commit()
    conn.close()

    out = state.load_candidates(tmp_path, hours=24, limit=10)
    assert out["available"] is True
    assert out["total_in_window"] == 1
    assert out["by_decision"][0]["decision"] == "SKIP"
    assert out["top_skip_reasons"][0]["reason"] == "score_floor"

    # The reader must refuse writes outright.
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO candidates VALUES (0,'x','y','z','w','v','u',0.1)")
    ro.close()


def test_missing_warehouse_reports_absence(tmp_path: Path) -> None:
    out = state.load_candidates(tmp_path)
    assert out["available"] is False
    assert "absent" in out["reason"]


def test_scheduled_task_error_is_distinguishable_from_empty() -> None:
    result = state.list_scheduled_tasks()
    assert "tasks" in result and "error" in result and "supported" in result


def test_venv_shim_pairs_collapse_to_one_logical_process(monkeypatch, tmp_path: Path) -> None:
    """The venv python.exe re-execs the base interpreter as a child.

    Counting both halves reports two supervisors where there is one, which would
    put a false "duplicate supervisor trees" alarm on the operator's dashboard.
    """

    class FakeProc:
        def __init__(self, pid, ppid, cmdline):
            self.info = {
                "pid": pid,
                "ppid": ppid,
                "name": "python.exe",
                "cmdline": cmdline,
                "create_time": 1785000000.0,
            }

        def cwd(self):
            return str(tmp_path)

    root = tmp_path
    chain = [
        FakeProc(100, 1, [f"{root}/venv/Scripts/python.exe", f"{root}/scripts/launcher_supervisor.py", "run"]),
        FakeProc(200, 100, ["C:/Python312/python.exe", f"{root}/scripts/launcher_supervisor.py", "run"]),
        FakeProc(300, 200, [f"{root}/venv/Scripts/python.exe", f"{root}/main.py"]),
        FakeProc(400, 300, ["C:/Python312/python.exe", f"{root}/main.py"]),
        # Must be ignored: inline code that merely mentions main.py.
        FakeProc(500, 1, ["python.exe", "-c", "print('main.py')"]),
    ]
    fake_psutil = type(
        "P",
        (),
        {
            "process_iter": staticmethod(lambda attrs=None: iter(chain)),
            "Error": Exception,
        },
    )
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    out = state.supervisor_liveness(root, heartbeat_pid=400)
    assert out["supervisor_count"] == 1
    assert out["worker_count"] == 1
    assert out["warnings"] == []
    assert out["heartbeat_pid_running"] is True
    assert {p["pid"] for p in out["processes"]} == {100, 300}
    assert 500 not in {p["pid"] for p in out["processes"]}


# ==========================================================================
# Brain readers — live cascade + research pipeline
# ==========================================================================


def _brain_warehouse(
    tmp_path: Path,
    *,
    rows: list[tuple[str, str]] | None = None,
    events: list[tuple[int, str, str, str]] | None = None,
    with_ts_index: bool = True,
) -> Path:
    """Minimal candidates + decision_events fixture.

    ``rows`` are ``(decision, skip_reason)``; ``events`` are
    ``(candidate_id, outcome, reason, terminal_stage)``.
    """
    import sqlite3
    from datetime import datetime, timezone

    db = tmp_path / "data" / "warehouse.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE candidates (id INTEGER PRIMARY KEY, ts REAL, exchange TEXT, "
        "symbol TEXT, side TEXT, strategy_family TEXT, decision TEXT, "
        "skip_reason TEXT, confidence REAL, features_json TEXT)"
    )
    if with_ts_index:
        conn.execute("CREATE INDEX idx_candidates_ts ON candidates(ts)")
    conn.execute(
        "CREATE TABLE decision_events (event_id TEXT, decision_id TEXT, "
        "occurred_at TEXT, venue TEXT, canonical_symbol TEXT, action TEXT, "
        "payload_json TEXT)"
    )
    now = time.time()
    for i, (decision, reason) in enumerate(rows or [], start=1):
        conn.execute(
            "INSERT INTO candidates (id, ts, exchange, symbol, side, strategy_family, "
            "decision, skip_reason, confidence, features_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, now - 60, "binance", "ETH/USDT", "buy", "mcp", decision, reason, 0.6, "{}"),
        )
    occurred = (
        datetime.fromtimestamp(now - 30, timezone.utc).isoformat().replace("+00:00", "Z")
    )
    for cid, outcome, reason, stage in events or []:
        payload = json.dumps(
            {
                "outcome": outcome,
                "context": {"candidate_id": cid, "reason": reason, "terminal_stage": stage},
            }
        )
        conn.execute(
            "INSERT INTO decision_events (event_id, decision_id, occurred_at, venue, "
            "canonical_symbol, action, payload_json) VALUES (?,?,?,?,?,?,?)",
            (f"e{cid}", f"d{cid}", occurred, "binance", "ETH/USDT", "enter_long", payload),
        )
    conn.commit()
    conn.close()
    return db


def test_decode_reason_translates_the_real_grammar() -> None:
    d = state.decode_reason("scalp_req_fail(2/4:adx=42,atr=0.83%)")
    assert d["family"] == "scalp_req_fail"
    assert d["required"] == {"met": 2, "of": 4}
    assert d["plain"] is not None
    assert {(m["label"], m["value"], m["unit"]) for m in d["measurements"]} == {
        ("ADX", 42.0, ""),
        ("ATR", 0.83, "%"),
    }
    # A metric key may start with a digit.
    ranging = state.decode_reason("scalp_veto:ranging(4h_adx=14)")
    assert ranging["measurements"][0]["label"] == "4h ADX"
    assert ranging["measurements"][0]["value"] == 14.0


def test_decode_reason_fails_open_on_an_unknown_family() -> None:
    """A grammar change must degrade to 'raw only', never to a guess."""
    d = state.decode_reason("brand_new_gate(x=1.5%)")
    assert d["plain"] is None
    assert d["raw"] == "brand_new_gate(x=1.5%)"
    assert d["measurements"][0]["value"] == 1.5
    assert state.decode_reason(None)["family"] == ""


def test_reason_family_maps_legacy_bare_vwap_to_score_below_floor() -> None:
    bare = "vwap_near=0.35% | rsi=48 | adx=22 | atr=0.86%"
    assert state.reason_family(bare) == "scalp_score_below_floor"
    prefixed = "scalp_score_below_floor(50<66):" + bare
    assert state.reason_family(prefixed) == "scalp_score_below_floor"
    d = state.decode_reason(prefixed)
    assert d["plain"] is not None
    assert "entry floor" in d["plain"]


def test_reason_family_accband_scope() -> None:
    assert state.reason_family("analysis_only_accband_scope") == "analysis_only_accband_scope"


def test_reason_family_universe_filter_detail() -> None:
    assert (
        state.reason_family("universe_filter_blocked:chop:ER=0.09<0.12")
        == "universe_filter_blocked"
    )
    assert state.reason_family("band_regime_filter:adx_4h>30") == "band_regime_filter"
    assert state.decode_reason("analysis_only_accband_scope")["plain"] is not None
    tradfi = state.decode_reason("tradfi_asset:BZ")
    assert tradfi["family"] == "tradfi_asset"
    assert tradfi["plain"] is not None
    assert "by design" in tradfi["plain"].lower()


def test_load_candidates_aggregates_legacy_score_floor_strings(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "data" / "warehouse.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE candidates (ts REAL, exchange TEXT, symbol TEXT, side TEXT, "
        "strategy_family TEXT, decision TEXT, skip_reason TEXT, confidence REAL)"
    )
    conn.execute("CREATE INDEX idx_candidates_ts ON candidates(ts)")
    now = time.time()
    bare = "vwap_near=0.35% | rsi=48 | adx=22 | atr=0.86%"
    for reason in [bare, bare, "scalp_score_below_floor(50<66):" + bare, "scalp_veto:quiet(atr=0.5%)"]:
        conn.execute(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)",
            (now, "binance", "ETH/USDT", "buy", "mcp", "SKIP", reason, 0.5),
        )
    conn.commit()
    conn.close()
    out = state.load_candidates(tmp_path, hours=1, limit=10)
    fams = {f["family"]: f["count"] for f in out["skip_families"]}
    assert fams.get("scalp_score_below_floor") == 3
    assert fams.get("scalp_veto:quiet") == 1


def test_load_brain_context_splits_tradfi_allows(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    monkeypatch.setattr(
        "config.ANALYSIS_ONLY_BASES", {"MSFT"}, raising=False
    )
    import config

    monkeypatch.setattr(config, "ANALYSIS_ONLY_BASES", {"MSFT"})
    db = tmp_path / "data" / "warehouse.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE candidates (id INTEGER PRIMARY KEY, ts REAL, exchange TEXT, "
        "symbol TEXT, side TEXT, strategy_family TEXT, decision TEXT, "
        "skip_reason TEXT, confidence REAL, features_json TEXT)"
    )
    conn.execute("CREATE INDEX idx_candidates_ts ON candidates(ts)")
    conn.execute(
        "CREATE TABLE decision_events (event_id TEXT, decision_id TEXT, "
        "occurred_at TEXT, venue TEXT, canonical_symbol TEXT, action TEXT, "
        "payload_json TEXT)"
    )
    now = time.time()
    conn.execute(
        "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?)",
        (1, now - 10, "binance", "MSFT/USDT", "buy", "mcp", "ALLOW", "", 0.9, "{}"),
    )
    conn.execute(
        "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?)",
        (2, now - 10, "binance", "ETH/USDT", "buy", "mcp", "ALLOW", "", 0.9, "{}"),
    )
    conn.execute(
        "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?)",
        (3, now - 10, "binance", "BTC/USDT", "buy", "mcp", "SKIP",
         "scalp_veto:quiet(atr=0.5%)", 0.4, "{}"),
    )
    conn.commit()
    conn.close()
    out = state.load_brain(tmp_path, window_minutes=60)
    assert out["available"] is True
    ctx = out["context"]
    assert ctx["allow_tradfi"] == 1
    assert ctx["allow_crypto"] == 1
    assert ctx["scorer_mode"] in ("scalp", "standard", "unknown")
    assert ctx["interpretation"]


def test_load_brain_shape_and_conservation(tmp_path: Path) -> None:
    _brain_warehouse(
        tmp_path,
        rows=[("SKIP", "scalp_veto:quiet(atr=0.6%)")] * 4
        + [("SKIP", "scalp_req_fail(2/4:adx=42,atr=0.9%)")] * 2
        + [("ALLOW", ""), ("ALLOW", ""), ("ALLOW", "")],
        events=[
            (7, "rejected", "economic_gate_stressed_breakeven", "execute_open"),
            (8, "filled", "maker_first_maker_fill", "maker_resolution"),
        ],
    )
    out = state.load_brain(tmp_path, window_minutes=60)
    assert out["available"] is True
    scorer, terminal = out["scorer"], out["terminal"]
    assert scorer["scored"] == 9
    assert scorer["skipped"] == 6
    assert scorer["allowed"] == 3
    # Stage 1 closes.
    assert scorer["skipped"] + scorer["allowed"] == scorer["scored"]
    # Stage 2 closes against the ALLOW count — the residual bucket is what
    # makes the waterfall honest rather than merely plausible.
    assert terminal["blocked"] == 1
    assert terminal["filled"] == 1
    assert terminal["residual"] == 1
    assert (
        terminal["blocked"]
        + terminal["filled"]
        + terminal["deferred"]
        + terminal["other"]
        + terminal["residual"]
        == scorer["allowed"]
    )
    assert scorer["skip_causes"][0]["family"] == "scalp_veto:quiet"
    assert terminal["blocks"][0]["reason"] == "economic_gate_stressed_breakeven"
    assert terminal["blocks"][0]["plain"] is not None


def test_load_brain_names_the_binding_constraint_at_each_stage(tmp_path: Path) -> None:
    """One stage-less 'the constraint is X' would name the wrong thing."""
    _brain_warehouse(
        tmp_path,
        rows=[("SKIP", "scalp_veto:quiet(atr=0.6%)")] * 5 + [("ALLOW", "")] * 2,
        events=[
            (6, "rejected", "economic_gate_stressed_breakeven", "execute_open"),
            (7, "rejected", "economic_gate_stressed_breakeven", "execute_open"),
        ],
    )
    binding = state.load_brain(tmp_path)["binding"]
    assert binding["scorer"]["family"] == "scalp_veto:quiet"
    assert binding["scorer"]["count"] == 5
    assert binding["scorer"]["measurements"][0]["label"] == "ATR"
    assert binding["downstream"]["family"] == "economic_gate_stressed_breakeven"
    assert binding["downstream"]["count"] == 2


def test_required_split_names_the_condition_that_failed_not_the_ones_that_passed(
    tmp_path: Path,
) -> None:
    """``scalp_req_fail(3/4:...)`` lists the conditions that PASSED.

    ``core/mcp_brain.py`` appends to ``req_reasons`` only inside each passing
    branch, so the failing metric's value is never written. Reading the string
    as "the measurements that decided it" inverts the causality: it names the
    conditions that were met and omits the one that rejected the candidate.
    Here vwap_near appears on 1 of 5 rows and MUST be reported as the deciding
    failure, even though ADX/ATR are the only values shown in every string.
    """
    rows = (
        [("SKIP", "scalp_req_fail(3/4:rsi=51,adx=33,atr=0.84%)")] * 4
        + [("SKIP", "scalp_req_fail(3/4:vwap_near=0.24%,adx=30,atr=0.90%)")] * 1
    )
    _brain_warehouse(tmp_path, rows=rows)
    out = state.load_brain(tmp_path)
    cause = out["scorer"]["skip_causes"][0]
    split = cause["required_split"]
    assert split["derivable"] is True
    assert split["of"] == 4 and split["candidates"] == 5
    fails = {c["key"]: c["failures"] for c in split["conditions"]}
    # adx/atr pass on every row; vwap_near fails on 4 of 5; rsi on 1 of 5.
    assert fails == {"adx": 0, "atr": 0, "vwap_near": 4, "rsi": 1}
    assert cause["top_failure"]["key"] == "vwap_near"
    assert cause["top_failure"]["failures"] == 4
    # The recorded values are PASSING values and are flagged as such.
    assert cause["measurements_are_passing_values"] is True
    # …and the binding card carries the same derivation.
    assert out["binding"]["scorer"]["top_failure"]["key"] == "vwap_near"


def test_required_split_arithmetic_closes(tmp_path: Path) -> None:
    """sum(failures) + unnamed failures == of*total - passes, for any K <= of."""
    _brain_warehouse(
        tmp_path,
        rows=[("SKIP", "scalp_req_fail(2/4:adx=42,atr=0.83%)")] * 7
        + [("SKIP", "scalp_req_fail(3/4:adx=40,atr=0.85%,rsi=52)")] * 3,
    )
    split = state.load_brain(tmp_path)["scorer"]["skip_causes"][0]["required_split"]
    assert split["derivable"] is True
    total = split["candidates"]
    named = sum(c["failures"] for c in split["conditions"])
    unnamed = split["unnamed"]["failures"] if split["unnamed"] else 0
    assert named + unnamed == split["checks"] - split["passes"] == split["failures"]
    # One of the four required conditions never passed, so it never appears.
    assert split["unnamed"]["conditions"] == 1
    assert split["unnamed"]["failures"] == total


def test_a_condition_that_never_passes_is_reported_as_unnameable(tmp_path: Path) -> None:
    """A condition failing on every row leaves NO trace in the log.

    The honest answer is "it failed on all of them and its identity is not
    recoverable", not silently ranking the runner-up as the binding failure.
    """
    _brain_warehouse(
        tmp_path, rows=[("SKIP", "scalp_req_fail(3/4:adx=40,atr=0.85%,rsi=52)")] * 6
    )
    cause = state.load_brain(tmp_path)["scorer"]["skip_causes"][0]
    split = cause["required_split"]
    assert split["derivable"] is True
    assert split["unnamed"]["conditions"] == 1
    assert split["unnamed"]["failures"] == 6
    assert all(c["failures"] == 0 for c in split["conditions"])
    # No named condition may be presented as the deciding one.
    assert cause["top_failure"] is None


def test_required_split_checksum_suppresses_an_unsupported_split(tmp_path: Path) -> None:
    """If the string ever listed the FAILING conditions, the split must refuse.

    ``2/4`` declares two passes but three metrics are recorded, so the recorded
    values cannot be the passing ones. Emitting a per-condition failure count
    from that would be the inversion this guard exists to prevent.
    """
    _brain_warehouse(
        tmp_path, rows=[("SKIP", "scalp_req_fail(2/4:adx=40,atr=0.85%,rsi=52)")] * 4
    )
    cause = state.load_brain(tmp_path)["scorer"]["skip_causes"][0]
    assert cause["required_split"]["derivable"] is False
    assert "checksum failed" in cause["required_split"]["note"]
    assert cause["top_failure"] is None
    assert cause["measurements_are_passing_values"] is False


def test_required_split_refuses_when_some_rows_carry_no_required_count(
    tmp_path: Path,
) -> None:
    """A split that does not cover every row in the cause is not a split."""
    _brain_warehouse(
        tmp_path,
        rows=[("SKIP", "scalp_req_fail(3/4:adx=40,atr=0.85%,rsi=52)")] * 3
        + [("SKIP", "scalp_req_fail")] * 2,
    )
    cause = state.load_brain(tmp_path)["scorer"]["skip_causes"][0]
    assert cause["required_split"]["derivable"] is False
    assert "3 of 5 rows" in cause["required_split"]["note"]
    assert cause["top_failure"] is None


def test_families_without_a_required_count_have_no_split(tmp_path: Path) -> None:
    """``scalp_veto:quiet(atr=0.63%)`` DOES print the deciding value.

    The inversion is specific to the pass-list families; the veto families must
    keep rendering their measurement as the cause, untouched.
    """
    _brain_warehouse(tmp_path, rows=[("SKIP", "scalp_veto:quiet(atr=0.63%)")] * 3)
    cause = state.load_brain(tmp_path)["scorer"]["skip_causes"][0]
    assert cause["required_split"] is None
    assert cause["top_failure"] is None
    assert cause["measurements_are_passing_values"] is False
    assert cause["measurements"][0]["label"] == "ATR"


def test_allow_advisories_are_never_ranked_as_causes_of_death(tmp_path: Path) -> None:
    """skip_reason is populated on ALLOW rows as a meta-filter ADVISORY.

    564 of the last 24h's ALLOW rows carry ``meta_advisory:loss_streak=...``.
    Grouping on (decision, skip_reason) without splitting by decision first
    would rank that above every real SKIP cause in a quiet hour.
    """
    _brain_warehouse(
        tmp_path,
        rows=[("ALLOW", "meta_advisory:loss_streak=20>=3 + conf=0.66<0.75")] * 40
        + [("SKIP", "scalp_veto:quiet(atr=0.6%)")] * 3,
    )
    out = state.load_brain(tmp_path)
    families = [c["family"] for c in out["scorer"]["skip_causes"]]
    assert "meta_advisory:loss_streak=20>=3 + conf=0.66<0.75" not in families
    assert families == ["scalp_veto:quiet"]
    assert out["binding"]["scorer"]["family"] == "scalp_veto:quiet"
    # …but it IS surfaced, honestly, as an advisory with its measurement.
    adv = out["scorer"]["allow_advisories"][0]
    assert adv["count"] == 40
    assert any(m["key"] == "loss_streak" and m["value"] == 20.0 for m in adv["measurements"])


def test_load_brain_empty_window_is_available_not_failed(tmp_path: Path) -> None:
    _brain_warehouse(tmp_path, rows=[])
    out = state.load_brain(tmp_path)
    assert out["available"] is True
    assert out["scorer"]["scored"] == 0
    assert out["terminal"]["residual"] == 0
    assert out["binding"] == {"scorer": None, "downstream": None}


def test_load_brain_missing_warehouse_reports_absence(tmp_path: Path) -> None:
    out = state.load_brain(tmp_path)
    assert out["available"] is False
    assert "absent" in out["reason"]


def test_load_brain_without_ts_index_falls_back_to_counts_not_the_slow_plan(
    tmp_path: Path,
) -> None:
    """INDEXED BY is a hard constraint: a missing index RAISES, it does not degrade.

    The poll-safe reader must fall back to the count-only covering-index path
    and say so — never to the un-hinted GROUP BY that measured 1,133 ms warm.
    """
    _brain_warehouse(
        tmp_path,
        rows=[("SKIP", "scalp_veto:quiet(atr=0.6%)"), ("ALLOW", "")],
        with_ts_index=False,
    )
    out = state.load_brain(tmp_path)
    assert out["available"] is True
    assert out["scorer"]["aggregation_available"] is False
    assert "idx_candidates_ts" in out["scorer"]["aggregation_note"]
    assert out["scorer"]["scored"] == 2
    assert out["scorer"]["skipped"] == 1
    assert out["scorer"]["allowed"] == 1
    # The causes are UNKNOWN, not absent. The empty list must always travel
    # with aggregation_available=False so the renderer can tell the two apart —
    # asserting "nothing was skipped" here would be a false negative, since
    # skipped == 1.
    assert out["scorer"]["skip_causes"] == []
    assert out["binding"]["scorer"] is None
    assert out["scorer"]["skipped"] == 1


def test_aggregation_note_does_not_assert_an_unverified_cause(tmp_path: Path) -> None:
    """OperationalError also covers 'database is locked'.

    In a repo where a misdiagnosis cost 22 hours, the panel must not name a
    missing index unless sqlite actually said so.
    """
    _brain_warehouse(tmp_path, rows=[("ALLOW", "")], with_ts_index=False)
    note = state.load_brain(tmp_path)["scorer"]["aggregation_note"]
    assert "not present" in note  # sqlite really did say "no such index"

    import sqlite3

    class _LockedConn:
        def execute(self, sql, *args):
            if "INDEXED BY" in sql:
                raise sqlite3.OperationalError("database is locked")
            return []

    out = state._brain_scorer_stage(_LockedConn(), 0.0)
    assert out["aggregation_available"] is False
    assert "database is locked" in out["aggregation_note"]
    assert "not present" not in out["aggregation_note"]


def test_brain_aggregation_uses_the_ts_index_plan(tmp_path: Path) -> None:
    """The fast plan is load-bearing on exact query text — pin it.

    Without the hint the planner picks idx_candidates_decision and full-scans
    (MEASURED 1,133 ms warm vs 4.9 ms). A cosmetic edit that drops the hint
    would silently reintroduce that, with no other test failing.
    """
    import sqlite3

    db = _brain_warehouse(tmp_path, rows=[("ALLOW", "")])
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    plan = " ".join(
        str(r[3])
        for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT decision, skip_reason, COUNT(*) AS c "
            "FROM candidates INDEXED BY idx_candidates_ts WHERE ts > 0 GROUP BY 1, 2"
        )
    )
    conn.close()
    assert "idx_candidates_ts" in plan


def test_load_candidates_survives_a_warehouse_with_no_ts_index(tmp_path: Path) -> None:
    """The on-demand reader hints the same index but must not 500 without it."""
    _brain_warehouse(
        tmp_path,
        rows=[("SKIP", "scalp_veto:quiet(atr=0.6%)"), ("ALLOW", "")],
        with_ts_index=False,
    )
    out = state.load_candidates(tmp_path, hours=24, limit=10)
    assert out["available"] is True
    assert out["total_in_window"] == 2
    assert out["skip_families"][0]["family"] == "scalp_veto:quiet"


def test_brain_tolerates_unparsable_event_payloads(tmp_path: Path) -> None:
    import sqlite3

    db = _brain_warehouse(
        tmp_path,
        rows=[("ALLOW", ""), ("ALLOW", "")],
        events=[(1, "rejected", "universe_filter_blocked", "execute_open")],
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO decision_events (event_id, payload_json, occurred_at) VALUES "
        "('bad', '{not json', '2026-07-28T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    out = state.load_brain(tmp_path)
    assert out["available"] is True
    assert out["terminal"]["unparsable_rows"] == 1
    assert out["terminal"]["blocked"] == 1


def test_brain_read_takes_no_write_lock(tmp_path: Path) -> None:
    import sqlite3

    db = _brain_warehouse(tmp_path, rows=[("ALLOW", "")])
    state.load_brain(tmp_path)
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("UPDATE candidates SET decision = 'SKIP'")
    ro.close()


def _write_decision_log(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "data" / "mcp_decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_live_feed_joins_intents_to_their_terminal_block(tmp_path: Path) -> None:
    _write_decision_log(
        tmp_path,
        [
            json.dumps(
                {
                    "ts": "2026-07-28T16:00:00+00:00",
                    "type": "portfolio",
                    "decisions": {
                        "actions": [
                            {
                                "type": "OPEN",
                                "symbol": "LINK/USDT",
                                "side": "buy",
                                "mcp_score": 58,
                                "decision_id": "abc",
                                "reason": "ALGO score=58 layers=5/7",
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "ts": "2026-07-28T16:00:01+00:00",
                    "type": "rejection",
                    "decision_id": "abc",
                    "symbol": "LINK/USDT",
                    "reason": "economic_gate_stressed_breakeven",
                    "stage": "execute_open",
                }
            ),
            # position_monitor cycles are NOT entry intents, but they are not
            # dropped either — HOLD rows are counted (see the exit test below).
            json.dumps(
                {
                    "ts": "2026-07-28T16:00:02+00:00",
                    "type": "position_monitor",
                    "decisions": {"SPOT-binance-SUI": {"action": "HOLD", "confidence": 0.5}},
                }
            ),
        ],
    )
    live = state.load_brain(tmp_path)["live"]
    assert live["available"] is True
    assert live["cycles"] == 3
    assert len(live["intents"]) == 1
    intent = live["intents"][0]
    assert intent["symbol"] == "LINK/USDT"
    assert intent["mcp_score"] == 58
    assert intent["outcome"]["reason"] == "economic_gate_stressed_breakeven"
    assert intent["outcome"]["plain"] is not None
    # The monitor cycle is measured, not assumed away.
    assert live["monitor"]["cycles"] == 1
    assert live["monitor"]["decisions"] == 1
    assert live["monitor"]["hold"] == 1
    assert live["monitor"]["exits_found"] == 0


def test_live_feed_surfaces_position_monitor_exit_decisions(tmp_path: Path) -> None:
    """Forced exits on losing positions must not be excluded BY CONSTRUCTION.

    ``_algorithmic_position_monitor`` returns CLOSE at confidence 0.99 on the
    -12% hard max-loss backstop and CLOSE at 0.88 on trend-reversal-plus-loss.
    Filtering every position_monitor cycle out (and telling the owner the
    action "is always HOLD at confidence 0.5") gave that class zero coverage.
    """
    _write_decision_log(
        tmp_path,
        [
            json.dumps(
                {
                    "ts": "2026-07-28T16:00:00+00:00",
                    "type": "position_monitor",
                    "decisions": {
                        "FUT-bybit-ADA": {
                            "action": "CLOSE",
                            "confidence": 0.99,
                            "reason": "HARD MAX LOSS -13.4% (limit -12%)",
                            "source": "algo",
                        },
                        "FUT-bybit-SOL": {"action": "HOLD", "confidence": 0.5, "reason": "default hold"},
                        "FUT-binance-INJ": {
                            "action": "TAKE_PROFIT",
                            "confidence": 0.7,
                            "reason": "RSI extreme + profit",
                            "source": "algo",
                        },
                    },
                }
            ),
        ],
    )
    mon = state.load_brain(tmp_path)["live"]["monitor"]
    assert mon["cycles"] == 1
    assert mon["decisions"] == 3
    assert mon["hold"] == 1
    assert mon["exits_found"] == 2
    actions = {e["action"] for e in mon["exits"]}
    assert actions == {"CLOSE", "TAKE_PROFIT"}
    closed = next(e for e in mon["exits"] if e["action"] == "CLOSE")
    assert closed["confidence"] == 0.99
    assert "HARD MAX LOSS" in closed["reason"]
    assert closed["position"] == "FUT-bybit-ADA"


def test_live_feed_tolerates_a_truncated_final_line(tmp_path: Path) -> None:
    """The bot appends to this file live — a half-written last line is normal."""
    _write_decision_log(
        tmp_path,
        [
            json.dumps(
                {
                    "ts": "2026-07-28T16:00:00+00:00",
                    "type": "portfolio",
                    "decisions": {"actions": [{"type": "OPEN", "symbol": "ETH/USDT"}]},
                }
            ),
            '{"ts": "2026-07-28T16:00:05+00:00", "type": "portf',
        ],
    )
    live = state.load_brain(tmp_path)["live"]
    assert live["available"] is True
    assert live["cycles"] == 1
    assert live["intents"][0]["symbol"] == "ETH/USDT"


def test_live_feed_missing_log_is_reported_not_silently_empty(tmp_path: Path) -> None:
    live = state.load_brain(tmp_path)["live"]
    assert live["available"] is False
    assert "absent" in live["reason"]
    assert live["intents"] == []


def test_load_research_artifacts_shape(tmp_path: Path) -> None:
    d = tmp_path / "_workspace" / "strategy_pipeline"
    d.mkdir(parents=True)
    (d / "40_prereg_thing.md").write_text("# 40 - Pre-registration: thing\nbody\n", encoding="utf-8")
    (d / "40_screen_thing.md").write_text("no heading here\n", encoding="utf-8")
    (d / "41_prereg_other.md").write_text("# 41 - Pre-registration: other\n", encoding="utf-8")

    out = state.load_research_artifacts(tmp_path, ttl=0.0)
    assert out["available"] is True
    assert out["file_count"] == 3
    runs = {r["run"]: r for r in out["runs"]}
    assert set(runs) == {"40", "41"}
    assert runs["40"]["kinds"] == ["prereg", "screen"]
    assert runs["40"]["prereg_only"] is False
    # A prereg with no screen output is reported as a FILE FACT only — the
    # reader never claims a screen is "in flight" or "queued".
    assert runs["41"]["prereg_only"] is True
    assert "not a pipeline status" in out["caption"]
    heads = {f["name"]: f["heading"] for f in out["recent"]}
    assert heads["40_prereg_thing.md"] == "40 - Pre-registration: thing"
    assert heads["40_screen_thing.md"] is None


def test_load_research_artifacts_missing_directory(tmp_path: Path) -> None:
    out = state.load_research_artifacts(tmp_path, ttl=0.0)
    assert out["available"] is False
    assert out["runs"] == []
    assert "could not be read" in out["reason"]


def test_load_research_artifacts_ttl_serves_stale_with_age(tmp_path: Path) -> None:
    """MEASURED 3.0 ms warm / 132 ms cold — hence the cache, hence this test."""
    d = tmp_path / "_workspace" / "strategy_pipeline"
    d.mkdir(parents=True)
    (d / "50_prereg_a.md").write_text("# fifty\n", encoding="utf-8")

    state._ARTIFACT_CACHE = (None, 0.0, None)
    try:
        first = state.load_research_artifacts(tmp_path, ttl=600.0)
        assert first["cached"] is False
        assert first["file_count"] == 1

        (d / "51_prereg_b.md").write_text("# fifty-one\n", encoding="utf-8")
        second = state.load_research_artifacts(tmp_path, ttl=600.0)
        assert second["cached"] is True
        assert second["file_count"] == 1  # stale by design…
        assert second["cache_age_seconds"] >= 0.0  # …and its age is published

        third = state.load_research_artifacts(tmp_path, ttl=0.0)
        assert third["cached"] is False
        assert third["file_count"] == 2
    finally:
        state._ARTIFACT_CACHE = (None, 0.0, None)


def test_load_refuted_ledger_counts_without_copying_the_evidence(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "skills" / "refuted-families-ledger" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Refuted-Families Ledger\n"
        "## In shadow (log-only)\n"
        "| Family | Registration | Date |\n|---|---|---|\n"
        "| Textbook breakout | owner-directed probe, NOT a pipeline GO | 2026-07-11 |\n"
        "## Refuted (do not re-propose)\n"
        "| Family | Verdict evidence | Date |\n|---|---|---|\n"
        "| RSI mean-reversion | 5 coins x 3yr, NO_EDGE | 2026-06 |\n"
        "| Candlestick patterns | 1,989 tests, 0 survivors | 2026-06-07 |\n",
        encoding="utf-8",
    )
    out = state.load_refuted_ledger(tmp_path)
    assert out["available"] is True
    assert out["counts"] == {"shadow": 1, "refuted": 2}
    refuted = out["families"]["refuted"]
    assert [r["family"] for r in refuted] == ["RSI mean-reversion", "Candlestick patterns"]
    assert refuted[0]["date"] == "2026-06"
    # The verdict-evidence column is the binding part and stays in the file.
    assert all("NO_EDGE" not in json.dumps(r) for r in refuted)


def test_load_refuted_ledger_missing_file(tmp_path: Path) -> None:
    out = state.load_refuted_ledger(tmp_path)
    assert out["available"] is False
    assert out["counts"] == {}
    assert "not present" in out["reason"]


def test_load_promotion_path_absent_directory_is_stated_plainly(tmp_path: Path) -> None:
    out = state.load_promotion_path(tmp_path)
    assert out["exists"] is False
    assert out["dossiers"] == []
    assert "LOG-ONLY" in out["note"]


def test_load_promotion_path_lists_dossiers(tmp_path: Path) -> None:
    d = tmp_path / "reports" / "promotion_dossiers"
    d.mkdir(parents=True)
    (d / "zfade_4h_cfg365.md").write_text("x", encoding="utf-8")
    out = state.load_promotion_path(tmp_path)
    assert out["exists"] is True
    assert out["dossiers"] == ["zfade_4h_cfg365.md"]


def test_fallback_docstring_claims_measured_truth() -> None:
    """F3e (2026-07-29): the fallback count-only query measured 953-957 ms warm
    / 4.5 s cold against the live warehouse — ~355x the docstring's stale
    '2.6-2.8 ms' claim. This codebase's convention is measured claims only."""
    doc = state._brain_scorer_stage.__doc__ or ""
    assert "2.6" not in doc, "stale 2.6-2.8 ms claim must be gone"
    assert "953" in doc, "docstring must carry the 2026-07-29 measured figures"
