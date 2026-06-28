from __future__ import annotations

import sqlite3
import time

import pytest

from core.strategy_readiness import evaluate_records


def _records(n: int, win: float, loss: float):
    out = []
    for i in range(n):
        pnl = win if i % 2 == 0 else loss
        out.append(
            {
                "ts": 1_700_000_000 + i,
                "pnl": pnl,
                "strategy": "machine",
                "detectors": {"valuation": 1, "stochastic": -1 if pnl < 0 else 0},
            }
        )
    return out


def test_readiness_passes_stable_positive_series():
    report = evaluate_records(_records(120, 1.0, -0.35), name="unit")

    assert report["ready"] is True
    assert report["verdict"] == "PROMOTE_ELIGIBLE"
    assert report["summary"]["profit_factor"] > 1.2
    assert report["positive_folds"] == 3


def test_readiness_fails_negative_or_small_series():
    report = evaluate_records(_records(20, 1.0, -2.0), name="unit")

    assert report["ready"] is False
    assert report["verdict"] == "PAPER_ONLY"
    assert any("n " in reason for reason in report["reasons"])


def test_detector_attribution_is_reported():
    report = evaluate_records(_records(120, 1.0, -0.35), name="unit")

    assert "valuation:bull" in report["detector_attribution"]
    assert "stochastic:bear" in report["detector_attribution"]


def _make_gate_db(path, pnls):
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE trades(
            status TEXT,
            ts_exit REAL,
            ts_entry REAL,
            symbol TEXT,
            side TEXT,
            strategy_family TEXT,
            mode TEXT,
            realized_pnl REAL,
            partial_realized_pnl REAL,
            mcp_score REAL,
            decision_id TEXT
        )
        """
    )
    now = time.time()
    for i, pnl in enumerate(pnls):
        con.execute(
            """
            INSERT INTO trades VALUES(
                'CLOSED', ?, ?, 'BTC/USDT:USDT', 'buy', 'machine', 'PAPER',
                ?, 0.0, 50.0, ?
            )
            """,
            (now - (len(pnls) - i) * 60, now - (len(pnls) - i + 1) * 60, pnl, f"d{i}"),
        )
    con.commit()
    con.close()


def test_live_strategy_readiness_gate_blocks_bad_evidence(tmp_path):
    from core.live_gate import enforce_strategy_readiness_gate

    db = tmp_path / "warehouse.sqlite"
    _make_gate_db(db, [-1.0] * 120)

    with pytest.raises(SystemExit) as exc:
        enforce_strategy_readiness_gate(
            "CONTROLLED_LIVE",
            db_path=db,
            strategy_family="machine",
        )
    assert "evidence gate failed" in str(exc.value)


def test_live_strategy_readiness_gate_allows_good_evidence(tmp_path):
    from core.live_gate import enforce_strategy_readiness_gate

    db = tmp_path / "warehouse.sqlite"
    _make_gate_db(db, [1.0 if i % 2 == 0 else -0.35 for i in range(120)])

    enforce_strategy_readiness_gate(
        "CONTROLLED_LIVE",
        db_path=db,
        strategy_family="machine",
    )
