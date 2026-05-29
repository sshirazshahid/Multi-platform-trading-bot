"""Honest promotion gate (2026-05-25, no-edge-forensics).

The live ensemble was promoted with PBO=1.0 / DSR=0.0008 because the gate
allowed MAX_PBO=1.0 / MIN_DSR=0.0. Tightened to MAX_PBO=0.5 / MIN_DSR=0.10.
These tests prove the overfit profile is now REJECTED and an honest one passes.
"""
from __future__ import annotations

import json

from core import promotion_gate as pg


def test_thresholds_tightened():
    """Pin the honest thresholds — guard against silent loosening."""
    assert pg.MIN_DSR >= 0.10, f"MIN_DSR loosened to {pg.MIN_DSR}"
    assert pg.MAX_PBO <= 0.5, f"MAX_PBO loosened to {pg.MAX_PBO}"


def _artifact(tmp_path, *, n_oos=4650, auc=0.76, wr_train=0.14):
    p = tmp_path / "ens.json"
    p.write_text(json.dumps({
        "metrics": {"n_oos_ensemble": n_oos, "auc_ensemble": auc,
                    "wr_train": wr_train}}), encoding="utf-8")
    return str(p)


def test_rejects_overfit_profile(tmp_path):
    """The exact profile that was wrongly promoted: oos_wr 0.80 but PBO=1.0,
    DSR~0. Must now be REJECTED."""
    row = {"model_version": "ens_overfit", "oos_wr": 0.80,
           "deflated_sharpe": 0.0008, "pbo": 1.0,
           "artifact_path": _artifact(tmp_path)}
    promote, reason, diag = pg.evaluate_model_version(row, market_type="futures")
    assert promote is False, f"overfit model must be rejected; reason={reason}"
    assert ("PBO" in reason) or ("DSR" in reason), (
        f"rejection should cite PBO/DSR; got: {reason}")


def test_accepts_honest_strong_model(tmp_path):
    """A model clearing all honest bars (PBO<0.5, DSR>=0.10, OOS-WR>=0.55,
    AUC>=0.60, uplift>=1.5) must be promoted."""
    row = {"model_version": "ens_honest", "oos_wr": 0.62,
           "deflated_sharpe": 0.30, "pbo": 0.25,
           "artifact_path": _artifact(tmp_path, auc=0.66, wr_train=0.35)}
    promote, reason, diag = pg.evaluate_model_version(row, market_type="futures")
    assert promote is True, f"honest strong model must promote; reason={reason}"


def test_rejects_zero_dsr_even_if_pbo_ok(tmp_path):
    """DSR=0 alone is disqualifying even with an acceptable PBO."""
    row = {"model_version": "ens_zero_dsr", "oos_wr": 0.62,
           "deflated_sharpe": 0.0, "pbo": 0.20,
           "artifact_path": _artifact(tmp_path, auc=0.66, wr_train=0.35)}
    promote, reason, diag = pg.evaluate_model_version(row, market_type="futures")
    assert promote is False
    assert "DSR" in reason
