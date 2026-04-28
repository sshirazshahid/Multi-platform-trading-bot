"""Phase 13.3 — train an LR entry model on warehouse data.

Pipeline:
  1. Pull (features_json, realized_pnl) pairs from candidates JOIN trades
     where the trade is CLOSED and features_json has the full feature set.
  2. Parse into a numeric feature matrix (X) + win/loss labels (y).
  3. Walk-forward CV (anchored, n_splits configurable, embargo_bars=24).
  4. Per fold: fit LR on the train slice, evaluate OOS on the test slice.
  5. Aggregate OOS predictions across folds, compute AUC + Brier + WR
     at threshold 0.55 + Sharpe of paper-PnL on threshold-passers.
  6. Compute Deflated Sharpe Ratio (Bailey & López de Prado) over the
     `n_trials` regularization grid for selection-bias correction.
  7. Fit the final model on full data, persist to data/models/, write a
     row to the `model_versions` table.

Usage:
  python scripts/train_models.py            # default settings
  python scripts/train_models.py --quick    # n_splits=3 for faster iteration
  python scripts/train_models.py --tag v1   # filename suffix
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
from core.models import LRModel  # noqa: E402
from core.stat_tests import bootstrap_ci, deflated_sharpe, sharpe  # noqa: E402
from core.walk_forward import WalkForward  # noqa: E402
from core.warehouse import get_warehouse  # noqa: E402

# Numeric features extracted from candidates.features_json. Keep this
# stable across training runs so historical model_versions rows remain
# comparable.
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
# Regularization grid — small (Bonferroni budget), spans 4 orders of magnitude.
C_GRID = [0.01, 0.1, 1.0, 10.0]


def _coerce(v) -> float:
    """Cast bool/int/float/None into float; True→1, False/None→0."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        f = float(v)
        return 0.0 if not np.isfinite(f) else f
    except (TypeError, ValueError):
        return 0.0


def load_dataset(warehouse=None):
    """Returns (X, y, ts_entry, realized_pnl, symbols).
       X is (n, len(FEATURE_KEYS)) float64, y is {0,1}."""
    wh = warehouse if warehouse is not None else get_warehouse()
    rows = wh.query(
        "SELECT c.features_json, t.ts_entry, t.realized_pnl, t.symbol "
        "FROM candidates c JOIN trades t ON t.candidate_id = c.id "
        "WHERE t.status = 'CLOSED' AND length(c.features_json) > 100 "
        "  AND c.features_json LIKE '%adx_1h%' "
        "ORDER BY t.ts_entry"
    )
    X_rows: list[list[float]] = []
    y_rows: list[int] = []
    ts_rows: list[float] = []
    pnl_rows: list[float] = []
    sym_rows: list[str] = []
    for r in rows:
        try:
            feats = json.loads(r["features_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        vec = [_coerce(feats.get(k)) for k in FEATURE_KEYS]
        X_rows.append(vec)
        y_rows.append(1 if (r["realized_pnl"] or 0) > 0 else 0)
        ts_rows.append(float(r["ts_entry"]))
        pnl_rows.append(float(r["realized_pnl"] or 0))
        sym_rows.append(str(r["symbol"]))
    X = np.array(X_rows, dtype=float)
    y = np.array(y_rows, dtype=int)
    ts = np.array(ts_rows, dtype=float)
    pnl = np.array(pnl_rows, dtype=float)
    return X, y, ts, pnl, sym_rows


def _walk_forward_oos_predictions(X, y, *, n_splits: int, embargo_bars: int,
                                  C: float):
    """Run anchored walk-forward, return concatenated OOS predictions
    (only the test_idx rows ever covered) and the matching y/idx arrays."""
    wf = WalkForward(n_splits=n_splits, embargo_bars=embargo_bars, anchored=True)
    oos_proba: dict[int, float] = {}
    fold_metrics = []
    from sklearn.metrics import brier_score_loss, roc_auc_score
    # min_train_n=12: with ~66 rows / 4 splits the earliest train slice is
    # ~16 rows, so 12 keeps all folds in play. Below 12, the LR fit is
    # genuinely meaningless (n_features=15, so we'd be fitting more
    # parameters than samples). Test_n=4 because TimeSeriesSplit's earliest
    # test fold can be that small.
    for fold_i, (train_idx, test_idx) in enumerate(wf.split(X)):
        if len(train_idx) < 12 or len(test_idx) < 4:
            fold_metrics.append({"fold": fold_i, "skipped": True,
                                 "train_n": int(len(train_idx)),
                                 "test_n": int(len(test_idx))})
            continue
        try:
            m = LRModel(C=C).fit(X[train_idx], y[train_idx])
            p = m.p_win(X[test_idx])
        except ValueError as e:
            fold_metrics.append({"fold": fold_i, "skipped": True, "reason": str(e)})
            continue
        for i, idx in enumerate(test_idx):
            oos_proba[int(idx)] = float(p[i])
        try:
            auc = float(roc_auc_score(y[test_idx], p))
        except ValueError:
            auc = float("nan")
        brier = float(brier_score_loss(y[test_idx], p))
        fold_metrics.append({
            "fold": fold_i, "skipped": False,
            "train_n": int(len(train_idx)),
            "test_n": int(len(test_idx)),
            "auc": auc, "brier": brier,
        })
    return oos_proba, fold_metrics


def _summarise(y, oos_proba, pnl, *, n_trials: int):
    """OOS metrics across all folds + Deflated Sharpe."""
    if not oos_proba:
        return {"n_oos": 0}
    idx = sorted(oos_proba.keys())
    p = np.array([oos_proba[i] for i in idx])
    yv = y[idx]
    pnlv = pnl[idx]
    from sklearn.metrics import brier_score_loss, roc_auc_score
    out = {"n_oos": int(len(idx))}
    try:
        out["auc"] = float(roc_auc_score(yv, p))
    except ValueError:
        out["auc"] = float("nan")
    out["brier"] = float(brier_score_loss(yv, p))
    out["wr_at_0_55"] = float(yv[p >= 0.55].mean()) if (p >= 0.55).any() else float("nan")
    out["n_at_0_55"] = int((p >= 0.55).sum())
    # Paper-PnL of trades the model would have taken at p >= 0.55.
    paper = pnlv[p >= 0.55]
    out["paper_pnl_sum_at_0_55"] = float(paper.sum()) if paper.size else 0.0
    out["paper_sharpe_at_0_55"] = float(sharpe(paper)) if paper.size > 1 else 0.0
    if paper.size > 5:
        try:
            lo, hi = bootstrap_ci(paper, n_resamples=2000, ci=0.95, random_state=42)
            out["paper_pnl_ci95"] = (float(lo), float(hi))
        except Exception:
            pass
    out["deflated_sharpe"] = float(deflated_sharpe(
        sr_observed=out["paper_sharpe_at_0_55"],
        n_trials=int(n_trials),
        n_obs=max(2, int(paper.size)),
    ))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quick", action="store_true",
                        help="3-split CV instead of 4 (faster, less robust)")
    parser.add_argument("--tag", default=None,
                        help="filename suffix for the saved artifact")
    parser.add_argument("--no-save", action="store_true",
                        help="evaluate only; don't write model artifact or version row")
    args = parser.parse_args()

    print("=" * 64)
    print("Phase 13.3 — LR fit on warehouse")
    print("=" * 64)
    print(f"feature schema ({len(FEATURE_KEYS)}):", FEATURE_KEYS)

    X, y, ts, pnl, syms = load_dataset()
    n_total = X.shape[0]
    print(f"\ndataset: n={n_total}, "
          f"wins={int(y.sum())}, losses={int(n_total - y.sum())}, "
          f"WR={100*y.mean():.1f}%")
    print(f"feature variance: {X.var(axis=0).round(3).tolist()}")

    if n_total < 30:
        print("\nABORT: < 30 fitted-feature rows. Cannot fit reliably.")
        sys.exit(1)

    n_splits = 3 if args.quick else 4
    embargo_bars = 24

    # Regularization sweep — pick the C that maximises OOS AUC.
    print(f"\nWalk-forward CV (n_splits={n_splits}, embargo={embargo_bars})")
    print(f"C grid: {C_GRID}\n")
    best_C = None
    best_summary = None
    best_oos = None
    for C in C_GRID:
        oos, folds = _walk_forward_oos_predictions(
            X, y, n_splits=n_splits, embargo_bars=embargo_bars, C=C)
        summary = _summarise(y, oos, pnl, n_trials=len(C_GRID))
        ok = [f for f in folds if not f.get("skipped")]
        skipped = [f for f in folds if f.get("skipped")]
        print(
            f"  C={C:<6}  n_oos={summary.get('n_oos'):>3}  "
            f"AUC={summary.get('auc', float('nan')):.3f}  "
            f"Brier={summary.get('brier', float('nan')):.3f}  "
            f"WR@0.55={summary.get('wr_at_0_55', float('nan')):.0%} "
            f"(n={summary.get('n_at_0_55')})  "
            f"PaperPnL={summary.get('paper_pnl_sum_at_0_55', 0):+.2f}  "
            f"DSR={summary.get('deflated_sharpe', float('nan')):.2f}  "
            f"folds_ok={len(ok)}/{len(folds)}"
        )
        # Selector: prefer the C with the highest AUC AMONG those that
        # produce at least 1 signal passing the 0.55 deployment threshold.
        # This avoids picking a C that's so conservative it never trades
        # — useful AUC but worthless in production. Fall back to pure-AUC
        # if no C produces any deployable signals.
        usable_now = summary.get("n_at_0_55", 0) >= 1
        cur_auc = summary.get("auc", -1) or -1
        best_auc = best_summary.get("auc", -1) if best_summary else -1
        best_usable = (best_summary and best_summary.get("n_at_0_55", 0) >= 1)
        replace = False
        if best_summary is None:
            replace = True
        elif usable_now and not best_usable:
            replace = True
        elif usable_now == best_usable and cur_auc > best_auc:
            replace = True
        if replace:
            best_C = C
            best_summary = summary
            best_oos = oos

    print(f"\nbest C: {best_C}")
    print(f"best OOS summary:")
    for k, v in best_summary.items():
        print(f"  {k}: {v}")

    if args.no_save:
        print("\n--no-save: skipping artifact + version-row write")
        return

    # Final fit on all data with best_C
    final = LRModel(C=best_C).fit(X, y)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{args.tag}" if args.tag else ""
    art = ROOT / "data" / "models" / f"lr_v{suffix}_{ts_str}.pkl"
    # Phase 13.5b — embed model_version in the artifact so ShadowPredictor
    # can recover the canonical version name even when loading via the
    # `lr_v_latest.pkl` symlink/copy (Windows = always a copy).
    model_version = f"lr_v{suffix}_{ts_str}"
    final.save(art, model_version=model_version)
    latest = ROOT / "data" / "models" / "lr_v_latest.pkl"
    try:
        if latest.exists():
            latest.unlink()
        # Symlink not always available on Windows; copy as fallback.
        try:
            latest.symlink_to(art.name)
        except (OSError, NotImplementedError):
            import shutil
            shutil.copy2(art, latest)
    except Exception as e:
        print(f"  WARN could not update lr_v_latest.pkl: {e}")
    print(f"\nsaved final model: {art}")

    # Calibrator on OOS preds
    cal = IsotonicCalibrator(min_samples=50)
    if best_oos:
        idx = sorted(best_oos.keys())
        p = np.array([best_oos[i] for i in idx])
        yv = y[idx]
        cal.fit(p, yv)
        cal_path = ROOT / "data" / "models" / f"calibrator_v{suffix}_{ts_str}.pkl"
        cal.save(cal_path)
        print(f"saved calibrator (fitted={cal.is_fitted()}): {cal_path}")

    # Persist a model_versions row
    wh = get_warehouse()
    model_version = f"lr_v{suffix}_{ts_str}"
    if best_oos:
        ts_arr = np.array([ts[i] for i in sorted(best_oos.keys())])
        train_start = int(ts_arr.min())
        train_end = int(ts_arr.max())
    else:
        train_start = train_end = int(time.time())
    wh._conn().execute(
        "INSERT OR REPLACE INTO model_versions("
        "model_version, trained_at, train_window_start, train_window_end, "
        "oos_sharpe, oos_wr, deflated_sharpe, pbo, artifact_path"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            model_version,
            int(time.time()),
            train_start,
            train_end,
            float(best_summary.get("paper_sharpe_at_0_55", 0.0)),
            float(best_summary.get("wr_at_0_55", 0.0) or 0.0),
            float(best_summary.get("deflated_sharpe", 0.0)),
            None,
            str(art),
        ),
    )
    print(f"\ninserted model_versions row: {model_version}")


if __name__ == "__main__":
    main()
