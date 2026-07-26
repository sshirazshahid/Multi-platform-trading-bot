"""Phase 13.2 — fitted model layer.

LRModel: thin sklearn LogisticRegression wrapper that:
  * Standardizes features internally (StandardScaler — saved + loaded
    alongside the model) so callers don't need to pre-scale.
  * Reproducibly fits with `random_state=42` by default.
  * Persists via joblib to a single .pkl file containing both the
    fitted scaler and the fitted classifier.
  * Exposes `predict_proba(X)` (full sklearn-shape (n,2)) and `p_win(X)`
    (just the class-1 column) for callers that only care about the
    "this trade will win" probability.

Why LR-only at this stage:
  * Sample size is the binding constraint (~290 fitted-attribution trades
    available). XGBoost + Optuna search would burn statistical power on
    multiple-comparison overhead.
  * `class_weight='balanced'` handles WR ≠ 50% gracefully without tuning.
  * Calibration is a separate concern: pair this with `core/calibration.IsotonicCalibrator`
    when you want post-fit probability quality.

Reference: compiled-pondering-key.md Phase 3.2.
"""
from __future__ import annotations

import hashlib
import io
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from core.contracts import ModelManifest as SharedModelManifest

MODEL_ARTIFACT_FORMAT = "trading_bot.model"
MODEL_ARTIFACT_CONTAINER_VERSION = 1
MODEL_MANIFEST_VERSION = 1
MAX_MODEL_ARTIFACT_AGE_DAYS = 7


class ArtifactCompatibilityError(RuntimeError):
    """Raised when a persisted model is unsafe to load in this runtime."""


def feature_schema_hash(feature_keys: Sequence[str]) -> str:
    """Return an order-sensitive SHA-256 for a model's feature contract."""
    keys = [str(key) for key in feature_keys]
    canonical = json.dumps(
        {"schema_version": 1, "feature_keys": keys},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash a file without loading the full artifact into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_payload_checksum(payload: Mapping[str, Any]) -> str:
    """Hash JSON content deterministically, excluding an embedded manifest."""
    content = {str(key): value for key, value in payload.items() if key != "manifest"}
    canonical = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def aggregate_artifact_checksum(checksums: Mapping[str, str]) -> str:
    """Bind a named set of artifact checksums into one stable digest."""
    canonical = json.dumps(
        {str(key): str(value) for key, value in checksums.items()},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _plain_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("manifest diagnostics and windows must be mappings")
    return json.loads(json.dumps(dict(value), default=_json_default))


def _runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
    }


def _library_versions() -> dict[str, str]:
    return {
        "numpy": str(np.__version__),
        "scikit-learn": str(sklearn.__version__),
        "joblib": str(joblib.__version__),
    }


def _major_minor(version: str) -> tuple[int, int] | None:
    try:
        parts = str(version).split(".")
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError, IndexError):
        return None


def _artifact_manifest_id(
    *,
    artifact_kind: str,
    model_version: str,
    artifact_uri: str,
    artifact_checksum: str,
    created_at: int,
    context: Mapping[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "artifact_kind": artifact_kind,
            "model_version": model_version,
            "artifact_uri": artifact_uri,
            "artifact_checksum": artifact_checksum,
            "created_at": int(created_at),
            "context": dict(context),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "model-" + hashlib.sha256(canonical.encode()).hexdigest()


class ModelManifest(SharedModelManifest):
    """Artifact-specific view of the shared immutable manifest contract."""

    @property
    def _artifact_context(self) -> dict[str, Any]:
        return dict(self.to_dict().get("context") or {})

    @property
    def manifest_version(self) -> int:
        return int(self._artifact_context.get("manifest_version", 0))

    @property
    def artifact_kind(self) -> str:
        return str(self._artifact_context.get("artifact_kind") or self.model_name)

    @property
    def runtime_versions(self) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in dict(
                self._artifact_context.get("runtime_versions") or {}
            ).items()
        }

    @property
    def library_versions(self) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in dict(
                self._artifact_context.get("library_versions") or {}
            ).items()
        }

    @property
    def feature_schema_hash(self) -> str:
        return str(
            self._artifact_context.get("feature_schema_hash")
            or self.feature_schema_version
        )

    @property
    def feature_count(self) -> int:
        return int(self._artifact_context.get("feature_count", 0))

    @property
    def artifact_checksum(self) -> str:
        return str(
            self._artifact_context.get("artifact_checksum")
            or self.artifact_sha256
        )

    @property
    def artifact_checksums(self) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in dict(
                self._artifact_context.get("artifact_checksums") or {}
            ).items()
        }

    @property
    def training_window(self) -> dict[str, Any]:
        return _plain_mapping(self._artifact_context.get("training_window"))

    @property
    def oos_window(self) -> dict[str, Any]:
        return _plain_mapping(self._artifact_context.get("oos_window"))

    @property
    def calibration_diagnostics(self) -> dict[str, Any]:
        return _plain_mapping(
            self._artifact_context.get("calibration_diagnostics")
        )

    @property
    def promotion_diagnostics(self) -> dict[str, Any]:
        return _plain_mapping(self._artifact_context.get("promotion_diagnostics"))

    @classmethod
    def create(
        cls,
        *,
        model_version: str,
        artifact_kind: str,
        feature_keys: Sequence[str],
        artifact_checksum: str,
        artifact_checksums: Mapping[str, str] | None = None,
        training_window: Mapping[str, Any] | None = None,
        oos_window: Mapping[str, Any] | None = None,
        calibration_diagnostics: Mapping[str, Any] | None = None,
        promotion_diagnostics: Mapping[str, Any] | None = None,
        created_at: int | None = None,
        artifact_uri: str | Path | None = None,
    ) -> ModelManifest:
        keys = [str(key) for key in feature_keys]
        checksum = str(artifact_checksum)
        runtime_versions = _runtime_versions()
        library_versions = _library_versions()
        train_window = _plain_mapping(training_window)
        test_window = _plain_mapping(oos_window)
        calibration = _plain_mapping(calibration_diagnostics)
        promotion = _plain_mapping(promotion_diagnostics)
        context = {
            "manifest_version": MODEL_MANIFEST_VERSION,
            "artifact_kind": str(artifact_kind),
            "runtime_versions": runtime_versions,
            "library_versions": library_versions,
            "feature_schema_hash": feature_schema_hash(keys),
            "feature_count": len(keys),
            "artifact_checksum": checksum,
            "artifact_checksums": {
                str(key): str(value)
                for key, value in (artifact_checksums or {}).items()
            },
            "training_window": train_window,
            "oos_window": test_window,
            "calibration_diagnostics": calibration,
            "promotion_diagnostics": promotion,
        }
        training_version = hashlib.sha256(
            json.dumps(
                {"training_window": train_window, "oos_window": test_window},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        code_version = hashlib.sha256(
            json.dumps(
                {"runtime": runtime_versions, "libraries": library_versions},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        created_timestamp = int(time.time() if created_at is None else created_at)
        artifact_location = str(
            artifact_uri or f"{artifact_kind}:{model_version}"
        )
        manifest_id = _artifact_manifest_id(
            artifact_kind=str(artifact_kind),
            model_version=str(model_version),
            artifact_uri=artifact_location,
            artifact_checksum=checksum,
            created_at=created_timestamp,
            context=context,
        )
        return cls(
            manifest_id=manifest_id,
            model_name=str(artifact_kind),
            model_version=str(model_version),
            created_at=datetime.fromtimestamp(created_timestamp, tz=timezone.utc),
            artifact_uri=artifact_location,
            artifact_sha256=checksum,
            feature_schema_version=feature_schema_hash(keys),
            training_data_version=f"sha256:{training_version}",
            code_version=f"sha256:{code_version}",
            metrics={
                "calibration_diagnostics": calibration,
                "promotion_diagnostics": promotion,
            },
            parameters={
                "runtime_versions": runtime_versions,
                "library_versions": library_versions,
            },
            context=context,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelManifest:
        if not isinstance(value, Mapping):
            raise ArtifactCompatibilityError("model manifest is not an object")
        try:
            return SharedModelManifest.from_dict.__func__(cls, value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactCompatibilityError(f"invalid model manifest: {exc}") from exc

    def validate(
        self,
        *,
        artifact_checksum: str | None = None,
        expected_artifact_kind: str | None = None,
        expected_feature_keys: Sequence[str] | None = None,
        expected_model_version: str | None = None,
        max_age_days: float | None = None,
        require_complete: bool = False,
        now: float | None = None,
    ) -> ModelManifest:
        errors: list[str] = []
        payload = self.to_dict()
        if self.manifest_version != MODEL_MANIFEST_VERSION:
            errors.append(
                f"manifest version {self.manifest_version} is unsupported"
            )
        if not self.model_version:
            errors.append("model_version is missing")
        if not self.artifact_kind:
            errors.append("artifact_kind is missing")
        if expected_artifact_kind and self.artifact_kind != expected_artifact_kind:
            errors.append(
                f"artifact kind {self.artifact_kind!r} != "
                f"{expected_artifact_kind!r}"
            )
        if expected_model_version and self.model_version != expected_model_version:
            errors.append(
                f"model version {self.model_version!r} != {expected_model_version!r}"
            )
        if self.model_name != self.artifact_kind:
            errors.append("artifact_kind does not match shared model_name")

        current_runtime = _runtime_versions()
        saved_python = self.runtime_versions.get("python")
        if _major_minor(saved_python or "") != _major_minor(current_runtime["python"]):
            errors.append(
                f"Python runtime {saved_python!r} is incompatible with "
                f"{current_runtime['python']!r}"
            )
        saved_impl = self.runtime_versions.get("implementation")
        if saved_impl != current_runtime["implementation"]:
            errors.append(
                f"Python implementation {saved_impl!r} is incompatible with "
                f"{current_runtime['implementation']!r}"
            )

        for name, current_version in _library_versions().items():
            saved_version = self.library_versions.get(name)
            if saved_version != current_version:
                errors.append(
                    f"{name} version {saved_version!r} is incompatible with "
                    f"{current_version!r}"
                )
        parameters = payload.get("parameters") or {}
        if parameters.get("runtime_versions") != self.runtime_versions:
            errors.append("runtime_versions disagree with shared parameters")
        if parameters.get("library_versions") != self.library_versions:
            errors.append("library_versions disagree with shared parameters")
        expected_code_version = hashlib.sha256(
            json.dumps(
                {
                    "runtime": self.runtime_versions,
                    "libraries": self.library_versions,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.code_version != f"sha256:{expected_code_version}":
            errors.append("code_version disagrees with runtime metadata")

        if len(self.feature_schema_hash) != 64:
            errors.append("feature schema hash is missing or malformed")
        if self.feature_schema_hash != self.feature_schema_version:
            errors.append("feature schema hash disagrees with shared contract")
        if self.feature_count <= 0:
            errors.append("feature_count must be positive")
        if expected_feature_keys is not None:
            expected_keys = [str(key) for key in expected_feature_keys]
            if self.feature_count != len(expected_keys):
                errors.append(
                    f"feature count {self.feature_count} != {len(expected_keys)}"
                )
            if self.feature_schema_hash != feature_schema_hash(expected_keys):
                errors.append("feature schema hash does not match expected feature order")

        if len(self.artifact_checksum) != 64:
            errors.append("artifact checksum is missing or malformed")
        if self.artifact_checksum != self.artifact_sha256:
            errors.append("artifact checksum disagrees with shared contract")
        if artifact_checksum and self.artifact_checksum != artifact_checksum:
            errors.append(
                f"artifact checksum mismatch: expected {self.artifact_checksum}, "
                f"got {artifact_checksum}"
            )
        expected_manifest_id = _artifact_manifest_id(
            artifact_kind=self.artifact_kind,
            model_version=self.model_version,
            artifact_uri=self.artifact_uri,
            artifact_checksum=self.artifact_checksum,
            created_at=int(self.created_at.timestamp()),
            context=self._artifact_context,
        )
        if self.manifest_id != expected_manifest_id:
            errors.append("manifest_id disagrees with artifact identity")

        expected_training_version = hashlib.sha256(
            json.dumps(
                {
                    "training_window": self.training_window,
                    "oos_window": self.oos_window,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.training_data_version != f"sha256:{expected_training_version}":
            errors.append("training_data_version disagrees with data windows")
        metrics = payload.get("metrics") or {}
        if metrics.get("calibration_diagnostics") != self.calibration_diagnostics:
            errors.append("calibration diagnostics disagree with shared metrics")
        if metrics.get("promotion_diagnostics") != self.promotion_diagnostics:
            errors.append("promotion diagnostics disagree with shared metrics")

        current_time = float(time.time() if now is None else now)
        created_at = self.created_at.timestamp()
        if created_at > current_time + 300:
            errors.append("created_at is in the future")
        elif max_age_days is not None:
            age_days = (current_time - created_at) / 86_400.0
            if age_days > float(max_age_days):
                errors.append(
                    f"artifact is stale: {age_days:.1f}d > {float(max_age_days):g}d"
                )

        if require_complete:
            for label, window in (
                ("training", self.training_window),
                ("OOS", self.oos_window),
            ):
                start = window.get("start") if isinstance(window, Mapping) else None
                end = window.get("end") if isinstance(window, Mapping) else None
                if start is None or end is None:
                    errors.append(f"{label} window is incomplete")
                else:
                    try:
                        if float(start) > float(end):
                            errors.append(f"{label} window start is after end")
                    except (TypeError, ValueError):
                        errors.append(f"{label} window is invalid")
            if not self.calibration_diagnostics:
                errors.append("calibration diagnostics are missing")
            if not self.promotion_diagnostics:
                errors.append("promotion diagnostics are missing")
            if not self.artifact_checksums:
                errors.append("component artifact checksums are missing")
            elif aggregate_artifact_checksum(
                self.artifact_checksums
            ) != self.artifact_checksum:
                errors.append("aggregate artifact checksum is inconsistent")

        if errors:
            raise ArtifactCompatibilityError("; ".join(errors))
        return self


def _positional_feature_keys(n_features: int) -> list[str]:
    return [f"__positional_feature_{index}" for index in range(int(n_features))]


def _serialize_payload(payload: Mapping[str, Any]) -> bytes:
    buffer = io.BytesIO()
    joblib.dump(dict(payload), buffer)
    return buffer.getvalue()


def _artifact_bytes_checksum(artifact: bytes) -> str:
    return hashlib.sha256(artifact).hexdigest()


def _atomic_joblib_dump(payload: Mapping[str, Any], path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        joblib.dump(dict(payload), tmp)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _save_model_artifact(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    artifact_kind: str,
    model_version: str | None,
    n_features: int,
    feature_keys: Sequence[str] | None,
    training_window: Mapping[str, Any] | None,
    oos_window: Mapping[str, Any] | None,
    calibration_diagnostics: Mapping[str, Any] | None,
    promotion_diagnostics: Mapping[str, Any] | None,
) -> ModelManifest:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    keys = (
        [str(key) for key in feature_keys]
        if feature_keys is not None
        else _positional_feature_keys(n_features)
    )
    if len(keys) != int(n_features):
        raise ValueError(
            f"feature_keys has {len(keys)} entries; expected {int(n_features)}"
        )

    version = str(model_version or destination.stem)
    inner_payload = dict(payload)
    inner_payload["model_version"] = version
    artifact = _serialize_payload(inner_payload)
    checksum = _artifact_bytes_checksum(artifact)
    manifest = ModelManifest.create(
        model_version=version,
        artifact_kind=artifact_kind,
        feature_keys=keys,
        artifact_checksum=checksum,
        artifact_uri=destination,
        training_window=training_window,
        oos_window=oos_window,
        calibration_diagnostics=calibration_diagnostics,
        promotion_diagnostics=promotion_diagnostics,
    )
    container = {
        "format": MODEL_ARTIFACT_FORMAT,
        "container_version": MODEL_ARTIFACT_CONTAINER_VERSION,
        "manifest": manifest.to_dict(),
        "feature_keys": keys,
        "artifact": artifact,
    }
    _atomic_joblib_dump(container, destination)
    return manifest


def _read_model_container(
    path: str | Path,
    *,
    expected_artifact_kind: str | None = None,
    expected_feature_keys: Sequence[str] | None = None,
    expected_model_version: str | None = None,
    max_age_days: float | None = None,
) -> tuple[dict[str, Any], ModelManifest]:
    source = Path(path)
    try:
        container = joblib.load(source)
    except Exception as exc:
        raise ArtifactCompatibilityError(
            f"model artifact could not be deserialized: {source}: {exc}"
        ) from exc
    if not isinstance(container, dict) or container.get("format") != MODEL_ARTIFACT_FORMAT:
        raise ArtifactCompatibilityError(
            "legacy model artifact has no ModelManifest; "
            "pass allow_legacy=True for research-only loading"
        )
    if container.get("container_version") != MODEL_ARTIFACT_CONTAINER_VERSION:
        raise ArtifactCompatibilityError(
            f"unsupported model container version: {container.get('container_version')}"
        )
    artifact = container.get("artifact")
    if not isinstance(artifact, bytes):
        raise ArtifactCompatibilityError("model container artifact payload is missing")
    keys = container.get("feature_keys")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        raise ArtifactCompatibilityError("model container feature_keys are invalid")

    manifest = ModelManifest.from_dict(container.get("manifest") or {})
    manifest.validate(
        artifact_checksum=_artifact_bytes_checksum(artifact),
        expected_artifact_kind=expected_artifact_kind,
        expected_feature_keys=(
            expected_feature_keys if expected_feature_keys is not None else keys
        ),
        expected_model_version=expected_model_version,
        max_age_days=max_age_days,
    )
    return container, manifest


def inspect_model_artifact(
    path: str | Path,
    *,
    expected_artifact_kind: str | None = None,
    expected_feature_count: int | None = None,
    expected_model_version: str | None = None,
    max_age_days: float | None = MAX_MODEL_ARTIFACT_AGE_DAYS,
) -> ModelManifest:
    """Validate a component container without unpickling its fitted model."""
    container, manifest = _read_model_container(
        path,
        expected_artifact_kind=expected_artifact_kind,
        expected_model_version=expected_model_version,
        max_age_days=max_age_days,
    )
    if expected_feature_count is not None and len(container["feature_keys"]) != int(
        expected_feature_count
    ):
        raise ArtifactCompatibilityError(
            f"feature count {len(container['feature_keys'])} != "
            f"{int(expected_feature_count)}"
        )
    return manifest


def _load_model_payload(
    path: str | Path,
    *,
    artifact_kind: str,
    allow_legacy: bool,
    expected_feature_keys: Sequence[str] | None,
    expected_model_version: str | None,
    max_age_days: float | None,
) -> tuple[dict[str, Any], ModelManifest | None, list[str] | None]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"model artifact not found: {source}")
    try:
        loaded = joblib.load(source)
    except Exception as exc:
        raise ArtifactCompatibilityError(
            f"model artifact could not be deserialized: {source}: {exc}"
        ) from exc

    if isinstance(loaded, dict) and loaded.get("format") == MODEL_ARTIFACT_FORMAT:
        container, manifest = _read_model_container(
            source,
            expected_artifact_kind=artifact_kind,
            expected_feature_keys=expected_feature_keys,
            expected_model_version=expected_model_version,
            max_age_days=max_age_days,
        )
        try:
            payload = joblib.load(io.BytesIO(container["artifact"]))
        except Exception as exc:
            raise ArtifactCompatibilityError(
                f"validated model payload could not be deserialized: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ArtifactCompatibilityError("model payload is not an object")
        if payload.get("model_version") != manifest.model_version:
            raise ArtifactCompatibilityError(
                "model payload version does not match its manifest"
            )
        return payload, manifest, list(container["feature_keys"])

    if not allow_legacy:
        raise ArtifactCompatibilityError(
            "legacy model artifact has no ModelManifest; "
            "pass allow_legacy=True for research-only loading"
        )
    if not isinstance(loaded, dict):
        raise ArtifactCompatibilityError("legacy model payload is not an object")
    return loaded, None, None


class LRModel:
    """Reproducible logistic regression with internal scaling + persistence."""

    def __init__(
        self,
        *,
        C: float = 1.0,
        random_state: int = 42,
        max_iter: int = 1000,
        class_weight: Optional[str] = "balanced",
    ) -> None:
        self.C = float(C)
        self.random_state = int(random_state)
        self.max_iter = int(max_iter)
        self.class_weight = class_weight
        self._scaler: Optional[StandardScaler] = None
        self._clf: Optional[LogisticRegression] = None
        self.n_features_: Optional[int] = None
        self.classes_: Optional[np.ndarray] = None
        self.manifest_: Optional[ModelManifest] = None
        self.feature_keys_: Optional[list[str]] = None
        self.model_version_: Optional[str] = None

    def fit(self, X, y) -> LRModel:
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).ravel()
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D (n, n_features); got shape {X.shape}")
        if X.shape[0] != y.shape[0]:
            raise ValueError(
                f"X.shape[0]={X.shape[0]} but y.shape[0]={y.shape[0]}")
        unique = np.unique(y)
        if unique.size < 2:
            raise ValueError(
                f"y must contain >= 2 classes; got {unique.tolist()}")

        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        clf = LogisticRegression(
            C=self.C,
            random_state=self.random_state,
            max_iter=self.max_iter,
            class_weight=self.class_weight,
            solver="lbfgs",
        )
        clf.fit(Xs, y)

        self._scaler = scaler
        self._clf = clf
        self.n_features_ = int(X.shape[1])
        self.classes_ = clf.classes_
        return self

    def predict_proba(self, X) -> np.ndarray:
        if self._clf is None or self._scaler is None:
            raise RuntimeError("LRModel must be fit() before predict_proba()")
        X = np.asarray(X, dtype=float)
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D; got shape {X.shape}")
        if X.shape[1] != self.n_features_:
            raise ValueError(
                f"expected {self.n_features_} features; got {X.shape[1]}")
        return self._clf.predict_proba(self._scaler.transform(X))

    def p_win(self, X) -> np.ndarray:
        """Probability of class==1 (the "win" class).

        Caller is responsible for ensuring training labels were 0=loss, 1=win.
        """
        proba = self.predict_proba(X)
        if self.classes_ is not None and 1 in self.classes_:
            idx = int(np.where(self.classes_ == 1)[0][0])
            return proba[:, idx]
        return proba[:, -1]

    def save(
        self,
        path,
        *,
        model_version: Optional[str] = None,
        feature_keys: Sequence[str] | None = None,
        training_window: Mapping[str, Any] | None = None,
        oos_window: Mapping[str, Any] | None = None,
        calibration_diagnostics: Mapping[str, Any] | None = None,
        promotion_diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist to a single .pkl. `model_version`, when provided, is
        embedded in the payload so callers (e.g. ShadowPredictor on
        Windows where symlinks aren't reliable) can recover the
        canonical version name regardless of the on-disk filename."""
        if self._clf is None:
            raise RuntimeError("Cannot save unfit model")
        payload = {
            "version": 1,
            "C": self.C,
            "random_state": self.random_state,
            "max_iter": self.max_iter,
            "class_weight": self.class_weight,
            "scaler": self._scaler,
            "clf": self._clf,
            "n_features": self.n_features_,
            "classes": self.classes_,
        }
        manifest = _save_model_artifact(
            path,
            payload,
            artifact_kind="lr",
            model_version=model_version,
            n_features=int(self.n_features_),
            feature_keys=feature_keys,
            training_window=training_window,
            oos_window=oos_window,
            calibration_diagnostics=calibration_diagnostics,
            promotion_diagnostics=promotion_diagnostics,
        )
        self.manifest_ = manifest
        self.feature_keys_ = (
            [str(key) for key in feature_keys]
            if feature_keys is not None
            else _positional_feature_keys(int(self.n_features_))
        )
        self.model_version_ = manifest.model_version

    @classmethod
    def load(
        cls,
        path,
        *,
        allow_legacy: bool = False,
        expected_feature_keys: Sequence[str] | None = None,
        expected_model_version: str | None = None,
        max_age_days: float | None = MAX_MODEL_ARTIFACT_AGE_DAYS,
    ) -> LRModel:
        data, manifest, feature_keys = _load_model_payload(
            path,
            artifact_kind="lr",
            allow_legacy=allow_legacy,
            expected_feature_keys=expected_feature_keys,
            expected_model_version=expected_model_version,
            max_age_days=max_age_days,
        )
        m = cls(
            C=float(data.get("C", 1.0)),
            random_state=int(data.get("random_state", 42)),
            max_iter=int(data.get("max_iter", 1000)),
            class_weight=data.get("class_weight", "balanced"),
        )
        m._scaler = data["scaler"]
        m._clf = data["clf"]
        m.n_features_ = int(data["n_features"])
        m.classes_ = data["classes"]
        if expected_feature_keys is not None and m.n_features_ != len(
            expected_feature_keys
        ):
            raise ArtifactCompatibilityError(
                f"feature count {m.n_features_} != {len(expected_feature_keys)}"
            )
        m.manifest_ = manifest
        m.feature_keys_ = feature_keys
        m.model_version_ = str(data.get("model_version") or "") or None
        return m


class GBMModel:
    """Reproducible HistGradientBoostingClassifier with internal scaling +
    persistence. Mirrors LRModel's API so callers can swap models cleanly
    via duck-typing.

    HistGradientBoosting captures non-linear feature interactions without
    needing LightGBM/XGBoost — sklearn 1.5.2 (already pinned) is enough.
    StandardScaler isn't strictly required for tree models, but we keep it
    so the persisted artifact is interchangeable with LRModel artifacts at
    the ensemble layer.
    """

    def __init__(
        self,
        *,
        max_iter: int = 200,
        learning_rate: float = 0.05,
        max_depth: int = 5,
        l2_regularization: float = 1.0,
        random_state: int = 42,
        class_weight: Optional[str] = "balanced",
    ) -> None:
        self.max_iter = int(max_iter)
        self.learning_rate = float(learning_rate)
        self.max_depth = int(max_depth)
        self.l2_regularization = float(l2_regularization)
        self.random_state = int(random_state)
        self.class_weight = class_weight
        self._scaler: Optional[StandardScaler] = None
        self._clf: Optional[HistGradientBoostingClassifier] = None
        self.n_features_: Optional[int] = None
        self.classes_: Optional[np.ndarray] = None
        self.manifest_: Optional[ModelManifest] = None
        self.feature_keys_: Optional[list[str]] = None
        self.model_version_: Optional[str] = None

    def fit(self, X, y) -> GBMModel:
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).ravel()
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D (n, n_features); got shape {X.shape}")
        if X.shape[0] != y.shape[0]:
            raise ValueError(
                f"X.shape[0]={X.shape[0]} but y.shape[0]={y.shape[0]}")
        unique = np.unique(y)
        if unique.size < 2:
            raise ValueError(
                f"y must contain >= 2 classes; got {unique.tolist()}")

        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        clf = HistGradientBoostingClassifier(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            l2_regularization=self.l2_regularization,
            random_state=self.random_state,
            class_weight=self.class_weight,
        )
        clf.fit(Xs, y)

        self._scaler = scaler
        self._clf = clf
        self.n_features_ = int(X.shape[1])
        self.classes_ = clf.classes_
        return self

    def predict_proba(self, X) -> np.ndarray:
        if self._clf is None or self._scaler is None:
            raise RuntimeError("GBMModel must be fit() before predict_proba()")
        X = np.asarray(X, dtype=float)
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D; got shape {X.shape}")
        if X.shape[1] != self.n_features_:
            raise ValueError(
                f"expected {self.n_features_} features; got {X.shape[1]}")
        return self._clf.predict_proba(self._scaler.transform(X))

    def p_win(self, X) -> np.ndarray:
        proba = self.predict_proba(X)
        if self.classes_ is not None and 1 in self.classes_:
            idx = int(np.where(self.classes_ == 1)[0][0])
            return proba[:, idx]
        return proba[:, -1]

    def save(
        self,
        path,
        *,
        model_version: Optional[str] = None,
        feature_keys: Sequence[str] | None = None,
        training_window: Mapping[str, Any] | None = None,
        oos_window: Mapping[str, Any] | None = None,
        calibration_diagnostics: Mapping[str, Any] | None = None,
        promotion_diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        if self._clf is None:
            raise RuntimeError("Cannot save unfit model")
        payload = {
            "version": 1,
            "max_iter": self.max_iter,
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "l2_regularization": self.l2_regularization,
            "random_state": self.random_state,
            "class_weight": self.class_weight,
            "scaler": self._scaler,
            "clf": self._clf,
            "n_features": self.n_features_,
            "classes": self.classes_,
        }
        manifest = _save_model_artifact(
            path,
            payload,
            artifact_kind="gbm",
            model_version=model_version,
            n_features=int(self.n_features_),
            feature_keys=feature_keys,
            training_window=training_window,
            oos_window=oos_window,
            calibration_diagnostics=calibration_diagnostics,
            promotion_diagnostics=promotion_diagnostics,
        )
        self.manifest_ = manifest
        self.feature_keys_ = (
            [str(key) for key in feature_keys]
            if feature_keys is not None
            else _positional_feature_keys(int(self.n_features_))
        )
        self.model_version_ = manifest.model_version

    @classmethod
    def load(
        cls,
        path,
        *,
        allow_legacy: bool = False,
        expected_feature_keys: Sequence[str] | None = None,
        expected_model_version: str | None = None,
        max_age_days: float | None = MAX_MODEL_ARTIFACT_AGE_DAYS,
    ) -> GBMModel:
        data, manifest, feature_keys = _load_model_payload(
            path,
            artifact_kind="gbm",
            allow_legacy=allow_legacy,
            expected_feature_keys=expected_feature_keys,
            expected_model_version=expected_model_version,
            max_age_days=max_age_days,
        )
        m = cls(
            max_iter=int(data.get("max_iter", 200)),
            learning_rate=float(data.get("learning_rate", 0.05)),
            max_depth=int(data.get("max_depth", 5)),
            l2_regularization=float(data.get("l2_regularization", 1.0)),
            random_state=int(data.get("random_state", 42)),
            class_weight=data.get("class_weight", "balanced"),
        )
        m._scaler = data["scaler"]
        m._clf = data["clf"]
        m.n_features_ = int(data["n_features"])
        m.classes_ = data["classes"]
        if expected_feature_keys is not None and m.n_features_ != len(
            expected_feature_keys
        ):
            raise ArtifactCompatibilityError(
                f"feature count {m.n_features_} != {len(expected_feature_keys)}"
            )
        m.manifest_ = manifest
        m.feature_keys_ = feature_keys
        m.model_version_ = str(data.get("model_version") or "") or None
        return m
