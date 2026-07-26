"""Full-bot audit fixes (2026-06-06) — correctness batch (#2 hygiene, #3, #5).

All confirmed by the adversarial audit. None changes expectancy (the bot is NO_EDGE); these reduce
attribution corruption / availability / LIVE-correctness risk only.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.risk_manager import RiskManager

_OM_SRC = (Path(__file__).resolve().parents[1] / "core" / "order_manager.py").read_text(encoding="utf-8")
_RM_SRC = (Path(__file__).resolve().parents[1] / "core" / "risk_manager.py").read_text(encoding="utf-8")


# ── #3: SL-floor TP expansion must never ship below min_rr_ratio ───────────────
def test_sl_floor_expansion_never_ships_below_min_rr_buy():
    rm = RiskManager()
    # Tiny ATR forces the 1% SL floor; the 2.0x TP-expansion cap would otherwise leave
    # R:R = 0.8:1 (< min_rr_ratio 1.2) — entry 100, sl_dist 0.2 -> floored to 1.0, tp 0.4 -> 0.8.
    sl, tp = rm.get_sl_tp(entry=100.0, side="buy", atr=0.2, atr_sl_mult=1.0, atr_tp_mult=2.0)
    sl_dist, tp_dist = 100.0 - sl, tp - 100.0
    assert sl_dist > 0 and tp_dist > 0
    assert tp_dist / sl_dist >= 1.2 - 1e-6, f"R:R {tp_dist/sl_dist:.3f} below min_rr_ratio 1.2"


def test_sl_floor_expansion_never_ships_below_min_rr_sell():
    rm = RiskManager()
    sl, tp = rm.get_sl_tp(entry=100.0, side="sell", atr=0.2, atr_sl_mult=1.0, atr_tp_mult=2.0)
    sl_dist, tp_dist = sl - 100.0, 100.0 - tp
    assert sl_dist > 0 and tp_dist > 0
    assert tp_dist / sl_dist >= 1.2 - 1e-6, f"R:R {tp_dist/sl_dist:.3f} below min_rr_ratio 1.2"


def test_normal_rr_unchanged_when_no_floor():
    # When ATR is healthy (no floor), the normal R:R is preserved (>= min, not forced to it).
    rm = RiskManager()
    sl, tp = rm.get_sl_tp(entry=100.0, side="buy", atr=2.0, atr_sl_mult=1.0, atr_tp_mult=2.5)
    sl_dist, tp_dist = 100.0 - sl, tp - 100.0
    assert tp_dist / sl_dist >= 2.5 - 1e-6  # original 2.5:1 intact (no floor triggered)


# ── #5: partial close must set positionSide for hedge-mode futures ─────────────
def _partial_body() -> str:
    m = re.search(r"def partial_close_position\(self[^)]*\)[^:]*:[\s\S]+?(?=\n    def |\Z)", _OM_SRC)
    assert m, "partial_close_position not found"
    return m.group(0)


def test_partial_close_keeps_reduce_only_on_futures():
    body = _partial_body()
    assert re.search(r'market_type\s*==\s*"futures"[\s\S]{0,400}?reduceOnly', body)


def test_partial_close_sets_positionside_for_hedge_mode():
    body = _partial_body()
    # Hedge-mode (NOT in _oneway_mode) futures partial close must carry positionSide so ccxt can
    # disambiguate the leg — mirroring close_position. Without it the partial leg is ambiguous on
    # Binance/Bybit hedge accounts.
    assert "positionSide" in body, "partial close missing positionSide for hedge-mode futures"
    assert "_oneway_mode" in body, "positionSide must be gated on hedge mode (not in _oneway_mode)"


# ── #2: daily-loss breaker must read _daily_pnl under the lock (hygiene) ────────
def test_daily_loss_breaker_reads_pnl_under_lock():
    m = re.search(r"def _daily_loss_reason\(self[\s\S]+?(?=\n    def |\Z)", _RM_SRC)
    assert m, "_daily_loss_reason not found"
    body = m.group(0)
    # The breaker read of _daily_pnl must be serialized with the locked writer (record_trade_pnl).
    assert re.search(r'with self\._lock:[\s\S]{0,160}?_daily_pnl', body), (
        "daily-loss breaker must read _daily_pnl under self._lock to match the writer"
    )
