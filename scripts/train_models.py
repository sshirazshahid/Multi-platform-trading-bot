"""Train the calibrated LR + GBM ensemble on warehouse triple-barrier labels.

Pipeline:
  1. Load (features, labels) by joining `labels` with `candidates.features_json`
     (preferred) plus the persisted `features` table for the extended schema.
     Falls back to the legacy `candidates JOIN trades` join if `labels` is
     empty so the trainer remains useful during initial bring-up.
  2. Split rows by market_type (futures vs spot). Train one model per market.
  3. Per market, run anchored walk-forward CV (n_splits=5, embargo_bars=24) on
     LR (regularization grid) + GBM (learning-rate grid). Per fold: fit, score
     OOS, accumulate (raw_proba, outcome) pairs.
  4. Fit IsotonicCalibrator on each model's OOS predictions.
  5. Solve constrained-least-squares for ensemble weights (LR vs GBM, w >= 0,
     sum to 1) on calibrated OOS probabilities.
  6. Write artifacts:
        data/models/{lr,gbm,iso_lr,iso_gbm}_{market}_{tag}.pkl
        data/models/ensemble_{market}_{tag}.json
     Insert one row per market into the warehouse `model_versions` table.

Usage:
  python scripts/train_models.py                           # both markets, default tag
  python scripts/train_models.py --market futures --tag v1
  python scripts/train_models.py --quick                   # n_splits=3
  python scripts/train_models.py --no-save                 # evaluate only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.calibration import IsotonicCalibrator  # noqa: E402
from core.models import GBMModel, LRModel  # noqa: E402
from core.stat_tests import bootstrap_ci, deflated_sharpe, pbo, sharpe  # noqa: E402
from core.walk_forward import WalkForward  # noqa: E402
from core.warehouse import get_warehouse  # noqa: E402

# Numeric features pulled from candidates.features_json. Keep stable so the
# model_versions row stays comparable across retrains. Mirrors the keys the
# live mcp_brain candidate snapshot has emitted historically.
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
# 2026-05-25 — microstructure forward-collection. The new features
# (oi_delta_6h, depth_ratio, basis_bps) are CAPTURED into candidate
# features_json now (mcp_brain._microstructure_features) but are
# DELIBERATELY NOT in FEATURE_KEYS yet: the live model artifact was trained
# on the current 15-key vector, and ShadowPredictor / model-gate build their
# inference vector from FEATURE_KEYS — adding keys while an older model is
# loaded causes a feature-count mismatch that breaks inference (13 shadow
# tests). Append these 3 keys HERE in the same change that retrains on the
# accumulated microstructure data (trigger: scripts/microstructure_readiness.py).
# FEATURE_KEYS and the model artifact MUST move together.
_MICROSTRUCTURE_FEATURES_PENDING = ["oi_delta_6h", "depth_ratio", "basis_bps"]
# Hyperparameter grids (kept small so the multiple-comparisons penalty in
# Deflated Sharpe stays defensible).
LR_C_GRID = [0.05, 0.5, 5.0]
GBM_LR_GRID = [0.03, 0.08]


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _coerce(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        f = float(v)
        return 0.0 if not np.isfinite(f) else f
    except (TypeError, ValueError):
        return 0.0


def _effective_embargo(requested: int, label_horizon: int) -> int:
    """Walk-forward embargo must be >= the label's forward horizon, else
    training rows within `label_horizon` bars of a test fold carry labels
    that peek into the test window (de Prado purging). 2026-05-25: the live
    ensemble was trained with embargo=24 < horizon=96 — the leak that let an
    overfit model (PBO=1.0) show an inflated OOS-WR. Clamp up, never down."""
    return max(int(requested), int(label_horizon))


def _load_label_horizon(market: str, default: int = 96) -> int:
    """Largest label_horizon_bars for this market from the warehouse labels."""
    import sqlite3
    try:
        c = sqlite3.connect("data/warehouse.sqlite")
        row = c.execute(
            "SELECT MAX(label_horizon_bars) FROM labels WHERE market_type=?",
            (market,)).fetchone()
        c.close()
        return int(row[0]) if row and row[0] else default
    except Exception:
        return default


def load_dataset(market_type: str, warehouse=None):
    """Returns (X, y, ts, syms). Prefers `labels JOIN candidates`; falls back
    to `candidates JOIN trades` if the labels table is empty for this market.

    The label table came online in this rebuild — early warehouses have only
    closed-trade rows joinable to candidates, so we keep the legacy path as
    a safety net. The trainer logs which path was used.
    """
    wh = warehouse if warehouse is not None else get_warehouse()
    rows = wh.query(
        "SELECT c.features_json, l.ts, l.symbol, l.y "
        "FROM labels l JOIN candidates c ON c.id = l.candidate_id "
        "WHERE l.market_type = ? AND length(c.features_json) > 100 "
        "  AND c.features_json LIKE '%adx_1h%' "
        "ORDER BY l.ts",
        (market_type,),
    )
    source = "labels"
    if not rows:
        rows = wh.query(
            "SELECT c.features_json, t.ts_entry AS ts, t.symbol, "
            "       CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END AS y "
            "FROM candidates c JOIN trades t ON t.candidate_id = c.id "
            "WHERE t.status = 'CLOSED' AND t.market_type = ? "
            "  AND length(c.features_json) > 100 "
            "  AND c.features_json LIKE '%adx_1h%' "
            "ORDER BY t.ts_entry",
            (market_type,),
        )
        source = "trades"

    X_rows: list[list[float]] = []
    y_rows: list[int] = []
    ts_rows: list[float] = []
    sym_rows: list[str] = []
    for r in rows:
        try:
            feats = json.loads(r["features_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        vec = [_coerce(feats.get(k)) for k in FEATURE_KEYS]
        X_rows.append(vec)
        y_rows.append(int(r["y"]))
        ts_rows.append(float(r["ts"]))
        sym_rows.append(str(r["symbol"]))
    X = np.array(X_rows, dtype=float) if X_rows else np.zeros((0, len(FEATURE_KEYS)))
    y = np.array(y_rows, dtype=int) if y_rows else np.zeros((0,), dtype=int)
    ts = np.array(ts_rows, dtype=float) if ts_rows else np.zeros((0,), dtype=float)
    return X, y, ts, sym_rows, source


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward driver (model-agnostic via build_model factory)
# ─────────────────────────────────────────────────────────────────────────────

def _walk_forward_oos(X, y, *, n_splits: int, embargo_bars: int, build_model):
    """Anchored walk-forward CV. Returns (oos_proba, fold_metrics, fold_pnl_matrix).

    `build_model` is a no-arg callable returning a fresh classifier instance.
    `fold_pnl_matrix` is a (n_splits, max_test_n) matrix of per-fold paper-PnL
    samples (NaN-padded) usable with `core.stat_tests.pbo`.
    """
    from sklearn.metrics import brier_score_loss, roc_auc_score

    wf = WalkForward(n_splits=n_splits, embargo_bars=embargo_bars, anchored=True)
    oos: dict[int, float] = {}
    folds: list[dict] = []
    fold_pnl: list[np.ndarray] = []

    for fold_i, (train_idx, test_idx) in enumerate(wf.split(X)):
        if len(train_idx) < 12 or len(test_idx) < 4:
            folds.append({"fold": fold_i, "skipped": True,
                          "train_n": int(len(train_idx)),
                          "test_n": int(len(test_idx))})
            continue
        try:
            m = build_model().fit(X[train_idx], y[train_idx])
            p = m.p_win(X[test_idx])
        except (ValueError, RuntimeError) as e:
            folds.append({"fold": fold_i, "skipped": True, "reason": str(e)})
            continue
        for i, idx in enumerate(test_idx):
            oos[int(idx)] = float(p[i])
        try:
            auc = float(roc_auc_score(y[test_idx], p))
        except ValueError:
            auc = float("nan")
        brier = float(brier_score_loss(y[test_idx], p))
        # Paper-PnL for the threshold = +1 win / -1 loss on each test row.
        pnl = np.where(y[test_idx] == 1, 1.0, -1.0)
        fold_pnl.append(pnl[(p >= 0.5)])  # decisions at the median, used by PBO
        folds.append({
            "fold": fold_i, "skipped": False,
            "train_n": int(len(train_idx)),
            "test_n": int(len(test_idx)),
            "auc": auc, "brier": brier,
        })

    if fold_pnl:
        max_n = max(len(p) for p in fold_pnl) or 1
        mat = np.full((len(fold_pnl), max_n), np.nan)
        for i, p in enumerate(fold_pnl):
            mat[i, :len(p)] = p
    else:
        mat = np.zeros((0, 0))
    return oos, folds, mat


def _summarise(y, oos_proba, *, n_trials: int):
    """OOS metrics across all folds + Deflated Sharpe at τ=0.55."""
    from sklearn.metrics import brier_score_loss, roc_auc_score
    if not oos_proba:
        return {"n_oos": 0}
    idx = sorted(oos_proba.keys())
    p = np.array([oos_proba[i] for i in idx])
    yv = y[idx]
    out: dict = {"n_oos": int(len(idx))}
    try:
        out["auc"] = float(roc_auc_score(yv, p))
    except ValueError:
        out["auc"] = float("nan")
    out["brier"] = float(brier_score_loss(yv, p))
    for tau in (0.55, 0.60, 0.65):
        mask = p >= tau
        out[f"wr_at_{tau:.2f}"] = float(yv[mask].mean()) if mask.any() else float("nan")
        out[f"n_at_{tau:.2f}"] = int(mask.sum())
    # Paper-pnl Sharpe at τ=0.55 using ±1 outcomes (we don't have realized
    # PnL here — labels are binary. The Sharpe of a {±1} stream still tracks
    # the directional edge, just on a normalized scale).
    paper = np.where(yv == 1, 1.0, -1.0)[p >= 0.55]
    out["paper_sharpe_at_0_55"] = float(sharpe(paper)) if paper.size > 1 else 0.0
    if paper.size > 5:
        try:
            lo, hi = bootstrap_ci(paper, n_resamples=2000, ci=0.95, random_state=42)
            out["paper_sharpe_ci95"] = (float(lo), float(hi))
        except Exception:
            pass
    out["deflated_sharpe"] = float(deflated_sharpe(
        sr_observed=out["paper_sharpe_at_0_55"],
        n_trials=int(n_trials),
        n_obs=max(2, int(paper.size)),
    ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble weights
# ─────────────────────────────────────────────────────────────────────────────

def _ensemble_weights(p_lr: np.ndarray, p_gbm: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Constrained least squares: minimize ||w_lr * p_lr + w_gbm * p_gbm - y||^2
    subject to w_lr + w_gbm = 1 and w_lr, w_gbm >= 0. Closed form: project the
    unconstrained optimum onto the simplex.
    """
    A = np.column_stack([p_lr, p_gbm])
    try:
        w, *_ = np.linalg.lstsq(A, y.astype(float), rcond=None)
    except np.linalg.LinAlgError:
        return 0.5, 0.5
    w = np.clip(w, 0.0, None)
    s = float(w.sum())
    if s <= 0:
        return 0.5, 0.5
    w = w / s
    return float(w[0]), float(w[1])


# ─────────────────────────────────────────────────────────────────────────────
# Main per-market trainer
# ─────────────────────────────────────────────────────────────────────────────

def train_one_market(market: str, *, tag: str, n_splits: int, embargo_bars: int,
                     no_save: bool, auto_promote: bool = False):
    print("=" * 72)
    print(f"TRAIN — market={market} tag={tag}")
    print("=" * 72)

    X, y, ts, syms, source = load_dataset(market)
    n_total = X.shape[0]
    if n_total == 0:
        print(f"ABORT: no rows for market={market}")
        return None

    # 2026-05-25 — clamp embargo >= label horizon (purge label leakage).
    _horizon = _load_label_horizon(market)
    _embargo_in = embargo_bars
    embargo_bars = _effective_embargo(embargo_bars, _horizon)
    if embargo_bars != _embargo_in:
        print(f"embargo clamped {_embargo_in} -> {embargo_bars} "
              f"(label horizon={_horizon}) to prevent CV leakage")
    n_pos = int(y.sum())
    n_neg = int(n_total - n_pos)
    print(f"data source: {source}")
    print(f"dataset: n={n_total}, wins={n_pos} ({n_pos/n_total:.1%}), "
          f"losses={n_neg} ({n_neg/n_total:.1%})")
    if n_total < 30:
        print("ABORT: < 30 rows. Cannot fit reliably.")
        return None

    # ── LR sweep ────────────────────────────────────────────────────
    print(f"\nLR sweep — C grid {LR_C_GRID}")
    best_lr = None  # (C, oos, summary, fold_pnl)
    for C in LR_C_GRID:
        oos, _folds, mat = _walk_forward_oos(
            X, y, n_splits=n_splits, embargo_bars=embargo_bars,
            build_model=lambda C=C: LRModel(C=C),
        )
        s = _summarise(y, oos, n_trials=len(LR_C_GRID))
        print(
            f"  C={C:<5}  n_oos={s.get('n_oos'):>4}  "
            f"AUC={s.get('auc', float('nan')):.3f}  "
            f"WR@0.55={s.get('wr_at_0.55', float('nan')) if not np.isnan(s.get('wr_at_0.55', float('nan'))) else float('nan'):.3f}  "
            f"DSR={s.get('deflated_sharpe', float('nan')):.2f}"
        )
        if (best_lr is None) or (s.get("auc", -1) > best_lr[2].get("auc", -1)):
            best_lr = (C, oos, s, mat)
    best_C, oos_lr, sum_lr, mat_lr = best_lr
    print(f"  best LR: C={best_C}  AUC={sum_lr.get('auc', float('nan')):.3f}")

    # ── GBM sweep ───────────────────────────────────────────────────
    print(f"\nGBM sweep — lr grid {GBM_LR_GRID}")
    best_gbm = None
    for lr in GBM_LR_GRID:
        oos, _folds, mat = _walk_forward_oos(
            X, y, n_splits=n_splits, embargo_bars=embargo_bars,
            build_model=lambda lr=lr: GBMModel(learning_rate=lr, max_iter=200),
        )
        s = _summarise(y, oos, n_trials=len(GBM_LR_GRID))
        print(
            f"  lr={lr:<5}  n_oos={s.get('n_oos'):>4}  "
            f"AUC={s.get('auc', float('nan')):.3f}  "
            f"WR@0.55={s.get('wr_at_0.55', float('nan')):.3f}  "
            f"DSR={s.get('deflated_sharpe', float('nan')):.2f}"
        )
        if (best_gbm is None) or (s.get("auc", -1) > best_gbm[2].get("auc", -1)):
            best_gbm = (lr, oos, s, mat)
    best_lrate, oos_gbm, sum_gbm, mat_gbm = best_gbm
    print(f"  best GBM: lr={best_lrate}  AUC={sum_gbm.get('auc', float('nan')):.3f}")

    # ── Calibrators on OOS preds ────────────────────────────────────
    cal_lr = IsotonicCalibrator(min_samples=50)
    cal_gbm = IsotonicCalibrator(min_samples=50)
    common_idx = sorted(set(oos_lr.keys()) & set(oos_gbm.keys()))
    if not common_idx:
        print("ABORT: LR and GBM share no OOS rows — cannot ensemble")
        return None
    p_lr = np.array([oos_lr[i] for i in common_idx])
    p_gbm = np.array([oos_gbm[i] for i in common_idx])
    y_oos = y[common_idx]
    cal_lr.fit(p_lr, y_oos)
    cal_gbm.fit(p_gbm, y_oos)

    p_lr_cal = cal_lr.transform(p_lr)
    p_gbm_cal = cal_gbm.transform(p_gbm)

    # ── Ensemble weights on calibrated OOS preds ────────────────────
    w_lr, w_gbm = _ensemble_weights(p_lr_cal, p_gbm_cal, y_oos)
    p_ens = w_lr * p_lr_cal + w_gbm * p_gbm_cal
    print(
        f"\nEnsemble weights: LR={w_lr:.2f}  GBM={w_gbm:.2f}\n"
        f"Ensemble OOS:"
    )
    from sklearn.metrics import brier_score_loss, roc_auc_score
    try:
        ens_auc = float(roc_auc_score(y_oos, p_ens))
    except ValueError:
        ens_auc = float("nan")
    ens_brier = float(brier_score_loss(y_oos, p_ens))
    ens_wr_55 = float(y_oos[p_ens >= 0.55].mean()) if (p_ens >= 0.55).any() else float("nan")
    ens_n_55 = int((p_ens >= 0.55).sum())
    print(f"  AUC={ens_auc:.3f}  Brier={ens_brier:.3f}  "
          f"WR@0.55={ens_wr_55:.3f} (n={ens_n_55})")

    # ── PBO on the ensemble fold-pnl matrix ─────────────────────────
    # Use the LR-best fold matrix as the PBO sample (GBM matrix could be
    # stacked in but with only 4 hyperparam combos and small folds the
    # PBO estimator is noisy; we report it as a directional signal).
    pbo_score = float("nan")
    if mat_lr.shape[0] >= 2 and mat_lr.shape[1] >= 4:
        try:
            # pbo() expects an (n_obs, n_strategies) matrix — stack LR fold
            # PnL across the C grid would be ideal, but with our grid sizing
            # we approximate via the chosen C's fold matrix (transposed).
            sample = mat_lr.T
            sample = sample[~np.all(np.isnan(sample), axis=1)]
            sample = np.where(np.isnan(sample), 0.0, sample)
            if sample.shape[0] >= 4 and sample.shape[1] >= 2:
                pbo_score = float(pbo(sample, n_partitions=min(8, sample.shape[0])))
        except Exception as e:
            print(f"  PBO compute skipped: {e}")
    print(f"  PBO ~ {pbo_score:.3f}")

    if no_save:
        print("\n--no-save: skipping artifact + version-row write")
        return None

    # ── Final fit on full data + persist ─────────────────────────────
    final_lr = LRModel(C=best_C).fit(X, y)
    final_gbm = GBMModel(learning_rate=best_lrate, max_iter=200).fit(X, y)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"{tag}_{ts_str}"
    art_dir = ROOT / "data" / "models"
    art_dir.mkdir(parents=True, exist_ok=True)

    lr_path = art_dir / f"lr_{market}_{suffix}.pkl"
    gbm_path = art_dir / f"gbm_{market}_{suffix}.pkl"
    iso_lr_path = art_dir / f"iso_lr_{market}_{suffix}.pkl"
    iso_gbm_path = art_dir / f"iso_gbm_{market}_{suffix}.pkl"
    ens_path = art_dir / f"ensemble_{market}_{suffix}.json"

    model_version = f"ens_{market}_{suffix}"
    final_lr.save(lr_path, model_version=model_version + "::lr")
    final_gbm.save(gbm_path, model_version=model_version + "::gbm")
    cal_lr.save(iso_lr_path)
    cal_gbm.save(iso_gbm_path)

    payload = {
        "model_version": model_version,
        "market_type": market,
        "trained_at": int(time.time()),
        "feature_keys": FEATURE_KEYS,
        "weights": {"lr": w_lr, "gbm": w_gbm, "mcp_rule": 0.25},
        "thresholds": {"futures": 0.55, "spot": 0.58}.get(market, 0.55),
        "artifacts": {
            "lr": str(lr_path.name),
            "gbm": str(gbm_path.name),
            "iso_lr": str(iso_lr_path.name),
            "iso_gbm": str(iso_gbm_path.name),
        },
        "metrics": {
            "auc_lr": sum_lr.get("auc"),
            "auc_gbm": sum_gbm.get("auc"),
            "auc_ensemble": ens_auc,
            "brier_ensemble": ens_brier,
            "wr_at_0_55_ensemble": ens_wr_55,
            "n_at_0_55_ensemble": ens_n_55,
            "deflated_sharpe_lr": sum_lr.get("deflated_sharpe"),
            "deflated_sharpe_gbm": sum_gbm.get("deflated_sharpe"),
            "pbo": pbo_score,
            "n_oos_ensemble": int(len(common_idx)),
            "n_train": n_total,
            "wr_train": float(y.mean()),
        },
    }
    ens_path.write_text(json.dumps(payload, indent=2, default=float))
    print("\nsaved artifacts:")
    print(f"  {lr_path}")
    print(f"  {gbm_path}")
    print(f"  {iso_lr_path}")
    print(f"  {iso_gbm_path}")
    print(f"  {ens_path}")

    # ── Insert model_versions row ───────────────────────────────────
    wh = get_warehouse()
    train_start = int(ts.min()) if len(ts) else int(time.time())
    train_end = int(ts.max()) if len(ts) else int(time.time())
    # Pick the better of LR/GBM for the headline DSR; gate uses ensemble AUC.
    # Headline DSR = the ensemble's deflated Sharpe at τ=0.55. Per-model
    # DSR is reported in the sweep table (see sum_lr, sum_gbm) but the
    # ensemble is what actually ships, so this is what the promotion gate
    # must judge against.
    paper_ens = np.where(y_oos == 1, 1.0, -1.0)[p_ens >= 0.55]
    if paper_ens.size > 1:
        ens_sharpe = float(sharpe(paper_ens))
        ens_dsr = float(deflated_sharpe(
            sr_observed=ens_sharpe,
            n_trials=len(LR_C_GRID) + len(GBM_LR_GRID),
            n_obs=int(paper_ens.size),
        ))
    else:
        ens_sharpe = 0.0
        ens_dsr = 0.0
    print(f"  ensemble paper Sharpe@0.55={ens_sharpe:.3f}  DSR={ens_dsr:.3f}")
    wh.record_model_version(
        model_version=model_version,
        trained_at=int(time.time()),
        train_window_start=train_start,
        train_window_end=train_end,
        oos_sharpe=ens_sharpe,
        oos_wr=float(ens_wr_55) if not np.isnan(ens_wr_55) else 0.0,
        deflated_sharpe=ens_dsr,
        pbo=float(pbo_score) if not np.isnan(pbo_score) else 1.0,
        artifact_path=str(ens_path),
    )
    print(f"inserted model_versions row: {model_version}")

    # ── Promotion gate (--auto-promote) ──────────────────────────────
    # Walk the just-written model_versions row through the gate. On pass,
    # data/models/ensemble_{market}_latest.json is rewritten atomically;
    # on fail, the prior pointer (if any) is left untouched. Either way an
    # audit row is appended to data/models/audit.jsonl.
    if auto_promote:
        from core.promotion_gate import promote_if_eligible
        # Reload the row we just inserted so we feed the gate the same shape
        # production would see (avoids field-name drift between record_model_version
        # and the gate's expectations).
        rows = wh.query(
            "SELECT * FROM model_versions WHERE model_version = ?",
            (model_version,),
        )
        if rows:
            promoted = promote_if_eligible(rows[0], market_type=market)
            print(f"promotion gate: {'PROMOTED' if promoted else 'HELD'} "
                  f"(see data/models/audit.jsonl for reasons)")
        else:
            print("promotion gate: SKIPPED — model_versions row vanished")
    return model_version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--market", choices=["futures", "spot", "both"], default="both")
    parser.add_argument("--tag", default="v1")
    parser.add_argument("--quick", action="store_true",
                        help="3-split CV instead of 5 (faster, less robust)")
    parser.add_argument("--no-save", action="store_true",
                        help="evaluate only; don't write model artifact or version row")
    parser.add_argument("--auto-promote", action="store_true",
                        help="after each market trains, run the promotion gate; "
                             "on pass rewrite data/models/ensemble_{market}_latest.json")
    args = parser.parse_args()

    n_splits = 3 if args.quick else 5
    embargo_bars = 24

    markets = ["futures", "spot"] if args.market == "both" else [args.market]
    for m in markets:
        train_one_market(
            m, tag=args.tag, n_splits=n_splits, embargo_bars=embargo_bars,
            no_save=args.no_save, auto_promote=args.auto_promote,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
