"""Contract tests for the post-geometry-fix verdict agent (SP1).

The agent's whole value is that it answers "is the new geometry working?"
without fooling itself. Two ways it could fool itself, both pinned here:

1. Windowing by EXIT time would pull pre-fix trades that merely closed after
   the epoch into the cohort — measuring the old geometry and calling it the
   new one. This was caught in design review before implementation.
2. Pooling two geometry epochs would measure neither.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.report_postfix_verdict import (
    GEOMETRY_EPOCHS,
    RESOLVED_TARGET,
    TIMEOUT_FLAG_MIN_N,
    build_report,
    classify,
    exit_path_breakdown,
)

V1_EPOCH = GEOMETRY_EPOCHS[0][1]
V2_EPOCH = GEOMETRY_EPOCHS[1][1]
# The CURRENT (open) cohort is always the last entry — never hard-code a
# label here, or every future epoch stamp breaks these tests.
CURRENT_EPOCH = GEOMETRY_EPOCHS[-1][1]
CURRENT_LABEL_ = GEOMETRY_EPOCHS[-1][0]

_DDL = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    status TEXT, mode TEXT, market_type TEXT,
    ts_entry REAL, ts_exit REAL,
    realized_pnl REAL, partial_realized_pnl REAL,
    decision_id TEXT, strategy_family TEXT,
    exit_reason TEXT, entry_px REAL, entry_stop_px REAL
);
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return c


def _trade(c, *, tid, ts_entry, ts_exit, pnl, reason="take_profit",
           family="algo_det", decision="d1", mode="PAPER"):
    c.execute(
        "INSERT INTO trades (id,status,mode,market_type,ts_entry,ts_exit,"
        "realized_pnl,partial_realized_pnl,decision_id,strategy_family,"
        "exit_reason,entry_px,entry_stop_px) "
        "VALUES (?,'CLOSED',?,'futures',?,?,?,0,?,?,?,100.0,98.5)",
        (tid, mode, ts_entry, ts_exit, pnl, decision, family, reason),
    )
    c.commit()


# ── the design-review catch ────────────────────────────────────────────────

def test_trade_opened_before_epoch_is_excluded_even_if_it_closed_after(conn):
    """THE regression guard. A trade entered under the OLD geometry that
    merely closed after the epoch must never count toward the new cohort."""
    _trade(conn, tid=1, ts_entry=V2_EPOCH - 3600, ts_exit=V2_EPOCH + 600, pnl=5.0)
    _trade(conn, tid=2, ts_entry=V2_EPOCH + 60, ts_exit=V2_EPOCH + 900, pnl=-1.0)

    report = build_report(conn, now=V2_EPOCH + 7200)
    # Target the epoch under test BY LABEL. Using cohorts[-1] silently retargets
    # this guard onto whatever the newest epoch happens to be, which would let
    # the leak it exists to catch pass unnoticed once a later epoch is stamped.
    v2 = next(c for c in report["cohorts"] if c["epoch_label"] == "v2")

    assert v2["resolved_n"] == 1, "pre-epoch entry leaked into the cohort"
    assert v2["net_after_cost_pnl"] == pytest.approx(-1.0)


def test_epochs_are_reported_separately_never_pooled(conn):
    _trade(conn, tid=1, ts_entry=V1_EPOCH + 60, ts_exit=V1_EPOCH + 600, pnl=-2.0)
    _trade(conn, tid=2, ts_entry=V2_EPOCH + 60, ts_exit=V2_EPOCH + 600, pnl=+3.0)

    report = build_report(conn, now=V2_EPOCH + 7200)
    by_label = {c["epoch_label"]: c for c in report["cohorts"]}

    assert by_label["v1"]["resolved_n"] == 1
    assert by_label["v1"]["net_after_cost_pnl"] == pytest.approx(-2.0)
    assert by_label["v1"]["closed"] is True
    assert by_label["v2"]["resolved_n"] == 1
    assert by_label["v2"]["net_after_cost_pnl"] == pytest.approx(+3.0)
    # Only the LAST epoch is open; every earlier one is bounded by its successor.
    assert by_label["v2"]["closed"] is True
    assert by_label[CURRENT_LABEL_]["closed"] is False
    assert report["verdict"] == by_label[CURRENT_LABEL_]["verdict"],         "headline must track the current cohort"


# ── provenance ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("family", ["manual", "reconcile", "reconciled_exchange"])
def test_adopted_and_reconciled_trades_never_enter_the_cohort(conn, family):
    _trade(conn, tid=1, ts_entry=V2_EPOCH + 60, ts_exit=V2_EPOCH + 600,
           pnl=99.0, family=family)
    report = build_report(conn, now=V2_EPOCH + 7200)
    assert report["cohorts"][-1]["resolved_n"] == 0


def test_rows_without_decision_id_are_excluded(conn):
    _trade(conn, tid=1, ts_entry=V2_EPOCH + 60, ts_exit=V2_EPOCH + 600,
           pnl=99.0, decision=None)
    report = build_report(conn, now=V2_EPOCH + 7200)
    assert report["cohorts"][-1]["resolved_n"] == 0


# ── verdict table ──────────────────────────────────────────────────────────

def _summary(**kw):
    base = {
        "closed_outcomes": 0, "net_after_cost_pnl": 0.0, "decisive_win_rate": None,
        "gross_profit": 0.0, "gross_loss": 0.0, "wins": 0, "losses": 0,
        "win_rate_ci95": [None, None],
    }
    base.update(kw)
    return base


_CLEAN = {"status": "UNAVAILABLE", "sub_1_rr_trade_ids": []}


def test_too_early_below_target():
    out = classify(_summary(closed_outcomes=RESOLVED_TARGET - 1, net_after_cost_pnl=5.0),
                   _CLEAN, {"take_profit": 29})
    assert out["verdict"] == "TOO_EARLY"


def test_not_working_when_net_negative_at_sample():
    out = classify(_summary(closed_outcomes=40, net_after_cost_pnl=-3.0,
                            decisive_win_rate=0.5, gross_profit=10, gross_loss=13,
                            wins=20, losses=20),
                   _CLEAN, {"stop_loss": 40})
    assert out["verdict"] == "NOT_WORKING"


def test_working_requires_payoff_to_clear_its_own_breakeven():
    # 40 trades, w=0.5 -> required payoff 1.0; realized payoff 2.0 clears it.
    out = classify(_summary(closed_outcomes=40, net_after_cost_pnl=+12.0,
                            decisive_win_rate=0.5, gross_profit=40, gross_loss=20,
                            wins=20, losses=20),
                   _CLEAN, {"take_profit": 40})
    assert out["verdict"] == "WORKING"
    assert out["required_payoff"] == pytest.approx(1.0)
    assert out["realized_payoff"] == pytest.approx(2.0)


def test_mixed_when_profitable_but_payoff_below_requirement():
    # w=0.8 -> required payoff 0.25; realized 0.2 falls short despite net>0.
    out = classify(_summary(closed_outcomes=40, net_after_cost_pnl=+1.0,
                            decisive_win_rate=0.8, gross_profit=6.4, gross_loss=8.0,
                            wins=32, losses=8),
                   _CLEAN, {"take_profit": 40})
    assert out["verdict"] == "MIXED"


def test_contamination_beats_every_other_verdict():
    out = classify(_summary(closed_outcomes=40, net_after_cost_pnl=+50.0,
                            decisive_win_rate=0.9, gross_profit=60, gross_loss=10,
                            wins=36, losses=4),
                   {"status": "OK", "sub_1_rr_trade_ids": [7]}, {"take_profit": 40})
    assert out["verdict"] == "COHORT_CONTAMINATED"


# ── timeout-interference modifier ──────────────────────────────────────────

def test_timeout_flag_needs_minimum_sample():
    """n=2 all-STALE must NOT fire the flag — the exact early state that
    prompted the tier-geometry hold."""
    out = classify(_summary(closed_outcomes=2), _CLEAN, {"STALE": 2})
    assert out["time_exit_share"] == pytest.approx(1.0)
    assert out["timeout_interference"] is False


def test_timeout_flag_fires_at_sample_with_majority_time_exits():
    paths = {"STALE": 6, "take_profit": 4}
    out = classify(_summary(closed_outcomes=TIMEOUT_FLAG_MIN_N), _CLEAN, paths)
    assert out["timeout_interference"] is True


def test_timeout_flag_silent_when_barriers_dominate():
    paths = {"take_profit": 8, "stop_loss": 4, "STALE": 2}
    out = classify(_summary(closed_outcomes=14), _CLEAN, paths)
    assert out["timeout_interference"] is False


# ── contamination guard must never claim a clean cohort it cannot prove ────

def test_contamination_guard_reports_itself_inert(conn):
    _trade(conn, tid=1, ts_entry=V2_EPOCH + 60, ts_exit=V2_EPOCH + 600, pnl=1.0)
    report = build_report(conn, now=V2_EPOCH + 7200)
    guard = report["cohorts"][-1]["contamination"]
    assert guard["status"] == "UNAVAILABLE"
    assert "target price" in guard["reason"]
    assert "INERT" in report["headline"]


# ── exit-path breakdown ────────────────────────────────────────────────────

def test_exit_path_breakdown_counts_by_reason(conn):
    _trade(conn, tid=1, ts_entry=V2_EPOCH + 1, ts_exit=V2_EPOCH + 10, pnl=1, reason="take_profit")
    _trade(conn, tid=2, ts_entry=V2_EPOCH + 2, ts_exit=V2_EPOCH + 20, pnl=-1, reason="stop_loss")
    _trade(conn, tid=3, ts_entry=V2_EPOCH + 3, ts_exit=V2_EPOCH + 30, pnl=0, reason="STALE")
    paths = exit_path_breakdown(conn, since_epoch=V2_EPOCH)
    assert paths == {"STALE": 1, "stop_loss": 1, "take_profit": 1}


# ── frozen constants ───────────────────────────────────────────────────────

def test_epoch_provenance_is_pinned():
    labels = [e[0] for e in GEOMETRY_EPOCHS]
    epochs = [e[1] for e in GEOMETRY_EPOCHS]
    assert labels == ["v1", "v2", "v3"]
    assert epochs == [1785673514.0, 1785778977.0, 1786022386.0]
    assert epochs == sorted(epochs), "epochs must be chronological"
    assert RESOLVED_TARGET == 30
    for _label, _epoch, note in GEOMETRY_EPOCHS:
        assert "respawn" in note, "each epoch must cite the restart that armed it"
