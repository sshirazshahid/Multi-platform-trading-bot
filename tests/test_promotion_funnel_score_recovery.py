"""The promotion gate's AUC must be measured, not defaulted (2026-07-31).

DEFECT: core/agents/probe_common.write_shadow_decision writes a literal
``"p_win": None`` for every row. Measured on the live warehouse, that makes
p_win NULL on 100% of RESOLVED rows for the four agents that use that helper:

    TsmomProbeAgent               66 resolved / 0 non-null
    Rsi2TrackerProbeAgent        117 resolved / 0 non-null
    ZfadeProbeAgent               59 resolved / 0 non-null
    PullbackMomentumProbeAgent    45 resolved / 0 non-null

(The older agents -- TrendAgent 24050/24050, PatternAgent 18660/18660 -- are
unaffected, which is what isolates the cause to that one helper.)

promotion_funnel.run_gate builds its AUC inputs with ``if o.get("p_win") is not
None``, so both score lists come back EMPTY, and _auc returns its degenerate
0.5 for empty input -- against MIN_AUC = 0.60. The gate could therefore never
pass for any probe lane, no matter how good the strategy was. Every
"failed on AUC 0.50" verdict on record was a non-measurement, not a result.

The scores were never lost: each probe writes a real, varying score to its own
table, joinable on proposal_id. Measured distinct-score counts: tsmom 67/69,
rsi2 118/118, zfade 59/59, pullback 46/46. This suite pins the recovery.

NOTE ON SEMANTICS: a probe ``score`` is a frozen ranking statistic, not a
calibrated probability. AUC only needs a ranking, so using it as the
discriminator is sound -- but it must never be presented as a win probability.
"""
from __future__ import annotations

import sqlite3

import pytest

import scripts.promotion_funnel as pf


def _warehouse(tmp_path, rows, *, probe_table="shadow_tsmom_probe",
               agent="TsmomProbeAgent", timeframe="4h"):
    """rows: list of (proposal_id, net_pnl, p_win_or_None, score_or_None)."""
    db = tmp_path / "wh.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shadow_decisions (ts INTEGER, agent_id TEXT, timeframe TEXT,"
        " proposal_id TEXT, label_status TEXT, p_win REAL, model_version TEXT)"
    )
    conn.execute("CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL)")
    conn.execute(f"CREATE TABLE {probe_table} (proposal_id TEXT, score REAL)")
    for pid, net, p_win, score in rows:
        conn.execute(
            "INSERT INTO shadow_decisions VALUES (?,?,?,?,?,?,?)",
            (1_785_000_000, agent, timeframe, pid, "RESOLVED", p_win, "v1"),
        )
        conn.execute("INSERT INTO shadow_outcomes VALUES (?,?)", (pid, net))
        if score is not None:
            conn.execute(f"INSERT INTO {probe_table} VALUES (?,?)", (pid, score))
    conn.commit()
    return conn


# -- the defect ---------------------------------------------------------------

def test_score_is_recovered_when_p_win_is_null(tmp_path):
    """THE BUG: p_win NULL must fall back to the probe's own frozen score."""
    conn = _warehouse(tmp_path, [
        ("p1", 1.0, None, 0.90),
        ("p2", 1.0, None, 0.80),
        ("p3", -1.0, None, 0.20),
        ("p4", -1.0, None, 0.10),
    ])
    outcomes = pf._load_probe_outcomes(conn, agent_id="TsmomProbeAgent", timeframe="4h")
    assert len(outcomes) == 4
    assert all(o["p_win"] is not None for o in outcomes), (
        f"scores not recovered: {outcomes}"
    )
    assert sorted(o["p_win"] for o in outcomes) == [0.10, 0.20, 0.80, 0.90]


def test_auc_is_no_longer_the_degenerate_half(tmp_path):
    """A perfectly discriminating probe must score AUC 1.0, not 0.5."""
    conn = _warehouse(tmp_path, [
        ("w1", 1.0, None, 0.90), ("w2", 1.0, None, 0.85),
        ("l1", -1.0, None, 0.15), ("l2", -1.0, None, 0.10),
    ])
    outcomes = pf._load_probe_outcomes(conn, agent_id="TsmomProbeAgent", timeframe="4h")
    res = pf.run_gate(outcomes)
    assert res["gates"]["auc"]["value"] == pytest.approx(1.0), (
        "AUC still degenerate -- the score never reached run_gate"
    )


def test_regression_all_null_scores_still_yields_half(tmp_path):
    """Honest failure mode: with no score anywhere, AUC stays 0.5 and the gate
    fails. The repair must not invent a discriminator out of nothing."""
    conn = _warehouse(tmp_path, [
        ("w1", 1.0, None, None), ("l1", -1.0, None, None),
    ])
    outcomes = pf._load_probe_outcomes(conn, agent_id="TsmomProbeAgent", timeframe="4h")
    res = pf.run_gate(outcomes)
    assert res["gates"]["auc"]["value"] == pytest.approx(0.5)
    assert res["gates"]["auc"]["ok"] is False


# -- do not regress the agents that already work ------------------------------

def test_existing_p_win_wins_over_probe_score(tmp_path):
    """TrendAgent et al. already write real p_win; it must take precedence."""
    conn = _warehouse(tmp_path, [
        ("p1", 1.0, 0.77, 0.11),
        ("p2", -1.0, 0.22, 0.99),
    ])
    outcomes = pf._load_probe_outcomes(conn, agent_id="TsmomProbeAgent", timeframe="4h")
    got = sorted(o["p_win"] for o in outcomes)
    assert got == [0.22, 0.77], f"probe score shadowed a real p_win: {got}"


def test_timeframe_filter_is_honoured(tmp_path):
    """tsmom runs 1h and 4h arms; they must not pool."""
    conn = _warehouse(tmp_path, [("a", 1.0, None, 0.6)], timeframe="4h")
    conn.execute(
        "INSERT INTO shadow_decisions VALUES (?,?,?,?,?,?,?)",
        (1_785_000_000, "TsmomProbeAgent", "1h", "b", "RESOLVED", None, "v1"),
    )
    conn.execute("INSERT INTO shadow_outcomes VALUES ('b', -5.0)")
    conn.execute("INSERT INTO shadow_tsmom_probe VALUES ('b', 0.4)")
    conn.commit()
    four = pf._load_probe_outcomes(conn, agent_id="TsmomProbeAgent", timeframe="4h")
    assert [o["net_pnl"] for o in four] == [1.0]


def test_unresolved_rows_are_excluded(tmp_path):
    conn = _warehouse(tmp_path, [("a", 1.0, None, 0.6)])
    conn.execute(
        "INSERT INTO shadow_decisions VALUES (?,?,?,?,?,?,?)",
        (1_785_000_000, "TsmomProbeAgent", "4h", "pending", "PENDING", None, "v1"),
    )
    conn.execute("INSERT INTO shadow_outcomes VALUES ('pending', 9.0)")
    conn.commit()
    out = pf._load_probe_outcomes(conn, agent_id="TsmomProbeAgent", timeframe="4h")
    assert [o["net_pnl"] for o in out] == [1.0]


def test_unknown_agent_degrades_without_raising(tmp_path):
    """An agent with no registered score table must still load outcomes."""
    conn = _warehouse(tmp_path, [("a", 1.0, 0.6, None)], agent="MysteryAgent")
    out = pf._load_probe_outcomes(conn, agent_id="MysteryAgent", timeframe="4h")
    assert [o["net_pnl"] for o in out] == [1.0]


def test_score_table_name_is_never_caller_supplied():
    """SQL-injection guard: table names come from a fixed in-module allowlist."""
    for agent, table in pf.PROBE_SCORE_TABLES.items():
        assert table.startswith("shadow_") and table.replace("_", "").isalnum(), (
            f"unsafe score table for {agent}: {table!r}"
        )
