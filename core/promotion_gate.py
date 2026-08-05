"""Phase 6.2: shadow → live promotion gate.

Statistical gate evaluating whether the shadow ML pipeline is ready to
take over from the live rubric. Returns one of:

  * PROMOTE              — all four López de Prado-style conditions met
  * HOLD                 — at least one condition fails, no special clause
  * ESCALATE_TO_OPERATOR — long-eligible + favourable point estimates but
                           the LB-vs-UB gate has not tipped (avoids the
                           "wide CI overlap forever" deadlock)
  * REJECT               — shadow strictly worse than live (point Sharpe
                           below live point Sharpe)

Promotion rule (all four must hold):
  1. Shadow Sharpe lower-95% bound > Live Sharpe upper-95% bound
  2. Shadow rolling 30-day win-rate ≥ 0.65 (user-tightened from 0.61)
  3. Shadow PBO < 0.5
  4. Shadow Deflated Sharpe p < 0.05

Escape clause (ESCALATE) when LB-vs-UB fails but:
  * days_eligible ≥ 90, AND
  * shadow point Sharpe > live point Sharpe, AND
  * shadow 30-day WR ≥ 0.65

Math is delegated to `core.stat_tests`: Sharpe, percentile-bootstrap CI,
Deflated Sharpe (selection-bias adjusted), PBO via CSCV.

PBO note — PBO requires a returns matrix of multiple strategies. For the
gate we treat shadow vs live as two competing strategies and feed the
2-column matrix to `pbo`. This answers "given these two candidates, does
picking the in-sample winner generalize OOS?" — interpretable as a
narrow-universe overfit check.

`apply_split(percent, path)` writes the A/B traffic-split flag for
`bot_engine` to consume. Value is clamped to [0.0, 1.0].
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from core.models import (
    ArtifactCompatibilityError,
    ModelManifest,
    aggregate_artifact_checksum,
    canonical_payload_checksum,
    inspect_model_artifact,
    sha256_file,
)
from core.stat_tests import bootstrap_ci, deflated_sharpe, pbo, sharpe


class GateVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    ESCALATE = "ESCALATE_TO_OPERATOR"
    REJECT = "REJECT"


@dataclass
class GateInputs:
    """Inputs to a single gate evaluation.

    `shadow_returns` and `live_returns` are 1-D per-trade (or per-bar)
    return arrays. They do not need to be the same length; the PBO step
    will use the shorter length so the matrix is rectangular.

    `shadow_wr_30d` is the rolling 30-day win-rate as a fraction in [0, 1].
    `days_eligible` is the number of calendar days the shadow has been in
    the gate-eligible regime (used for the escape clause).

    `n_trials_shadow` is the number of strategy variants tried during the
    shadow's research history; the Deflated Sharpe applies a multiple-
    testing penalty proportional to this.
    """
    shadow_returns: np.ndarray
    live_returns: np.ndarray
    shadow_wr_30d: float
    days_eligible: int
    n_trials_shadow: int = 1


@dataclass
class PromotionGate:
    """Stateless gate. Construct with thresholds, call `evaluate`."""
    # Dual WR floors (intentional, do not "reconcile" by loosening either):
    # this shadow-vs-live PromotionGate uses wr_floor=0.65; the model-artifact
    # gate below uses MIN_OOS_WR=0.55. Both stay frozen.
    wr_floor: float = 0.65
    pbo_threshold: float = 0.5
    dsr_p_threshold: float = 0.05
    escape_clause_days: int = 90
    ci_level: float = 0.95
    bootstrap_resamples: int = 5000
    pbo_partitions: int = 16
    random_state: int = 42
    # Reasons accumulator gets reset per evaluate() call.
    _reasons: list[str] = field(default_factory=list, init=False, repr=False)

    def evaluate(self, inputs: GateInputs) -> tuple[GateVerdict, dict]:
        """Compute verdict + diagnostics dict.

        Returns a `(verdict, diagnostics)` tuple. Diagnostics keys:
            shadow_sharpe_lb, shadow_sharpe_ub, shadow_sharpe_point,
            live_sharpe_lb, live_sharpe_ub, live_sharpe_point,
            shadow_wr_30d, days_eligible, pbo, deflated_sharpe_p,
            sharpe_lb_beats_live_ub, wr_floor_pass, pbo_pass, dsr_pass,
            all_four_pass, escape_eligible, reasons.
        """
        reasons: list[str] = []

        shadow = np.asarray(inputs.shadow_returns, dtype=float)
        live = np.asarray(inputs.live_returns, dtype=float)

        # ── Point Sharpes + CIs ─────────────────────────────────────
        shadow_point = sharpe(shadow)
        live_point = sharpe(live)

        shadow_lb, shadow_ub = bootstrap_ci(
            shadow,
            n_resamples=self.bootstrap_resamples,
            ci=self.ci_level,
            random_state=self.random_state,
        )
        live_lb, live_ub = bootstrap_ci(
            live,
            n_resamples=self.bootstrap_resamples,
            ci=self.ci_level,
            random_state=self.random_state,
        )

        # ── Condition 1: LB beats UB ────────────────────────────────
        lb_beats_ub = bool(shadow_lb > live_ub)
        if not lb_beats_ub:
            reasons.append(
                f"shadow Sharpe LB ({shadow_lb:.3f}) does not exceed "
                f"live Sharpe UB ({live_ub:.3f})"
            )

        # ── Condition 2: WR floor ───────────────────────────────────
        wr_pass = bool(inputs.shadow_wr_30d >= self.wr_floor)
        if not wr_pass:
            reasons.append(
                f"shadow 30d WR {inputs.shadow_wr_30d:.3f} < floor {self.wr_floor:.2f}"
            )

        # ── Condition 3: PBO ────────────────────────────────────────
        # Build a (T, 2) matrix from shadow + live — truncate to shorter.
        T = min(len(shadow), len(live))
        if T >= self.pbo_partitions:
            R = np.column_stack([shadow[:T], live[:T]])
            try:
                pbo_value = float(pbo(R, n_partitions=self.pbo_partitions))
            except Exception:
                pbo_value = 0.5  # neutral on failure
        else:
            # Not enough data for CSCV — neutral, fail-closed.
            pbo_value = 0.5
        pbo_pass = bool(pbo_value < self.pbo_threshold)
        if not pbo_pass:
            reasons.append(f"PBO {pbo_value:.3f} >= {self.pbo_threshold:.2f}")

        # ── Condition 4: Deflated Sharpe ────────────────────────────
        n_obs = int(shadow.size)
        if n_obs >= 4:
            skew_val = float(_skew(shadow))
            kurt_val = float(_kurt(shadow))
        else:
            skew_val, kurt_val = 0.0, 3.0
        # `deflated_sharpe` returns Pr[true SR > 0]; we want this HIGH
        # (>= 1 - p_threshold) to declare skill, equivalently the
        # complementary "p" must be < threshold.
        dsr_prob = float(
            deflated_sharpe(
                sr_observed=shadow_point,
                n_trials=int(inputs.n_trials_shadow),
                n_obs=max(2, n_obs),
                skew=skew_val,
                kurt=kurt_val,
                # Under the null each of the N trial Sharpes is an i.i.d. estimate
                # with sampling variance ~1/n_obs, so the cross-trial Sharpe
                # variance that E[max SR] needs is ~1/n_obs — NOT the placeholder
                # 1.0, which assumed unit-variance trials and inflated E[max SR],
                # making the gate reject even strong signals (SR 0.6–1.0). Matches
                # the sr_var convention in scripts/train_models.py. This is ONE of
                # four conjunctive conditions; WR>=0.65 remains the binding gate.
                sr_var=1.0 / max(2, n_obs),
            )
        )
        dsr_p = 1.0 - dsr_prob
        dsr_pass = bool(dsr_p < self.dsr_p_threshold)
        if not dsr_pass:
            reasons.append(
                f"Deflated Sharpe p {dsr_p:.3f} >= {self.dsr_p_threshold:.2f}"
            )

        all_four = lb_beats_ub and wr_pass and pbo_pass and dsr_pass

        # ── Escape-clause eligibility (only meaningful when LB/UB fails) ─
        escape_eligible = bool(
            (not lb_beats_ub)
            and inputs.days_eligible >= self.escape_clause_days
            and shadow_point > live_point
            and wr_pass
        )

        # ── Verdict resolution ──────────────────────────────────────
        if all_four:
            verdict = GateVerdict.PROMOTE
        elif shadow_point < live_point and not wr_pass:
            # Shadow worse than live on both point Sharpe and WR — clear reject.
            verdict = GateVerdict.REJECT
        elif escape_eligible:
            verdict = GateVerdict.ESCALATE
        else:
            verdict = GateVerdict.HOLD

        diag = {
            "shadow_sharpe_lb": float(shadow_lb),
            "shadow_sharpe_ub": float(shadow_ub),
            "shadow_sharpe_point": float(shadow_point),
            "live_sharpe_lb": float(live_lb),
            "live_sharpe_ub": float(live_ub),
            "live_sharpe_point": float(live_point),
            "shadow_wr_30d": float(inputs.shadow_wr_30d),
            "days_eligible": int(inputs.days_eligible),
            "pbo": float(pbo_value),
            "deflated_sharpe_p": float(dsr_p),
            "sharpe_lb_beats_live_ub": lb_beats_ub,
            "wr_floor_pass": wr_pass,
            "pbo_pass": pbo_pass,
            "dsr_pass": dsr_pass,
            "all_four_pass": bool(all_four),
            "escape_eligible": escape_eligible,
            "reasons": reasons,
        }
        return verdict, diag


def _skew(x: np.ndarray) -> float:
    """Sample skewness (Fisher-Pearson, biased). 0 on degenerate input."""
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 2:
        return 0.0
    mu = x.mean()
    s = x.std(ddof=0)
    if s <= 0:
        return 0.0
    return float(((x - mu) ** 3).mean() / (s ** 3))


def _kurt(x: np.ndarray) -> float:
    """Sample kurtosis (raw — normal = 3). 3 on degenerate input."""
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 2:
        return 3.0
    mu = x.mean()
    s = x.std(ddof=0)
    if s <= 0:
        return 3.0
    return float(((x - mu) ** 4).mean() / (s ** 4))


# ─────────────────────────────────────────────────────────────────────────────
# Model-version gate (Phase 5 of the predict-spot+futures plan).
#
# Distinct from `PromotionGate.evaluate` above: that one promotes the *shadow
# runner* over the *live rubric*. THIS one decides whether a freshly-trained
# `model_versions` row is good enough to overwrite `data/models/ensemble_*_latest.json`.
# Both gates share the same statistical primitives (`core.stat_tests`); they
# just answer different questions.
# ─────────────────────────────────────────────────────────────────────────────

# Thresholds — see plan §Phase 5. n_oos floors are different per market because
# spot has structurally less data (long-only, longer hold).
#
# DSR / PBO floor note: `core.stat_tests.deflated_sharpe` returns Pr[true SR > 0]
# on a per-TRADE Sharpe sample. With realistic crypto setup data the trades-
# above-threshold subset is small (~50-200 obs) so the multiple-comparisons
# penalty drives Pr[skill] very low even when the model is unambiguously
# discriminating (AUC >> 0.5 over 1000s of test rows).
#
# Similarly the trainer's PBO call currently feeds in *folds-as-strategies*
# instead of *(C × lr)-strategies*; until the trainer is upgraded to stack
# the full hyperparameter grid for CSCV, PBO from this pipeline is not a
# trustworthy signal. The TRAINER TODO lives in scripts/train_models.py.
#
# 2026-04-30 (gate-restore): the original commit citing "ENSEMBLE AUC, WR
# uplift over base rate, and n_oos floor are stronger guards" relaxed
# MIN_DSR/MAX_PBO but did NOT actually wire the AUC and WR-uplift floors
# it claimed were load-bearing. Result: a model with AUC barely above
# 0.5 would have promoted past the gate. Floors below now make those
# guards real:
#   - MIN_AUC = 0.60: model must materially discriminate (random = 0.5).
#   - MIN_WR_UPLIFT = 1.5: oos_wr at decision threshold must be at least
#     1.5x the training-set base rate. Catches models that "achieve" high
#     OOS WR only because they predict the positive class on rare events.
# DSR / PBO stay permissive (the underlying computations are documented
# unreliable for this trainer); flip the per-call kwargs to tighten once
# the trainer's PBO is fixed (see TODO in scripts/train_models.py).
# The shadow-vs-live PromotionGate above keeps its strict 0.5 floors.
MIN_OOS = {"futures": 200, "spot": 100}
# Model-artifact OOS WR floor (0.55). Distinct from PromotionGate.wr_floor
# (0.65) above — dual floors are documented intentionally; never lower either.
MIN_OOS_WR = 0.55
MIN_AUC = 0.60
MIN_WR_UPLIFT = 1.5
# 2026-05-25 — honest thresholds (no-edge-forensics). The previous values
# (MIN_DSR=0.0, MAX_PBO=1.0) rubber-stamped a maximally-overfit model
# (the live ensemble had DSR=0.0008, PBO=1.0 and was PROMOTED). A Deflated
# Sharpe floor > 0 and a PBO ceiling < 1.0 are the minimum bar for a model
# whose backtest edge is more likely real than a selection artifact.
MIN_DSR = 0.10
MAX_PBO = 0.5
MAX_MODEL_AGE_DAYS = 7

LATEST_TEMPLATE = "ensemble_{market}_latest.json"
AUDIT_LOG = Path("data/models/audit.jsonl")
REQUIRED_ENSEMBLE_ARTIFACTS = ("lr", "gbm", "iso_lr", "iso_gbm")


def _audit(payload: dict) -> None:
    """Append-only newline-delimited JSON of every promotion attempt."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=float) + "\n")


def _read_ensemble_payload(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactCompatibilityError(
            f"ensemble artifact is missing: {artifact_path}"
        ) from exc
    except (OSError, ValueError) as exc:
        raise ArtifactCompatibilityError(
            f"ensemble artifact is unreadable: {artifact_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ArtifactCompatibilityError("ensemble artifact is not a JSON object")
    return payload


def _component_path(
    ensemble_path: Path, artifacts: Mapping[str, Any], name: str,
) -> Path:
    value = artifacts.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ArtifactCompatibilityError(
            f"ensemble artifact mapping is missing {name!r}"
        )
    path = Path(value)
    return path if path.is_absolute() else ensemble_path.parent / path


def _ensemble_checksums(
    ensemble_path: Path,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    feature_keys = payload.get("feature_keys")
    if (
        not isinstance(feature_keys, list)
        or not feature_keys
        or not all(isinstance(key, str) and key for key in feature_keys)
        or len(set(feature_keys)) != len(feature_keys)
    ):
        raise ArtifactCompatibilityError("ensemble feature_keys are invalid")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ArtifactCompatibilityError("ensemble artifacts mapping is missing")

    model_version = str(payload.get("model_version") or "")
    if not model_version:
        raise ArtifactCompatibilityError("ensemble model_version is missing")
    lr_path = _component_path(ensemble_path, artifacts, "lr")
    gbm_path = _component_path(ensemble_path, artifacts, "gbm")
    inspect_model_artifact(
        lr_path,
        expected_artifact_kind="lr",
        expected_feature_count=len(feature_keys),
        expected_model_version=f"{model_version}::lr",
    )
    inspect_model_artifact(
        gbm_path,
        expected_artifact_kind="gbm",
        expected_feature_count=len(feature_keys),
        expected_model_version=f"{model_version}::gbm",
    )

    checksums = {"ensemble": canonical_payload_checksum(payload)}
    for name in REQUIRED_ENSEMBLE_ARTIFACTS:
        component = _component_path(ensemble_path, artifacts, name)
        if not component.is_file():
            raise ArtifactCompatibilityError(
                f"ensemble component is missing: {name}={component}"
            )
        checksums[name] = sha256_file(component)
    return checksums


def _window(
    payload_value: Any,
    *,
    start: Any,
    end: Any,
    n_samples: Any,
    method: str | None = None,
) -> dict[str, Any]:
    if isinstance(payload_value, Mapping):
        result = dict(payload_value)
    else:
        result = {"start": start, "end": end, "n_samples": n_samples}
    if method and "method" not in result:
        result["method"] = method
    return result


def _build_ensemble_manifest(
    row: Mapping[str, Any],
    *,
    market_type: str,
    ensemble_path: Path,
    payload: Mapping[str, Any],
    promotion_diagnostics: Mapping[str, Any],
) -> ModelManifest:
    model_version = str(row.get("model_version") or "")
    if not model_version or str(payload.get("model_version") or "") != model_version:
        raise ArtifactCompatibilityError(
            "ensemble model_version does not match the model_versions row"
        )
    payload_market = payload.get("market_type")
    if payload_market is not None and str(payload_market) != market_type:
        raise ArtifactCompatibilityError(
            f"ensemble market_type {payload_market!r} != {market_type!r}"
        )

    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ArtifactCompatibilityError("ensemble metrics are missing")
    brier = metrics.get("brier_ensemble")
    try:
        brier_value = float(brier)
    except (TypeError, ValueError) as exc:
        raise ArtifactCompatibilityError(
            "calibration diagnostics are missing brier_ensemble"
        ) from exc
    if not np.isfinite(brier_value):
        raise ArtifactCompatibilityError("brier_ensemble is not finite")

    train_start = row.get("train_window_start")
    train_end = row.get("train_window_end")
    training_window = _window(
        payload.get("training_window"),
        start=train_start,
        end=train_end,
        n_samples=metrics.get("n_train"),
    )
    oos_window = _window(
        payload.get("oos_window"),
        start=row.get("oos_window_start", train_start),
        end=row.get("oos_window_end", train_end),
        n_samples=metrics.get("n_oos_ensemble"),
        method="walk_forward_bounds",
    )
    checksums = _ensemble_checksums(ensemble_path, payload)
    checksum = aggregate_artifact_checksum(checksums)
    created_at = row.get("trained_at") or payload.get("trained_at")
    return ModelManifest.create(
        model_version=model_version,
        artifact_kind="ensemble",
        feature_keys=payload.get("feature_keys") or [],
        artifact_checksum=checksum,
        artifact_uri=ensemble_path,
        artifact_checksums=checksums,
        training_window=training_window,
        oos_window=oos_window,
        calibration_diagnostics={
            "method": "isotonic",
            "brier_ensemble": brier_value,
            "calibrator_artifacts": ["iso_lr", "iso_gbm"],
        },
        promotion_diagnostics=promotion_diagnostics,
        created_at=int(created_at) if created_at is not None else None,
    )


def _write_ensemble_manifest(
    ensemble_path: Path,
    payload: dict[str, Any],
    manifest: ModelManifest,
) -> None:
    updated = dict(payload)
    updated["manifest"] = manifest.to_dict()
    tmp = ensemble_path.with_suffix(ensemble_path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(updated, indent=2, default=float), encoding="utf-8"
        )
        tmp.replace(ensemble_path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _validate_ensemble_manifest(
    row: Mapping[str, Any],
    *,
    market_type: str,
    max_age_days: float | None = MAX_MODEL_AGE_DAYS,
) -> ModelManifest:
    ensemble_path = Path(str(row.get("artifact_path") or ""))
    payload = _read_ensemble_payload(ensemble_path)
    raw_manifest = payload.get("manifest")
    if not isinstance(raw_manifest, Mapping):
        raise ArtifactCompatibilityError("ensemble ModelManifest is missing")

    payload_version = str(payload.get("model_version") or "")
    row_version = str(row.get("model_version") or "")
    if payload_version != row_version:
        raise ArtifactCompatibilityError(
            "ensemble model_version does not match the promotion record"
        )
    payload_market = payload.get("market_type")
    if payload_market is not None and str(payload_market) != market_type:
        raise ArtifactCompatibilityError(
            f"ensemble market_type {payload_market!r} != {market_type!r}"
        )

    checksums = _ensemble_checksums(ensemble_path, payload)
    manifest = ModelManifest.from_dict(raw_manifest)
    manifest.validate(
        artifact_checksum=aggregate_artifact_checksum(checksums),
        expected_artifact_kind="ensemble",
        expected_feature_keys=payload.get("feature_keys") or [],
        expected_model_version=row_version,
        max_age_days=max_age_days,
        require_complete=True,
    )
    if manifest.artifact_checksums != checksums:
        raise ArtifactCompatibilityError("component artifact checksum mismatch")

    promotion = manifest.promotion_diagnostics
    for key in ("oos_wr", "deflated_sharpe", "pbo"):
        try:
            expected = float(row.get(key))
            recorded = float(promotion.get(key))
        except (TypeError, ValueError) as exc:
            raise ArtifactCompatibilityError(
                f"promotion diagnostics are missing {key}"
            ) from exc
        if not np.isclose(expected, recorded, rtol=0.0, atol=1e-12):
            raise ArtifactCompatibilityError(
                f"promotion diagnostic {key} does not match authorization record"
            )
    if promotion.get("promote") is not True:
        raise ArtifactCompatibilityError(
            "promotion diagnostics do not contain an eligible verdict"
        )
    return manifest


def evaluate_model_version(
    row: dict,
    *,
    market_type: str,
    min_oos: int | None = None,
    min_oos_wr: float = MIN_OOS_WR,
    min_dsr: float = MIN_DSR,
    max_pbo: float = MAX_PBO,
    min_auc: float = MIN_AUC,
    min_wr_uplift: float = MIN_WR_UPLIFT,
    require_manifest: bool = True,
    max_model_age_days: float | None = MAX_MODEL_AGE_DAYS,
) -> tuple[bool, str, dict]:
    """Decide whether `row` (a `model_versions` row + paired ensemble JSON) is
    fit to publish as the live `*_latest.json` pointer.

    Args:
        row: dict with keys `model_version, oos_wr, deflated_sharpe, pbo,
             artifact_path` — i.e. the columns of `core.warehouse.model_versions`.
        market_type: 'futures' or 'spot' — controls the n_oos floor.

    Returns:
        (promote, reason, diag) where `promote` is bool. On True the caller
        should rewrite `data/models/ensemble_{market}_latest.json` to point
        at `row['artifact_path']`.
    """
    floor_n = int(min_oos if min_oos is not None else MIN_OOS.get(market_type, 100))
    art_path = row.get("artifact_path") or ""
    n_oos = 0
    auc = float("nan")
    base_rate = float("nan")  # training-set positive base rate
    try:
        if art_path:
            payload = json.loads(Path(art_path).read_text())
            metrics = payload.get("metrics") or {}
            n_oos = int(metrics.get("n_oos_ensemble") or 0)
            auc = float(metrics.get("auc_ensemble") or float("nan"))
            # `wr_train` is the trainer-emitted base rate (mean of training
            # labels). Used to enforce min_wr_uplift.
            wr_train = metrics.get("wr_train")
            if wr_train is not None:
                base_rate = float(wr_train)
    except (FileNotFoundError, ValueError, OSError):
        pass

    oos_wr = float(row.get("oos_wr") or 0.0)
    wr_uplift = (
        oos_wr / base_rate if (np.isfinite(base_rate) and base_rate > 0) else float("nan")
    )

    diag = {
        "model_version": str(row.get("model_version")),
        "market_type": market_type,
        "artifact_path": str(art_path),
        "oos_wr": oos_wr,
        "deflated_sharpe": float(row.get("deflated_sharpe") or 0.0),
        "pbo": float(row.get("pbo") or 1.0),
        "n_oos": n_oos,
        "auc_ensemble": auc,
        "base_rate": base_rate,
        "wr_uplift": wr_uplift,
        "thresholds": {
            "min_oos": floor_n,
            "min_oos_wr": float(min_oos_wr),
            "min_auc": float(min_auc),
            "min_wr_uplift": float(min_wr_uplift),
            "min_dsr": float(min_dsr),
            "max_pbo": float(max_pbo),
        },
    }
    reasons: list[str] = []
    if diag["oos_wr"] < min_oos_wr:
        reasons.append(f"oos_wr {diag['oos_wr']:.3f} < {min_oos_wr:.2f}")
    # AUC is the load-bearing discrimination guard. NaN means the artifact
    # JSON didn't carry an `auc_ensemble` field — fail-closed.
    if not np.isfinite(auc) or auc < min_auc:
        reasons.append(f"AUC {auc} < {min_auc:.2f}")
    # WR-uplift over base rate. Research callers can explicitly bypass the
    # manifest boundary, but production authorization fails closed when the
    # training base rate is unavailable.
    if np.isfinite(wr_uplift) and wr_uplift < min_wr_uplift:
        reasons.append(
            f"wr_uplift {wr_uplift:.2f} < {min_wr_uplift:.2f} "
            f"(oos_wr={oos_wr:.3f}, base_rate={base_rate:.3f})"
        )
    elif require_manifest and not np.isfinite(wr_uplift):
        reasons.append("wr_uplift unavailable: training base rate is missing")
    if diag["deflated_sharpe"] < min_dsr:
        reasons.append(f"DSR {diag['deflated_sharpe']:.2f} < {min_dsr:.2f}")
    # PBO can be NaN when not enough folds — treat NaN as fail-closed.
    pbo_v = diag["pbo"]
    if not np.isfinite(pbo_v) or pbo_v > max_pbo:
        reasons.append(f"PBO {pbo_v} > {max_pbo:.2f}")
    if n_oos < floor_n:
        reasons.append(f"n_oos {n_oos} < {floor_n}")

    if require_manifest:
        try:
            manifest = _validate_ensemble_manifest(
                row,
                market_type=market_type,
                max_age_days=max_model_age_days,
            )
            diag["manifest_valid"] = True
            diag["manifest_version"] = manifest.manifest_version
            diag["feature_schema_hash"] = manifest.feature_schema_hash
            diag["artifact_checksum"] = manifest.artifact_checksum
        except (ArtifactCompatibilityError, OSError, ValueError) as exc:
            diag["manifest_valid"] = False
            diag["manifest_error"] = str(exc)
            reasons.append(f"manifest invalid: {exc}")
    else:
        diag["manifest_valid"] = None

    promote = (not reasons)
    reason = "OK" if promote else "; ".join(reasons)
    diag["promote"] = promote
    diag["reason"] = reason
    return promote, reason, diag


def promote_if_eligible(
    row: dict,
    *,
    market_type: str,
    models_dir: str | Path = "data/models",
    **kwargs,
) -> bool:
    """End-to-end: evaluate, log audit row, atomically rewrite *_latest.json on pass.

    The latest pointer is itself a small JSON file (NOT a symlink — Windows
    where the bot runs doesn't reliably support symlinks). Atomicity comes
    from write-to-temp + replace.
    """
    art_path = row.get("artifact_path") or ""
    metric_kwargs = dict(kwargs)
    metric_kwargs.pop("require_manifest", None)
    promote, _reason, diag = evaluate_model_version(
        row,
        market_type=market_type,
        require_manifest=False,
        **metric_kwargs,
    )
    if not promote:
        diag["ts"] = int(time.time())
        _audit(diag)
        return False

    try:
        ensemble_path = Path(str(art_path))
        payload_in = _read_ensemble_payload(ensemble_path)
        manifest = _build_ensemble_manifest(
            row,
            market_type=market_type,
            ensemble_path=ensemble_path,
            payload=payload_in,
            promotion_diagnostics=diag,
        )
        _write_ensemble_manifest(ensemble_path, payload_in, manifest)
        promote, _reason, diag = evaluate_model_version(
            row,
            market_type=market_type,
            require_manifest=True,
            **metric_kwargs,
        )
    except (ArtifactCompatibilityError, OSError, TypeError, ValueError) as exc:
        promote = False
        diag["promote"] = False
        diag["manifest_valid"] = False
        diag["manifest_error"] = str(exc)
        diag["reason"] = f"manifest invalid: {exc}"

    diag["ts"] = int(time.time())
    _audit(diag)
    if not promote:
        return False

    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    latest = models_dir / LATEST_TEMPLATE.format(market=market_type)
    tmp = latest.with_suffix(".json.tmp")
    payload = {
        "model_version": str(row.get("model_version")),
        "artifact_path": str(art_path),
        "promoted_at": int(time.time()),
        "diag": diag,
        "manifest": manifest.to_dict(),
    }
    tmp.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    tmp.replace(latest)
    return True


def validate_model_pointer(
    ptr: dict,
    *,
    market_type: str,
    max_age_days: int = MAX_MODEL_AGE_DAYS,
) -> tuple[bool, str, dict]:
    """Revalidate an existing ``ensemble_*_latest.json`` pointer before load.

    A pointer can become unsafe after thresholds tighten, after an artifact is
    manually edited, or simply because it is stale. Treat the pointer as a
    cached promotion decision, not a permanent authorization.
    """
    if not isinstance(ptr, dict):
        return False, "latest pointer is not a JSON object", {}

    diag_in = ptr.get("diag")
    if not isinstance(diag_in, dict):
        return False, "latest pointer missing promotion diagnostics", {
            "model_version": ptr.get("model_version"),
            "market_type": market_type,
        }

    art_path = ptr.get("artifact_path") or diag_in.get("artifact_path") or ""
    pointer_manifest = ptr.get("manifest")
    if not isinstance(pointer_manifest, dict):
        return False, "latest pointer missing ModelManifest", {
            "model_version": ptr.get("model_version"),
            "market_type": market_type,
            "artifact_path": art_path,
        }
    try:
        artifact_payload = _read_ensemble_payload(art_path)
    except ArtifactCompatibilityError as exc:
        return False, str(exc), {
            "model_version": ptr.get("model_version"),
            "market_type": market_type,
            "artifact_path": art_path,
        }
    if artifact_payload.get("manifest") != pointer_manifest:
        return False, "latest pointer ModelManifest does not match artifact", {
            "model_version": ptr.get("model_version"),
            "market_type": market_type,
            "artifact_path": art_path,
        }

    row = {
        "model_version": ptr.get("model_version") or diag_in.get("model_version"),
        "artifact_path": art_path,
        "oos_wr": diag_in.get("oos_wr"),
        "deflated_sharpe": diag_in.get("deflated_sharpe"),
        "pbo": diag_in.get("pbo"),
    }
    ok, reason, diag = evaluate_model_version(
        row,
        market_type=market_type,
        max_model_age_days=max_age_days,
    )

    promoted_at = float(ptr.get("promoted_at") or 0.0)
    now = time.time()
    age_days = (now - promoted_at) / 86_400.0 if promoted_at > 0 else float("inf")
    diag["promoted_at"] = promoted_at
    diag["pointer_age_days"] = age_days
    diag["max_model_age_days"] = int(max_age_days)

    if promoted_at <= 0:
        return False, "latest pointer missing promoted_at", diag
    if promoted_at > now + 300:
        return False, "latest pointer promoted_at is in the future", diag
    if age_days > max_age_days:
        return False, (
            f"latest pointer stale: {age_days:.1f}d > {int(max_age_days)}d"
        ), diag
    if not ok:
        return False, reason, diag
    return True, "OK", diag


def apply_split(percent_to_shadow: float, path: str | Path = "data/ab_split.json") -> None:
    """Write the A/B traffic-split flag for `bot_engine` to consume.

    `percent_to_shadow` is clamped to [0.0, 1.0]. The destination directory
    is created if it does not yet exist.
    """
    pct = float(percent_to_shadow)
    if pct < 0.0:
        pct = 0.0
    elif pct > 1.0:
        pct = 1.0

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"percent_to_shadow": pct}
    p.write_text(json.dumps(payload, indent=2))
