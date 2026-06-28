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


def _valid_latest_payload(version: str, art: Path) -> dict:
    return {
        "model_version": version,
        "artifact_path": str(art),
        "promoted_at": int(__import__("time").time()),
        "diag": {
            "model_version": version,
            "oos_wr": 0.59,
            "deflated_sharpe": 0.80,
            "pbo": 0.30,
        },
    }


def test_promotion_gate_refuses_low_oos_wr(tmp_path: Path, monkeypatch):
    """Gate must refuse a model that fails the WR floor regardless of how
    healthy the other metrics look."""
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_x.json"
    _make_ensemble_json(art, n_oos=300, auc=0.55)

    row = {
        "model_version": "low_wr",
        "oos_wr": 0.40,                # below MIN_OOS_WR=0.55
        "deflated_sharpe": 0.80,       # high enough on its own
        "pbo": 0.30,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False
    assert not (tmp_path / "ensemble_futures_latest.json").exists()
    audit = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(audit) == 1
    assert "oos_wr" in json.loads(audit[0])["reason"]


def test_promotion_gate_refuses_low_n_oos(tmp_path: Path, monkeypatch):
    """Below the n_oos floor (200 futures / 100 spot), gate must refuse."""
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_x.json"
    _make_ensemble_json(art, n_oos=50, auc=0.65)

    row = {
        "model_version": "low_n",
        "oos_wr": 0.62,
        "deflated_sharpe": 0.40,
        "pbo": 0.30,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False
    assert "n_oos" in json.loads(
        (tmp_path / "audit.jsonl").read_text().strip().splitlines()[0]
    )["reason"]


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
# 2026-04-30 (gate-restore): the original "permissive DSR/PBO" change cited
# AUC + WR-uplift as the load-bearing guards but never wired them. These
# tests pin the new floors so a future relaxation can't silently re-break it.
# ─────────────────────────────────────────────────────────────────────────────


def _make_ensemble_json_v2(
    path: Path, *, n_oos: int, auc: float, wr_train: float | None = None,
) -> None:
    """Like _make_ensemble_json but also writes wr_train for WR-uplift checks."""
    metrics = {"n_oos_ensemble": n_oos, "auc_ensemble": auc}
    if wr_train is not None:
        metrics["wr_train"] = wr_train
    path.write_text(json.dumps({
        "model_version": path.stem,
        "metrics": metrics,
    }))


def test_promotion_gate_refuses_low_auc(tmp_path: Path, monkeypatch):
    """A model with AUC barely above 0.5 must NOT promote, even if every
    other guard is permissive. AUC is the load-bearing discrimination guard.
    """
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_low_auc.json"
    _make_ensemble_json_v2(art, n_oos=300, auc=0.55, wr_train=0.40)

    row = {
        "model_version": "low_auc",
        "oos_wr": 0.62,                # WR floor passes
        "deflated_sharpe": 0.80,       # DSR permissive (always passes at 0.0)
        "pbo": 0.30,                   # PBO permissive
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False
    audit_line = json.loads(
        (tmp_path / "audit.jsonl").read_text().strip().splitlines()[0]
    )
    assert "AUC" in audit_line["reason"]


def test_promotion_gate_refuses_low_wr_uplift(tmp_path: Path, monkeypatch):
    """A model whose oos_wr barely beats the training base rate must NOT
    promote, even with high AUC. Catches degenerate fits that look good
    in absolute WR but have no real edge over the base rate."""
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_low_uplift.json"
    # AUC=0.7 looks good, but base_rate=0.55 means uplift=0.60/0.55 ≈ 1.09
    _make_ensemble_json_v2(art, n_oos=300, auc=0.70, wr_train=0.55)

    row = {
        "model_version": "low_uplift",
        "oos_wr": 0.60,
        "deflated_sharpe": 0.80,
        "pbo": 0.30,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False
    audit_line = json.loads(
        (tmp_path / "audit.jsonl").read_text().strip().splitlines()[0]
    )
    assert "wr_uplift" in audit_line["reason"]


def test_promotion_gate_rejects_overfit_despite_strong_uplift(tmp_path: Path, monkeypatch):
    """2026-05-25 (no-edge-forensics): this is the deployed ensemble's exact
    profile — oos_wr 0.71, uplift ~5x — BUT deflated_sharpe=0.0 and pbo=1.0.
    The old permissive gate (MIN_DSR=0.0, MAX_PBO=1.0) PROMOTED it; that was
    the bug that put an overfit mirage live. The honest gate must REJECT it:
    a high WR-uplift coexisting with PBO=1.0 / DSR=0 is the textbook signature
    of selection overfit, not edge."""
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_real.json"
    _make_ensemble_json_v2(art, n_oos=4670, auc=0.76, wr_train=0.14)

    row = {
        "model_version": "real_like_deployed",
        "oos_wr": 0.71,
        "deflated_sharpe": 0.0,        # zero risk-adjusted edge
        "pbo": 1.0,                    # maximal overfit probability
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is False, "overfit profile (PBO=1.0/DSR=0) must be rejected"
    audit_line = json.loads(
        (tmp_path / "audit.jsonl").read_text().strip().splitlines()[0]
    )
    diag = audit_line
    assert diag["promote"] is False
    # uplift is still computed and large — proving the gate rejected on
    # PBO/DSR, not on a weak uplift.
    assert diag["wr_uplift"] >= 5.0, (
        f"profile still shows ≥5x uplift, got {diag['wr_uplift']}")


def test_promotion_gate_legacy_artifact_without_wr_train_passes(
    tmp_path: Path, monkeypatch,
):
    """Legacy artifacts (pre wr_train) must still be able to promote on
    AUC + n_oos + oos_wr alone. WR-uplift check is fail-open when
    base_rate is unknown — we don't punish callers for missing optional
    metadata."""
    from core import promotion_gate as pg

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    art = tmp_path / "ensemble_legacy.json"
    # No wr_train field — older trainer.
    _make_ensemble_json_v2(art, n_oos=300, auc=0.65, wr_train=None)

    row = {
        "model_version": "legacy",
        "oos_wr": 0.59,
        "deflated_sharpe": 0.80,
        "pbo": 0.30,
        "artifact_path": str(art),
    }
    promote = pg.promote_if_eligible(row, market_type="futures",
                                     models_dir=tmp_path)
    assert promote is True


def test_module_constants_match_documented_intent():
    """The module-level constants are consumed by `evaluate_model_version`'s
    default kwargs. Pin them so a future "tune the floors" commit has to
    explicitly update this test."""
    from core import promotion_gate as pg
    assert pg.MIN_OOS == {"futures": 200, "spot": 100}
    assert pg.MIN_OOS_WR == 0.55
    assert pg.MIN_AUC == 0.60
    assert pg.MIN_WR_UPLIFT == 1.5
    # 2026-05-25 — DSR/PBO tightened to honest floors (was 0.0 / 1.0, which
    # rubber-stamped the overfit live ensemble). See no-edge-forensics.
    assert pg.MIN_DSR == 0.10
    assert pg.MAX_PBO == 0.5


def test_load_model_bundle_recovers_after_retry_window(tmp_path: Path, monkeypatch):
    """Regression: previously `_load_model_bundle` cached load failures
    permanently in a `_model_load_attempted: set`. If the bot started
    BEFORE the weekly retrain wrote `ensemble_*_latest.json`, the gate
    stayed dormant for the rest of the process lifetime — a fresh model
    would never be picked up without a restart.

    Fix: timestamped negative cache that expires after
    `_MODEL_LOAD_RETRY_SEC`. This test simulates the failure → wait →
    fresh-pointer-appears → next call succeeds path.
    """
    import time as _t

    from core.mcp_brain import MCPBrain

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "models").mkdir(parents=True)

    b = MCPBrain()
    # 1st call: no latest pointer → fails, stamps the failure.
    out1 = b._load_model_bundle("futures")
    assert out1 == {}
    assert "futures" in b._model_load_failed_at

    # 2nd call within the retry window: short-circuits to {} without
    # re-attempting (previous behavior was permanent stamp; new behavior
    # is time-bounded).
    out2 = b._load_model_bundle("futures")
    assert out2 == {}

    # Now: weekly retrain "appears" — write a minimal valid bundle on disk.
    from core.calibration import IsotonicCalibrator
    from core.models import GBMModel, LRModel

    rng = np.random.default_rng(1)
    X = rng.standard_normal((100, 3))
    y = (X[:, 0] > 0).astype(int)
    lr = LRModel().fit(X, y)
    gbm = GBMModel(max_iter=50).fit(X, y)
    iso_l = IsotonicCalibrator(min_samples=10).fit(lr.p_win(X), y)
    iso_g = IsotonicCalibrator(min_samples=10).fit(gbm.p_win(X), y)
    md = tmp_path / "data" / "models"
    lr.save(md / "lr.pkl")
    gbm.save(md / "gbm.pkl")
    iso_l.save(md / "iso_lr.pkl")
    iso_g.save(md / "iso_gbm.pkl")
    art = md / "ensemble_futures_v_x.json"
    art.write_text(json.dumps({
        "model_version": "fresh",
        "feature_keys": ["a", "b", "c"],
        "weights": {"lr": 0.4, "gbm": 0.6, "mcp_rule": 0.25},
        "artifacts": {
            "lr": "lr.pkl", "gbm": "gbm.pkl",
            "iso_lr": "iso_lr.pkl", "iso_gbm": "iso_gbm.pkl",
        },
        "metrics": {"n_oos_ensemble": 300, "auc_ensemble": 0.62},
    }))
    (md / "ensemble_futures_latest.json").write_text(
        json.dumps(_valid_latest_payload("fresh", art)))

    # 3rd call: still inside retry window, NEW pointer ignored.
    out3 = b._load_model_bundle("futures")
    assert out3 == {}, "still inside retry window — should not have loaded yet"

    # Force the stamp into the past so the retry window is elapsed.
    b._model_load_failed_at["futures"] = _t.time() - (b._MODEL_LOAD_RETRY_SEC + 1)
    out4 = b._load_model_bundle("futures")
    assert out4, "retry window elapsed + valid pointer present → must load"
    assert out4["version"] == "fresh"
    # Successful load clears the failure stamp.
    assert "futures" not in b._model_load_failed_at


# ─────────────────────────────────────────────────────────────────────────────
# MCPBrain.score_via_model
# ─────────────────────────────────────────────────────────────────────────────

def test_load_model_bundle_rejects_overfit_latest_pointer(tmp_path: Path, monkeypatch):
    """A latest pointer that fails the current gate is treated as revoked."""
    from core.mcp_brain import MCPBrain

    monkeypatch.chdir(tmp_path)
    md = tmp_path / "data" / "models"
    md.mkdir(parents=True)
    art = md / "ensemble_futures_overfit.json"
    art.write_text(json.dumps({
        "model_version": "overfit",
        "metrics": {"n_oos_ensemble": 4670, "auc_ensemble": 0.76},
    }))
    (md / "ensemble_futures_latest.json").write_text(json.dumps({
        "model_version": "overfit",
        "artifact_path": str(art),
        "promoted_at": int(__import__("time").time()),
        "diag": {
            "model_version": "overfit",
            "oos_wr": 0.71,
            "deflated_sharpe": 0.0008,
            "pbo": 1.0,
        },
    }))

    b = MCPBrain()
    assert b._load_model_bundle("futures") == {}
    assert "futures" in b._model_load_failed_at


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
    art("ensemble_futures_latest.json").write_text(
        json.dumps(_valid_latest_payload("test_bundle", ens_path)))

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
