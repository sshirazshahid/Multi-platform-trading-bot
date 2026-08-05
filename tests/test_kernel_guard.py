"""Phase 0 (audit 2026-06-22): the immutable safety-kernel guard.

The guard is the foundation of the autonomous self-modifier — it mechanically
bars a self-authored change from touching the safety kernel (SL rails, mode/
DRY_RUN gating, leverage caps, breakers, the promotion gate, the CONTROLLED_LIVE
latch) OR writing any runtime control file (the data/ab_split.json data-channel
bypass the red-team flagged). It MUST be fail-closed and self-protecting.
"""
from __future__ import annotations

import pytest

from core.selfmod import kernel_guard as kg


# ── protected code paths are blocked ─────────────────────────────────────────
@pytest.mark.parametrize("path", [
    "config.py",
    "config/",
    "config/modes.py",
    "main.py",
    "core/live_gate.py",
    "core/risk_manager.py",
    "core/order_manager.py",
    "core/promotion_gate.py",
    "core/btc_vol_pause.py",
    "docs/CONTROLLED_LIVE_CHECKLIST.md",
])
def test_protected_code_paths_blocked(path):
    ok, v = kg.assert_kernel_safe([path])
    assert ok is False
    assert path in v


# ── the guard protects ITSELF (self-modifier can't weaken its own fence) ─────
@pytest.mark.parametrize("path", [
    "core/selfmod/kernel_guard.py",
    "core/selfmod/kernel_manifest.py",
    "core/selfmod/__init__.py",
])
def test_guard_self_protection(path):
    ok, _ = kg.assert_kernel_safe([path])
    assert ok is False


# ── .env* secrets/latches are blocked by prefix ──────────────────────────────
@pytest.mark.parametrize("path", [".env", ".env.local", ".env.production"])
def test_env_prefix_blocked(path):
    ok, _ = kg.assert_kernel_safe([path])
    assert ok is False


# ── runtime-state control files are blocked (the data-channel bypass) ────────
@pytest.mark.parametrize("path", [
    "data/ab_split.json",
    "data/self_heal_state.json",
    "data/selfmod_state.json",
    "data/selfmod_disabled",
    "data/selfmod_kill",
    "data/risk_state.json",
    "data/review_required.json",
    "data/auto_mutations.json",
])
def test_runtime_state_blocked(path):
    ok, v = kg.assert_kernel_safe([path])
    assert ok is False
    assert path in v


# ── non-kernel files are allowed (the self-modifier CAN improve these) ───────
@pytest.mark.parametrize("path", [
    "skills/position-sizer/scripts/scorer.py",
    "scripts/some_research_lab.py",
    "tests/test_something.py",
    "core/learning_engine.py",       # NOTE: not in the Phase-0 kernel set
    "data/ohlcv_cache/BTCUSDT.parquet",
])
def test_non_kernel_paths_allowed(path):
    ok, v = kg.assert_kernel_safe([path])
    assert ok is True
    assert v == []


# ── Windows backslash paths normalize and are still caught ───────────────────
def test_backslash_path_normalized():
    ok, _ = kg.assert_kernel_safe([r"core\order_manager.py"])
    assert ok is False
    ok2, _ = kg.assert_kernel_safe([r".\config.py"])
    assert ok2 is False


# ── a mixed change set fails if ANY path is kernel ───────────────────────────
def test_mixed_set_blocks_on_any_kernel_path():
    ok, v = kg.assert_kernel_safe(
        ["scripts/ok.py", "config.py", "tests/test_ok.py"])
    assert ok is False
    assert "config.py" in v and "scripts/ok.py" not in v


# ── FAIL-CLOSED: missing manifest => everything is a violation ───────────────
def test_fail_closed_when_manifest_missing(monkeypatch):
    monkeypatch.setattr(kg, "_m", None)
    ok, v = kg.assert_kernel_safe(["totally_benign_file.py"])
    assert ok is False
    assert v  # non-empty


def test_fail_closed_when_manifest_empty(monkeypatch):
    class _Empty:
        PROTECTED_PATHS = frozenset()
        RUNTIME_STATE_DENY = frozenset()
        PROTECTED_PREFIXES = ()
    monkeypatch.setattr(kg, "_m", _Empty)
    ok, v = kg.assert_kernel_safe(["anything.py"])
    assert ok is False
    assert v


# ── empty/clean inputs ───────────────────────────────────────────────────────
def test_empty_changeset_is_safe():
    ok, v = kg.assert_kernel_safe([])
    assert ok is True and v == []


# ── CLI exit codes (hook behavior) ───────────────────────────────────────────
def test_cli_blocks_kernel_path():
    assert kg.main(["config.py"]) == 1


def test_cli_allows_benign_path():
    assert kg.main(["scripts/research.py"]) == 0
