"""Tests for research/screen_cost_aware_accband_kappa.py (prereg 52, frozen 2026-07-31).

Offline screen only — no live path. Frozen constants per
_workspace/strategy_pipeline/52_prereg_cost_aware_accband_kappa.md:
C_stress=0.00315, kappa in {1.5,2.0,2.5,3.0}, min n per cell 80,
tp_frac buy=0.45 / sell=0.35 / else 0.50.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.screen_cost_aware_accband_kappa import (  # noqa: E402
    C_STRESS,
    KAPPA_CELLS,
    MIN_N_PER_CELL,
    admit,
    cell_metrics,
    decide_verdict,
    planned_tp_pct,
    run_screen,
)


def _row(**kw):
    base = {
        "side": "buy",
        "strategy_family": "algo_det",
        "entry_px": 100.0,
        "entry_stop_px": 98.0,
        "realized_pnl": 1.0,
        "r_multiple": 0.5,
    }
    base.update(kw)
    return base


class TestPlannedTpPct:
    def test_buy_uses_045_frac(self):
        # sl_pct = 2/100 = 0.02 -> tp = 0.02 * 0.45 = 0.009
        assert planned_tp_pct(_row()) == pytest.approx(0.009)

    def test_sell_uses_035_frac(self):
        r = _row(side="sell", entry_stop_px=102.0)
        assert planned_tp_pct(r) == pytest.approx(0.007)

    def test_unknown_side_uses_050_frac(self):
        r = _row(side="")
        assert planned_tp_pct(r) == pytest.approx(0.010)

    def test_missing_stop_excluded(self):
        assert planned_tp_pct(_row(entry_stop_px=None)) is None
        assert planned_tp_pct(_row(entry_stop_px=0.0)) is None

    def test_missing_entry_excluded(self):
        assert planned_tp_pct(_row(entry_px=None)) is None


class TestAdmit:
    def test_admit_at_low_kappa(self):
        assert admit(0.009, 1.5, C_STRESS) is True  # 0.009 >= 0.004725

    def test_reject_at_high_kappa(self):
        assert admit(0.009, 3.0, C_STRESS) is False  # 0.009 < 0.00945

    def test_boundary_is_inclusive(self):
        assert admit(2.0 * C_STRESS, 2.0, C_STRESS) is True


class TestCellMetrics:
    def test_basic_metrics(self):
        rows = [
            _row(realized_pnl=2.0, r_multiple=1.0),
            _row(realized_pnl=2.0, r_multiple=1.0),
            _row(realized_pnl=-1.0, r_multiple=-0.5),
        ]
        m = cell_metrics(rows)
        assert m["n"] == 3
        assert m["win_rate"] == pytest.approx(2 / 3)
        assert m["mean_realized_pnl"] == pytest.approx(1.0)
        assert m["mean_r_multiple"] == pytest.approx(0.5)
        assert m["profit_factor"] == pytest.approx(4.0)

    def test_empty_rows(self):
        m = cell_metrics([])
        assert m["n"] == 0
        assert m["win_rate"] is None


class TestVerdict:
    def _cell(self, n=100, passed=True, delta_ev=0.1):
        return {"n": n, "all_gates_pass": passed, "delta_ev": delta_ev}

    def test_insufficient_when_all_below_min_n(self):
        cells = {k: self._cell(n=10, passed=False) for k in KAPPA_CELLS}
        assert decide_verdict(cells) == "INSUFFICIENT_DATA"

    def test_no_go_when_none_pass(self):
        cells = {k: self._cell(passed=False) for k in KAPPA_CELLS}
        assert decide_verdict(cells) == "NO_GO"

    def test_go_needs_adjacent_same_sign_delta_ev(self):
        cells = {k: self._cell(passed=False, delta_ev=-0.1) for k in KAPPA_CELLS}
        cells[2.0] = self._cell(passed=True, delta_ev=0.1)
        # both neighbors (1.5, 2.5) have negative delta_ev -> no adjacency support
        assert decide_verdict(cells) == "NO_GO"
        cells[2.5]["delta_ev"] = 0.05
        assert decide_verdict(cells) == "GO"


class TestRunScreen:
    @pytest.fixture
    def db(self, tmp_path):
        path = tmp_path / "wh.sqlite"
        con = sqlite3.connect(path)
        con.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY, side TEXT, strategy_family TEXT,
                entry_px REAL, entry_stop_px REAL, realized_pnl REAL,
                r_multiple REAL, status TEXT, mode TEXT, exit_reason TEXT,
                ts_exit TEXT)"""
        )
        rows = []
        # 100 eligible cohort rows: buy, sl 2% -> tp 0.9% (admits kappa<=2.5, rejects 3.0)
        for i in range(100):
            rows.append(("buy", "algo_det", 100.0, 98.0, 1.0 if i < 60 else -1.0,
                         0.5 if i < 60 else -0.5, "CLOSED", "PAPER", "tp", "2026-08-01"))
        # non-cohort family — must be excluded
        rows.append(("buy", "claude_portfolio", 100.0, 98.0, 5.0, 2.0, "CLOSED", "PAPER", "tp", "2026-08-01"))
        # LIVE mode — excluded
        rows.append(("buy", "algo_det", 100.0, 98.0, 5.0, 2.0, "CLOSED", "LIVE", "tp", "2026-08-01"))
        # OPEN — excluded
        rows.append(("buy", "algo_det", 100.0, 98.0, 5.0, 2.0, "OPEN", "PAPER", None, None))
        # no stop geometry — dropped from n
        rows.append(("buy", "algo", 100.0, None, 5.0, 2.0, "CLOSED", "PAPER", "tp", "2026-08-01"))
        con.executemany(
            "INSERT INTO trades (side, strategy_family, entry_px, entry_stop_px, realized_pnl,"
            " r_multiple, status, mode, exit_reason, ts_exit) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        con.commit()
        con.close()
        return path

    def test_end_to_end(self, db):
        res = run_screen(str(db))
        assert res["baseline"]["n"] == 100  # only eligible cohort rows
        assert res["excluded_no_geometry"] == 1
        # tp 0.009: kappa 1.5 (0.004725) and 2.0 (0.0063) and 2.5 (0.007875) admit all
        assert res["cells"][1.5]["n"] == 100
        assert res["cells"][2.5]["n"] == 100
        # kappa 3.0 threshold 0.00945 > 0.009 -> nothing admitted
        assert res["cells"][3.0]["n"] == 0
        assert res["cells"][3.0]["insufficient"] is True
        # WR 0.60 in band, but delta_ev == 0 vs baseline -> gate 5 fails -> NO_GO
        assert res["verdict"] == "NO_GO"
        assert res["prereg_sha256"] == res["expected_sha256"]

    def test_min_n_marks_insufficient(self):
        assert MIN_N_PER_CELL == 80
