"""Regression: the learning engine must analyze the REAL active paper account,
not the stale profile-sim accounts.

2026-06-01 bug: `_load_all_trades` merged data/profiles/{conservative,moderate,
aggressive}/positions.json (frozen 4/26, dead multi-profile sim) into the active
account → it reported a FAKE +$110 over 93 trades and a "READY FOR LIVE" nudge,
while the true active paper account was ~ -$28 over 29 trades (17% WR). The
owner nearly acted on the phantom profit. Default scoping is now the active
account only; profiles merge only when LEARNING_MERGE_PROFILES is explicitly on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.learning_engine as le


def _write(rel: str, closed: list):
    p = Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"open": [], "closed": closed}), encoding="utf-8")


def _t(tid: str, pnl: float):
    return {
        "id": tid, "symbol": "BTC/USDT:USDT", "strategy": "claude_portfolio",
        "pnl": pnl, "exit_price": 100.0, "paper_trade": True,
        "close_reason": "tp", "open_time": 0, "total_fees": 0.01,
    }


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    yield


def test_excludes_profile_sims_by_default(monkeypatch):
    """Default: only the active account (data/positions.json) is analyzed."""
    monkeypatch.setattr(le, "MERGE_PROFILE_TRADES", False)
    _write("data/positions.json", [_t("R1", -1.0), _t("R2", -0.5)])  # real: -1.5
    for prof in ("conservative", "moderate", "aggressive"):
        _write(f"data/profiles/{prof}/positions.json", [_t(f"{prof[:3]}W", 5.0)])  # fake wins
    eng = object.__new__(le.LearningEngine)
    trades = eng._load_all_trades()
    assert {t["id"] for t in trades} == {"R1", "R2"}, "profile-sim trades leaked into the active account"
    assert sum(t["pnl"] for t in trades) == pytest.approx(-1.5)


def test_learn_does_not_rerecord_into_calibrator(monkeypatch):
    """learn() must NOT re-record the whole trade history every cycle (the
    2026-06-01 runaway-calibrator bug that grew data/calibration.json to 24k+).
    order_manager._finalize_close is the sole per-close calibration recorder."""
    from unittest.mock import MagicMock

    closed = [
        {"id": f"T{i}", "symbol": "BTC/USDT:USDT", "strategy": "claude_portfolio",
         "pnl": (2.0 if i % 2 else -1.0), "exit_price": 100.0, "paper_trade": True,
         "close_reason": "tp", "open_time": 0, "total_fees": 0.01, "confidence": 0.6}
        for i in range(6)
    ]
    _write("data/positions.json", closed)
    eng = le.LearningEngine()
    eng.calibrator = MagicMock()
    eng.calibrator.get_summary.return_value = {"is_calibrated": False}
    eng.kelly = None
    eng.learn(force=True)
    eng.calibrator.record.assert_not_called()


def test_includes_profiles_only_when_flag_set(monkeypatch):
    """Opt-in: LEARNING_MERGE_PROFILES on => profiles merge (for a live multi-profile run)."""
    monkeypatch.setattr(le, "MERGE_PROFILE_TRADES", True)
    _write("data/positions.json", [_t("R1", -1.0)])
    _write("data/profiles/conservative/positions.json", [_t("CW", 5.0)])
    _write("data/profiles/moderate/positions.json", [])
    _write("data/profiles/aggressive/positions.json", [])
    eng = object.__new__(le.LearningEngine)
    trades = eng._load_all_trades()
    assert {t["id"] for t in trades} == {"R1", "CW"}
