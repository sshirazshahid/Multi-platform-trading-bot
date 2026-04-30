"""End-to-end tests for the calibrated LR+GBM model gate.

Covers:
  * GBMModel fit / predict_proba / save / load round-trip.
  * Promotion gate: refuses bad models, accepts good ones, atomically updates
    the latest pointer, audit log appended on every attempt.
  * MCPBrain.score_via_model: rule-only fallback when no bundle, calibrated
    blend when bundle is loaded.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# GBMModel
# ─────────────────────────────────────────────────────────────────────────────

def test_gbm_fit_predict_roundtrip(tmp_path: Path):
    from core.models import GBMModel

    rng = np.random.default_rng(0)
    X = rng.standard_normal((400, 6))
    # Non-linear signal: y = 1 iff x0*x1 - x2 > 0
    y = (X[:, 0] * X[:, 1] - X[:, 2] > 0).astype(int)

    m = GBMModel(max_iter=80, learning_rate=0.05).fit(X, y)
    p = m.p_win(X)
    assert p.shape == (X.shape[0],)
    assert (p >= 0).all() and (p <= 1).all()
    # Trees can fit non-linear separability — expect train acc clearly >chance.
    train_acc = ((p >= 0.5) == (y == 1)).mean()
    assert train_acc > 0.65, f"GBM train acc {train_acc:.2f} too low"

    # Round-trip via save/load.
    out = tmp_path / "gbm.pkl"
    m.save(out, model_version="test_v1")
    m2 = GBMModel.load(out)
    p2 = m2.p_win(X)
    assert np.allclose(p, p2)


# ─────────────────────────────────────────────────────────────────────────────
# Promotion gate
# ─────────────────────────────────────────────────────────────────────────────

def _make_ensemble_json(path: Path, *, n_oos: int, auc: float) -> None:
    path.write_text(json.dumps({
        "model_version": path.stem,
        "metrics": {"n_oos_ensemble": n_oos, "auc_ensemble": auc},
    }))


def test_promotion_gate_refuses_low_dsr(tmp_path: Path, monkeypatch):
    from core import promotion_gate as pg

    # Redirect audit + models dir so the test doesn't pollute the project.
    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_x.json"
    _make_ensemble_json(art, n_oos=300, auc=0.55)

    row = {
        "model_version": "low_dsr",
        "oos_wr": 0.56,
        "deflated_sharpe": 0.10,   # below MIN_DSR=0.5
        "pbo": 0.30,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False
    assert not (tmp_path / "ensemble_futures_latest.json").exists()
    audit = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(audit) == 1
    assert "DSR" in json.loads(audit[0])["reason"]


def test_promotion_gate_promotes_good_model(tmp_path: Path, monkeypatch):
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_good.json"
    _make_ensemble_json(art, n_oos=300, auc=0.62)

    row = {
        "model_version": "good_v1",
        "oos_wr": 0.59,
        "deflated_sharpe": 0.80,
        "pbo": 0.30,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is True

    latest = tmp_path / "ensemble_futures_latest.json"
    assert latest.exists()
    payload = json.loads(latest.read_text())
    assert payload["model_version"] == "good_v1"
    assert payload["artifact_path"].endswith("ensemble_good.json")


def test_promotion_gate_keeps_prior_on_reject(tmp_path: Path, monkeypatch):
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    # Pre-existing latest pointer.
    latest = tmp_path / "ensemble_futures_latest.json"
    latest.write_text(json.dumps({"model_version": "prior", "artifact_path": "/tmp/x"}))

    art = tmp_path / "ensemble_bad.json"
    _make_ensemble_json(art, n_oos=20, auc=0.51)
    row = {
        "model_version": "bad",
        "oos_wr": 0.50,
        "deflated_sharpe": 0.20,
        "pbo": 0.80,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False
    # Latest pointer untouched.
    assert json.loads(latest.read_text())["model_version"] == "prior"


# ─────────────────────────────────────────────────────────────────────────────
# MCPBrain.score_via_model
# ─────────────────────────────────────────────────────────────────────────────

def test_score_via_model_rule_fallback(tmp_path: Path, monkeypatch):
    """No latest pointer => rule-only sigmoid fallback."""
    from core.mcp_brain import MCPBrain

    # Force the loader to look at an empty tmp models dir.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "models").mkdir(parents=True)
    b = MCPBrain()
    out = b.score_via_model(market_type="futures", feats={"score": 65}, rule_score=65.0)
    assert out["model_version"] is None
    assert np.isnan(out["p_win_lr"])
    assert np.isnan(out["p_win_gbm"])
    # sigmoid((65-65)/8) == 0.5
    assert out["p_win_ensemble"] == pytest.approx(0.5, abs=1e-6)
    out2 = b.score_via_model(market_type="futures", feats={"score": 80}, rule_score=80.0)
    assert out2["p_win_ensemble"] > 0.85


def test_score_via_model_with_bundle(tmp_path: Path, monkeypatch):
    """Trained bundle on disk => calibrated blend returns finite p_win_lr/gbm."""
    from core.calibration import IsotonicCalibrator
    from core.mcp_brain import MCPBrain
    from core.models import GBMModel, LRModel

    monkeypatch.chdir(tmp_path)
    models_dir = tmp_path / "data" / "models"
    models_dir.mkdir(parents=True)

    # Train tiny LR + GBM so the bundle is real.
    rng = np.random.default_rng(42)
    feature_keys = ["score", "rsi_1h", "adx_1h"]
    X = rng.standard_normal((200, 3))
    y = (X[:, 0] + 0.3 * X[:, 1] > 0).astype(int)

    lr = LRModel().fit(X, y)
    gbm = GBMModel(max_iter=80).fit(X, y)
    iso_lr = IsotonicCalibrator(min_samples=10).fit(lr.p_win(X), y)
    iso_gbm = IsotonicCalibrator(min_samples=10).fit(gbm.p_win(X), y)

    art = lambda name: models_dir / name  # noqa: E731
    lr.save(art("lr.pkl"))
    gbm.save(art("gbm.pkl"))
    iso_lr.save(art("iso_lr.pkl"))
    iso_gbm.save(art("iso_gbm.pkl"))

    ens_path = art("ensemble_futures_v_x.json")
    ens_path.write_text(json.dumps({
        "model_version": "test_bundle",
        "feature_keys": feature_keys,
        "weights": {"lr": 0.4, "gbm": 0.6, "mcp_rule": 0.25},
        "artifacts": {
            "lr": "lr.pkl", "gbm": "gbm.pkl",
            "iso_lr": "iso_lr.pkl", "iso_gbm": "iso_gbm.pkl",
        },
        "metrics": {"n_oos_ensemble": 200, "auc_ensemble": 0.62},
    }))
    art("ensemble_futures_latest.json").write_text(json.dumps({
        "model_version": "test_bundle",
        "artifact_path": str(ens_path),
    }))

    b = MCPBrain()
    out = b.score_via_model(
        market_type="futures",
        feats={"score": 70, "rsi_1h": 1.0, "adx_1h": 0.5},
        rule_score=70.0,
    )
    assert out["model_version"] == "test_bundle"
    assert 0 <= out["p_win_lr"] <= 1
    assert 0 <= out["p_win_gbm"] <= 1
    assert 0 <= out["p_win_ensemble"] <= 1


# ─────────────────────────────────────────────────────────────────────────────
# ShadowPredictor drift watcher
# ─────────────────────────────────────────────────────────────────────────────

def test_shadow_drift_watcher(tmp_path: Path, monkeypatch):
    from core.shadow_predictor import ShadowPredictor
    from core.warehouse import Warehouse

    wh_path = tmp_path / "warehouse.sqlite"
    wh = Warehouse(wh_path)
    # Inject 50 (prediction, trade) pairs where p_win=0.85 but realized WR=0.40.
    for i in range(50):
        wh._conn().execute(
            "INSERT INTO trades(candidate_id, ts_entry, exchange, symbol, side, "
            "  market_type, status, realized_pnl, ts_exit) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (i, float(i), "binance", "BTC/USDT:USDT", "buy", "futures",
             "CLOSED", 1.0 if i < 20 else -1.0, float(i + 100)),
        )
        wh._conn().execute(
            "INSERT INTO predictions(ts, model_version, symbol, side, p_win, "
            "  raw_score, feature_hash, candidate_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (i, "test_drift", "BTC/USDT:USDT", "buy", 0.85, 70.0, "h", i),
        )
    sp = ShadowPredictor()
    sp._model_version = "test_drift"
    alert_path = tmp_path / "drift.json"
    diag = sp.check_drift(
        warehouse=wh, window=50, threshold=0.10, alert_path=str(alert_path),
    )
    assert diag is not None
    assert diag["alert"] is True
    assert alert_path.exists()
    payload = json.loads(alert_path.read_text())
    assert payload["model_version"] == "test_drift"
    assert payload["gap"] > 0.10
