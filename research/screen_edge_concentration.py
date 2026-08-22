"""Screen 31 — Is the 0.031 OOS AUC excess UNIFORM or CONCENTRATED?

Executes the FROZEN pre-registration
`_workspace/strategy_pipeline/31_prereg_edge_concentration.md`
(body SHA-256 48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1,
commit 9e52bd92eacef7a52ef04dd03a1449ff0867461d) EXACTLY.

READ-ONLY on the bot. The warehouse is opened `mode=ro` through a shim passed to
`scripts.train_models.load_dataset`; no `core/`, `config.py`, `.env` or process is
touched. `scripts/train_models.py` is imported, never edited.

Stage order is the frozen order (§5 → §8 unit check → §10 MDE → §11 verdict):

  S0  harvest + measured geometry (vs the §7 frozen table)
  S1  REPRODUCTION GATE — pooled OOS AUC must land in [0.520, 0.541]
  S2  machinery validation (Mann-Whitney day-block decomposition == roc_auc_score)
  S3  MANDATORY unit check — synthetic per-bucket-offset score must give near-uniform
      omnibus p (the executable proof the Simpson / base-rate artifact is neutralised)
  S4  measured MDE (§10) — power curve, NO_ANSWER: underpowered if MDE > 0.80
  S5  main analysis on the real ensemble score
  S6  mechanical four-state verdict (§11)

Inference is a panel-synchronous circular moving-block bootstrap over UTC day-blocks
(§8). Hanley-McNeil / DeLong / row-level bootstrap are PROHIBITED and are not
implemented anywhere in this file.

Usage:
    venv/Scripts/python.exe research/screen_edge_concentration.py
    venv/Scripts/python.exe research/screen_edge_concentration.py --smoke
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.calibration import IsotonicCalibrator  # noqa: E402
from core.decision.monte_carlo import block_bootstrap  # noqa: E402  (circular block resampler)
from core.models import GBMModel, LRModel  # noqa: E402
from scripts.train_models import (  # noqa: E402
    GBM_LR_GRID,
    LR_C_GRID,
    _effective_embargo,
    _ensemble_weights,
    _walk_forward_oos,
    load_dataset,
)

# ─────────────────────────────────────────────────────────────────────────────
# FROZEN CONSTANTS — every one of these is fixed by the pre-registration.
# Changing any of them is an invalidating deviation (§15).
# ─────────────────────────────────────────────────────────────────────────────
PREREG_SHA256 = "48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1"
PREREG_PATH = ROOT / "_workspace" / "strategy_pipeline" / "31_prereg_edge_concentration.md"

MARKET = "futures"
TS_MAX = 1784370569.0                 # §5 train_window_end of ens_futures_v1_20260725_171507
N_SPLITS = 5                          # §5
EMBARGO_REQUESTED = 24                # §5 -> _effective_embargo(24, 96) = 96
LABEL_HORIZON = 96                    # §5/§14 DEFAULT_TIME_BARS["futures"] (hardcoded; do NOT
#                                       call _load_label_horizon: it opens the live DB rw)
GBM_RANDOM_STATE = 0                  # §5 verbatim ("GBMModel(random_state=0)")
SEED = 20260726                       # §5/§8

REPRO_LO, REPRO_HI = 0.520, 0.541     # §5 reproduction gate band
REPRO_REFERENCE = 0.5305171091569454  # §5 recorded pooled OOS AUC

CUT_ADX_4H = 23.20                    # §6 frozen harvest median, X[:, 7]
CUT_ATR_PCT_1H = 0.94                 # §6 frozen harvest median, X[:, 8]
IDX_ADX_4H = 7                        # §6 FEATURE_KEYS[7]
IDX_ATR_PCT_1H = 8                    # §6 FEATURE_KEYS[8]
MAJOR_BASES = ("BTC", "ETH", "BNB", "SOL", "XRP")   # §6 frozen 5-major basket

FLOOR_N = 12_000                      # §7.1
FLOOR_MINORITY = 2_500                # §7.2
FLOOR_DAYS = 20                       # §7.3
FLOOR_DAYS_PER_HALF = 8               # §7.3
FLOOR_SYMBOLS = 20                    # §7.4
FLOOR_SYMBOLS_MAJOR = 5               # §7.4 (structural)
MIN_QUALIFYING_BUCKETS = 4            # §7 binding failure clause

B_BOOT = 20_000                       # §8
BLOCK_LEN_PRIMARY = 1                 # §8 (30 day-units)
BLOCK_PAIR_2DAY = 2                   # §11 cond 7 (15 paired units)

M_MULTIPLICITY = 16                   # §9
ALPHA_TEST = 0.05 / M_MULTIPLICITY    # = 0.003125
LCB_PCTL = 100.0 * ALPHA_TEST         # = 0.3125th percentile
EQUIV_LEVEL = 99.375                  # §9 equivalence leg, two-sided simultaneous
EQUIV_LO_PCTL = (100.0 - EQUIV_LEVEL) / 2.0        # 0.3125
EQUIV_HI_PCTL = 100.0 - EQUIV_LO_PCTL              # 99.6875

THR_A = 0.560                         # §9/§11 cond 2 practical-effect floor on A_b
THR_D = 0.030                         # §9/§11 cond 2 practical-effect floor on D_b
THR_LCB = 0.60                        # §11 cond 3 and cond 7
THR_NARROW_SHARE = 0.25               # §11 cond 4
THR_HALF_A = 0.540                    # §11 cond 5
THR_HALF_D = 0.015                    # §11 cond 5
THR_THIRD_A = 0.60                    # §11 cond 6
THR_THIRDS_REQUIRED = 2               # §11 cond 6 (2 of 3)
EQUIV_BAND = 0.030                    # §11 state 3
BASE_RATE_ARTIFACT_A = 0.510          # §11 state 3 sub-flag

MDE_DELTAS = (0.55, 0.60, 0.65, 0.70, 0.75)   # §10
MDE_SIMS = 200                                # §10
MDE_POWER = 0.80                              # §10
MDE_NO_ANSWER_ABOVE = 0.80                    # §10

UNIT_CHECK_DRAWS = 200                        # execution decision — see SPEC_GAPS

# §11 cond 6 thirds — explicit CALENDAR boundaries, verbatim from the prereg.
THIRD_BOUNDS = ("2026-06-23", "2026-07-05")

# ─────────────────────────────────────────────────────────────────────────────
# SPECIFICATION GAPS — clauses the frozen prereg leaves under-specified.
# Each is resolved here by a named constant DECLARED BEFORE THE RUN, and is
# reported in its own section of the output. These are execution decisions,
# NOT deviations from a specified value.
# ─────────────────────────────────────────────────────────────────────────────
SPEC_GAPS = [
    {
        "id": "SG1",
        "clause": "§10 MDE — 'inject ... into one bucket' never names which bucket.",
        "decision": (
            "Primary target = the smallest P1 cell that clears the §11 cond-4 <=25% "
            "narrowness cap (smallest = hardest to detect = conservative MDE). "
            "Secondary target = P2 MAJOR (fewest symbols, largest single-symbol share). "
            "The REPORTED MDE is the larger (more conservative) of the two."
        ),
    },
    {
        "id": "SG2",
        "clause": "§10 — 'detected' is not defined.",
        "decision": (
            "Detected = terminal state CONCENTRATED **and** the single qualifying bucket "
            "is the injected one (faithful: the full decision rule must fire on it). "
            "The softer diagnostic 'injected bucket cleared conditions 1-7 regardless of "
            "terminal state' is reported alongside, so an artifactual power loss (a second "
            "bucket qualifying because injection into a P1 cell perturbs P2) is visible."
        ),
    },
    {
        "id": "SG3",
        "clause": "§8 — 'near-uniform omnibus p' has no numeric pass criterion.",
        "decision": (
            f"{UNIT_CHECK_DRAWS} independent synthetic draws; PASS iff the empirical "
            "P(p <= 0.05) <= 0.10. A single draw cannot evidence uniformity of a p-value."
        ),
    },
    {
        "id": "SG4",
        "clause": "§7.3 / §11 cond 5 — 'chronological half' is not defined.",
        "decision": (
            "Halves = the ORDERED LIST of populated OOS UTC days split 15/15 (consistent "
            "with §11 cond 7, which says 'ordered list' explicitly). Thirds keep the "
            "explicit CALENDAR boundaries §11 cond 6 gives."
        ),
    },
    {
        "id": "SG5",
        "clause": "§8 — omnibus replicate weights are written as n_k/N_P without saying "
                  "whether n_k is the observed or the replicate count.",
        "decision": "Fixed OBSERVED n_k/N_P for both observed and replicate T (literal reading, "
                    "lower variance).",
    },
    {
        "id": "SG6",
        "clause": "§11 cond 7 — '2-day blocks pair consecutive entries ... -> 15 blocks'.",
        "decision": "Days are paired (1-2, 3-4, ...) into 15 fixed super-clusters, then the "
                    "same circular moving-block bootstrap runs over those 15 units.",
    },
    {
        "id": "SG7",
        "clause": "§5 specifies GBMModel(random_state=0); scripts/train_models.py uses the "
                  "class default random_state=42, and sklearn's early_stopping='auto' fires "
                  "above 10k rows so the choice moves p_ens.",
        "decision": (
            "The prereg is the binding document: random_state=0 is used. If the reproduction "
            "gate FAILS, the mechanical outcome is NO_ANSWER: reproduction — the seed is NOT "
            "switched to obtain a passing gate (that would be the sample/threshold tuning §12 "
            "lists as invalidating). A random_state=42 pooled AUC is then additionally reported, "
            "labelled EXPLORATORY, purely so the owner can see whether the halt came from the "
            "seed inconsistency rather than data drift."
        ),
    },
]

OUT_JSON = ROOT / "_workspace" / "strategy_pipeline" / "31_screen_edge_concentration.json"
OUT_MD = ROOT / "_workspace" / "strategy_pipeline" / "31_screen_edge_concentration.md"

RESULTS: dict = {}


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def _flush() -> None:
    """Write the JSON incrementally so a killed session still leaves staged evidence."""
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(RESULTS, indent=2, default=_jsonable), encoding="utf-8")


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


# ─────────────────────────────────────────────────────────────────────────────
# read-only warehouse shim (§13.10)
# ─────────────────────────────────────────────────────────────────────────────
class ReadOnlyWarehouse:
    def __init__(self, path: Path):
        uri = f"file:{path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, timeout=60)
        self._conn.row_factory = sqlite3.Row

    def query(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def close(self):
        self._conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Mann-Whitney AUC decomposed over day-blocks.
#
# AUC over any multiset of days with counts c is EXACTLY
#     U(c) = c' M c ,  n_pos(c) = c'P ,  n_neg(c) = c'N ,  AUC = U / (n_pos n_neg)
# where M[i][j] = #{(pos in day i, neg in day j) : s_pos > s_neg} + 0.5 * #{ties}.
# This is the tie-aware Mann-Whitney statistic, i.e. exactly roc_auc_score
# (validated in S2), and it makes B=20,000 cluster-bootstrap replicates a
# (B,30)@(30,30) matmul instead of 20,000 sorts.
#
# NOTE: there is no orientation flip anywhere in this file. A bucket that
# measures A_b = 0.487 is reported as 0.487 (§5).
# ─────────────────────────────────────────────────────────────────────────────
def build_M(row_idx: np.ndarray, scores: np.ndarray, y: np.ndarray,
            day: np.ndarray, n_days: int):
    s = scores[row_idx]
    yy = y[row_idx]
    d = day[row_idx]
    pos_by_day: list[np.ndarray] = []
    neg_by_day: list[np.ndarray] = []
    for k in range(n_days):
        m = d == k
        sk = s[m]
        yk = yy[m]
        pos_by_day.append(np.sort(sk[yk == 1]))
        neg_by_day.append(np.sort(sk[yk == 0]))
    P = np.array([p.size for p in pos_by_day], dtype=float)
    N = np.array([n.size for n in neg_by_day], dtype=float)
    M = np.zeros((n_days, n_days), dtype=float)
    for i in range(n_days):
        p = pos_by_day[i]
        if p.size == 0:
            continue
        for j in range(n_days):
            ng = neg_by_day[j]
            if ng.size == 0:
                continue
            lo = int(np.searchsorted(ng, p, side="left").sum(dtype=np.int64))
            hi = int(np.searchsorted(ng, p, side="right").sum(dtype=np.int64))
            M[i, j] = lo + 0.5 * (hi - lo)
    return M, P, N


def auc_from_M(M, P, N, c) -> float:
    c = np.asarray(c, dtype=float)
    npos = float(c @ P)
    nneg = float(c @ N)
    if npos <= 0 or nneg <= 0:
        return float("nan")
    return float(c @ M @ c) / (npos * nneg)


def auc_boot(M, P, N, C) -> np.ndarray:
    """Vectorised AUC over a (B, n_days) matrix of day multiplicities."""
    npos = C @ P
    nneg = C @ N
    U = np.einsum("bi,bi->b", C @ M, C)
    denom = npos * nneg
    out = np.full(C.shape[0], np.nan)
    ok = denom > 0
    out[ok] = U[ok] / denom[ok]
    return out


def pair_blocks(M, P, N, n_days: int):
    """Aggregate the 30x30 day machinery into the 15x15 two-day machinery (SG6)."""
    n_pairs = (n_days + 1) // 2
    A = np.zeros((n_pairs, n_days))
    for k in range(n_days):
        A[k // 2, k] = 1.0
    return A @ M @ A.T, A @ P, A @ N


def counts_matrix(n_units: int, block_len: int, b: int, seed: int) -> np.ndarray:
    """Circular moving-block bootstrap over the ORDERED unit list -> multiplicity counts.

    Reuses core.decision.monte_carlo.block_bootstrap (the repo's circular block
    resampler) by resampling the unit INDEX sequence.
    """
    draws = block_bootstrap(np.arange(n_units, dtype=float), block_len, b, seed)
    idx = draws.astype(np.int64)
    flat = (np.arange(idx.shape[0])[:, None] * n_units + idx).ravel()
    return np.bincount(flat, minlength=idx.shape[0] * n_units).reshape(
        idx.shape[0], n_units
    ).astype(float)


# ─────────────────────────────────────────────────────────────────────────────
# S0 — harvest
# ─────────────────────────────────────────────────────────────────────────────
def stage0_harvest():
    _log("S0 — harvest")
    wh = ReadOnlyWarehouse(ROOT / "data" / "warehouse.sqlite")
    X, y, ts, syms, source = load_dataset(MARKET, warehouse=wh)
    wh.close()
    keep = ts <= TS_MAX                        # §5, the one additional restriction
    X, y, ts = X[keep], y[keep], ts[keep]
    syms = [s for s, k in zip(syms, keep) if k]
    n = X.shape[0]
    test_size = n // 6
    oos_start = n - 5 * test_size
    _log(f"    harvest n={n}  source={source}  test_size={test_size}  oos_start={oos_start}")
    RESULTS["stage0_harvest"] = {
        "market": MARKET,
        "source_path": source,
        "ts_max_applied": TS_MAX,
        "n_harvest": int(n),
        "n_harvest_prereg_s14": 325047,
        "test_size": int(test_size),
        "test_size_prereg_s14": 54174,
        "oos_start": int(oos_start),
        "oos_start_prereg_s14": 54177,
        "harvest_median_adx_4h_measured": float(np.median(X[:, IDX_ADX_4H])),
        "harvest_median_atr_pct_1h_measured": float(np.median(X[:, IDX_ATR_PCT_1H])),
        "frozen_cut_adx_4h": CUT_ADX_4H,
        "frozen_cut_atr_pct_1h": CUT_ATR_PCT_1H,
        "note": (
            "Cuts are FROZEN CONSTANTS. The measured medians are reported for transparency "
            "only and are NOT used — re-deriving them would be an invalidating deviation."
        ),
    }
    _flush()
    return X, y, ts, np.array(syms, dtype=object)


# ─────────────────────────────────────────────────────────────────────────────
# S1 — reproduce the ensemble OOS score, then the reproduction gate
# ─────────────────────────────────────────────────────────────────────────────
def build_p_ens(X, y, gbm_random_state: int):
    from sklearn.metrics import roc_auc_score

    embargo = _effective_embargo(EMBARGO_REQUESTED, LABEL_HORIZON)

    def _auc(oos):
        idx = sorted(oos.keys())
        return float(roc_auc_score(y[idx], np.array([oos[i] for i in idx])))

    best_lr = None
    for C in LR_C_GRID:
        oos, _f, _m = _walk_forward_oos(
            X, y, n_splits=N_SPLITS, embargo_bars=embargo,
            build_model=lambda C=C: LRModel(C=C),
        )
        a = _auc(oos)
        _log(f"    LR C={C:<6} OOS AUC={a:.6f}")
        if best_lr is None or a > best_lr[2]:
            best_lr = (C, oos, a)

    best_gbm = None
    for lr in GBM_LR_GRID:
        oos, _f, _m = _walk_forward_oos(
            X, y, n_splits=N_SPLITS, embargo_bars=embargo,
            build_model=lambda lr=lr: GBMModel(
                learning_rate=lr, max_iter=200, random_state=gbm_random_state
            ),
        )
        a = _auc(oos)
        _log(f"    GBM lr={lr:<6} OOS AUC={a:.6f}")
        if best_gbm is None or a > best_gbm[2]:
            best_gbm = (lr, oos, a)

    oos_lr, oos_gbm = best_lr[1], best_gbm[1]
    common_idx = sorted(set(oos_lr) & set(oos_gbm))
    p_lr = np.array([oos_lr[i] for i in common_idx])
    p_gbm = np.array([oos_gbm[i] for i in common_idx])
    y_oos = y[common_idx]
    cal_lr, cal_gbm = IsotonicCalibrator(min_samples=50), IsotonicCalibrator(min_samples=50)
    cal_lr.fit(p_lr, y_oos)
    cal_gbm.fit(p_gbm, y_oos)
    p_lr_c = cal_lr.transform(p_lr)
    p_gbm_c = cal_gbm.transform(p_gbm)
    w_lr, w_gbm = _ensemble_weights(p_lr_c, p_gbm_c, y_oos)
    p_ens = w_lr * p_lr_c + w_gbm * p_gbm_c
    return {
        "idx": np.array(common_idx),
        "p_ens": p_ens,
        "y": y_oos,
        "auc": float(roc_auc_score(y_oos, p_ens)),
        "best_C": best_lr[0], "auc_lr": best_lr[2],
        "best_gbm_lr": best_gbm[0], "auc_gbm": best_gbm[2],
        "w_lr": w_lr, "w_gbm": w_gbm,
        "embargo": embargo,
    }


def stage1_reproduction(X, y, ts, syms):
    _log("S1 — rebuilding the OOS ensemble score (5 walk-forward sweeps)")
    t0 = time.time()
    ens = build_p_ens(X, y, GBM_RANDOM_STATE)
    n = X.shape[0]
    test_size = n // 6
    idx = ens["idx"]
    geom_ok = (int(idx.min()) == n - 5 * test_size) and (len(idx) == 5 * test_size)
    passed = REPRO_LO <= ens["auc"] <= REPRO_HI
    _log(f"    pooled OOS AUC = {ens['auc']:.10f}  gate[{REPRO_LO},{REPRO_HI}] -> "
         f"{'PASS' if passed else 'FAIL'}   ({time.time() - t0:.0f}s)")
    RESULTS["stage1_reproduction"] = {
        "pooled_oos_auc": ens["auc"],
        "reference_auc": REPRO_REFERENCE,
        "gate_band": [REPRO_LO, REPRO_HI],
        "passed": bool(passed),
        "n_oos": int(len(idx)),
        "n_oos_prereg_s14": 270870,
        "fold_geometry_assert_ok": bool(geom_ok),
        "gbm_random_state": GBM_RANDOM_STATE,
        "best_lr_C": ens["best_C"], "auc_lr": ens["auc_lr"],
        "best_gbm_lr": ens["best_gbm_lr"], "auc_gbm": ens["auc_gbm"],
        "ensemble_weights": {"lr": ens["w_lr"], "gbm": ens["w_gbm"]},
        "embargo_bars": ens["embargo"],
        "seconds": round(time.time() - t0, 1),
    }
    _flush()
    if not passed:
        # SG7 pre-committed branch: report the alternate-seed AUC as an EXPLORATORY
        # diagnostic only. The verdict is NO_ANSWER: reproduction either way.
        _log("    gate FAILED — computing EXPLORATORY random_state=42 diagnostic")
        try:
            alt = build_p_ens(X, y, 42)
            RESULTS["stage1_reproduction"]["exploratory_random_state_42_auc"] = alt["auc"]
        except Exception as e:  # pragma: no cover
            RESULTS["stage1_reproduction"]["exploratory_random_state_42_auc"] = f"failed: {e}"
        _flush()
    return ens


# ─────────────────────────────────────────────────────────────────────────────
# bucket construction (§6)
# ─────────────────────────────────────────────────────────────────────────────
def _base_of(sym: str) -> str:
    s = str(sym).upper().replace(":USDT", "")
    if "/" in s:
        return s.split("/")[0]
    for q in ("USDT", "USD", "USDC"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def build_buckets(Xo, symo):
    adx_hi = Xo[:, IDX_ADX_4H] >= CUT_ADX_4H
    atr_hi = Xo[:, IDX_ATR_PCT_1H] >= CUT_ATR_PCT_1H
    major = np.array([_base_of(s) in MAJOR_BASES for s in symo])
    return [
        {"name": "P1_ADXlo_ATRlo", "partition": "P1", "mask": (~adx_hi) & (~atr_hi)},
        {"name": "P1_ADXlo_ATRhi", "partition": "P1", "mask": (~adx_hi) & atr_hi},
        {"name": "P1_ADXhi_ATRlo", "partition": "P1", "mask": adx_hi & (~atr_hi)},
        {"name": "P1_ADXhi_ATRhi", "partition": "P1", "mask": adx_hi & atr_hi},
        {"name": "P2_MAJOR", "partition": "P2", "mask": major},
        {"name": "P2_ALT", "partition": "P2", "mask": ~major},
    ]


def check_floors(bk, y_o, symo, day, day_half, n_days_total):
    m = bk["mask"]
    n = int(m.sum())
    npos = int(y_o[m].sum())
    minority = min(npos, n - npos)
    days = np.unique(day[m])
    d1 = int((day_half[days] == 0).sum())
    d2 = int((day_half[days] == 1).sum())
    nsym = int(len(set(symo[m])))
    need_sym = FLOOR_SYMBOLS_MAJOR if bk["name"] == "P2_MAJOR" else FLOOR_SYMBOLS
    checks = {
        "n_rows": bool(n >= FLOOR_N),
        "minority": bool(minority >= FLOOR_MINORITY),
        "days_total": bool(len(days) >= FLOOR_DAYS),
        "days_per_half": bool(d1 >= FLOOR_DAYS_PER_HALF and d2 >= FLOOR_DAYS_PER_HALF),
        "symbols": bool(nsym >= need_sym),
    }
    _, cnt = np.unique(symo[m].astype(str), return_counts=True)
    return {
        "n": n, "n_pos": npos, "minority": minority,
        "share_of_oos": n / len(y_o),
        "days": int(len(days)), "days_first_half": d1, "days_second_half": d2,
        "symbols": nsym, "symbol_floor_required": need_sym,
        "max_single_symbol_share": float(cnt.max() / n) if n else 0.0,
        "checks": checks,
        "qualifies": bool(all(checks.values())),
    }


# ─────────────────────────────────────────────────────────────────────────────
# the frozen decision rule (§11), evaluated mechanically on any score vector
# ─────────────────────────────────────────────────────────────────────────────
class Analyzer:
    """Holds everything score-independent; `evaluate(scores)` runs the frozen rule."""

    def __init__(self, y_o, day, n_days, buckets, floors, C1, C2, half_mask, third_of_day):
        self.y = y_o
        self.day = day
        self.n_days = n_days
        self.buckets = buckets
        self.floors = floors
        self.C1 = C1                    # (B, n_days) 1-day-block multiplicities
        self.C2 = C2                    # (B, n_pairs) 2-day-block multiplicities
        self.half_mask = half_mask      # (2, n_days) bool over the ORDERED day list
        self.third_of_day = third_of_day  # (n_days,) in {0,1,2} by CALENDAR date
        self.n_oos = len(y_o)
        self.row_idx = {b["name"]: np.flatnonzero(b["mask"]) for b in buckets}
        # complements are taken WITHIN the bucket's own partition (§8.2); both
        # partitions are exhaustive over the OOS rows.
        self.comp_idx = {}
        for b in buckets:
            same = np.zeros(self.n_oos, dtype=bool)
            for o in buckets:
                if o["partition"] == b["partition"] and o["name"] != b["name"]:
                    same |= o["mask"]
            self.comp_idx[b["name"]] = np.flatnonzero(same)

    def _M(self, idx, scores):
        return build_M(idx, scores, self.y, self.day, self.n_days)

    def evaluate(self, scores, *, want_omnibus=True, short_circuit=True, partitions=None):
        ones = np.ones(self.n_days)
        parts = partitions or ("P1", "P2")
        per: dict[str, dict] = {}
        for b in self.buckets:
            name = b["name"]
            if b["partition"] not in parts:
                continue
            f = self.floors[name]
            if not f["qualifies"]:
                per[name] = {"status": "INSUFFICIENT_DATA"}
                continue
            Mb, Pb, Nb = self._M(self.row_idx[name], scores)
            Mc, Pc, Nc = self._M(self.comp_idx[name], scores)
            A = auc_from_M(Mb, Pb, Nb, ones)
            Ac = auc_from_M(Mc, Pc, Nc, ones)
            D = A - Ac
            Ab_rep = auc_boot(Mb, Pb, Nb, self.C1)
            Ac_rep = auc_boot(Mc, Pc, Nc, self.C1)
            D_rep = Ab_rep - Ac_rep
            good = np.isfinite(Ab_rep) & np.isfinite(Ac_rep)
            lcb = float(np.percentile(Ab_rep[np.isfinite(Ab_rep)], LCB_PCTL))
            p_A = float(np.mean(Ab_rep[np.isfinite(Ab_rep)] >= 2 * A - THR_A))
            p_D = float(np.mean(D_rep[good] >= 2 * D - THR_D))
            d_lo = float(np.percentile(D_rep[good], EQUIV_LO_PCTL))
            d_hi = float(np.percentile(D_rep[good], EQUIV_HI_PCTL))

            rec = {
                "status": "TESTED", "n": f["n"], "share_of_oos": f["share_of_oos"],
                "A_b": A, "A_not_b": Ac, "D_b": D,
                "LCB_b_1day": lcb, "p_A": p_A, "p_D": p_D,
                "D_ci_99375": [d_lo, d_hi],
                "base_rate": float(self.y[self.row_idx[name]].mean()),
            }

            # conditions 1-4
            c1 = True
            c2 = (A > THR_A and p_A <= ALPHA_TEST) and (D > THR_D and p_D <= ALPHA_TEST)
            c3 = lcb > THR_LCB
            c4 = f["share_of_oos"] <= THR_NARROW_SHARE

            # condition 5 — both chronological halves (SG4)
            halves = []
            for h in range(2):
                ch = self.half_mask[h].astype(float)
                Ah = auc_from_M(Mb, Pb, Nb, ch)
                Ach = auc_from_M(Mc, Pc, Nc, ch)
                halves.append({"A": Ah, "D": Ah - Ach})
            c5 = all(
                np.isfinite(h["A"]) and np.isfinite(h["D"])
                and h["A"] >= THR_HALF_A and h["D"] >= THR_HALF_D
                for h in halves
            )

            # condition 6 — >=2 of 3 CALENDAR thirds with point A_b >= 0.60
            thirds = []
            for t in range(3):
                ct = (self.third_of_day == t).astype(float)
                thirds.append(auc_from_M(Mb, Pb, Nb, ct))
            c6 = sum(1 for a in thirds if np.isfinite(a) and a >= THR_THIRD_A) \
                >= THR_THIRDS_REQUIRED

            # condition 7 — 2-day block robustness (fails CLOSED)
            if short_circuit and not (c2 and c3 and c4 and c5 and c6):
                c7 = False
                lcb2 = None
            else:
                M2, P2_, N2_ = pair_blocks(Mb, Pb, Nb, self.n_days)
                A2_rep = auc_boot(M2, P2_, N2_, self.C2)
                lcb2 = float(np.percentile(A2_rep[np.isfinite(A2_rep)], LCB_PCTL))
                c7 = lcb2 > THR_LCB

            rec.update({
                "halves": halves, "thirds": thirds, "LCB_b_2day": lcb2,
                "conditions": {"c1_floors": c1, "c2_A_and_D": bool(c2), "c3_LCB": bool(c3),
                               "c4_narrow": bool(c4), "c5_halves": bool(c5),
                               "c6_thirds": bool(c6), "c7_2day_block": bool(c7)},
                "qualifying_pocket": bool(c1 and c2 and c3 and c4 and c5 and c6 and c7),
                "_A_rep": Ab_rep,
            })
            per[name] = rec

        omni = {}
        if want_omnibus:
            for part in parts:
                names = [b["name"] for b in self.buckets
                         if b["partition"] == part and per[b["name"]]["status"] == "TESTED"]
                if len(names) < 2:
                    omni[part] = {"status": "SKIPPED_TOO_FEW_BUCKETS"}
                    continue
                nk = np.array([per[n_]["n"] for n_ in names], dtype=float)
                w = nk / nk.sum()
                Aobs = np.array([per[n_]["A_b"] for n_ in names])
                Abar = float(w @ Aobs)
                T_obs = float(w @ (Aobs - Abar) ** 2)
                reps = np.vstack([per[n_]["_A_rep"] for n_ in names])       # (k, B)
                ok = np.all(np.isfinite(reps), axis=0)
                tilde = reps - Aobs[:, None] + Abar                          # impose H0
                bar_rep = w @ tilde
                T_rep = w @ (tilde - bar_rep[None, :]) ** 2
                p = float(np.mean(T_rep[ok] >= T_obs))
                omni[part] = {"status": "TESTED", "buckets": names,
                              "A_within_weighted_mean": Abar, "T_obs": T_obs, "p": p,
                              "fires_at_alpha": bool(p < ALPHA_TEST)}

        for r in per.values():
            r.pop("_A_rep", None)
        return {"buckets": per, "omnibus": omni}

    def verdict(self, ev):
        per, omni = ev["buckets"], ev["omnibus"]
        tested = [k for k, v in per.items() if v["status"] == "TESTED"]
        if len(tested) < MIN_QUALIFYING_BUCKETS:
            return {"state": "NO_ANSWER", "reason": "insufficient cells",
                    "qualifying_pockets": []}
        pockets = [k for k in tested if per[k]["qualifying_pocket"]]
        ps = [omni[p]["p"] for p in ("P1", "P2") if omni.get(p, {}).get("status") == "TESTED"]
        omni_fires = bool(ps) and min(ps) < ALPHA_TEST
        if len(pockets) == 1 and omni_fires:
            return {"state": "CONCENTRATED", "bucket": pockets[0],
                    "qualifying_pockets": pockets, "omnibus_fires": omni_fires}
        if len(pockets) >= 2:
            return {"state": "NON_UNIFORM_NOT_NARROW", "qualifying_pockets": pockets,
                    "omnibus_fires": omni_fires}
        # equivalence leg — requires ALL SIX intervals (§11 state 3)
        all_six = len(tested) == len(self.buckets)
        inside = all_six and all(
            per[k]["D_ci_99375"][0] >= -EQUIV_BAND and per[k]["D_ci_99375"][1] <= EQUIV_BAND
            for k in tested
        )
        if inside and not pockets:
            flags = []
            if not omni_fires:
                flags.append("UNIFORM_HOMOGENEOUS")
            else:
                flags.append("HETEROGENEOUS_BUT_UNACTIONABLE")
            p1 = omni.get("P1", {})
            if p1.get("status") == "TESTED" and p1["A_within_weighted_mean"] \
                    <= BASE_RATE_ARTIFACT_A:
                flags.append("UNIFORM_BASE_RATE_ARTIFACT")
            return {"state": "UNIFORM", "sub_flags": flags, "qualifying_pockets": [],
                    "omnibus_fires": omni_fires}
        return {"state": "INDETERMINATE", "qualifying_pockets": pockets,
                "equivalence_met": bool(inside), "omnibus_fires": omni_fires}


# ─────────────────────────────────────────────────────────────────────────────
# synthetic score helpers (unit check + MDE)
# ─────────────────────────────────────────────────────────────────────────────
def _rank_map_to(reference: np.ndarray, raw: np.ndarray) -> np.ndarray:
    """Map `raw` onto the empirical marginal of `reference`, preserving raw's order."""
    order = np.argsort(raw, kind="mergesort")
    ref = np.sort(reference)
    out = np.empty_like(raw, dtype=float)
    out[order] = ref
    return out


def synth_auc_score(y_sub: np.ndarray, target_auc: float, ref: np.ndarray,
                    rng: np.random.Generator) -> np.ndarray:
    """Binormal draw with E[AUC] = target_auc, then rank-mapped onto ref's marginal.

    SG8 (identified in POST-RUN review of the 2026-07-26 execution, NOT pre-declared):
    this draw is i.i.d. PER ROW, so the injected bucket has almost no day-to-day dispersion
    in discrimination, while the real p_ens does. The day-block bootstrap prices exactly that
    dispersion, so the injected alternative is EASIER to detect than a real pocket would be
    and the measured MDE is a LOWER BOUND. §10 does not specify the injected score's
    dependence structure. It was deliberately NOT changed post-hoc (that would be §12
    invalidating conduct); a future pre-registration should pin the cluster structure of the
    injected alternative explicitly.


    Binormal equal-variance: AUC = Phi(mu / sqrt(2))  =>  mu = sqrt(2) * Phi^-1(AUC).
    The rank-map keeps the injected bucket's score marginal identical to the real one,
    so only the RANKING (i.e. the discrimination) is synthetic.
    """
    mu = math.sqrt(2.0) * _probit(target_auc)
    raw = mu * y_sub.astype(float) + rng.standard_normal(y_sub.size)
    return _rank_map_to(ref, raw)


def _probit(p: float) -> float:
    from scipy.stats import norm
    return float(norm.ppf(p))


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="machinery smoke test on RANDOM scores at tiny B; computes no "
                         "real bucket statistic and no verdict")
    args = ap.parse_args()

    RESULTS["run"] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "prereg": str(PREREG_PATH.relative_to(ROOT)),
        "prereg_sha256": PREREG_SHA256,
        "smoke": bool(args.smoke),
    }
    # verify the prereg hash (CRLF-normalised body through the sentinel)
    import hashlib
    body = PREREG_PATH.read_bytes().replace(b"\r\n", b"\n")
    sent = b"<!-- SHA256-BODY-END -->"
    digest = hashlib.sha256(body[: body.index(sent) + len(sent)]).hexdigest()
    RESULTS["run"]["prereg_sha256_recomputed"] = digest
    RESULTS["run"]["prereg_hash_ok"] = digest == PREREG_SHA256
    RESULTS["spec_gaps"] = SPEC_GAPS
    _flush()
    if digest != PREREG_SHA256:
        _log(f"!! prereg hash mismatch: {digest}")

    X, y, ts, syms = stage0_harvest()

    if args.smoke:
        # machinery-only: skip the model refit, take the fold geometry directly and use a
        # RANDOM score. No real bucket statistic is computed and no verdict is produced.
        n = X.shape[0]
        test_size = n // 6
        oos_idx = np.arange(n - 5 * test_size, n)
        rs = np.random.default_rng(1).random(oos_idx.size)
        ens = {"idx": oos_idx, "p_ens": rs, "y": y[oos_idx], "auc": float("nan")}
        RESULTS["stage1_reproduction"] = {"passed": False, "smoke_skipped": True}
    else:
        ens = stage1_reproduction(X, y, ts, syms)
    idx = ens["idx"]
    Xo, y_o, ts_o, symo = X[idx], ens["y"], ts[idx], syms[idx]
    p_ens = ens["p_ens"]

    # ── day-blocks (§8) ────────────────────────────────────────────────────
    day_key = (ts_o // 86400).astype(np.int64)
    uniq_days = np.unique(day_key)
    n_days = len(uniq_days)
    day = np.searchsorted(uniq_days, day_key)
    day_dates = [datetime.fromtimestamp(d * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
                 for d in uniq_days]
    half_of_day = np.array([0 if k < n_days // 2 else 1 for k in range(n_days)])  # SG4
    half_mask = np.vstack([half_of_day == 0, half_of_day == 1])
    third_of_day = np.array(
        [0 if d < THIRD_BOUNDS[0] else (1 if d < THIRD_BOUNDS[1] else 2) for d in day_dates]
    )

    buckets = build_buckets(Xo, symo)
    floors_list = [check_floors(b, y_o, symo, day, half_of_day, n_days) for b in buckets]
    floors = {b["name"]: f for b, f in zip(buckets, floors_list)}

    RESULTS["stage0_geometry"] = {
        "n_oos": int(len(y_o)),
        "oos_window_utc": [
            datetime.fromtimestamp(float(ts_o.min()), tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(float(ts_o.max()), tz=timezone.utc).isoformat(),
        ],
        "n_symbols": int(len(set(symo))),
        "n_populated_days": int(n_days),
        "day_list": day_dates,
        "half_split": {"first": day_dates[: n_days // 2], "second": day_dates[n_days // 2:]},
        "thirds_bounds": list(THIRD_BOUNDS),
        "third_sizes": [int((third_of_day == t).sum()) for t in range(3)],
        "base_rate_oos": float(y_o.mean()),
        "buckets": floors,
        "prereg_s7_table": {
            "P1_ADXlo_ATRlo": 93631, "P1_ADXlo_ATRhi": 56354, "P1_ADXhi_ATRlo": 55634,
            "P1_ADXhi_ATRhi": 65251, "P2_MAJOR": 54206, "P2_ALT": 216664,
        },
    }
    _flush()
    _log(f"    OOS n={len(y_o)}  days={n_days}  symbols={len(set(symo))}")
    for b in buckets:
        f = floors[b["name"]]
        _log(f"      {b['name']:<16} n={f['n']:>7} share={f['share_of_oos']:.3f} "
             f"days={f['days']:>2} sym={f['symbols']:>2} qual={f['qualifies']}")

    # ── bootstrap multiplicity matrices (shared by every statistic) ────────
    b_eff = 200 if args.smoke else B_BOOT
    C1 = counts_matrix(n_days, BLOCK_LEN_PRIMARY, b_eff, SEED)
    n_pairs = (n_days + 1) // 2
    C2 = counts_matrix(n_pairs, BLOCK_LEN_PRIMARY, b_eff, SEED)

    az = Analyzer(y_o, day, n_days, buckets, floors, C1, C2, half_mask, third_of_day)

    # ── S2 machinery validation ────────────────────────────────────────────
    _log("S2 — machinery validation (Mann-Whitney day-block == roc_auc_score)")
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(SEED)
    tied = np.round(rng.random(len(y_o)), 2)      # heavy ties -> exercises the tie path
    checks = []
    ones = np.ones(n_days)
    for name, ridx in list(az.row_idx.items()) + [("__pooled__", np.arange(len(y_o)))] + \
            [(f"comp::{k}", v) for k, v in az.comp_idx.items()]:
        M, P, N = build_M(ridx, tied, y_o, day, n_days)
        a_fast = auc_from_M(M, P, N, ones)
        a_ref = float(roc_auc_score(y_o[ridx], tied[ridx]))
        checks.append({"set": name, "auc_decomposed": a_fast, "auc_sklearn": a_ref,
                       "abs_err": abs(a_fast - a_ref),
                       "n_pos_ok": bool(P.sum() == y_o[ridx].sum()),
                       "n_neg_ok": bool(N.sum() == (y_o[ridx] == 0).sum())})
    max_err = max(c["abs_err"] for c in checks)
    ok = max_err < 1e-10 and all(c["n_pos_ok"] and c["n_neg_ok"] for c in checks)
    RESULTS["stage2_machinery_validation"] = {"max_abs_err": max_err, "passed": bool(ok),
                                              "checks": checks}
    _flush()
    _log(f"    max |decomposed - sklearn| = {max_err:.3e} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        RESULTS["verdict"] = {"state": "NO_ANSWER", "reason": "machinery validation failed"}
        _flush()
        return 1

    if args.smoke:
        _log("S-smoke — full decision rule on a RANDOM score (no real statistic computed)")
        t0 = time.time()
        ev = az.evaluate(tied)
        v = az.verdict(ev)
        _log(f"    smoke verdict={v['state']}  ({time.time() - t0:.1f}s per full evaluation)")
        RESULTS["smoke"] = {"seconds_per_evaluation": round(time.time() - t0, 2),
                            "verdict_on_random_score": v}
        _flush()
        return 0

    if not RESULTS["stage1_reproduction"]["passed"]:
        RESULTS["verdict"] = {
            "state": "NO_ANSWER", "reason": "reproduction",
            "detail": f"pooled OOS AUC {ens['auc']:.6f} outside [{REPRO_LO}, {REPRO_HI}]",
        }
        _flush()
        _log("HALT — NO_ANSWER: reproduction (no bucket statistic computed)")
        write_report()
        return 0

    # ── S3 unit check (§8, mandatory) ──────────────────────────────────────
    _log(f"S3 — unit check ({UNIT_CHECK_DRAWS} synthetic offset-only draws)")
    t0 = time.time()
    p1 = [b for b in buckets if b["partition"] == "P1"]
    # per-bucket constant offset ordered by bucket base rate -> pooled AUC ~ 0.53,
    # within-bucket AUC 0.500 by construction (score independent of y inside a bucket).
    brs = np.array([float(y_o[b["mask"]].mean()) for b in p1])
    offs = (brs - brs.mean()) / (brs.std() + 1e-12)
    ps, pooled_aucs = [], []
    rng = np.random.default_rng(SEED + 1)
    for i in range(UNIT_CHECK_DRAWS):
        s = rng.standard_normal(len(y_o))
        for k, b in enumerate(p1):
            s[b["mask"]] += offs[k] * 0.60
        ev = az.evaluate(s, short_circuit=True, partitions=("P1",))
        ps.append(ev["omnibus"]["P1"]["p"])
        if i < 20:
            pooled_aucs.append(float(roc_auc_score(y_o, s)))
    ps = np.array(ps)
    frac05 = float((ps <= 0.05).mean())
    uc_ok = frac05 <= 0.10
    RESULTS["stage3_unit_check"] = {
        "draws": UNIT_CHECK_DRAWS, "criterion": "P(p<=0.05) <= 0.10 (SG3)",
        "frac_p_le_0.05": frac05, "p_quantiles": np.percentile(ps, [5, 25, 50, 75, 95]).tolist(),
        "pooled_auc_of_synthetic_first20": pooled_aucs,
        "mean_pooled_auc_synthetic": float(np.mean(pooled_aucs)),
        "passed": bool(uc_ok), "seconds": round(time.time() - t0, 1),
    }
    _flush()
    _log(f"    P(p<=0.05)={frac05:.3f}  pooled synthetic AUC~{np.mean(pooled_aucs):.3f} -> "
         f"{'PASS' if uc_ok else 'FAIL'}  ({time.time() - t0:.0f}s)")
    if not uc_ok:
        RESULTS["verdict"] = {"state": "NO_ANSWER", "reason": "unit check"}
        _flush()
        write_report()
        return 0

    # ── S4 measured MDE (§10) ──────────────────────────────────────────────
    _log("S4 — measured MDE")
    t0 = time.time()
    eligible_p1 = [b["name"] for b in buckets
                   if b["partition"] == "P1"
                   and floors[b["name"]]["qualifies"]
                   and floors[b["name"]]["share_of_oos"] <= THR_NARROW_SHARE]
    primary = min(eligible_p1, key=lambda n_: floors[n_]["n"]) if eligible_p1 else None
    targets = [t for t in (primary, "P2_MAJOR") if t and floors[t]["qualifies"]]
    mde_out = {}
    for tgt in targets:
        ridx = az.row_idx[tgt]
        ref = p_ens[ridx]
        curve = {}
        for delta in MDE_DELTAS:
            det, soft = 0, 0
            for s_i in range(MDE_SIMS):
                r = np.random.default_rng(SEED + 1000 * int(delta * 100) + s_i)
                s = p_ens.copy()
                s[ridx] = synth_auc_score(y_o[ridx], delta, ref, r)
                ev = az.evaluate(s)
                v = az.verdict(ev)
                if v["state"] == "CONCENTRATED" and v.get("bucket") == tgt:
                    det += 1
                if ev["buckets"][tgt].get("qualifying_pocket"):
                    soft += 1
            curve[f"{delta:.2f}"] = {"power_strict": det / MDE_SIMS,
                                     "power_soft_bucket_qualifies": soft / MDE_SIMS}
            _log(f"    {tgt} delta={delta:.2f} power={det / MDE_SIMS:.2f} "
                 f"(soft {soft / MDE_SIMS:.2f})  [{time.time() - t0:.0f}s]")
            RESULTS.setdefault("stage4_mde", {})[tgt] = {"curve": curve}
            _flush()
        hit = [d for d in MDE_DELTAS if curve[f"{d:.2f}"]["power_strict"] >= MDE_POWER]
        mde = min(hit) if hit else float("inf")
        mde_out[tgt] = {"curve": curve, "mde": mde if math.isfinite(mde) else None,
                        "mde_infinite_means": "no tested delta reached 80% power (MDE > 0.75)"}
    reported_mde = max(
        (v["mde"] if v["mde"] is not None else float("inf")) for v in mde_out.values()
    )
    RESULTS["stage4_mde"] = {
        "targets": targets, "primary_target": primary, "per_target": mde_out,
        "reported_mde": reported_mde if math.isfinite(reported_mde) else None,
        "underpowered": bool(not math.isfinite(reported_mde)
                             or reported_mde > MDE_NO_ANSWER_ABOVE),
        "seconds": round(time.time() - t0, 1),
    }
    _flush()

    # ── S5 main analysis ───────────────────────────────────────────────────
    _log("S5 — main analysis on the real ensemble score")
    # identity assertion on the REAL score (advisor item 1, second leg)
    M, P, N = build_M(np.arange(len(y_o)), p_ens, y_o, day, n_days)
    a_fast = auc_from_M(M, P, N, ones)
    a_ref = float(roc_auc_score(y_o, p_ens))
    RESULTS["stage5_identity_on_real_score"] = {"decomposed": a_fast, "sklearn": a_ref,
                                                "abs_err": abs(a_fast - a_ref)}
    pooled_rep = auc_boot(M, P, N, C1)
    RESULTS["stage5_pooled"] = {
        "pooled_auc": a_ref,
        "day_block_clustered_ci95": [float(np.percentile(pooled_rep, 2.5)),
                                     float(np.percentile(pooled_rep, 97.5))],
        "n_clusters": int(n_days),
        "contains_0_500": bool(np.percentile(pooled_rep, 2.5) <= 0.5
                               <= np.percentile(pooled_rep, 97.5)),
    }
    ev = az.evaluate(p_ens, short_circuit=False)
    RESULTS["stage5_buckets"] = ev["buckets"]
    RESULTS["stage5_omnibus"] = ev["omnibus"]
    _flush()

    # EXPLORATORY score-decile table (D3) — cannot move the verdict
    dec = np.clip(np.searchsorted(np.percentile(p_ens, np.arange(10, 100, 10)), p_ens), 0, 9)
    decs = []
    for d in range(10):
        m = dec == d
        try:
            a = float(roc_auc_score(y_o[m], p_ens[m]))
        except ValueError:
            a = float("nan")
        decs.append({"decile": d + 1, "n": int(m.sum()), "base_rate": float(y_o[m].mean()),
                     "within_decile_auc": a})
    RESULTS["exploratory_score_deciles"] = {
        "NON_CONFIRMATORY": "EXPLORATORY only (§D3) — cannot move the verdict in any direction",
        "table": decs,
    }

    # ── S6 verdict ─────────────────────────────────────────────────────────
    v = az.verdict(ev)
    if RESULTS["stage4_mde"]["underpowered"]:
        v = {"state": "NO_ANSWER", "reason": "underpowered",
             "detail": f"measured MDE {RESULTS['stage4_mde']['reported_mde']} > "
                       f"{MDE_NO_ANSWER_ABOVE}",
             "verdict_that_would_have_been_reported": v}
    RESULTS["verdict"] = v
    RESULTS["run"]["finished_utc"] = datetime.now(timezone.utc).isoformat()
    _flush()
    _log(f"VERDICT: {v['state']}")
    write_report()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# report
# ─────────────────────────────────────────────────────────────────────────────
def write_report() -> None:
    R = RESULTS
    v = R.get("verdict", {})
    L: list[str] = []
    a = L.append
    a("# 31 — SCREEN RESULT: is the 0.031 OOS AUC excess UNIFORM or CONCENTRATED?")
    a("")
    a(f"**Executed:** {R['run'].get('started_utc')} → {R['run'].get('finished_utc', 'n/a')}  ")
    a(f"**Pre-registration:** `{R['run']['prereg']}` — "
      f"hash re-verified: **{R['run']['prereg_hash_ok']}** (`{PREREG_SHA256}`)  ")
    a("**Read-only run.** Warehouse opened `mode=ro`; no `core/`, `config.py`, `.env` or "
      "process touched.")
    a("")
    a(f"## MECHANICAL VERDICT: `{v.get('state')}`"
      + (f" — {v.get('reason')}" if v.get("reason") else ""))
    a("")
    if v.get("state") == "UNIFORM":
        a(f"Sub-flags: `{', '.join(v.get('sub_flags', []))}`")
        a("")
        a("Per §11 STATE 3 this is an **equivalence** result, not a failure to reject: all six "
          "simultaneous 99.375% intervals on `D_b` lie wholly inside ±0.030.")
    a("")
    a("Binding interpretation clause (§11, verbatim): *\"Do not relabel failure to find a "
      "pocket as scientific uniformity. Both UNIFORM and INDETERMINATE prohibit further "
      "conditioning work; only CONCENTRATED permits one future screen.\"*")
    a("")

    # gates
    a("## Gates (frozen order)")
    a("")
    a("| Stage | Result |")
    a("|---|---|")
    s1 = R.get("stage1_reproduction", {})
    a(f"| S1 reproduction — pooled OOS AUC in [0.520, 0.541] | "
      f"{s1.get('pooled_oos_auc')} → **{'PASS' if s1.get('passed') else 'FAIL'}** |")
    s2 = R.get("stage2_machinery_validation", {})
    a(f"| S2 machinery (day-block Mann-Whitney == `roc_auc_score`) | "
      f"max abs err {s2.get('max_abs_err'):.2e} → **{'PASS' if s2.get('passed') else 'FAIL'}** |"
      if s2 else "| S2 | not reached |")
    s3 = R.get("stage3_unit_check", {})
    if s3:
        a(f"| S3 unit check (offset-only synthetic, {s3['draws']} draws) | "
          f"P(p≤0.05)={s3['frac_p_le_0.05']:.3f} → "
          f"**{'PASS' if s3['passed'] else 'FAIL'}** |")
    s4 = R.get("stage4_mde", {})
    if s4 and "reported_mde" in s4:
        a(f"| S4 measured MDE (§10) | reported MDE = {s4.get('reported_mde')} → "
          f"**{'UNDERPOWERED' if s4.get('underpowered') else 'ADEQUATE'}** |")
    a("")

    g = R.get("stage0_geometry", {})
    if g:
        a("## Measured geometry")
        a("")
        a(f"- OOS rows **{g['n_oos']}** over **{g['n_populated_days']} populated UTC days** "
          f"and {g['n_symbols']} symbols — window {g['oos_window_utc'][0]} → "
          f"{g['oos_window_utc'][1]}.")
        a(f"- Base rate {g['base_rate_oos']:.4f}.")
        a("- **The binding sample size is 30 day-clusters, not 270k rows.** Every interval "
          "below is a panel-synchronous circular moving-block bootstrap over those day "
          "blocks (B=20,000). The LCB's resolution is limited by the 30 clusters, not by B: "
          "the 0.3125th percentile is read off a distribution whose support comes from 30 "
          "day draws. §12 item 1 acknowledges this; §11 cond 7 (2-day blocks) mitigates it.")
        a("")
        a("| Bucket | n | share | days | symbols | max sym share | floors |")
        a("|---|---:|---:|---:|---:|---:|---|")
        for k, f in g["buckets"].items():
            a(f"| {k} | {f['n']} | {f['share_of_oos']:.3f} | {f['days']} | {f['symbols']} | "
              f"{f['max_single_symbol_share']:.3f} | "
              f"{'QUALIFIES' if f['qualifies'] else 'INSUFFICIENT_DATA'} |")
        a("")

    b = R.get("stage5_buckets")
    if b:
        p = R.get("stage5_pooled", {})
        a("## Pooled")
        a("")
        a(f"Pooled OOS AUC **{p.get('pooled_auc'):.6f}**; day-block-clustered 95% CI "
          f"**[{p['day_block_clustered_ci95'][0]:.4f}, {p['day_block_clustered_ci95'][1]:.4f}]** "
          f"on {p['n_clusters']} clusters. Contains 0.500: **{p['contains_0_500']}**.")
        if p.get("contains_0_500"):
            a("")
            a("> At the honest effective sample size the 0.031 excess is **not distinguishable "
              "from zero**.")
        a("")
        a("## Per-bucket result")
        a("")
        a("| Bucket | n | A_b | A_not_b | D_b | LCB(1d) | p(A) | p(D) | "
          "D 99.375% CI | pocket |")
        a("|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
        for k, r in b.items():
            if r["status"] != "TESTED":
                a(f"| {k} | — | — | — | — | — | — | — | — | INSUFFICIENT_DATA |")
                continue
            a(f"| {k} | {r['n']} | {r['A_b']:.4f} | {r['A_not_b']:.4f} | {r['D_b']:+.4f} | "
              f"{r['LCB_b_1day']:.4f} | {r['p_A']:.4f} | {r['p_D']:.4f} | "
              f"[{r['D_ci_99375'][0]:+.4f}, {r['D_ci_99375'][1]:+.4f}] | "
              f"{'YES' if r['qualifying_pocket'] else 'no'} |")
        a("")
        a(f"Thresholds (frozen, m={M_MULTIPLICITY}, α_test={ALPHA_TEST}): a pocket needs "
          f"`A_b>{THR_A}` **and** `D_b>{THR_D}` (both at p≤{ALPHA_TEST}), `LCB_b>{THR_LCB}`, "
          f"≤{int(THR_NARROW_SHARE * 100)}% of rows, both-halves stability, 2-of-3 thirds, and "
          "2-day-block agreement.")
        a("")
        o = R.get("stage5_omnibus", {})
        a("## Heterogeneity omnibus (centred on the within-bucket weighted mean)")
        a("")
        a("| Partition | Ā_within | T_obs | p | fires at α=0.003125 |")
        a("|---|---:|---:|---:|---|")
        for part, r in o.items():
            if r.get("status") != "TESTED":
                a(f"| {part} | — | — | — | {r.get('status')} |")
                continue
            a(f"| {part} | {r['A_within_weighted_mean']:.4f} | {r['T_obs']:.3e} | "
              f"{r['p']:.4f} | {r['fires_at_alpha']} |")
        a("")
        a("*(§8: P2 has exactly two cells, so `D_ALT = −D_MAJOR` and the P2 omnibus is "
          "algebraically a restatement of `D_MAJOR²` — one effective degree of freedom, one "
          "line of evidence, never mutual corroboration.)*")
        a("")

    if s4 and s4.get("per_target"):
        a("## Measured power (§10)")
        a("")
        a("| Target | " + " | ".join(f"AUC {d:.2f}" for d in MDE_DELTAS) + " | MDE |")
        a("|---|" + "---:|" * len(MDE_DELTAS) + "---|")
        for tgt, r in s4["per_target"].items():
            row = [f"{r['curve'][f'{d:.2f}']['power_strict']:.2f}" for d in MDE_DELTAS]
            a(f"| {tgt} | " + " | ".join(row) + f" | {r['mde'] if r['mde'] else '>0.75'} |")
        a("")

    ex = R.get("exploratory_score_deciles")
    if ex:
        a("## EXPLORATORY — score-decile table (NON-CONFIRMATORY, §D3)")
        a("")
        a("Reported because raising `MCP_ENTRY_MIN_SCORE` is the program's most-proposed "
          "filter. It is **not** in the confirmatory family and **cannot move the verdict**.")
        a("")
        a("| Decile | n | base rate | within-decile AUC |")
        a("|---:|---:|---:|---:|")
        for r in ex["table"]:
            a(f"| {r['decile']} | {r['n']} | {r['base_rate']:.4f} | "
              f"{r['within_decile_auc']:.4f} |")
        a("")

    a("## Specification gaps — execution decisions declared before the run")
    a("")
    a("These are clauses the frozen prereg leaves under-specified. Each was resolved by a "
      "named constant in `research/screen_edge_concentration.py` **before** execution. They "
      "are execution decisions, not deviations from a specified value.")
    a("")
    for sg in SPEC_GAPS:
        a(f"- **{sg['id']}** — {sg['clause']}  \n  → {sg['decision']}")
    a("")
    a("## Scope binding (§13)")
    a("")
    a("Any verdict here is bound to **this** signal, **this** window (30 populated days, "
      "2026-06-10 → 07-18, a single regime), **this** six-bucket conditioner family, at the "
      "**measured MDE**. It does not close conditioning across regimes and does not test the "
      "BTC-regime or idiosyncratic-vol axes (D2). No finite bucket experiment can prove "
      "uniformity over every measurable filter; the universal closure comes only from the "
      "separate analytic result that when the primary side carries zero directional "
      "information, `E[filter × side × return] = 0`.")
    a("")
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    _log(f"wrote {OUT_MD}")


if __name__ == "__main__":
    raise SystemExit(main())
