"""Transactional repair for the exact July pytest pollution signatures."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import repair_test_pollution as rtp


def _paths(tmp_path: Path) -> rtp.RepairPaths:
    return rtp.RepairPaths(
        close_fail=tmp_path / "close_fail.json",
        spot=tmp_path / "spot.json",
        s1=tmp_path / "s1.json",
        post_mortem=tmp_path / "post.json",
        prediction_log=tmp_path / "prediction.jsonl",
        mcp_decisions=tmp_path / "mcp.jsonl",
        journal=tmp_path / "journal.md",
        risk_state=tmp_path / "risk.json",
        positions=tmp_path / "positions.json",
        wallet=tmp_path / "wallet.json",
        warehouse=tmp_path / "warehouse.sqlite",
        backup_root=tmp_path / "backups",
    )


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _create_warehouse(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE predictions (
                ts INTEGER NOT NULL,
                model_version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                p_win REAL NOT NULL,
                raw_score REAL,
                feature_hash TEXT,
                candidate_id INTEGER,
                PRIMARY KEY (ts, model_version, symbol, side)
            );
            CREATE TABLE shadow_outcomes (
                proposal_id TEXT PRIMARY KEY,
                exit_px REAL,
                exit_reason TEXT,
                gross_pnl REAL,
                net_pnl REAL,
                fees REAL,
                slippage REAL,
                funding REAL,
                mfe REAL,
                mae REAL,
                bars_held INTEGER,
                r_multiple REAL,
                resolved_ts INTEGER,
                label_status TEXT,
                ltp_touched INTEGER,
                ltp_filled INTEGER,
                ltp_exit_reason TEXT
            );
            """
        )


def _insert_warehouse_rows(
    path: Path,
    *,
    predictions=(),
    outcomes=(),
) -> None:
    prediction_columns = ", ".join(rtp.WAREHOUSE_PREDICTION_COLUMNS)
    prediction_marks = ", ".join("?" for _ in rtp.WAREHOUSE_PREDICTION_COLUMNS)
    outcome_columns = ", ".join(rtp.WAREHOUSE_SHADOW_OUTCOME_COLUMNS)
    outcome_marks = ", ".join("?" for _ in rtp.WAREHOUSE_SHADOW_OUTCOME_COLUMNS)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            f"INSERT INTO predictions ({prediction_columns}) VALUES ({prediction_marks})",
            predictions,
        )
        conn.executemany(
            f"INSERT INTO shadow_outcomes ({outcome_columns}) VALUES ({outcome_marks})",
            outcomes,
        )


def _warehouse_counts(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as conn:
        predictions = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        outcomes = conn.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0]
    return predictions, outcomes


def _post_fixture(**updates):
    row = {
        "timestamp": 1_784_388_464.4,
        "symbol": "ETH/USDT",
        "exchange": "Binance",
        "side": "buy",
        "market_type": "futures",
        "strategy": "claude_portfolio",
        "entry_price": 100.0,
        "close_price": 100.0,
        "pnl": 0,
        "pnl_pct": None,
        "close_reason": "take_profit",
        "leverage": 3,
        "duration_min": 0,
        "verdict": "LOSS",
        "mistakes": [],
        "lessons": ["Good entry on claude_portfolio"],
    }
    row.update(updates)
    return row


def _prediction_fixture(**updates):
    row = {
        "ts": 1_784_388_469.5,
        "symbol": "ATOM/USDT",
        "side": "buy",
        "mcp_score": 72,
        "mode": "VETO",
        "prediction": "CONFIRM",
        "confidence": 0.8,
        "reasoning": "Looks good",
        "model_used": "haiku",
        "prompt_hash": rtp.PREDICTION_PROMPT_HASH,
    }
    row.update(updates)
    return row


def _empty(ts: str):
    return {"ts": ts, "type": "portfolio", "decisions": {"actions": []}}


def _fake_open(ts: str):
    return {
        "ts": ts,
        "type": "portfolio",
        "decisions": {
            "actions": [
                {
                    "type": "OPEN",
                    "symbol": "BTC/USDT:USDT",
                    "side": "buy",
                    "confidence": 0.7,
                    "exchange": "binance",
                    "decision_id": str(uuid.uuid4()),
                    "source": "algo_det",
                }
            ]
        },
    }


def test_dry_run_removes_only_exact_signatures(tmp_path):
    paths = _paths(tmp_path)
    _write_json(paths.close_fail, {"a1": 1, "real-position": 2})
    _write_json(paths.spot, rtp.SPOT_FIXTURE)
    _write_json(
        paths.s1,
        {
            "ts": 1_784_389_107.1,
            "regime": "NORMAL",
            "version": 2,
            "states": rtp.S1_STATES_FIXTURE,
        },
    )
    _write_json(
        paths.post_mortem,
        {"analyses": [_post_fixture(), _post_fixture(pnl=0.01)]},
    )
    paths.prediction_log.write_text(
        "\n".join(
            json.dumps(row)
            for row in [_prediction_fixture(), _prediction_fixture(mcp_score=73)]
        )
        + "\n",
        encoding="utf-8",
    )
    mcp_rows = [
        _empty("2026-07-18T15:27:32.650000+00:00"),
        _empty("2026-07-18T15:27:32.680000+00:00"),
        _fake_open("2026-07-18T15:27:32.713000+00:00"),
        {"ts": "2026-07-18T15:28:00+00:00", "type": "portfolio", "decisions": {"actions": [{"type": "HOLD"}]}},
    ]
    paths.mcp_decisions.write_text(
        "".join(json.dumps(row) + "\n" for row in mcp_rows), encoding="utf-8"
    )
    exact_journal = next(
        row for row in rtp.EXACT_JOURNAL_ROWS if "pnl=$-0.01" in row
    )
    near_journal = exact_journal.replace("pnl=$-0.01", "pnl=$-0.02")
    paths.journal.write_text(
        f"# Journal\n{exact_journal}\n{near_journal}\n", encoding="utf-8"
    )

    before = {path: path.read_bytes() for path in paths.__dict__.values() if path.is_file()}
    plan = rtp.repair(paths)

    assert plan.ambiguities == []
    assert plan.findings == {
        "close_fail_a1": 1,
        "journal_fixture_rows": 1,
        "mcp_fake_clusters": 1,
        "post_mortem_fixture_rows": 1,
        "prediction_mock_rows": 1,
        "s1_fixture_reset": 1,
        "spot_fixture_reset": 1,
        "warehouse_prop_c10_outcome": 0,
        "warehouse_test_drift_predictions": 0,
    }
    assert plan.applied is False
    assert all(path.read_bytes() == payload for path, payload in before.items())

    after_by_label = {
        change.label: change.after for change in plan.changes
    }
    assert b"real-position" in after_by_label["close_fail_a1"]
    assert b'"pnl": 0.01' in after_by_label["post_mortem_fixture_rows"]
    assert b'"mcp_score": 73' in after_by_label["prediction_mock_rows"]
    assert b'"type": "HOLD"' in after_by_label["mcp_fake_clusters"]
    assert near_journal.encode() in after_by_label["journal_fixture_rows"]


def test_apply_creates_backup_manifest_and_is_idempotent(tmp_path):
    paths = _paths(tmp_path)
    _write_json(paths.close_fail, {"a1": 1, "keep": 7})
    now = datetime(2026, 7, 18, 17, 0, tzinfo=timezone.utc)

    plan = rtp.repair(paths, apply=True, now=now)

    assert plan.applied is True
    assert json.loads(paths.close_fail.read_text(encoding="utf-8")) == {"keep": 7}
    assert plan.backup_dir is not None
    manifest = json.loads(
        (plan.backup_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "committed"
    assert manifest["files"][0]["label"] == "close_fail_a1"
    backup = plan.backup_dir / manifest["files"][0]["backup"]
    assert json.loads(backup.read_text(encoding="utf-8")) == {"a1": 1, "keep": 7}

    second = rtp.repair(paths)
    assert second.changes == []
    assert second.findings["close_fail_a1"] == 0


def test_ambiguous_mcp_signature_prevents_every_write(tmp_path):
    paths = _paths(tmp_path)
    _write_json(paths.close_fail, {"a1": 1})
    paths.mcp_decisions.write_text(
        json.dumps(_fake_open("2026-07-18T15:27:32.713000+00:00")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(rtp.RepairError, match="ambiguous"):
        rtp.repair(paths, apply=True)

    assert json.loads(paths.close_fail.read_text(encoding="utf-8")) == {"a1": 1}
    assert not paths.backup_root.exists()


def test_warehouse_dry_run_detects_exact_rows_without_writing(tmp_path):
    paths = _paths(tmp_path)
    _create_warehouse(paths.warehouse)
    predictions = rtp._expected_test_drift_predictions()
    _insert_warehouse_rows(
        paths.warehouse,
        predictions=predictions,
        outcomes=(rtp.EXACT_WAREHOUSE_PROP_C10,),
    )

    plan = rtp.repair(paths)

    assert plan.ambiguities == []
    assert plan.findings["warehouse_test_drift_predictions"] == 50
    assert plan.findings["warehouse_prop_c10_outcome"] == 1
    assert plan.warehouse_repair is not None
    assert _warehouse_counts(paths.warehouse) == (50, 1)
    assert not paths.backup_root.exists()


def test_warehouse_apply_deletes_exact_rows_and_preserves_near_matches(tmp_path):
    paths = _paths(tmp_path)
    _create_warehouse(paths.warehouse)
    near_prediction = (
        50,
        "test_drift",
        "BTC/USDT:USDT",
        "buy",
        0.85,
        70.0,
        "h",
        50,
    )
    near_outcome = ("prop-c10-near",) + rtp.EXACT_WAREHOUSE_PROP_C10[1:]
    _insert_warehouse_rows(
        paths.warehouse,
        predictions=rtp._expected_test_drift_predictions() + (near_prediction,),
        outcomes=(rtp.EXACT_WAREHOUSE_PROP_C10, near_outcome),
    )

    plan = rtp.repair(
        paths,
        apply=True,
        now=datetime(2026, 7, 18, 17, 1, tzinfo=timezone.utc),
    )

    assert plan.applied is True
    assert _warehouse_counts(paths.warehouse) == (1, 1)
    with sqlite3.connect(paths.warehouse) as conn:
        assert conn.execute("SELECT ts FROM predictions").fetchall() == [(50,)]
        assert conn.execute(
            "SELECT proposal_id FROM shadow_outcomes"
        ).fetchall() == [("prop-c10-near",)]
    assert plan.backup_dir is not None
    manifest = json.loads(
        (plan.backup_dir / "manifest.json").read_text(encoding="utf-8")
    )
    warehouse_entry = next(
        row for row in manifest["files"] if row["label"] == "warehouse_exact_pytest_fixtures"
    )
    assert warehouse_entry["removed"] == 51
    assert _warehouse_counts(plan.backup_dir / warehouse_entry["backup"]) == (51, 2)


def test_incomplete_warehouse_fixture_aborts_all_changes(tmp_path):
    paths = _paths(tmp_path)
    _write_json(paths.close_fail, {"a1": 1})
    _create_warehouse(paths.warehouse)
    _insert_warehouse_rows(
        paths.warehouse,
        predictions=rtp._expected_test_drift_predictions()[:-1],
    )

    with pytest.raises(rtp.RepairError, match="ambiguous"):
        rtp.repair(paths, apply=True)

    assert json.loads(paths.close_fail.read_text(encoding="utf-8")) == {"a1": 1}
    assert _warehouse_counts(paths.warehouse) == (49, 0)
    assert not paths.backup_root.exists()


def test_warehouse_rows_are_restored_if_file_commit_fails(tmp_path):
    paths = _paths(tmp_path)
    _write_json(paths.close_fail, {"a1": 1})
    _create_warehouse(paths.warehouse)
    _insert_warehouse_rows(
        paths.warehouse,
        predictions=rtp._expected_test_drift_predictions(),
        outcomes=(rtp.EXACT_WAREHOUSE_PROP_C10,),
    )
    plan = rtp.build_plan(paths)

    def fail_replace(_source, _target):
        raise OSError("injected replace failure")

    with pytest.raises(rtp.RepairError, match="rolled back"):
        rtp.apply_plan(
            plan,
            paths.backup_root,
            now=datetime(2026, 7, 18, 17, 2, tzinfo=timezone.utc),
            replace_func=fail_replace,
        )

    assert json.loads(paths.close_fail.read_text(encoding="utf-8")) == {"a1": 1}
    assert _warehouse_counts(paths.warehouse) == (50, 1)
    manifest = json.loads(
        next(paths.backup_root.glob("*/manifest.json")).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "rolled_back"


def test_warehouse_near_matches_are_not_repaired(tmp_path):
    paths = _paths(tmp_path)
    _create_warehouse(paths.warehouse)
    near_prediction = list(rtp._expected_test_drift_predictions()[0])
    near_prediction[4] = 0.84
    near_outcome = list(rtp.EXACT_WAREHOUSE_PROP_C10)
    near_outcome[2] = "time"
    _insert_warehouse_rows(
        paths.warehouse,
        predictions=(tuple(near_prediction),),
        outcomes=(tuple(near_outcome),),
    )

    plan = rtp.repair(paths)

    assert plan.ambiguities == []
    assert plan.warehouse_repair is None
    assert plan.findings["warehouse_test_drift_predictions"] == 0
    assert plan.findings["warehouse_prop_c10_outcome"] == 0
    assert _warehouse_counts(paths.warehouse) == (1, 1)


def test_risk_reconstruction_uses_partial_plus_runner_pnl(tmp_path):
    paths = _paths(tmp_path)
    _write_json(
        paths.risk_state,
        {
            "is_halted": False,
            "halt_reason": "",
            "daily_pnl": -999.0,
            "max_drawdown_pct": 0.5,
            "start_balance": 1.0,
            "peak_balance": 1.0,
            "trading_day": "2026-07-18",
            "trades_today": 99,
            "recent_results": [False],
            "trade_history": [[False, -1.0]],
            "symbol_pauses": {"BTC": 123.0},
            "family_pauses": {"sys": 456.0},
            "global_streak": [False],
            "recent_sl_by_pair_side": {},
            "timestamp": 0.0,
        },
    )
    _write_json(
        paths.positions,
        {
            "open": [],
            "closed": [
                {
                    "paper_trade": True,
                    "market_type": "futures",
                    "open_time": 1_784_332_800.0,
                    "close_time": 1_784_336_400.0,
                    "pnl": -1.0,
                    "realized_partial_pnl": 3.0,
                }
            ],
        },
    )
    _write_json(paths.wallet, {"balances": {"binance": 102.0}})

    plan = rtp.build_plan(
        paths,
        reconstruct_risk=True,
        operating_mode="PAPER",
        dry_run=True,
        now=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )

    assert plan.ambiguities == []
    change = next(c for c in plan.changes if c.label == "risk_state_reconstruction")
    repaired = json.loads(change.after)
    assert repaired["daily_pnl"] == pytest.approx(2.0)
    assert repaired["start_balance"] == pytest.approx(100.0)
    assert repaired["peak_balance"] == pytest.approx(102.0)
    assert repaired["trades_today"] == 1
    assert repaired["symbol_pauses"] == {"BTC": 123.0}
    assert repaired["family_pauses"] == {"sys": 456.0}
    assert repaired["recent_results"] == [False]


def test_risk_reconstruction_fails_closed_with_open_position(tmp_path):
    paths = _paths(tmp_path)
    _write_json(
        paths.risk_state,
        {
            "is_halted": False,
            "halt_reason": "",
            "symbol_pauses": {},
            "family_pauses": {},
        },
    )
    _write_json(paths.positions, {"open": [{"id": "p1"}], "closed": []})
    _write_json(paths.wallet, {"balances": {"binance": 100.0}})

    plan = rtp.build_plan(
        paths,
        reconstruct_risk=True,
        operating_mode="PAPER",
        dry_run=True,
        now=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )

    assert any("zero open positions" in item for item in plan.ambiguities)
    assert not any(c.label == "risk_state_reconstruction" for c in plan.changes)
