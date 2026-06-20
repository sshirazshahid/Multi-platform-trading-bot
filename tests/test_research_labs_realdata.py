"""Real-data regression + CLI smoke tests for the research labs.

Runs against the committed multi-year BTC fixture
(research/sample_data/btc_daily_fmp_2023_2026.csv) so the documented regime
behaviour is locked in, and subprocess-runs each lab's CLI demo so the __main__
blocks can't bit-rot. Stdlib-only deps (the labs need nothing beyond Python).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from research import data_io
from research import dca_rebalance_lab as lab

REPO = Path(__file__).resolve().parents[1]
BTC_CSV = REPO / "research" / "sample_data" / "btc_daily_fmp_2023_2026.csv"


@pytest.fixture(scope="module")
def btc_closes():
    if not BTC_CSV.exists():
        pytest.skip(f"fixture missing: {BTC_CSV}")
    closes = data_io.load_closes_csv(BTC_CSV)
    assert len(closes) > 1000  # ~3y of daily data
    return closes


def test_realdata_loads_and_simulates(btc_closes):
    dca = lab.simulate_dca(btc_closes, capital=1000.0, interval_bars=7)
    lump = lab.simulate_lump_sum(btc_closes, capital=1000.0)
    assert dca.extra["coins"] > 0
    assert dca.invested == pytest.approx(1000.0)
    assert len(lump.equity_curve) == len(btc_closes)
    assert lump.final_value > 0 and dca.final_value > 0


def test_realdata_lump_beats_dca_in_bull(btc_closes):
    # Committed window is a net +138% bull -> lump-sum must beat averaging-in.
    dca = lab.simulate_dca(btc_closes, capital=1000.0, interval_bars=7)
    lump = lab.simulate_lump_sum(btc_closes, capital=1000.0)
    assert lump.total_return_pct > dca.total_return_pct


def test_realdata_dca_lowers_drawdown_in_recent_selloff(btc_closes):
    # Last 200 bars are a drawdown -> DCA's max drawdown must be smaller.
    recent = btc_closes[-200:]
    dca = lab.simulate_dca(recent, capital=1000.0, interval_bars=1)
    lump = lab.simulate_lump_sum(recent, capital=1000.0)
    assert dca.max_drawdown_pct < lump.max_drawdown_pct


def _run_demo(rel_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / rel_path)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )


def test_dca_lab_cli_demo_runs():
    r = _run_demo("research/dca_rebalance_lab.py")
    assert r.returncode == 0, r.stderr
    assert "lump_sum_hold" in r.stdout and "dca" in r.stdout


def test_funding_carry_lab_cli_demo_runs():
    r = _run_demo("research/funding_carry_lab.py")
    assert r.returncode == 0, r.stderr
    assert "cash-and-carry" in r.stdout.lower() or "carry" in r.stdout.lower()
