"""The goal target is profit and drawdown, not a win-rate band (2026-08-02).

WHY: the previous terminal success label was win-rate-banded. A lane that was
PROFITABLE at 75% WR returned ``WIN_RATE_ABOVE_TARGET`` -- success reported as a
miss -- which is what made "manufacture a win rate" look like progress. The
AccBand geometry did exactly that: it compressed take-profit to ~0.35% against a
~0.48% stop to land inside the band, and its own docstring concedes
"accuracy-by-geometry, NOT profit edge". Measured result: 60%+ WR, still losing.

THE MISSING NUMBER: nothing computed the win rate the realized payoff actually
requires. Live 7d at the time of writing -- wins 43, losses 28, gross_profit
8.545174, gross_loss 19.18282 -- gives b = mean_win/mean_loss = 0.290067 and a
breakeven of 1/(1+b) = 77.5%, against an actual 60.6% and a band topping out at
67%. Hitting the top of the goal would still lose money. That gap is the target.

DENOMINATOR NOTE: ``win_rate`` is wins/n (ties in the denominator), but the
breakeven identity assumes a two-outcome world. The comparison therefore uses
the decisive base wins/(wins+losses). Every live lane currently has ties == 0 so
the two coincide, but the guard is real and is tested below.

Companion suite: tests/test_goal_progress_honest.py covers provenance filtering,
INSUFFICIENT_SAMPLE, and that in-band-but-unprofitable stays a failure. This file
covers breakeven, drawdown, and the status precedence.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.report_goal_progress import (
    DRAWDOWN_TO_GROSS_PROFIT_CAP,
    RESOLVED_FLOOR,
    performance_summary,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            ts_entry REAL,
            ts_exit REAL,
            market_type TEXT,
            strategy_family TEXT,
            realized_pnl REAL,
            partial_realized_pnl REAL,
            status TEXT,
            mode TEXT,
            decision_id TEXT
        )"""
    )
    return conn


def _insert(conn, trade_id, pnl, *, ts_exit=None):
    conn.execute(
        "INSERT INTO trades (id,ts_entry,ts_exit,market_type,strategy_family,"
        "realized_pnl,partial_realized_pnl,status,mode,decision_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (trade_id, 100.0, float(ts_exit if ts_exit is not None else 200 + trade_id),
         "futures", "algo_det", pnl, 0.0, "CLOSED", "PAPER", "decision"),
    )


def _lane(pnls) -> dict:
    """Build a lane from an ordered pnl sequence. Order is preserved via ts_exit."""
    conn = _conn()
    for i, pnl in enumerate(pnls, start=1):
        _insert(conn, i, pnl)
    return performance_summary(conn, lane="x", since_epoch=0)


# -- breakeven win rate ------------------------------------------------------

def test_symmetric_payoff_breaks_even_at_half():
    """mean_win == mean_loss => b = 1 => breakeven 50%."""
    res = _lane([1.0] * 15 + [-1.0] * 15)
    assert res["breakeven_win_rate"] == pytest.approx(0.5)


def test_breakeven_falls_when_wins_are_larger():
    """20 wins of +1.0, 10 losses of -0.5 => b = 2.0 => breakeven 1/3."""
    res = _lane([1.0] * 20 + [-0.5] * 10)
    assert res["breakeven_win_rate"] == pytest.approx(1 / 3, abs=1e-6)


def test_breakeven_exceeds_actual_when_losses_dominate():
    """The live failure shape: high win rate, bigger losses, still losing.

    21 wins of +0.10 and 9 losses of -1.00 => WR 0.70 (above the 0.67 band top)
    while breakeven is 0.909 -- so the band is unreachable-by-construction here.
    """
    res = _lane([0.10] * 21 + [-1.00] * 9)
    assert res["win_rate"] == pytest.approx(0.70)
    assert res["breakeven_win_rate"] == pytest.approx(1 / 1.1, abs=1e-6)
    assert res["win_rate_vs_breakeven"] < 0
    assert res["target_status"] == "NEGATIVE_AFTER_COST_ECONOMICS"


def test_breakeven_is_none_when_there_are_no_losses():
    """Live case: current_profile_directional had n=1, wins=1, gross_loss=0.

    A breakeven ratio is undefined without a loss to divide by. Emit None --
    never a fabricated number.
    """
    res = _lane([1.0] * 5)
    assert res["breakeven_win_rate"] is None
    assert res["win_rate_vs_breakeven"] is None


def test_breakeven_is_none_when_there_are_no_wins():
    """mean_win = 0 would yield a meaningless breakeven of exactly 1.0."""
    res = _lane([-1.0] * 5)
    assert res["breakeven_win_rate"] is None


def test_breakeven_uses_the_decisive_base_when_ties_exist():
    """Ties sit in win_rate's denominator but not in the breakeven identity.

    2 wins, 2 losses, 4 ties: win_rate = 2/8 = 0.25, but the decisive base is
    2/(2+2) = 0.50, which is what breakeven must be compared against.
    """
    res = _lane([1.0, 1.0, -1.0, -1.0] + [0.0] * 4)
    assert res["ties"] == 4
    assert res["win_rate"] == pytest.approx(0.25)
    assert res["decisive_win_rate"] == pytest.approx(0.50)
    assert res["breakeven_win_rate"] == pytest.approx(0.50)
    assert res["win_rate_vs_breakeven"] == pytest.approx(0.0)


# -- drawdown ----------------------------------------------------------------

def test_drawdown_measures_the_worst_peak_to_trough():
    """Curve: +10 (peak 10) then -4, -3 (trough 3) then +5. Max drawdown 7."""
    res = _lane([10.0, -4.0, -3.0, 5.0])
    assert res["max_drawdown_abs"] == pytest.approx(7.0)
    assert res["max_drawdown_pct"] == pytest.approx(0.7)


def test_drawdown_pct_is_none_when_the_curve_never_rises_above_zero():
    """A monotonically losing lane has no positive peak to divide by; a
    percentage there would be arithmetic theatre. Absolute stays meaningful."""
    res = _lane([-1.0, -2.0, -3.0])
    assert res["max_drawdown_abs"] == pytest.approx(6.0)
    assert res["max_drawdown_pct"] is None


def test_monotonically_rising_curve_has_zero_drawdown():
    res = _lane([1.0, 1.0, 1.0])
    assert res["max_drawdown_abs"] == pytest.approx(0.0)


# -- status precedence -------------------------------------------------------

def test_profitable_above_the_band_is_target_met_not_a_miss():
    """THE BUG: profitable at 80% WR previously returned WIN_RATE_ABOVE_TARGET.

    Winning too often must never be reported as missing the target.
    """
    res = _lane([1.0] * 24 + [-0.5] * 6)
    assert res["closed_outcomes"] == RESOLVED_FLOOR
    assert res["win_rate"] == pytest.approx(0.8)
    assert res["net_after_cost_pnl"] > 0
    assert res["target_status"] == "TARGET_MET"


def test_profitable_below_the_band_is_also_target_met():
    """Profit at a low win rate is success too -- that is a trend shape."""
    res = _lane([5.0] * 12 + [-0.5] * 18)
    assert res["win_rate"] < 0.63
    assert res["net_after_cost_pnl"] > 0
    assert res["target_status"] == "TARGET_MET"


def test_unprofitable_inside_the_band_stays_a_failure():
    """Regression guard: today's live 7d shape must not flip to a pass."""
    res = _lane([0.1] * 20 + [-1.0] * 10)
    assert 0.63 <= res["win_rate"] <= 0.67
    assert res["target_status"] == "NEGATIVE_AFTER_COST_ECONOMICS"


def test_deep_drawdown_is_reported_but_does_not_gate_the_status():
    """Drawdown is surfaced, never blocking.

    A blocking drawdown status would withhold TARGET_MET, and
    scripts/promotion_funnel.py:356 stages GATE_READY off TARGET_MET -- so it
    would silently change promotion behaviour. Changing a promotion gate is a
    governance act (hashed prereg + owner sign-off), not a reporting change.
    Report the breach; let a human act on it.
    """
    res = _lane([1.0] * 5 + [-4.0] * 3)
    assert res["max_drawdown_to_gross_profit"] > DRAWDOWN_TO_GROSS_PROFIT_CAP
    assert res["drawdown_exceeds_cap"] is True
    assert res["target_status"] != "DRAWDOWN_EXCEEDED"


def test_shallow_drawdown_reports_the_flag_false():
    res = _lane([1.0] * 24 + [-0.5] * 6)
    assert res["drawdown_exceeds_cap"] is False
    assert res["drawdown_cap_to_gross_profit"] == pytest.approx(
        DRAWDOWN_TO_GROSS_PROFIT_CAP
    )


def test_drawdown_flag_is_defined_on_losing_lanes():
    """The old peak-relative flag was null on 6 of 7 live lanes -- including
    every losing one, which is precisely where a drawdown verdict matters.
    Gross profit exists as soon as the lane has one win, so the ratio does too.
    """
    res = _lane([2.0] + [-1.0] * 10)
    assert res["net_after_cost_pnl"] < 0
    assert res["max_drawdown_to_gross_profit"] is not None
    assert res["drawdown_exceeds_cap"] is True
    # And this is why the peak-relative pct cannot be the thresholded metric:
    # it is unbounded on a curve that starts at zero (here 10.0/2.0 = 500%).
    assert res["max_drawdown_pct"] == pytest.approx(5.0)


def test_drawdown_denominator_is_peak_at_trough_not_final_peak():
    """Regression: dividing by the FINAL peak understated by 10x.

    [10, -9.5] and [10, -9.5, +100] contain the identical 9.5 drawdown. The old
    code reported 95% and 9.45%, because recovery inflated the denominator --
    biased downward exactly on the recovering lanes that had a defined pct.
    """
    shallow = _lane([10.0, -9.5])
    recovered = _lane([10.0, -9.5, 100.0])
    assert shallow["max_drawdown_abs"] == pytest.approx(9.5)
    assert recovered["max_drawdown_abs"] == pytest.approx(9.5)
    assert shallow["max_drawdown_pct"] == pytest.approx(0.95)
    assert recovered["max_drawdown_pct"] == pytest.approx(0.95)


def test_note_indicts_exit_geometry_not_the_win_rate():
    """The note must not read as 'raise the win rate by N points'.

    Compressing take-profit to lift WR lowers mean_win/mean_loss, which RAISES
    breakeven -- the target retreats as you chase it. So the note states the
    payoff ratio the CURRENT win rate requires.
    """
    res = _lane([0.10] * 21 + [-1.00] * 9)      # WR 0.70, payoff b = 0.10
    note = res["win_rate_note"]
    assert "mean_win/mean_loss" in note
    assert "Fix exit geometry, not the win rate" in note
    # at WR 0.70, breakeven needs b >= (1-0.7)/0.7 = 0.43; it has 0.10
    assert "0.43" in note and "0.10" in note


def test_note_uses_the_decisive_base_so_it_agrees_with_the_gap_field():
    """Regression: the note took wins/n while the gap field took the decisive
    base, so on a lane with ties the two contradicted each other in one payload.
    """
    res = _lane([1.0, 1.0, -1.0, -1.0] + [0.0] * 4)
    assert res["win_rate_vs_breakeven"] == pytest.approx(0.0)
    assert "at 50.0% win rate" in res["win_rate_note"]


def test_insufficient_sample_still_wins_precedence():
    res = _lane([1.0] * 3)
    assert res["target_status"] == "INSUFFICIENT_SAMPLE"


def test_win_rate_note_is_advisory_and_never_a_status():
    """Band commentary survives as a note, but cannot fail a profitable lane."""
    res = _lane([1.0] * 24 + [-0.5] * 6)
    assert res["target_status"] == "TARGET_MET"
    assert isinstance(res.get("win_rate_note"), str) and res["win_rate_note"]


def test_promotion_funnel_contract_string_is_unchanged():
    """scripts/promotion_funnel.py:356 stages GATE_READY off this exact literal.

    Renaming it would silently stop staging every probe lane.
    """
    res = _lane([1.0] * 24 + [-0.5] * 6)
    assert res["target_status"] == "TARGET_MET"
