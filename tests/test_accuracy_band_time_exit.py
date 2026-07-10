"""ACCURACY_TARGET_MODE time-exit leak fix (2026-07-10).

Warehouse diagnosis: with the accuracy band live (TP = 0.40-0.5 x SL), today's
take_profit exits ran 10/10 wins while STALE (n=5) and AGE_LIMIT (n=1) were ALL
losses — the Phase-14-era ~60min/4h time cutoffs close band trades BEFORE the
tight TP can be touched, converting would-be geometric wins into guaranteed
losses. The band's 63-67% WR was audited on pure first-touch SL/TP with a 72h
horizon, so while a band position is inside ACCURACY_TARGET_MODE
["max_hold_hours"] (env ACCURACY_MAX_HOLD_HOURS, default 72) the time-based
close authorities (STALE / AGE_LIMIT / AGE_LOSS / scalp time wall / scalp
stale) must be SUPPRESSED and first-touch SL/TP must govern. Past the horizon
the existing time exits apply (zombie protection). Non-band positions and
flag-off keep today's behavior byte-identically.

Band marker: `pos._accuracy_band = True` stamped at the _execute_open
chokepoint when the band rewrites tp_pct, plus a restart-surviving geometry
fallback (TP distance < entry-SL distance — impossible for non-band entries
because the min-R:R gate enforces tp >= min_rr x sl, and tsmom has tp=0).

Harness mirrors tests/test_scalp_stale_winner_exempt.py.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config import RISK, SCALP_MODE
from core.order_manager import OrderManager, _accuracy_band_hold_active
from core.position_tracker import Position

# ── fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture
def acc_on(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
         "max_hold_hours": 72.0},
        raising=False,
    )


@pytest.fixture
def acc_off(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": False, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
         "max_hold_hours": 72.0},
        raising=False,
    )


@pytest.fixture
def stable_risk(monkeypatch):
    """Pin the RISK time cutoffs so ages used below are deterministic."""
    monkeypatch.setitem(RISK, "max_position_age_hours", 24)
    monkeypatch.setitem(RISK, "max_stale_hours", 4)


def _om(monkeypatch):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = True
    om.mcp_brain = None
    om.close_position = MagicMock()
    om.accrue_paper_funding = MagicMock()
    om.sim = MagicMock()
    om.sim.check_wick_trigger.return_value = ("", None)
    om.trailing = MagicMock()
    om.trailing.update.return_value = (False, "none", 0.0)
    om._early_breakeven_move = lambda pos, price, eff: eff
    monkeypatch.setattr("core.order_manager._should_fire_partial_tp",
                        lambda *a, **k: (False, 0.0, 0.0))
    return om


def _pos(*, age_hours: float, entry=100.0, sl=92.0, tp=103.0, side="buy"):
    """Float-epoch open_time so duration_minutes works in the age/stale block.

    Default geometry is a BAND shape: TP distance (3.0) < SL distance (8.0).
    """
    return Position(
        id="b1", exchange="Binance", symbol="BTC/USDT", side=side,
        market_type="futures", strategy="claude_portfolio",
        entry_price=entry, size=1.0, stop_loss=sl, take_profit=tp,
        leverage=1, open_time=time.time() - age_hours * 3600.0)


def _scalp_pos(*, age_min: float, entry=100.0, sl=92.0, tp=100.5):
    """tz-aware datetime open_time so the SCALP block computes a real age."""
    ot = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    p = Position(
        id="s1", exchange="Binance", symbol="BTC/USDT", side="buy",
        market_type="futures", strategy="scalp",
        entry_price=entry, size=1.0, stop_loss=sl, take_profit=tp,
        leverage=1, open_time=ot)
    p._scalp = True
    return p


def _run(om, pos, *, net_pnl, net_pct, last=101.0):
    ex = MagicMock()
    ex.name = "Binance"
    ex.fetch_ticker.return_value = {"last": last}
    om.tracker.get_open.return_value = [pos]
    om._net_pnl_at_price = MagicMock(return_value=(net_pnl, net_pct, pos.entry_price))
    om.check_sl_tp(ex, "futures")
    return om.close_position


def _closed_reason(close_mock):
    if not close_mock.called:
        return None
    args = close_mock.call_args.args
    return args[2] if len(args) > 2 else close_mock.call_args.kwargs.get("reason")


_STALE_H = 5.0        # >= max_stale_hours (pinned 4), well inside 72h horizon
_AGE_H = 25.0         # >= max_position_age_hours (pinned 24), inside horizon
_PAST_HORIZON_H = 80.0


# ── helper unit tests: _accuracy_band_hold_active ────────────────────────────

def test_helper_flag_off_is_always_false(acc_off):
    p = _pos(age_hours=1.0)
    p._accuracy_band = True
    assert _accuracy_band_hold_active(p, 1.0) is False


def test_helper_stamped_flag_inside_horizon(acc_on):
    p = _pos(age_hours=1.0, tp=200.0)  # non-band geometry, flag wins
    p._accuracy_band = True
    assert _accuracy_band_hold_active(p, 1.0) is True


def test_helper_geometry_marker_survives_restart(acc_on):
    # No stamped flag (restart lost it): TP dist 3 < SL dist 8 -> band.
    assert _accuracy_band_hold_active(_pos(age_hours=1.0), 1.0) is True
    # Short side too.
    short = _pos(age_hours=1.0, side="sell", sl=108.0, tp=97.0)
    assert _accuracy_band_hold_active(short, 1.0) is True


def test_helper_geometry_uses_entry_stop_not_trailed_stop(acc_on):
    # Trailing/BE moves stop_loss toward entry; the IMMUTABLE entry stop must
    # keep the marker alive (entry_stop_loss captured in __post_init__).
    p = _pos(age_hours=1.0)          # entry 100, sl 92, tp 103
    p.stop_loss = 99.5               # trailed to near-breakeven
    assert _accuracy_band_hold_active(p, 1.0) is True


def test_helper_non_band_geometry_is_false(acc_on):
    # Normal 2:1 shape: TP dist 16 > SL dist 8 -> not a band position.
    assert _accuracy_band_hold_active(_pos(age_hours=1.0, tp=116.0), 1.0) is False
    # tsmom shape (tp=0) never matches.
    assert _accuracy_band_hold_active(_pos(age_hours=1.0, tp=0.0), 1.0) is False


def test_helper_past_horizon_is_false(acc_on):
    p = _pos(age_hours=_PAST_HORIZON_H)
    p._accuracy_band = True
    assert _accuracy_band_hold_active(p, _PAST_HORIZON_H) is False


def test_helper_fails_closed_on_garbage_pos(acc_on):
    junk = SimpleNamespace(entry_price=None, take_profit=None, stop_loss=None)
    assert _accuracy_band_hold_active(junk, 1.0) is False


# ── (a) band position suppresses STALE inside horizon ───────────────────────

def test_band_position_is_NOT_stale_closed_inside_horizon(
        acc_on, stable_risk, monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos(age_hours=_STALE_H), net_pnl=-0.05, net_pct=-0.1)
    assert not close.called, (
        "a band position inside the 72h horizon must NOT be STALE-closed — "
        "first-touch SL/TP governs")


def test_band_position_is_NOT_age_limit_closed_inside_horizon(
        acc_on, stable_risk, monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos(age_hours=_AGE_H), net_pnl=-2.0, net_pct=-2.0)
    assert _closed_reason(close) != "AGE_LIMIT", (
        "a losing band position inside the horizon must not be AGE_LIMIT-closed")


def test_band_scalp_is_NOT_scalp_stale_or_wall_closed(acc_on, monkeypatch):
    # Band scalp aged past BOTH the stale window and the 60-min time wall:
    # scalp time exits are suppressed, so the deterministic TP (100.5, below
    # the 101.0 mark) closes it downstream — a geometric win, not a time loss.
    om = _om(monkeypatch)
    age = SCALP_MODE["time_wall_min"] + 10
    close = _run(om, _scalp_pos(age_min=age), net_pnl=0.0, net_pct=0.0)
    assert _closed_reason(close) == "take_profit", (
        "band scalp must reach its TP, not scalp_time_wall/scalp_stale_close")


# ── (b) non-band position keeps today's behavior exactly ────────────────────

def test_non_band_position_still_stale_closes(acc_on, stable_risk, monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos(age_hours=_STALE_H, tp=116.0),
                 net_pnl=-0.05, net_pct=-0.1)
    assert _closed_reason(close) == "STALE"


def test_non_band_position_still_age_limit_closes(
        acc_on, stable_risk, monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos(age_hours=_AGE_H, tp=116.0),
                 net_pnl=-2.0, net_pct=-2.0)
    assert _closed_reason(close) == "AGE_LIMIT"


# ── (c) past-horizon band position closes (zombie protection) ───────────────

def test_band_position_past_horizon_stale_closes(
        acc_on, stable_risk, monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos(age_hours=_PAST_HORIZON_H),
                 net_pnl=-0.05, net_pct=-0.1)
    assert _closed_reason(close) == "STALE"


# ── (d) flag OFF -> byte-identical behavior ──────────────────────────────────

def test_flag_off_band_geometry_still_stale_closes(
        acc_off, stable_risk, monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos(age_hours=_STALE_H), net_pnl=-0.05, net_pct=-0.1)
    assert _closed_reason(close) == "STALE", (
        "flag off must preserve today's STALE behavior even on band geometry")


# ── config knob ──────────────────────────────────────────────────────────────

def test_config_max_hold_hours_default_72():
    import os

    import config
    assert "max_hold_hours" in config.ACCURACY_TARGET_MODE
    if os.getenv("ACCURACY_MAX_HOLD_HOURS") is None:
        assert config.ACCURACY_TARGET_MODE["max_hold_hours"] == 72.0


# ── (e) source-scan pins ─────────────────────────────────────────────────────

def test_time_exit_authorities_are_band_guarded():
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    assert "def _accuracy_band_hold_active" in src
    # AGE_LIMIT / AGE_LOSS / STALE block guard.
    i = src.index("POSITION AGE LIMIT ENFORCEMENT")
    block = src[i:i + 6000]
    assert "_accuracy_band_hold_active" in block, (
        "the AGE_LIMIT/AGE_LOSS/STALE block must consult the band hold")
    # SCALP time wall + scalp stale guard.
    j = src.index("SCALP TIME WALL")
    scalp_block = src[j:j + 3000]
    assert "_accuracy_band_hold_active" in scalp_block, (
        "the scalp time wall / scalp stale block must consult the band hold")


def test_execute_open_stamps_band_marker():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i = src.index("def _execute_open")
    block = src[i:src.index("def _execute_close")]
    assert "_accuracy_band = True" in block, (
        "_execute_open must stamp pos._accuracy_band when the band rewrote TP")


def test_config_exposes_env_knob():
    src = Path("config.py").read_text(encoding="utf-8")
    assert "ACCURACY_MAX_HOLD_HOURS" in src


# ── 2026-07-10 second leak: partial-TP/breakeven vs the band ─────────────────
# Live forensics (per-side epoch, first 5 closes): 3 losses had
# exit_px == entry_px EXACTLY with positive partial_realized_pnl — the
# compressed band TP put the partial level a whisker from entry, partial-TP
# fired instantly, SL moved to breakeven, wiggle stopped it, fees made it a
# loss. Band positions must be PURE first-touch: no partial, no BE move.
def _band_pos(side="buy", entry=100.0, tp=100.9, sl=98.0):
    class P:
        pass
    p = P()
    p.side = side
    p.entry_price = entry
    p.take_profit = tp
    p.stop_loss = sl
    p.partial_taken = False
    return p


def test_partial_tp_never_fires_on_band_position(acc_on):
    from core.order_manager import _should_fire_partial_tp

    pos = _band_pos()  # tp dist 0.9 < sl dist 2.0 -> band geometry
    fire, _, _ = _should_fire_partial_tp(
        pos, price=100.89, partial_tp_config={"enabled": True,
                                              "first_take_at_pct": 0.5,
                                              "first_take_size": 0.5})
    assert fire is False, "band positions are pure first-touch — no partial TP"


def test_partial_tp_still_fires_on_non_band_position(acc_on):
    from core.order_manager import _should_fire_partial_tp

    pos = _band_pos(tp=104.0, sl=98.0)  # tp dist 4 > sl dist 2 -> NOT band
    fire, size, level = _should_fire_partial_tp(
        pos, price=102.5, partial_tp_config={"enabled": True,
                                             "first_take_at_pct": 0.5,
                                             "first_take_size": 0.5})
    assert fire is True and size == 0.5


def test_partial_tp_unchanged_when_flag_off(acc_off):
    from core.order_manager import _should_fire_partial_tp

    pos = _band_pos()  # inverted geometry but flag OFF -> legacy behavior
    fire, _, _ = _should_fire_partial_tp(
        pos, price=100.89, partial_tp_config={"enabled": True,
                                              "first_take_at_pct": 0.5,
                                              "first_take_size": 0.5})
    assert fire is True, "flag off must stay byte-identical"
