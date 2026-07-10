"""ACCURACY_TARGET_MODE (owner goal 2026-07-10): exit-geometry accuracy band.

The owner's standing goal is a 60-65% win-rate futures bot. Win rate is exit
geometry: at the default 2:1 R:R (TP twice the SL distance) a no-edge signal
mathematically realizes ~30% WR (measured: 28.6%/30d). Inverting the geometry
(TP = ``tp_frac_of_sl`` x SL distance, default 0.5) puts the theoretical hit
rate near SL/(SL+TP) ~ 67%, landing realized accuracy in the target band.

HONESTY (recorded here so the test file carries the caveat): this meets the
ACCURACY target by construction; it does NOT create profit edge — expectancy
stays ~ -costs on a no-edge signal. PAPER research posture only; the promotion
gate to CONTROLLED_LIVE is untouched and still requires after-cost expectancy.

Surface: config.ACCURACY_TARGET_MODE (default OFF -> byte-identical) +
mcp_brain._apply_accuracy_target(sl_pct, tp_pct) applied at BOTH TP authorities:
the algorithmic SL/TP block and the Claude-proposal ingestion clamp.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.mcp_brain as mb


@pytest.fixture
def acc_on(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5},
        raising=False,
    )


@pytest.fixture
def acc_off(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": False, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5},
        raising=False,
    )


def test_flag_on_inverts_geometry(acc_on):
    # SL 2.0% -> TP 1.0% (half the stop distance), regardless of incoming TP.
    assert mb._apply_accuracy_target(2.0, 4.0) == pytest.approx(1.0)
    # STAR-extended TP (2.5x) is overridden too — the band wins.
    assert mb._apply_accuracy_target(1.6, 4.0) == pytest.approx(0.8)


def test_flag_on_respects_cost_floor(acc_on):
    # TP never compresses below min_tp_pct (must clear round-trip costs).
    assert mb._apply_accuracy_target(0.8, 1.6) == pytest.approx(0.5)


def test_flag_off_is_byte_identical(acc_off):
    assert mb._apply_accuracy_target(2.0, 4.0) == pytest.approx(4.0)
    assert mb._apply_accuracy_target(1.5, 3.75) == pytest.approx(3.75)


def test_bad_inputs_fail_open_to_original_tp(acc_on):
    # Zero/negative SL: no geometry to invert -> return the original TP.
    assert mb._apply_accuracy_target(0.0, 3.0) == pytest.approx(3.0)
    assert mb._apply_accuracy_target(-1.0, 3.0) == pytest.approx(3.0)


def test_config_default_is_off():
    import os

    # The config DEFAULT (no env override) must be OFF; the owner opts in
    # via .env. When the env var IS set (owner machine), just assert shape.
    import config
    assert isinstance(config.ACCURACY_TARGET_MODE, dict)
    if os.getenv("ACCURACY_TARGET_MODE") is None:
        assert config.ACCURACY_TARGET_MODE["enabled"] is False


# ── wire-in pins (source-scan, matching repo scan-test style) ────────────────
def test_applied_at_both_tp_authorities():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert src.count("_apply_accuracy_target(") >= 3, (
        "helper def + BOTH call sites: the algorithmic SL/TP block and the "
        "Claude-proposal ingestion clamp must route TP through the accuracy band"
    )
    i = src.index("_claude_tp_clamped = _apply_accuracy_target")
    assert i > 0, "Claude ingestion clamp must apply the accuracy geometry"


def test_execute_open_chokepoint_covers_all_builders():
    """First live entry (ARB 2026-07-10, sl=0.8/tp=1.3 via the SCALP path)
    proved builder-level overrides miss paths. _execute_open is the final TP
    authority: every executed futures entry with a real TP must route through
    the band, tsmom (tp=0) excluded, and the min-R:R gate must not reject the
    intentional inverted shape when the mode produced it."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i = src.index("def _execute_open")
    block = src[i:]
    j = block.index("actual_rr = tp_pct / sl_pct")
    pre_rr = block[:j]
    assert "_apply_accuracy_target(sl_pct, tp_pct)" in pre_rr, (
        "the chokepoint must apply the band BEFORE the R:R gate"
    )
    rr_window = block[j : j + 400]
    assert "not _acc_mode_on" in rr_window, (
        "the min-R:R gate must carve out the intentional accuracy-band shape"
    )
