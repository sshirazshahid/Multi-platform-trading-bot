"""scripts/gate_status.py — promotion-readiness status reporter (Codex port).

REPORTING ONLY. The script reads warehouse/shadow tables and existing gate
constants; it must never become a second gate authority:
  * frozen thresholds (DSR/PBO/OOS-WR/AUC) are IMPORTED from
    core/promotion_gate.py — nothing re-declared;
  * the >=30 resolved floor is IMPORTED from scripts/report_goal_progress.py;
  * owner sign-off is always PENDING — the reporter can never grant it;
  * exit code 2 while anything is blocked.

Also pins the two honesty diagnostics (informational, not gates):
  (a) expectancy-drift of the deep_breakout lane vs the FROZEN research
      baseline (median full-history breakout_60d expectancy from
      Codex/reports/deep_futures_runs.csv), flagged outside +/-30%;
  (b) outlier-removed profit factor (drop top max(1, ceil(5% n)) winners),
      flagged when < 1.0.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_GS = None


def _gs():
    global _GS
    if _GS is None:
        spec = importlib.util.spec_from_file_location(
            "gate_status_under_test", ROOT / "scripts" / "gate_status.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GS = mod
    return _GS


NOW = 1_800_000_000.0  # fixed eval time for deterministic day math


def _fixture_db(tmp_path) -> Path:
    """Minimal warehouse: one arm with resolved outcomes + scores, one arm
    pending-only, deep_breakout lane trades; several probe tables absent."""
    db = tmp_path / "wh_fixture.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE shadow_decisions (
            proposal_id TEXT, model_version TEXT, ts INTEGER, label_status TEXT);
        CREATE TABLE shadow_outcomes (
            proposal_id TEXT PRIMARY KEY, net_pnl REAL, r_multiple REAL,
            label_status TEXT, resolved_ts INTEGER);
        CREATE TABLE shadow_listing_probe (
            proposal_id TEXT, score REAL, decision TEXT);
        CREATE TABLE trades (
            ts_entry REAL, ts_exit REAL, exchange TEXT, symbol TEXT, side TEXT,
            strategy_family TEXT, realized_pnl REAL, partial_realized_pnl REAL,
            r_multiple REAL, status TEXT, mode TEXT);
        """
    )
    # listing_short: 3 RESOLVED (2 wins), 1 PENDING (must not count)
    dec = [
        ("ls-1", "listing_short_probe_v1", 1, "RESOLVED"),
        ("ls-2", "listing_short_probe_v1", 2, "RESOLVED"),
        ("ls-3", "listing_short_probe_v1", 3, "RESOLVED"),
        ("ls-4", "listing_short_probe_v1", 4, "PENDING"),
        # unlock W1 pending-only; the -sl8 counterfactual row is RESOLVED but
        # carries a different model_version -> must NOT count toward W1
        ("uw-1", "unlock_short_w1_v1", 5, "PENDING"),
        ("uw-1-sl8", "unlock_short_w1_sl8_v1", 5, "RESOLVED"),
    ]
    conn.executemany("INSERT INTO shadow_decisions VALUES (?,?,?,?)", dec)
    out = [
        ("ls-1", 1.0, 0.5, "RESOLVED", 10),
        ("ls-2", 2.0, 1.0, "RESOLVED", 11),
        ("ls-3", -1.0, -0.5, "RESOLVED", 12),
        ("uw-1-sl8", 1.0, 0.5, "RESOLVED", 13),
    ]
    conn.executemany("INSERT INTO shadow_outcomes VALUES (?,?,?,?,?)", out)
    conn.executemany(
        "INSERT INTO shadow_listing_probe VALUES (?,?,?)",
        [("ls-1", 0.9, "ENTER"), ("ls-2", 0.8, "ENTER"), ("ls-3", 0.1, "ENTER")],
    )
    # deep_breakout lane: 4 closed PAPER trades, first entry 5 days ago
    t0 = NOW - 5 * 86400
    trades = [
        (t0, t0 + 3600, "binance", "BTC/USDT:USDT", "buy",
         "deep_breakout", 30.0, 0.0, 3.0, "CLOSED", "PAPER"),
        (t0 + 7200, t0 + 9000, "binance", "ETH/USDT:USDT", "sell",
         "deep_breakout", -10.0, 0.0, -1.0, "CLOSED", "PAPER"),
        (t0 + 20000, t0 + 30000, "bybit", "SOL/USDT:USDT", "buy",
         "deep_breakout", -10.0, 0.0, -1.0, "CLOSED", "PAPER"),
        (t0 + 40000, t0 + 50000, "bybit", "ADA/USDT:USDT", "buy",
         "deep_breakout", 5.0, 0.0, 0.5, "CLOSED", "PAPER"),
        # open lane trade + a foreign-family trade: both excluded
        (t0 + 60000, None, "binance", "XRP/USDT:USDT", "buy",
         "deep_breakout", None, None, None, "OPEN", "PAPER"),
        (t0, t0 + 100, "binance", "BTC/USDT:USDT", "sell",
         "claude_portfolio", 99.0, 0.0, 2.0, "CLOSED", "PAPER"),
    ]
    conn.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", trades)
    conn.commit()
    conn.close()
    return db


def _checks_by(report, scope, name=None):
    hits = [c for c in report["checks"]
            if c["scope"] == scope and (name is None or c["name"] == name)]
    return hits[0] if name is not None else hits


# ── no second gate authority ─────────────────────────────────────────────
def test_thresholds_imported_not_redeclared():
    import core.promotion_gate as pg

    gs = _gs()
    assert gs.MIN_DSR == pg.MIN_DSR
    assert gs.MAX_PBO == pg.MAX_PBO
    assert gs.MIN_OOS_WR == pg.MIN_OOS_WR
    assert gs.MIN_AUC == pg.MIN_AUC
    src = (ROOT / "scripts" / "gate_status.py").read_text(encoding="utf-8")
    assert "from core.promotion_gate import" in src
    # the gate constants must not be re-declared locally (drift risk)
    for name in ("MIN_DSR", "MAX_PBO", "MIN_OOS_WR", "MIN_AUC"):
        assert not re.search(rf"^{name}\s*=", src, re.M), f"{name} re-declared"


def test_resolved_floor_shared_with_goal_reporter():
    gs = _gs()
    spec = importlib.util.spec_from_file_location(
        "goal_progress_floor_check", ROOT / "scripts" / "report_goal_progress.py")
    goal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(goal)
    assert gs.RESOLVED_FLOOR == goal.RESOLVED_FLOOR == 30


def test_db_opened_read_only():
    src = (ROOT / "scripts" / "gate_status.py").read_text(encoding="utf-8")
    assert "mode=ro" in src  # reporter must never be able to write the warehouse


# ── report content: shadow arms ──────────────────────────────────────────
def test_all_arms_and_lane_reported(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    scopes = {c["scope"] for c in report["checks"]}
    for scope in ("listing_short", "unlock_short_w1", "unlock_short_w2",
                  "tsmom_1h", "tsmom_4h", "breakout_60d", "deep_breakout"):
        assert scope in scopes, f"missing scope {scope}"


def test_listing_short_arm_metrics(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    floor = _checks_by(report, "listing_short", "resolved_events")
    assert floor["actual"] == 3  # PENDING row not counted
    assert floor["passed"] is False  # 3 < 30
    assert str(gs.RESOLVED_FLOOR) in floor["requirement"]
    wr = _checks_by(report, "listing_short", "oos_wr")
    assert wr["actual"] == pytest.approx(2 / 3, abs=1e-4)
    assert wr["passed"] is True  # 0.667 >= 0.55 (floor still blocks overall)
    auc = _checks_by(report, "listing_short", "auc")
    assert auc["actual"] == pytest.approx(1.0)  # scores perfectly separate W/L
    assert auc["passed"] is True
    dsr = _checks_by(report, "listing_short", "dsr")
    assert isinstance(dsr["actual"], float)  # computable with n=3
    pbo = _checks_by(report, "listing_short", "pbo")
    assert pbo["actual"] is None  # never computable from one arm's outcomes
    assert pbo["passed"] is False  # fail-closed


def test_exact_model_version_match_excludes_sl8_counterfactual(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    w1 = _checks_by(report, "unlock_short_w1", "resolved_events")
    assert w1["actual"] == 0  # the resolved -sl8 row must not leak into W1


def test_arm_with_no_rows_and_missing_probe_table_degrades(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    for scope in ("tsmom_1h", "tsmom_4h", "breakout_60d"):
        floor = _checks_by(report, scope, "resolved_events")
        assert floor["actual"] == 0 and floor["passed"] is False
        assert _checks_by(report, scope, "auc")["actual"] is None


def test_missing_tables_entirely_no_crash(tmp_path):
    gs = _gs()
    db = tmp_path / "empty.sqlite"
    sqlite3.connect(db).close()  # zero tables
    report = gs.build_report(db, now=NOW)
    assert report["blocked"] is True
    floor = _checks_by(report, "listing_short", "resolved_events")
    assert floor["actual"] == 0


# ── report content: deep_breakout lane ───────────────────────────────────
def test_deep_breakout_gate_metrics(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    floor = _checks_by(report, "deep_breakout", "resolved_events")
    assert floor["actual"] == 4  # closed lane trades only (open + foreign excluded)
    wr = _checks_by(report, "deep_breakout", "oos_wr")
    assert wr["actual"] == pytest.approx(0.5)
    assert wr["passed"] is False  # 0.5 < 0.55


def test_deep_breakout_codex_forward_progress_is_informational(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    info = {c["name"]: c for c in report["checks"]
            if c["scope"] == "deep_breakout" and c.get("info")}
    assert info["forward_trades"]["actual"] == 4
    assert "60" in info["forward_trades"]["requirement"]
    # PF = (3.0 + 0.5) / (1.0 + 1.0) on r_multiples
    assert info["forward_profit_factor"]["actual"] == pytest.approx(1.75)
    assert "1.15" in info["forward_profit_factor"]["requirement"]
    assert "12%" in info["forward_max_drawdown"]["requirement"] or \
        "0.12" in info["forward_max_drawdown"]["requirement"]
    assert info["forward_days_elapsed"]["actual"] == pytest.approx(5.0, abs=0.1)
    assert "90" in info["forward_days_elapsed"]["requirement"]
    # informational lines never gate: none carries passed=False that blocks
    for c in info.values():
        assert c.get("info") is True


# ── honesty diagnostics ──────────────────────────────────────────────────
def test_frozen_expectancy_baseline_value_and_provenance():
    gs = _gs()
    # median per-trade expectancy (R) over the 10 breakout_60d full_history
    # rows of Codex/reports/deep_futures_runs.csv (frozen 2026-07-12)
    assert gs.BREAKOUT_60D_RESEARCH_EXPECTANCY_R == pytest.approx(0.094436, abs=1e-5)
    src = (ROOT / "scripts" / "gate_status.py").read_text(encoding="utf-8")
    assert "deep_futures_runs.csv" in src  # provenance documented


def test_expectancy_drift_flagged_outside_30pct(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    drift = _checks_by(report, "deep_breakout", "expectancy_drift")
    assert drift["info"] is True
    # realized mean R = (3 - 1 - 1 + 0.5)/4 = 0.375 vs baseline 0.0944 -> ~+297%
    assert drift["flagged"] is True
    assert drift["actual"] == pytest.approx(0.375 / gs.BREAKOUT_60D_RESEARCH_EXPECTANCY_R - 1,
                                            rel=1e-6)


def test_expectancy_drift_not_flagged_within_band():
    gs = _gs()
    base = gs.BREAKOUT_60D_RESEARCH_EXPECTANCY_R
    assert gs.expectancy_drift([base * 1.2]) == pytest.approx(0.2)
    assert gs.expectancy_drift([base * 1.2]) <= gs.EXPECTANCY_DRIFT_TOLERANCE
    assert abs(gs.expectancy_drift([base * 0.6])) > gs.EXPECTANCY_DRIFT_TOLERANCE
    assert gs.expectancy_drift([]) is None  # no trades -> n/a, never flagged


def test_outlier_removed_pf_math():
    gs = _gs()
    # n=4 -> drop max(1, ceil(0.05*4)) = 1 top winner (3.0): PF = 0.5/1.5
    assert gs.outlier_removed_profit_factor([3.0, 0.5, -1.0, -0.5]) == \
        pytest.approx(0.5 / 1.5)
    # no winners -> 0.0 (Codex semantics)
    assert gs.outlier_removed_profit_factor([-1.0, -2.0]) == 0.0
    # robust book: dropping the best winner still leaves PF >= 1
    assert gs.outlier_removed_profit_factor([1.0, 1.0, 1.0, -0.5]) == \
        pytest.approx(2.0 / 0.5)
    # n=40 -> ceil(2.0) = 2 winners dropped
    rets = [2.0, 1.5] + [0.1] * 18 + [-0.05] * 20
    assert gs.outlier_removed_profit_factor(rets) == \
        pytest.approx((0.1 * 18) / (0.05 * 20))


def test_outlier_removed_pf_flag_in_report(tmp_path):
    gs = _gs()
    report = gs.build_report(_fixture_db(tmp_path), now=NOW)
    opf = _checks_by(report, "deep_breakout", "outlier_removed_pf")
    assert opf["info"] is True
    assert opf["actual"] == pytest.approx(0.5 / 2.0)  # drop 3.0 -> 0.5/(1+1)
    assert opf["flagged"] is True  # < 1.0


# ── AUC helper ───────────────────────────────────────────────────────────
def test_rank_auc():
    gs = _gs()
    assert gs.rank_auc([0.9, 0.8, 0.1], [1, 1, 0]) == pytest.approx(1.0)
    assert gs.rank_auc([0.1, 0.8, 0.9], [1, 1, 0]) == pytest.approx(0.0)
    assert gs.rank_auc([0.6, 0.2, 0.8], [1, 0, 0]) == pytest.approx(0.5)
    assert gs.rank_auc([0.5, 0.5], [1, 0]) == pytest.approx(0.5)  # ties half-credit
    assert gs.rank_auc([0.5, 0.6], [1, 1]) is None  # one class -> undefined


# ── output shape, JSON, exit code ────────────────────────────────────────
LINE_RE = re.compile(r"^\[(PASS|FAIL|INFO|FLAG)\] [a-z0-9_]+\.[a-z0-9_]+: .+ \(.+\)$")


def test_text_output_shape(tmp_path, capsys):
    gs = _gs()
    rc = gs.main(["--db", str(_fixture_db(tmp_path))])
    out = capsys.readouterr().out
    assert rc == 2  # floors unmet + sign-off pending -> blocked
    lines = [ln for ln in out.splitlines() if ln.startswith("[")]
    assert len(lines) >= 30  # 7 scopes x >=5 criteria
    for ln in lines:
        assert LINE_RE.match(ln), f"malformed line: {ln!r}"
    assert "owner sign-off: PENDING" in out
    assert "readiness" in out.lower()  # informational readiness score


def test_json_output(tmp_path, capsys):
    gs = _gs()
    rc = gs.main(["--db", str(_fixture_db(tmp_path)), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["blocked"] is True
    assert isinstance(payload["checks"], list)
    assert {"scope", "name", "passed", "actual", "requirement"} <= set(payload["checks"][0])


def test_missing_db_exits_blocked(tmp_path, capsys):
    gs = _gs()
    rc = gs.main(["--db", str(tmp_path / "nope.sqlite")])
    assert rc == 2
