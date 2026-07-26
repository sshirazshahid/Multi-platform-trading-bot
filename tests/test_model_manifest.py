"""Model artifact provenance, integrity, and production authorization tests."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pytest

FEATURE_KEYS = [
    "score",
    "layers_ok",
    "confidence",
    "sl_pct",
    "tp_pct",
    "rsi_1h",
    "adx_1h",
    "adx_4h",
    "atr_pct_1h",
    "bb_width_4h",
    "vol_ratio",
    "ema20_above_50_4h",
    "ema20_above_50_1h",
    "funding_rate",
    "ob_imbalance",
]


def _fit_lr(n_features: int = 3):
    from core.models import LRModel

    rng = np.random.default_rng(20260713)
    X = rng.normal(size=(120, n_features))
    y = (X[:, 0] + 0.25 * X[:, 1] > 0).astype(int)
    return LRModel().fit(X, y), X


def _save_manifested_lr(path: Path, *, feature_keys: list[str] | None = None):
    keys = feature_keys or ["a", "b", "c"]
    model, X = _fit_lr(len(keys))
    model.save(
        path,
        model_version="lr_manifest_v1",
        feature_keys=keys,
        training_window={"start": 100, "end": 200, "n_samples": 120},
        oos_window={"start": 160, "end": 200, "n_samples": 40},
        calibration_diagnostics={"brier": 0.18},
        promotion_diagnostics={"verdict": "SHADOW"},
    )
    return model, X


def test_manifest_roundtrip_records_required_provenance(tmp_path: Path):
    from core.contracts import ModelManifest as SharedModelManifest
    from core.models import LRModel, ModelManifest, feature_schema_hash

    path = tmp_path / "lr.pkl"
    original, X = _save_manifested_lr(path)

    container = joblib.load(path)
    manifest = ModelManifest.from_dict(container["manifest"])
    assert isinstance(manifest, SharedModelManifest)
    context = manifest.to_dict()["context"]
    assert {
        "runtime_versions",
        "library_versions",
        "feature_schema_hash",
        "artifact_checksum",
        "training_window",
        "oos_window",
        "calibration_diagnostics",
        "promotion_diagnostics",
    } <= set(context)
    assert manifest.runtime_versions["python"]
    assert manifest.runtime_versions["implementation"]
    assert {"numpy", "scikit-learn", "joblib"} <= set(
        manifest.library_versions
    )
    assert manifest.feature_schema_hash == feature_schema_hash(["a", "b", "c"])
    assert len(manifest.artifact_checksum) == 64
    assert manifest.training_window["start"] == 100
    assert manifest.oos_window["start"] == 160
    assert manifest.calibration_diagnostics == {"brier": 0.18}
    assert manifest.promotion_diagnostics == {"verdict": "SHADOW"}

    restored = LRModel.load(
        path,
        expected_feature_keys=["a", "b", "c"],
        max_age_days=1,
    )
    np.testing.assert_allclose(original.p_win(X), restored.p_win(X))
    assert restored.manifest_ == manifest


def test_load_rejects_corrupt_embedded_artifact(tmp_path: Path):
    from core.models import ArtifactCompatibilityError, LRModel

    path = tmp_path / "lr.pkl"
    _save_manifested_lr(path)
    container = joblib.load(path)
    artifact = bytearray(container["artifact"])
    artifact[len(artifact) // 2] ^= 0x01
    container["artifact"] = bytes(artifact)
    joblib.dump(container, path)

    with pytest.raises(ArtifactCompatibilityError, match="checksum"):
        LRModel.load(path)


def test_load_rejects_incompatible_library_version(tmp_path: Path):
    from core.models import ArtifactCompatibilityError, LRModel

    path = tmp_path / "lr.pkl"
    _save_manifested_lr(path)
    container = joblib.load(path)
    container["manifest"]["context"]["library_versions"][
        "scikit-learn"
    ] = "0.0.0"
    joblib.dump(container, path)

    with pytest.raises(ArtifactCompatibilityError, match="scikit-learn"):
        LRModel.load(path)


def test_load_rejects_stale_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from core import models
    from core.models import ArtifactCompatibilityError, LRModel

    path = tmp_path / "lr.pkl"
    _save_manifested_lr(path)
    future = time.time() + 10 * 86_400
    monkeypatch.setattr(models.time, "time", lambda: future)

    with pytest.raises(ArtifactCompatibilityError, match="stale"):
        LRModel.load(path)
    assert LRModel.load(path, max_age_days=None).n_features_ == 3


def test_legacy_load_requires_explicit_research_opt_in(tmp_path: Path):
    from core.models import ArtifactCompatibilityError, LRModel

    manifested = tmp_path / "manifested.pkl"
    _save_manifested_lr(manifested)
    container = joblib.load(manifested)
    legacy_payload = joblib.load(io.BytesIO(container["artifact"]))
    legacy = tmp_path / "legacy.pkl"
    joblib.dump(legacy_payload, legacy)

    with pytest.raises(ArtifactCompatibilityError, match="legacy"):
        LRModel.load(legacy)
    restored = LRModel.load(legacy, allow_legacy=True)
    assert restored.n_features_ == 3
    assert restored.manifest_ is None


def test_shadow_predictor_disables_feature_schema_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from core import shadow_predictor as sp

    model_path = tmp_path / "lr_v_latest.pkl"
    _save_manifested_lr(model_path, feature_keys=list(reversed(FEATURE_KEYS)))
    monkeypatch.setattr(sp, "LATEST_MODEL", model_path)

    predictor = sp.ShadowPredictor()
    assert predictor.predict_p_win({"score": 80}) is None
    assert predictor.disabled_reason is not None
    assert "feature schema" in predictor.disabled_reason.lower()


def _write_promotable_bundle(tmp_path: Path) -> tuple[Path, dict]:
    from core.calibration import IsotonicCalibrator
    from core.models import GBMModel, LRModel

    rng = np.random.default_rng(7)
    feature_keys = ["a", "b", "c"]
    X = rng.normal(size=(140, len(feature_keys)))
    y = (X[:, 0] - 0.2 * X[:, 2] > 0).astype(int)
    lr = LRModel().fit(X, y)
    gbm = GBMModel(max_iter=30).fit(X, y)
    iso_lr = IsotonicCalibrator(min_samples=10).fit(lr.p_win(X), y)
    iso_gbm = IsotonicCalibrator(min_samples=10).fit(gbm.p_win(X), y)

    lr.save(tmp_path / "lr.pkl", model_version="ensemble_v1::lr")
    gbm.save(tmp_path / "gbm.pkl", model_version="ensemble_v1::gbm")
    iso_lr.save(tmp_path / "iso_lr.pkl")
    iso_gbm.save(tmp_path / "iso_gbm.pkl")

    ensemble = tmp_path / "ensemble.json"
    ensemble.write_text(json.dumps({
        "model_version": "ensemble_v1",
        "market_type": "futures",
        "trained_at": int(time.time()),
        "feature_keys": feature_keys,
        "weights": {"lr": 0.4, "gbm": 0.6, "mcp_rule": 0.25},
        "artifacts": {
            "lr": "lr.pkl",
            "gbm": "gbm.pkl",
            "iso_lr": "iso_lr.pkl",
            "iso_gbm": "iso_gbm.pkl",
        },
        "metrics": {
            "auc_ensemble": 0.67,
            "brier_ensemble": 0.18,
            "n_oos_ensemble": 300,
            "n_train": 140,
            "wr_train": 0.35,
        },
    }), encoding="utf-8")
    row = {
        "model_version": "ensemble_v1",
        "trained_at": int(time.time()),
        "train_window_start": 100,
        "train_window_end": 500,
        "oos_window_start": 350,
        "oos_window_end": 500,
        "oos_wr": 0.62,
        "deflated_sharpe": 0.30,
        "pbo": 0.25,
        "artifact_path": str(ensemble),
    }
    return ensemble, row


def test_promotion_writes_and_revalidates_authoritative_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from core import promotion_gate as pg
    from core.models import ModelManifest

    monkeypatch.setattr(pg, "AUDIT_LOG", tmp_path / "audit.jsonl")
    ensemble, row = _write_promotable_bundle(tmp_path)
    assert pg.promote_if_eligible(row, market_type="futures", models_dir=tmp_path)

    artifact_payload = json.loads(ensemble.read_text(encoding="utf-8"))
    manifest_payload = artifact_payload["manifest"]
    manifest = ModelManifest.from_dict(manifest_payload)
    assert manifest.feature_schema_hash
    assert len(manifest.artifact_checksum) == 64
    assert set(manifest.artifact_checksums) == {
        "ensemble", "lr", "gbm", "iso_lr", "iso_gbm",
    }
    assert manifest.training_window["start"] == 100
    assert manifest.oos_window["start"] == 350
    assert manifest.calibration_diagnostics["brier_ensemble"] == 0.18
    assert manifest.promotion_diagnostics["promote"] is True

    pointer = json.loads(
        (tmp_path / "ensemble_futures_latest.json").read_text(encoding="utf-8")
    )
    assert pointer["manifest"] == manifest_payload
    ok, reason, _ = pg.validate_model_pointer(pointer, market_type="futures")
    assert ok is True, reason

    with (tmp_path / "lr.pkl").open("ab") as handle:
        handle.write(b"corrupt")
    ok, reason, _ = pg.validate_model_pointer(pointer, market_type="futures")
    assert ok is False
    assert "checksum" in reason.lower()


def test_legacy_ensemble_cannot_authorize_production(tmp_path: Path):
    from core import promotion_gate as pg

    artifact = tmp_path / "legacy.json"
    artifact.write_text(json.dumps({
        "model_version": "legacy",
        "feature_keys": ["a"],
        "metrics": {
            "n_oos_ensemble": 300,
            "auc_ensemble": 0.70,
            "wr_train": 0.35,
        },
    }), encoding="utf-8")
    row = {
        "model_version": "legacy",
        "oos_wr": 0.62,
        "deflated_sharpe": 0.30,
        "pbo": 0.25,
        "artifact_path": str(artifact),
    }

    promote, reason, _ = pg.evaluate_model_version(row, market_type="futures")
    assert promote is False
    assert "manifest" in reason.lower()
