"""Phase 1.3: diagnostic report unit tests.

The report is the gate that determines whether Phase 2+ proceeds. Tests
verify the verdict logic, grouping, and report formatting — not the
specific dollar values from the production warehouse (those are
data-dependent).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def diag():
    # stat_tests is a dependency of the diagnostic
    _load("stat_tests_diag", "core/stat_tests.py")
    return _load("diagnostic_report_diag", "scripts/diagnostic_report.py")


def test_verdict_positive_lb_is_edge_present(diag):
    assert diag.verdict(lb=0.5, ub=1.5) == "EDGE_PRESENT"


def test_verdict_negative_ub_is_negative_edge(diag):
    assert diag.verdict(lb=-2.0, ub=-0.1) == "NEGATIVE_EDGE"


def test_verdict_straddle_is_ambiguous(diag):
    assert diag.verdict(lb=-0.5, ub=0.5) == "EDGE_AMBIGUOUS"


def test_verdict_zero_lb_is_ambiguous(diag):
    """LB exactly at 0 → not strictly positive → ambiguous."""
    assert diag.verdict(lb=0.0, ub=0.5) == "EDGE_AMBIGUOUS"


def _make_rows(n, alpha_mean, alpha_std, fees=0.04, group="strat_a"):
    rng = np.random.default_rng(42)
    out = []
    for i in range(n):
        a = float(rng.normal(alpha_mean, alpha_std))
        out.append({
            "alpha": a, "spread": 0.0, "slippage": 0.0,
            "funding": 0.0, "fees": fees,
            "realized_pnl": a - fees,
            "strategy_family": group, "symbol": f"SYM{i % 3}/USDT",
        })
    return out


def test_per_group_report_groups_by_field(diag):
    rows = _make_rows(20, alpha_mean=0.5, alpha_std=1.0, group="x")
    rows += _make_rows(20, alpha_mean=-0.5, alpha_std=1.0, group="y")
    out = diag.per_group_report(rows, "strategy_family")
    groups = {r["group"] for r in out}
    assert {"x", "y"} == groups


def test_per_group_report_skips_tiny_groups(diag):
    rows = _make_rows(3, alpha_mean=0.5, alpha_std=1.0, group="tiny")
    rows += _make_rows(15, alpha_mean=0.5, alpha_std=1.0, group="big")
    out = diag.per_group_report(rows, "strategy_family")
    groups = {r["group"] for r in out}
    assert "big" in groups
    assert "tiny" not in groups, "groups < 10 trades must be excluded"


def test_per_group_report_emits_verdict_field(diag):
    """Strong-positive alpha group → EDGE_PRESENT."""
    rows = _make_rows(100, alpha_mean=2.0, alpha_std=0.5)
    out = diag.per_group_report(rows, "strategy_family")
    assert out[0]["verdict"] == "EDGE_PRESENT"


def test_per_group_report_negative_edge_detected(diag):
    """Strong-negative alpha → NEGATIVE_EDGE."""
    rows = _make_rows(100, alpha_mean=-2.0, alpha_std=0.5)
    out = diag.per_group_report(rows, "strategy_family")
    assert out[0]["verdict"] == "NEGATIVE_EDGE"


def test_format_report_contains_all_sections(diag):
    rows = _make_rows(50, alpha_mean=0.1, alpha_std=1.0)
    overall = diag.per_group_report([{**r, "_all": "ALL"} for r in rows], "_all")[0]
    by_strat = diag.per_group_report(rows, "strategy_family")
    by_sym = diag.per_group_report(rows, "symbol")
    text = diag.format_report(overall, by_strat, by_sym)
    assert "# Attribution Diagnostic Report" in text
    assert "## Overall" in text
    assert "## Per Strategy" in text
    assert "## Per Symbol" in text
    assert "## Decision Gate" in text
    # Verdict appears
    assert "VERDICT:" in text or "verdict" in text


def test_decision_gate_recommends_proceed_when_edge_present(diag):
    rows = _make_rows(100, alpha_mean=2.0, alpha_std=0.5)
    overall = diag.per_group_report([{**r, "_all": "ALL"} for r in rows], "_all")[0]
    by_strat = diag.per_group_report(rows, "strategy_family")
    text = diag.format_report(overall, by_strat, by_strat)
    assert "Proceed" in text or "proceed" in text


def test_decision_gate_recommends_stop_when_negative(diag):
    rows = _make_rows(100, alpha_mean=-2.0, alpha_std=0.5)
    overall = diag.per_group_report([{**r, "_all": "ALL"} for r in rows], "_all")[0]
    by_strat = diag.per_group_report(rows, "strategy_family")
    text = diag.format_report(overall, by_strat, by_strat)
    assert "STOP" in text or "stop" in text.lower()


# ── Phase 2 / Task E: re-gate flags ───────────────────────────────────


def test_parse_since_iso_date(diag):
    ts = diag._parse_since("2026-04-27")
    from datetime import datetime, timezone
    expected = datetime(2026, 4, 27, tzinfo=timezone.utc).timestamp()
    assert ts == pytest.approx(expected)


def test_parse_since_relative_days(diag):
    import time
    before = time.time()
    ts = diag._parse_since("7d")
    delta = before - ts
    assert 7 * 86400 - 5 < delta < 7 * 86400 + 5


def test_parse_since_relative_hours(diag):
    import time
    before = time.time()
    ts = diag._parse_since("24h")
    delta = before - ts
    assert 24 * 3600 - 5 < delta < 24 * 3600 + 5


def test_parse_since_iso_datetime(diag):
    ts = diag._parse_since("2026-04-27T12:00:00")
    from datetime import datetime, timezone
    expected = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert ts == pytest.approx(expected)


def test_parse_since_rejects_garbage(diag):
    with pytest.raises(ValueError):
        diag._parse_since("not-a-date")
    with pytest.raises(ValueError):
        diag._parse_since("")


def test_zero_cost_predicate_matches_backfill_rows(diag):
    """All three cost fields == 0 → predicate fires (the pre-Task-A blind spot)."""
    assert diag._is_zero_cost_backfill(
        {"spread": 0.0, "slippage": 0.0, "funding": 0.0})
    # None values coerce to 0
    assert diag._is_zero_cost_backfill(
        {"spread": None, "slippage": None, "funding": None})


def test_zero_cost_predicate_misses_real_rows(diag):
    """Any non-zero cost field → row is real, predicate misses."""
    assert not diag._is_zero_cost_backfill(
        {"spread": 0.01, "slippage": 0.0, "funding": 0.0})
    assert not diag._is_zero_cost_backfill(
        {"spread": 0.0, "slippage": 0.001, "funding": 0.0})
    assert not diag._is_zero_cost_backfill(
        {"spread": 0.0, "slippage": 0.0, "funding": 0.05})


def test_format_report_surfaces_backfill_count(diag):
    rows = _make_rows(50, alpha_mean=0.1, alpha_std=1.0)
    overall = diag.per_group_report([{**r, "_all": "ALL"} for r in rows], "_all")[0]
    by_strat = diag.per_group_report(rows, "strategy_family")
    by_sym = diag.per_group_report(rows, "symbol")
    text = diag.format_report(
        overall, by_strat, by_sym,
        n_zero_cost=42, n_total=50, since="2026-04-27",
    )
    assert "Backfill blind spot" in text
    assert "42 of 50" in text
    assert "2026-04-27" in text


def test_format_report_omits_backfill_section_when_clean(diag):
    rows = _make_rows(50, alpha_mean=0.1, alpha_std=1.0)
    overall = diag.per_group_report([{**r, "_all": "ALL"} for r in rows], "_all")[0]
    by_strat = diag.per_group_report(rows, "strategy_family")
    text = diag.format_report(overall, by_strat, by_strat)
    # default n_total=0 → section absent
    assert "Backfill blind spot" not in text
