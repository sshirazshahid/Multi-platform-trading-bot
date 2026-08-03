"""BAND REGIME FILTER (2026-07-12) — band-lane-ONLY toxic-regime entry veto.

Evidence base: pre-registered screen
`_workspace/strategy_pipeline/13_band_conditional_screen.json`
(14,555 resolved accuracy-band outcomes, 12d span, Bonferroni m=16,
band baseline WR 65.7%). Two conditionally-WORST regimes:

  * 4h ADX > 30 at entry           -> band WR 59.0% (n=5,352, halves 59.0/59.0)
  * BTC 1h ATR / 30d median < 0.7  -> band WR 55.6% (n=3,203)

HONESTY (the test file carries the caveat, matching repo convention): this is
WR-band protection + bleed reduction — it removes the conditionally-worst
buckets. It is NOT edge: every bucket in the screen kept a NEGATIVE after-cost
expectancy; PnL-positive still requires the evidence lanes.

Binding scope: the veto lives ONLY inside bot_engine._execute_open's
accuracy-band carve-out (`_acc_mode_on`). mcp_brain scoring, the deep_breakout
lane, and shadow probes must be untouched. Fail-OPEN on any missing datum.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.btc_vol_pause as bvp_mod
from core.bot_engine import BotEngine
from core.btc_vol_pause import BtcVolPause

# ── helpers ──────────────────────────────────────────────────────────────────

BVP_TEST_CFG = {
    "enabled": True, "timeframe": "1h",
    "vol_spike_mult": 2.0, "hysteresis_mult": 1.5, "clear_minutes": 30,
    "min_samples": 5, "append_min_interval_sec": 3600, "buffer_max": 1000,
}


def _cache(atr):
    return {"BTC": {"1h": {"atr_pct": atr}}}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(bvp_mod, "_cfg", lambda: dict(BVP_TEST_CFG))
    return BtcVolPause()


def _warm(gate, atr=1.0, n=5, t0=0.0):
    t = t0
    for _ in range(n):
        gate.update_and_evaluate(_cache(atr), now=t)
        t += 3600
    return t


class _FakeBvp:
    """Stub exposing only current_ratio, like the real gate's new method."""

    def __init__(self, ratio):
        self._ratio = ratio

    def current_ratio(self, indicator_cache):
        return self._ratio


def _engine(ratio=None, mcp_brain=None):
    """Bare BotEngine (no __init__ — engine setup is heavy) with just the
    attributes _band_regime_veto reads."""
    eng = BotEngine.__new__(BotEngine)
    eng._btc_vol_pause = _FakeBvp(ratio)
    eng.mcp_brain = mcp_brain
    return eng


@pytest.fixture
def flag_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "BAND_REGIME_FILTER_ENABLED", True, raising=False)


@pytest.fixture
def flag_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "BAND_REGIME_FILTER_ENABLED", False, raising=False)


# ── BtcVolPause.current_ratio (read-only baseline reuse) ─────────────────────

def test_current_ratio_computes_atr_over_median(gate):
    _warm(gate, atr=1.0, n=5)
    assert gate.current_ratio(_cache(0.5)) == pytest.approx(0.5, abs=0.01)
    assert gate.current_ratio(_cache(1.4)) == pytest.approx(1.4, abs=0.01)


def test_current_ratio_none_on_missing_btc_data(gate):
    _warm(gate, atr=1.0, n=5)
    assert gate.current_ratio(None) is None
    assert gate.current_ratio({}) is None
    assert gate.current_ratio({"BTC": {"1h": {}}}) is None


def test_current_ratio_none_during_warmup(gate):
    _warm(gate, atr=1.0, n=3)  # below min_samples=5
    assert gate.current_ratio(_cache(0.5)) is None


def test_current_ratio_is_read_only(gate):
    """Must NOT append to the baseline or touch pause state — the C.4 gate's
    update_and_evaluate() owns the buffer."""
    _warm(gate, atr=1.0, n=5)
    buf_before = [list(x) for x in gate._buf]
    pause_before = gate._pause_until
    gate.current_ratio(_cache(9.9))
    assert gate._buf == buf_before
    assert gate._pause_until == pause_before


# ── _band_regime_veto: flag gating ───────────────────────────────────────────

def test_flag_off_never_vetoes_even_in_toxic_regime(flag_off):
    eng = _engine(ratio=0.5)
    assert eng._band_regime_veto({"adx_4h": 45.0}) == ""


def test_config_default_is_off():
    import os

    import config
    if os.getenv("BAND_REGIME_FILTER_ENABLED") is None:
        assert config.BAND_REGIME_FILTER_ENABLED is False


# ── _band_regime_veto: veto 1 — 4h ADX > 30 ─────────────────────────────────

def test_adx_above_30_vetoes(flag_on):
    eng = _engine(ratio=1.0)
    assert eng._band_regime_veto({"adx_4h": 30.1}) == "band_regime_filter:adx_4h>30"
    assert eng._band_regime_veto({"adx_4h": 45.0}) == "band_regime_filter:adx_4h>30"


def test_adx_at_or_below_30_allows(flag_on):
    """Strict > 30: the screen's 20-30 bucket (WR 68.1%) is a KEEP bucket."""
    eng = _engine(ratio=1.0)
    assert eng._band_regime_veto({"adx_4h": 30.0}) == ""
    assert eng._band_regime_veto({"adx_4h": 22.0}) == ""


def test_adx_missing_fails_open(flag_on):
    """Sources that don't carry adx_4h (e.g. Claude ingestion) fail open."""
    eng = _engine(ratio=1.0)
    assert eng._band_regime_veto({}) == ""
    assert eng._band_regime_veto({"adx_4h": None}) == ""


def test_adx_garbage_fails_open(flag_on):
    eng = _engine(ratio=1.0)
    assert eng._band_regime_veto({"adx_4h": "n/a"}) == ""


# ── _band_regime_veto: veto 2 — BTC 1h ATR ratio < 0.7 ──────────────────────

def test_btc_ratio_below_0p7_vetoes(flag_on):
    eng = _engine(ratio=0.69)
    assert eng._band_regime_veto({"adx_4h": 22.0}) == "band_regime_filter:btc_vol<0.7"


def test_btc_ratio_at_or_above_0p7_allows(flag_on):
    """Strict < 0.7: the 0.7-1.3 bucket (WR 67.2%) is a KEEP bucket."""
    assert _engine(ratio=0.7)._band_regime_veto({"adx_4h": 22.0}) == ""
    assert _engine(ratio=1.2)._band_regime_veto({"adx_4h": 22.0}) == ""


def test_btc_ratio_unavailable_fails_open(flag_on):
    assert _engine(ratio=None)._band_regime_veto({"adx_4h": 22.0}) == ""


def test_bvp_exception_fails_open(flag_on):
    class _Boom:
        def current_ratio(self, indicator_cache):
            raise RuntimeError("boom")

    eng = BotEngine.__new__(BotEngine)
    eng._btc_vol_pause = _Boom()
    eng.mcp_brain = None
    assert eng._band_regime_veto({"adx_4h": 22.0}) == ""


def test_real_btc_vol_pause_wired_through_veto(flag_on, gate):
    """End-to-end with the REAL BtcVolPause: calm-below-median BTC tape
    (ratio < 0.7) vetoes; normal tape allows."""
    _warm(gate, atr=1.0, n=5)

    class _Brain:
        _indicator_cache = _cache(0.5)  # ratio 0.5 vs median 1.0

    eng = BotEngine.__new__(BotEngine)
    eng._btc_vol_pause = gate
    eng.mcp_brain = _Brain()
    assert eng._band_regime_veto({"adx_4h": 22.0}) == "band_regime_filter:btc_vol<0.7"
    _Brain._indicator_cache = _cache(1.0)  # ratio 1.0 -> allow
    assert eng._band_regime_veto({"adx_4h": 22.0}) == ""


def test_adx_veto_checked_before_btc_ratio(flag_on):
    """Both toxic -> the ADX reason wins (stable funnel attribution)."""
    eng = _engine(ratio=0.5)
    assert eng._band_regime_veto({"adx_4h": 45.0}) == "band_regime_filter:adx_4h>30"


def test_data_gap_logs_once(flag_on):
    eng = _engine(ratio=None)
    eng._band_regime_veto({})
    seen = set(eng._band_regime_logged)
    eng._band_regime_veto({})  # same gaps again -> no new log keys
    assert eng._band_regime_logged == seen
    assert len(seen) == 2  # adx gap + ratio gap, each once


# ── wire-in pins (source-scan, matching repo scan-test style) ────────────────

def test_veto_lives_only_inside_the_accuracy_band_carveout():
    """The veto must run inside _execute_open's _acc_mode_on block — after the
    band geometry is applied, before the R:R gate — and set reject_reason so
    the candidates funnel stays auditable. Exactly one call site."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert src.count("self._band_regime_veto(action)") == 1
    # Band lane is now inverted-geometry after apply (not merely TP-changed).
    i = src.index("_acc_mode_on = (")
    assert "tp_pct < sl_pct" in src[i:i + 280]
    j = src.index("actual_rr = tp_pct / sl_pct", i)
    window = src[i:j]
    assert "self._band_regime_veto(action)" in window, (
        "veto must be inside the accuracy-band carve-out (band-lane ONLY)"
    )
    k = window.index("self._band_regime_veto(action)")
    assert 'action["reject_reason"] = _brf_reason' in window[k:], (
        "vetoed entries must record their distinct reject_reason"
    )


def test_deep_breakout_lane_and_shadow_probes_unaffected():
    """Binding scope rule: the filter must be unreachable from the
    deep_breakout lane, shadow probes, and mcp_brain's scoring (which serves
    all consumers)."""
    for f in ("core/deep_breakout_lane.py", "core/shadow_runner.py",
              "core/mcp_brain.py"):
        assert "band_regime" not in Path(f).read_text(encoding="utf-8"), f
    for p in Path("core/agents").glob("*.py"):
        assert "band_regime" not in p.read_text(encoding="utf-8"), str(p)


def test_algo_action_payload_threads_adx_4h():
    """mcp_brain's algo action builder must carry the already-computed 4h ADX
    (threaded, not recomputed) so the veto can read it at _execute_open."""
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    i = src.index('"type": "OPEN"')
    assert '"adx_4h"' in src[i:i + 1500], (
        "OPEN action payload must include adx_4h for the band-lane veto"
    )
