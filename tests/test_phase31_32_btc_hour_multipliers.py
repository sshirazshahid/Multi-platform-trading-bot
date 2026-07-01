"""Phase 31/32 — cross-regime + hour-of-day soft size multipliers.

Phase 31: BTC cross-regime soft veto (TradingAgents/freqtrade pattern).
The 2026-04-07 disaster (5 SHORT trades all hit SL during a BTC bull
squeeze) shows the catastrophic-day pattern. SHORTS_REQUIRE_BTC_BEAR
config exists but is OFF per UNBLOCK_ALL — we apply a SOFT multiplier:

  BUY  + BTC bear  → ×0.6  (counter-trend size cut)
  SELL + BTC bull  → ×0.6
  aligned          → ×1.0

Phase 32: hour-of-day historical-EV size multiplier. Trade audit
showed 4h+ hold bucket = $-41 of bleed; finer check on UTC-hour cells
catches "hour 17 SHORTs are catastrophic" patterns. Pure warehouse
query on (hour, side, last 30d):

  n < 10:               ×1.0  (insufficient data)
  mean >= 0:            ×1.0  (positive)
  -$0.20 <= mean < 0:   ×0.75
  mean < -$0.20:        ×0.50

Composes with Phase 27 (per-symbol EV), Phase 28 (asymmetric SHORT),
Phase 22 (regime). Pure-data, self-correcting, no biases.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

# ─── Phase 31: BTC cross-regime ───────────────────────────────────────


def test_btc_multiplier_full_size_when_aligned():
    """BUY + BTC bull = aligned → ×1.0."""
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    eng._get_btc_trend = MagicMock(return_value="bull")
    mult, reason = eng._btc_cross_regime_multiplier("buy")
    assert mult == 1.0


def test_btc_multiplier_06_when_buy_in_bear():
    """BUY in BTC bear = counter-trend → ×0.6."""
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    eng._get_btc_trend = MagicMock(return_value="bear")
    mult, reason = eng._btc_cross_regime_multiplier("buy")
    assert mult == 0.6
    assert "btc_bear_vs_buy" in reason


def test_btc_multiplier_06_when_sell_in_bull():
    """SELL in BTC bull = counter-trend → ×0.6 (the 04-07 pattern)."""
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    eng._get_btc_trend = MagicMock(return_value="bull")
    mult, reason = eng._btc_cross_regime_multiplier("sell")
    assert mult == 0.6
    assert "btc_bull_vs_sell" in reason


def test_btc_multiplier_full_size_when_neutral():
    """Neutral BTC → no effect."""
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    eng._get_btc_trend = MagicMock(return_value="neutral")
    assert eng._btc_cross_regime_multiplier("buy") == (1.0, "")
    assert eng._btc_cross_regime_multiplier("sell") == (1.0, "")


def test_btc_multiplier_handles_trend_exception():
    """If _get_btc_trend raises, fall through with full size (don't
    accidentally block trades on internal errors)."""
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    eng._get_btc_trend = MagicMock(side_effect=RuntimeError("boom"))
    mult, reason = eng._btc_cross_regime_multiplier("buy")
    assert mult == 1.0


# ─── Phase 32: Hour-of-day EV ─────────────────────────────────────────


def _patch_warehouse(monkeypatch, rows):
    fake_wh = MagicMock()
    fake_wh.query = MagicMock(return_value=rows)
    import core.warehouse as wh_mod
    monkeypatch.setattr(wh_mod, "get_warehouse",
                        MagicMock(return_value=fake_wh), raising=False)


def test_hour_multiplier_full_size_when_low_sample(monkeypatch):
    _patch_warehouse(monkeypatch, [(0.5,), (-0.3,)])  # n=2 < 10
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    mult, _ = eng._hour_of_day_multiplier("buy")
    assert mult == 1.0


def test_hour_multiplier_full_size_when_positive_ev(monkeypatch):
    rows = [(0.5,)] * 12  # n=12, mean=+0.5
    _patch_warehouse(monkeypatch, rows)
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    mult, _ = eng._hour_of_day_multiplier("buy")
    assert mult == 1.0


def test_hour_multiplier_075_when_mildly_negative(monkeypatch):
    """mean -$0.10, n=12 → ×0.75"""
    rows = [(-0.10,)] * 12
    _patch_warehouse(monkeypatch, rows)
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    mult, reason = eng._hour_of_day_multiplier("buy")
    assert mult == 0.75
    assert "neg" in reason


def test_hour_multiplier_05_when_strongly_negative(monkeypatch):
    """mean -$0.50, n=12 → ×0.50"""
    rows = [(-0.50,)] * 12
    _patch_warehouse(monkeypatch, rows)
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    mult, reason = eng._hour_of_day_multiplier("sell")
    assert mult == 0.50
    assert "negstrong" in reason


def test_hour_multiplier_handles_warehouse_exception(monkeypatch):
    """Defensive: warehouse error → full size (don't accidentally
    block on transient errors)."""
    fake_wh = MagicMock()
    fake_wh.query = MagicMock(side_effect=RuntimeError("db gone"))
    import core.warehouse as wh_mod
    monkeypatch.setattr(wh_mod, "get_warehouse",
                        MagicMock(return_value=fake_wh), raising=False)
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    mult, _ = eng._hour_of_day_multiplier("buy")
    assert mult == 1.0


# ─── Source-level wire-up invariants ──────────────────────────────────


def test_phase31_block_in_size_chain():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_btc_cross_regime_multiplier" in src
    assert "Phase 31 BTC" in src


def test_phase32_block_in_size_chain():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_hour_of_day_multiplier" in src
    assert "Phase 32 hour-EV" in src


def test_phase31_32_run_after_phase27_in_chain():
    """Order: 27 EV → 31 BTC → 32 hour → 22 regime. Pin via source idx."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    p27 = src.index("size_fraction *= _ev_symbol_mult")
    p31 = src.index("size_fraction *= _btc_mult")
    p32 = src.index("size_fraction *= _hr_mult")
    p22 = src.index("size_fraction *= _regime_size_mult")
    assert p27 < p31 < p32 < p22, (
        "expected order: 27 → 31 → 32 → 22 in size_fraction chain")
