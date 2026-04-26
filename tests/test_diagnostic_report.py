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
