"""Phase 13.5b — wire-up correctness fixes.

Bugs found in the Phase 13.5 review:

  1. predictions.symbol = "ETH/USDT" but trades.symbol = "ETH/USDT:USDT".
     The predictions→trades JOIN never matched. Fix: log_entry takes
     `market_type` and appends ":USDT" for futures.

  2. No way to JOIN predictions to trades for re-fitting. Fix: schema
     adds `candidate_id` column; log_entry takes `candidate_id`; the
     mcp_brain wire passes the cand_id from `record_candidate`.

  3. ShadowPredictor.model_version was always "lr_v_latest" because the
     resolved Path on a Windows-copied symlink stem-names back to the
     copy. Fix: embed `model_version` inside the joblib payload at save
     time; predictor reads it back on load.

  4. Predictions logged for blacklisted symbols too (ETH had 75 entries
     even though ETH was BLACKLIST_HARD). NOT FIXED in this commit —
     mcp_brain runs scoring across the full universe; the blacklist
     filter is downstream in bot_engine. Predictions log what the
     scorer sees; that's by design (forward-attribution). Documented.
"""
from __future__ import annotations

import pytest

pytest.importorskip("joblib")
pytest.importorskip("sklearn")

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "models").mkdir(exist_ok=True)

    wh_mod = _load("wh_wire_fix", "core/warehouse.py")
    wh = wh_mod.Warehouse(path=tmp_path / "wh.sqlite")

    mod_mod = _load("models_wire_fix", "core/models.py")

    # Train a tiny model so the predictor can load
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, size=(80, 15))
    y = (X[:, 0] > 0).astype(int)
    m = mod_mod.LRModel().fit(X, y)
    art_path = tmp_path / "data" / "models" / "lr_v_phase13_test_20260428_TEST.pkl"
    latest_path = tmp_path / "data" / "models" / "lr_v_latest.pkl"
    m.save(art_path, model_version="lr_v_phase13_test_20260428_TEST")
    # Simulate the Windows fallback (copy-not-symlink).
    import shutil
    shutil.copy2(art_path, latest_path)

    sp_mod = _load("shadow_wire_fix", "core/shadow_predictor.py")
    # Force the LATEST_MODEL constant to point inside tmp_path. The module
    # uses Path() at import; we mutate the module-level binding.
    sp_mod.LATEST_MODEL = latest_path
    sp_mod.ShadowPredictor._instance = None  # reset singleton
    return wh, sp_mod


def _features_dict():
    """Sample candidate feature snapshot — the 15 keys the trainer expects."""
    return {
        "score": 80, "layers_ok": 7, "confidence": 0.78,
        "sl_pct": 1.5, "tp_pct": 3.0, "rsi_1h": 65,
        "adx_1h": 22, "adx_4h": 28, "atr_pct_1h": 0.8,
        "bb_width_4h": 8.0, "vol_ratio": 1.2,
        "ema20_above_50_4h": True, "ema20_above_50_1h": True,
        "funding_rate": 0.0001, "ob_imbalance": 0.0,
    }


# ── Bug 1: symbol format normalization ─────────────────────────────────

def test_log_entry_appends_usdt_suffix_for_futures(env):
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    p = sp.log_entry(
        ts=1000, symbol="ETH/USDT", side="buy",
        features=_features_dict(),
        warehouse=wh,
        market_type="futures",
    )
    assert p is not None
    rows = wh.query("SELECT symbol FROM predictions")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT:USDT", (
        f"futures market_type must normalise symbol to BASE/QUOTE:QUOTE, "
        f"got {rows[0]['symbol']!r}")


def test_log_entry_keeps_existing_suffix(env):
    """Don't double-append when caller already passed the suffix."""
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    sp.log_entry(
        ts=1001, symbol="BTC/USDT:USDT", side="buy",
        features=_features_dict(), warehouse=wh, market_type="futures",
    )
    rows = wh.query("SELECT symbol FROM predictions WHERE ts=1001")
    assert rows[0]["symbol"] == "BTC/USDT:USDT"


def test_log_entry_no_suffix_for_spot(env):
    """Spot symbols stay as-is (no settle currency)."""
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    sp.log_entry(
        ts=1002, symbol="ETH/USDT", side="buy",
        features=_features_dict(), warehouse=wh, market_type="spot",
    )
    rows = wh.query("SELECT symbol FROM predictions WHERE ts=1002")
    assert rows[0]["symbol"] == "ETH/USDT"


# ── Bug 2: candidate_id linkage ────────────────────────────────────────

def test_log_entry_persists_candidate_id(env):
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    sp.log_entry(
        ts=1003, symbol="ATOM/USDT", side="buy",
        features=_features_dict(), warehouse=wh,
        candidate_id=42, market_type="futures",
    )
    rows = wh.query("SELECT candidate_id FROM predictions WHERE ts=1003")
    assert rows[0]["candidate_id"] == 42


def test_predictions_join_to_trades_via_candidate_id(env):
    """End-to-end: log a prediction, record a trade with the same
    candidate_id, verify the JOIN finds the pair."""
    wh, sp_mod = env
    cand_id = wh.record_candidate(
        exchange="binance", symbol="ATOM/USDT:USDT",
        side="buy", strategy_family="systematic_v3_1",
        decision="ALLOW", features={"score": 80},
    )
    sp = sp_mod.ShadowPredictor.get()
    sp.log_entry(
        ts=1004, symbol="ATOM/USDT", side="buy",
        features=_features_dict(), warehouse=wh,
        candidate_id=cand_id, market_type="futures",
    )
    tid = wh.record_trade_open(
        exchange="binance", symbol="ATOM/USDT:USDT", side="buy",
        ts_entry=1010, entry_px=10.0, size=1.0,
        candidate_id=cand_id,
    )
    wh.record_trade_close(
        trade_id=tid, ts_exit=2000, exit_px=10.5,
        realized_pnl=0.5, exit_reason="TP",
    )
    rows = wh.query(
        "SELECT p.p_win, t.realized_pnl FROM predictions p "
        "JOIN trades t ON t.candidate_id = p.candidate_id "
        "WHERE t.status='CLOSED'"
    )
    assert len(rows) == 1, "predictions JOIN trades via candidate_id must work"
    assert rows[0]["realized_pnl"] == pytest.approx(0.5)


def test_log_entry_candidate_id_optional(env):
    """Backwards compat — log_entry without candidate_id still writes
    a row (with NULL candidate_id)."""
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    sp.log_entry(
        ts=1005, symbol="ARB/USDT", side="buy",
        features=_features_dict(), warehouse=wh,
        market_type="futures",
    )
    rows = wh.query("SELECT candidate_id FROM predictions WHERE ts=1005")
    assert rows[0]["candidate_id"] is None


# ── Bug 3: model_version recovery from artifact ────────────────────────

def test_model_version_recovered_from_artifact(env):
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    assert sp._ensure_model()
    # Should be the canonical version string we embedded at save time,
    # not the on-disk filename "lr_v_latest".
    assert sp._model_version == "lr_v_phase13_test_20260428_TEST", (
        f"expected embedded version, got {sp._model_version!r}")


def test_predictions_carry_real_model_version(env):
    wh, sp_mod = env
    sp = sp_mod.ShadowPredictor.get()
    sp.log_entry(
        ts=1006, symbol="ATOM/USDT", side="buy",
        features=_features_dict(), warehouse=wh,
        market_type="futures",
    )
    rows = wh.query("SELECT model_version FROM predictions WHERE ts=1006")
    assert rows[0]["model_version"] == "lr_v_phase13_test_20260428_TEST"


# ── Schema migration ───────────────────────────────────────────────────

def test_predictions_table_has_candidate_id_column(env):
    wh, _sp_mod = env
    cols = {r["name"] for r in wh.query("PRAGMA table_info(predictions)")}
    assert "candidate_id" in cols


def test_idx_predictions_candidate_exists(env):
    wh, _sp_mod = env
    rows = wh.query(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='predictions'"
    )
    names = {r["name"] for r in rows}
    assert "idx_predictions_candidate" in names
