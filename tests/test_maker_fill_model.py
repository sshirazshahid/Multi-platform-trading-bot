"""Phase 3 — S5 maker-aware execution sim tests (cost-reduction overlay, NOT alpha).

Pins three behaviors:
  1. through-trade fill: a resting limit fills ONLY if the next bar trades
     THROUGH it; a price that runs away leaves the order UNFILLED (no phantom).
  2. adverse selection: because winning breakouts run away (unfilled) and losers
     come back (filled), the maker overlay LOWERS EV vs the 'always fills' shortcut.
  3. governance: S5 is cost-reduction-only — refused as standalone alpha, and the
     overlay may only be layered onto a strategy that already cleared accept().
"""
import json

import pytest

from core.maker_fill_model import (
    S5_ROLE,
    STANDALONE_ALPHA_ALLOWED,
    maker_fills,
    realized_maker_fill_rate,
    refuse_as_standalone_alpha,
    simulate_maker_overlay,
)


# ----------------------------------------------------------- through-trade fill
def test_buy_limit_unfilled_when_price_runs_away():
    # buy limit at 100; next bar gaps up and never trades down to 100 -> no fill
    bar = {"open": 100.6, "high": 101.5, "low": 100.4, "close": 101.2}
    assert maker_fills("buy", 100.0, bar) is False


def test_buy_limit_filled_when_bar_trades_through():
    bar = {"open": 100.2, "high": 100.5, "low": 99.0, "close": 99.3}
    assert maker_fills("buy", 100.0, bar) is True


def test_sell_limit_unfilled_when_price_runs_away():
    # sell limit at 100; next bar drops and never trades up to 100 -> no fill
    bar = {"open": 99.4, "high": 99.6, "low": 98.0, "close": 98.2}
    assert maker_fills("sell", 100.0, bar) is False


def test_sell_limit_filled_when_bar_trades_through():
    bar = {"open": 99.8, "high": 101.0, "low": 99.5, "close": 100.7}
    assert maker_fills("sell", 100.0, bar) is True


def test_touch_without_through_does_not_fill():
    # exact touch at the limit (low == limit) is NOT a through-trade (queue model)
    bar = {"open": 100.5, "high": 100.8, "low": 100.0, "close": 100.6}
    assert maker_fills("buy", 100.0, bar) is False


# ------------------------------------------------------------- adverse selection
def _adverse_trades():
    # winner is a breakout that runs away (unfilled); losers come back (filled)
    return [
        {  # WINNER, runs away -> unfilled
            "side": "buy", "limit_px": 100.0,
            "fill_bar": {"high": 101.5, "low": 100.4},
            "net_pnl_taker": 0.05,
        },
        {  # loser, comes back -> filled
            "side": "buy", "limit_px": 100.0,
            "fill_bar": {"high": 100.6, "low": 99.0},
            "net_pnl_taker": -0.02,
        },
        {  # loser, comes back -> filled
            "side": "buy", "limit_px": 100.0,
            "fill_bar": {"high": 100.3, "low": 99.5},
            "net_pnl_taker": -0.01,
        },
    ]


def test_adverse_selection_lowers_ev_vs_always_fills():
    rep = simulate_maker_overlay(
        _adverse_trades(), venue="binance", base_accepted=True
    )
    assert rep["n_filled"] == 2  # winner did not fill
    assert rep["ev_maker"] < rep["ev_always_fills"]


def test_maker_overlay_applies_fee_saving():
    rep = simulate_maker_overlay(
        _adverse_trades(), venue="binance", base_accepted=True
    )
    # entry taker->maker saving is strictly positive on binance futures
    assert rep["maker_fee_saving"] > 0.0


# ----------------------------------------------------------------- governance
def test_overlay_refuses_unaccepted_base_strategy():
    with pytest.raises(ValueError):
        simulate_maker_overlay(
            _adverse_trades(), venue="binance", base_accepted=False
        )


def test_s5_is_cost_reduction_only():
    assert STANDALONE_ALPHA_ALLOWED is False
    assert "cost" in S5_ROLE.lower()
    with pytest.raises(ValueError):
        refuse_as_standalone_alpha()


# --------------------------------------------------------- realized fill rate
def test_realized_fill_rate_from_execution_stats(tmp_path):
    p = tmp_path / "execution_stats.json"
    p.write_text(json.dumps({"total_orders": 10, "limit_fills": 4}), encoding="utf-8")
    assert realized_maker_fill_rate(p) == pytest.approx(0.4)


def test_realized_fill_rate_none_when_no_data(tmp_path):
    p = tmp_path / "missing.json"
    assert realized_maker_fill_rate(p) is None
