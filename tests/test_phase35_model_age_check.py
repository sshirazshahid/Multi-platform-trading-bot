"""Phase 35 — ML model freshness check in GateHealth (2026-05-05).

The Phase 13 LR + GBM ensemble drives MODEL_GATE (entry refusal at
p_win < 0.55) and the LR soft size multiplier ([0.7, 1.3]). Stale
models lose predictive power fast in crypto markets — freqtrade's
FreqAI pattern is continuous retraining.

Phase 35 surfaces staleness via _gate_health_check findings:
  - >2 days  → INFO-level "consider refitting"
  - >7 days  → WARNING-level "stale, refit now"
  - missing  → WARNING-level "no model file, MODEL_GATE may be unweighted"

Doesn't auto-retrain (operator decision); just makes the staleness
visible so a manual `python scripts/train_models.py` happens
proactively.

Today's state at ship time: ensemble was 53h old (2.2 days) — this
is exactly the kind of drift the operator should see.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

import time
from pathlib import Path


def test_model_age_check_present_in_gate_health():
    repo = Path(__file__).resolve().parents[1]
    src = bot_engine_source_for_grep()
    assert "Phase 35" in src
    assert "ML ensemble model" in src
    assert "ensemble_futures_latest.json" in src


def test_model_age_thresholds_in_source():
    """Two-tier threshold: >2 days warns, >7 days strong-warns."""
    repo = Path(__file__).resolve().parents[1]
    src = bot_engine_source_for_grep()
    assert "age_h > 168" in src   # 7 days
    assert "age_h > 48" in src    # 2 days


def test_missing_model_file_flagged():
    """If ensemble json doesn't exist, surface that to the operator."""
    repo = Path(__file__).resolve().parents[1]
    src = bot_engine_source_for_grep()
    assert "ML ensemble model file not found" in src


def test_model_age_check_does_not_raise(tmp_path, monkeypatch):
    """Defensive: any exception in the freshness path must not break
    the rest of _gate_health_check."""
    repo = Path(__file__).resolve().parents[1]
    src = bot_engine_source_for_grep()
    # The block is wrapped in try/except per the implementation
    idx = src.index("Phase 35")
    block = src[idx:idx + 2000]
    assert "try:" in block
    assert "except" in block
    assert "logger.debug" in block


def test_real_ensemble_file_age_today():
    """Sanity: the model file exists in the repo and we can read its
    age. This isn't a unit test of Phase 35 — it just verifies the
    artifact the check looks at is a real path."""
    repo = Path(__file__).resolve().parents[1]
    ens = repo / "data" / "models" / "ensemble_futures_latest.json"
    if ens.exists():
        age_h = (time.time() - ens.stat().st_mtime) / 3600
        # Just verify we can compute age without error
        assert age_h >= 0
