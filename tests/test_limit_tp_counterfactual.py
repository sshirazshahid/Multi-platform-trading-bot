"""C10 (tpbot retrofit): limit-TP touched-vs-filled counterfactual (shadow only).

The mandatory honesty gate before any live reduce-only LIMIT TP: today's
sim/resolver model touch==fill for TP — CORRECT for the live market-trigger
TPs the bot actually places, but it would score a resting maker TP as free
money (a limit at the level needs price to trade THROUGH it; the touched-
but-unfilled case then often reverses to the SL). Per the Jun-4 diagnosis
the ceiling of a maker TP is fee savings ~= breakeven — this measurement
decides whether even that is real.

  * limit_tp_counterfactual(scan, sl, tp, is_buy): whole-path simulation of
    a RESTING limit TP — conservative trade-through fill (touch != fill;
    through_frac ~1bp as the tick proxy, no per-symbol tick metadata in the
    resolver), SL-first same-bar pessimistic tie-break (AFML convention,
    same as the primary resolver), time-exit otherwise
  * resolve_one attaches ltp_touched / ltp_filled / ltp_exit_reason
  * _write_outcome persists them; shadow_outcomes migrated additively
  * measurement ONLY — no order-path change anywhere; sim_execution is
    deliberately untouched (its touch==fill mirrors live market-triggers)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.shadow_resolver import limit_tp_counterfactual, resolve_one


def _bar(high, low, close=None):
    close = close if close is not None else (high + low) / 2.0
    return [0, low, high, low, close, 1.0]


# entry 100, SL 95, TP 105 (buy) throughout
SL, TP = 95.0, 105.0


# ── pure counterfactual paths ────────────────────────────────────────────────
def test_trade_through_fills():
    scan = [_bar(103, 99), _bar(105.5, 101)]  # bar 2 trades through 105
    touched, filled, reason = limit_tp_counterfactual(scan, SL, TP, True)
    assert (touched, filled, reason) == (1, 1, "take_profit_limit")


def test_touch_exact_never_through_then_sl_is_the_adverse_case():
    # Touches 105.0 exactly (market-trigger world exits TP) then reverses to
    # the stop — the resting limit never filled: the metric's raison d'etre.
    scan = [_bar(105.0, 100), _bar(101, 94.5)]
    touched, filled, reason = limit_tp_counterfactual(scan, SL, TP, True)
    assert (touched, filled, reason) == (1, 0, "stop_loss")


def test_never_touched_time_exit():
    scan = [_bar(103, 99), _bar(102, 98)]
    touched, filled, reason = limit_tp_counterfactual(scan, SL, TP, True)
    assert (touched, filled, reason) == (0, 0, "time")


def test_same_bar_sl_and_through_is_sl_first():
    # Pessimistic AFML tie-break, consistent with the primary resolver.
    scan = [_bar(106.0, 94.0)]
    touched, filled, reason = limit_tp_counterfactual(scan, SL, TP, True)
    assert filled == 0
    assert reason == "stop_loss"


def test_sell_side_mirror():
    # sell: entry 100, SL 105, TP 95. Trade-through = below 95 by the frac.
    scan = [_bar(101, 94.5)]
    touched, filled, reason = limit_tp_counterfactual(scan, 105.0, 95.0, False)
    assert (touched, filled) == (1, 1)
    # touch-exact only -> unfilled
    scan2 = [_bar(101, 95.0), _bar(105.5, 100)]
    touched, filled, reason = limit_tp_counterfactual(scan2, 105.0, 95.0, False)
    assert (touched, filled, reason) == (1, 0, "stop_loss")


def test_no_tp_level_is_inert():
    touched, filled, reason = limit_tp_counterfactual([_bar(103, 99)], SL, 0.0, True)
    assert (touched, filled) == (0, 0)


# ── resolve_one integration ──────────────────────────────────────────────────
def _row():
    return {
        "side": "buy",
        "entry_px": 100.0,
        "sl_px": SL,
        "tp_px": TP,
        "horizon_bars": 8,
        "projected_notional_current": 1000.0,
    }


def test_resolve_one_attaches_counterfactual_fields():
    # Market-trigger world: TP touch at bar 1 -> take_profit. Limit world:
    # never traded through -> the divergence the metric measures. Pad the
    # scan so the market-world exit (bar 1) leaves no censoring question.
    candles = [_bar(105.0, 101), _bar(104, 96)] + [_bar(103, 99)] * 6
    out = resolve_one(_row(), candles)
    assert out is not None
    assert out["exit_reason"] == "take_profit"
    assert out["ltp_touched"] == 1
    assert out["ltp_filled"] == 0
    assert out["ltp_exit_reason"] in ("stop_loss", "time")


def test_resolve_one_filled_when_through():
    candles = [_bar(106.0, 101)] + [_bar(103, 99)] * 7
    out = resolve_one(_row(), candles)
    assert out is not None
    assert out["ltp_filled"] == 1
    assert out["ltp_exit_reason"] == "take_profit_limit"


# ── persistence ──────────────────────────────────────────────────────────────
@pytest.fixture
def wh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    import core.warehouse

    core.warehouse._default = None
    from core.warehouse import get_warehouse

    yield get_warehouse()
    core.warehouse._default = None


def test_shadow_outcomes_migrated_with_ltp_columns(wh):
    cols = {r["name"] for r in wh.query("PRAGMA table_info(shadow_outcomes)")}
    assert {"ltp_touched", "ltp_filled", "ltp_exit_reason"} <= cols


def test_write_outcome_persists_counterfactual(wh):
    from core.shadow_resolver import _write_outcome

    candles = [_bar(105.0, 101), _bar(104, 96)] + [_bar(103, 99)] * 6
    out = resolve_one(_row(), candles)
    # minimal decisions row so the UPDATE half of the txn has a target
    wh._conn().execute("INSERT OR IGNORE INTO shadow_decisions(id, ts) VALUES (1, 0)")
    wh._conn().commit()
    _write_outcome(wh, "prop-c10", out, now=123)
    row = wh.query(
        "SELECT ltp_touched, ltp_filled, ltp_exit_reason "
        "FROM shadow_outcomes WHERE proposal_id='prop-c10'"
    )[0]
    assert row["ltp_touched"] == 1
    assert row["ltp_filled"] == 0
    assert row["ltp_exit_reason"] in ("stop_loss", "time")
