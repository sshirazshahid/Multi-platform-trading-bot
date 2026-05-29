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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np

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

LATEST_TEMPLATE = "ensemble_{market}_latest.json"
AUDIT_LOG = Path("data/models/audit.jsonl")


def _audit(payload: dict) -> None:
    """Append-only newline-delimited JSON of every promotion attempt."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=float) + "\n")


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
    # WR-uplift over base rate. Skip if base_rate is unknown (older
    # artifacts that don't carry `wr_train` — fail-open here so legacy
    # artifacts can still pass; new trainer always emits wr_train).
    if np.isfinite(wr_uplift) and wr_uplift < min_wr_uplift:
        reasons.append(
            f"wr_uplift {wr_uplift:.2f} < {min_wr_uplift:.2f} "
            f"(oos_wr={oos_wr:.3f}, base_rate={base_rate:.3f})"
        )
    if diag["deflated_sharpe"] < min_dsr:
        reasons.append(f"DSR {diag['deflated_sharpe']:.2f} < {min_dsr:.2f}")
    # PBO can be NaN when not enough folds — treat NaN as fail-closed.
    pbo_v = diag["pbo"]
    if not np.isfinite(pbo_v) or pbo_v > max_pbo:
        reasons.append(f"PBO {pbo_v} > {max_pbo:.2f}")
    if n_oos < floor_n:
        reasons.append(f"n_oos {n_oos} < {floor_n}")

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
    promote, reason, diag = evaluate_model_version(
        row, market_type=market_type, **kwargs
    )
    diag["ts"] = int(__import__("time").time())
    _audit(diag)

    if not promote:
        return False

    art_path = row.get("artifact_path") or ""
    if not art_path or not Path(art_path).exists():
        diag["reason"] = f"artifact missing: {art_path}"
        diag["promote"] = False
        _audit(diag)
        return False

    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    latest = models_dir / LATEST_TEMPLATE.format(market=market_type)
    tmp = latest.with_suffix(".json.tmp")
    payload = {
        "model_version": str(row.get("model_version")),
        "artifact_path": str(art_path),
        "promoted_at": int(__import__("time").time()),
        "diag": diag,
    }
    tmp.write_text(json.dumps(payload, indent=2, default=float))
    tmp.replace(latest)
    return True


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
