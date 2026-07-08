"""C11 (tpbot retrofit): backtest_v3 exercises the PRODUCTION TP path.

Gap (survey 2026-07-08, biggest backtest-realism finding): backtest_v3
re-implemented its own scorer with a hard tp_pct = sl_pct * 2.0 — the
official V3 backtest never exercised DistFitSL, the TP logic the live bot
actually runs (mcp_brain routes every entry through it). Backtest TP
realism and live TP selection had silently drifted apart: any DistFitSL
change shipped unvalidated.

score_bar now prefers the production fit (cached once per symbol/side per
run — fitted quantiles are data-driven, not ATR-scaled) and keeps the old
formula as the byte-identical fallback when the fit is unavailable
('fallback' source, no symbol passed, or any error).

⚠ Honesty (documented in backtest_v3): DistFitSL fits on the warehouse AS
OF NOW — replaying a historical window under today's fit is "current TP
policy replay", NOT an out-of-sample backtest. PAIR_OVERRIDES / S-D / STAR
are operator/live-context refinement layers and deliberately stay out.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import backtest_v3
from core.dist_fit_sl import FitResult


def _bars():
    bar_4h = {"adx": 30.0, "bb_width": 2.0, "ema_gap_pct": 0.5,
              "ema20_above_50": True}
    bar_1h = {"ema20_above_50": True, "rsi": 50.0, "adx": 25.0,
              "atr_pct": 2.0}
    return bar_1h, bar_4h


@pytest.fixture(autouse=True)
def _clear_fit_cache():
    backtest_v3._FIT_CACHE.clear()
    yield
    backtest_v3._FIT_CACHE.clear()


def _patch_compute(monkeypatch, result=None, raises=False):
    calls = {"n": 0}

    def fake_compute(self, symbol, side, atr_pct, **kw):
        calls["n"] += 1
        if raises:
            raise RuntimeError("no warehouse")
        return result

    from core import dist_fit_sl
    monkeypatch.setattr(dist_fit_sl.DistFitSL, "compute", fake_compute)
    return calls


def test_fitted_path_replaces_the_hardcode(monkeypatch):
    _patch_compute(monkeypatch, FitResult(
        sl_pct=2.2, tp_pct=5.1, source="fitted", n=42, cell="c", r_target=2.3))
    bar_1h, bar_4h = _bars()
    out = backtest_v3.score_bar(bar_1h, bar_4h, symbol="ETH/USDT:USDT")
    assert out["side"] == "buy"
    assert out["sl_pct"] == pytest.approx(2.2)
    assert out["tp_pct"] == pytest.approx(5.1)      # NOT sl*2 = 4.4
    assert out["tp_pct"] != pytest.approx(out["sl_pct"] * 2.0)


def test_fallback_source_keeps_legacy_formula(monkeypatch):
    # DistFitSL 'fallback' = <30 trades/cell -> its ATR-scaled numbers depend
    # on per-bar ATR; the backtest keeps the LEGACY formula there (cacheable,
    # byte-identical to pre-C11) rather than freezing one ATR into the cache.
    _patch_compute(monkeypatch, FitResult(
        sl_pct=3.6, tp_pct=9.0, source="fallback", n=0, cell="c", r_target=2.5))
    bar_1h, bar_4h = _bars()
    out = backtest_v3.score_bar(bar_1h, bar_4h, symbol="ETH/USDT:USDT")
    assert out["sl_pct"] == pytest.approx(3.0)      # max(1.5,min(3.5,2.0*1.5))
    assert out["tp_pct"] == pytest.approx(6.0)      # legacy sl*2


def test_compute_error_keeps_legacy_formula(monkeypatch):
    _patch_compute(monkeypatch, raises=True)
    bar_1h, bar_4h = _bars()
    out = backtest_v3.score_bar(bar_1h, bar_4h, symbol="ETH/USDT:USDT")
    assert out["sl_pct"] == pytest.approx(3.0)
    assert out["tp_pct"] == pytest.approx(6.0)


def test_no_symbol_is_byte_identical_legacy(monkeypatch):
    calls = _patch_compute(monkeypatch, FitResult(
        sl_pct=2.2, tp_pct=5.1, source="fitted", n=42, cell="c", r_target=2.3))
    bar_1h, bar_4h = _bars()
    out = backtest_v3.score_bar(bar_1h, bar_4h)  # legacy call shape
    assert out["sl_pct"] == pytest.approx(3.0)
    assert out["tp_pct"] == pytest.approx(6.0)
    assert calls["n"] == 0  # production path never consulted


def test_fit_cached_once_per_symbol_side(monkeypatch):
    calls = _patch_compute(monkeypatch, FitResult(
        sl_pct=2.2, tp_pct=5.1, source="fitted", n=42, cell="c", r_target=2.3))
    bar_1h, bar_4h = _bars()
    backtest_v3.score_bar(bar_1h, bar_4h, symbol="ETH/USDT:USDT")
    backtest_v3.score_bar(bar_1h, bar_4h, symbol="ETH/USDT:USDT")
    assert calls["n"] == 1  # one warehouse-backed fit per (symbol, side) per run


def test_run_backtest_passes_symbol_through():
    src = Path("backtest_v3.py").read_text(encoding="utf-8")
    i = src.index("def run_backtest")
    assert "score_bar(bar_1h, bar_4h, symbol=symbol)" in src[i:i + 6000], (
        "run_backtest must feed the symbol so the production TP path engages")
