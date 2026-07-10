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


@pytest.fixture
def acc_on_per_side(monkeypatch):
    """Per-side overrides set (2026-07-10 sweep: buy~0.45 / sell~0.35)."""
    import config
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
         "tp_frac_buy": 0.45, "tp_frac_sell": 0.35},
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


# ── per-side overrides (2026-07-10 geometry sweep, 8,878 entries + audit) ────
# At a single global frac 0.50 longs realized 67.0% WR vs shorts 56.2% —
# "shorts would need a smaller frac" to put both sides in the 63-67% band.
def test_per_side_override_applied_by_side(acc_on_per_side):
    # buy/long -> tp_frac_buy 0.45
    assert mb._apply_accuracy_target(2.0, 4.0, side="buy") == pytest.approx(0.9)
    assert mb._apply_accuracy_target(2.0, 4.0, side="long") == pytest.approx(0.9)
    # sell/short -> tp_frac_sell 0.35
    assert mb._apply_accuracy_target(2.0, 4.0, side="sell") == pytest.approx(0.7)
    assert mb._apply_accuracy_target(2.0, 4.0, side="short") == pytest.approx(0.7)
    # case-insensitive
    assert mb._apply_accuracy_target(2.0, 4.0, side="SELL") == pytest.approx(0.7)
    # no side -> global frac 0.5
    assert mb._apply_accuracy_target(2.0, 4.0) == pytest.approx(1.0)
    # unknown side -> global frac 0.5
    assert mb._apply_accuracy_target(2.0, 4.0, side="hold") == pytest.approx(1.0)


def test_per_side_override_respects_cost_floor(acc_on_per_side):
    # SL 1.0% x sell-frac 0.35 = 0.35 -> floored at min_tp_pct 0.5
    assert mb._apply_accuracy_target(1.0, 2.0, side="sell") == pytest.approx(0.5)


def test_unset_per_side_falls_back_to_global(acc_on):
    # acc_on fixture has NO tp_frac_buy/tp_frac_sell keys -> global 0.5.
    assert mb._apply_accuracy_target(2.0, 4.0, side="buy") == pytest.approx(1.0)
    assert mb._apply_accuracy_target(2.0, 4.0, side="sell") == pytest.approx(1.0)


def test_zero_per_side_falls_back_to_global(monkeypatch):
    # env "0" -> falsy override -> global frac (the config None-semantics).
    import config
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
         "tp_frac_buy": None, "tp_frac_sell": 0},
        raising=False,
    )
    assert mb._apply_accuracy_target(2.0, 4.0, side="buy") == pytest.approx(1.0)
    assert mb._apply_accuracy_target(2.0, 4.0, side="sell") == pytest.approx(1.0)


def test_flag_off_identity_with_side(acc_off):
    # Per-side path must preserve the flag-off byte-identical guarantee.
    assert mb._apply_accuracy_target(2.0, 4.0, side="buy") == pytest.approx(4.0)
    assert mb._apply_accuracy_target(2.0, 4.0, side="sell") == pytest.approx(4.0)


def test_config_per_side_default_is_unset():
    import os

    import config
    if os.getenv("ACCURACY_TP_FRAC_BUY") is None:
        assert not config.ACCURACY_TARGET_MODE.get("tp_frac_buy")
    if os.getenv("ACCURACY_TP_FRAC_SELL") is None:
        assert not config.ACCURACY_TARGET_MODE.get("tp_frac_sell")


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
    # Per-side band (2026-07-10): both authorities must pass their side so
    # tp_frac_buy/tp_frac_sell can split the geometry.
    assert '_apply_accuracy_target(sl_pct, tp_pct, side=side)' in src, (
        "algorithmic SL/TP block must pass its side variable"
    )
    assert 'side=a.get("side")' in src[i : i + 300], (
        "Claude ingestion clamp must pass the action side"
    )


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
    assert "_apply_accuracy_target(sl_pct, tp_pct, side=side)" in pre_rr, (
        "the chokepoint must apply the band (with the side arg for the "
        "per-side frac split) BEFORE the R:R gate"
    )
    rr_window = block[j : j + 400]
    assert "not _acc_mode_on" in rr_window, (
        "the min-R:R gate must carve out the intentional accuracy-band shape"
    )
