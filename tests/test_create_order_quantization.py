"""C2 (tpbot retrofit): quantize at the create_order boundary + partial-close rounding.

Gap (survey 2026-07-08): BaseExchange.round_price/round_quantity exist and the
highest-risk active path uses them, but enforcement was by-convention at call
sites — at least six live order paths bypassed rounding entirely (partial
close, smart-executor limit price, spot-manager sell, fund-ops buy/sell,
external closes) and worked only because ccxt/venues tolerated it. This
commit makes ``BaseExchange.create_order`` the single quantization chokepoint:

  * ``amount`` -> round_quantity (floor to lot step; never over-exposes)
  * ``price``  -> round_price (when given)
  * trigger-price params (stopPrice / triggerPrice / stopLossPrice /
    takeProfitPrice / activationPrice) -> round_price (when numeric)
  * an amount that floors to ZERO at the lot step raises ValueError
    fail-closed instead of sending dust the venue would reject cryptically
  * non-numeric values pass through untouched (never crash the boundary)

And fixes the real tracker-drift bug in ``partial_close_position``: the
partial size is quantized BEFORE the notional check / the order / the tracker
decrement, so ``position.size`` can no longer drift from the venue fill on
step-constrained contracts (e.g. Bitget 0.1-step).
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.order_manager import OrderManager
from core.position_tracker import Position
from exchanges.base import BaseExchange, OrderSubmissionUncertain


@pytest.fixture(autouse=True)
def _isolate_warehouse(tmp_path, monkeypatch):
    """Partial-close tests reach the warehouse via C6's _record_lifecycle —
    without tmp isolation the module-level singleton (and its thread-local
    connection) would pin the PRODUCTION data/ dir for every later test in
    the same process (order-dependent pollution, found 2026-07-09)."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    import core.warehouse
    core.warehouse._default = None
    yield
    core.warehouse._default = None


class _StubExchange(BaseExchange):
    """Concrete subclass for unit-testing the create_order boundary
    without touching real ccxt clients (same idiom as test_leverage_clamp)."""

    name = "TestEx"

    def _init_exchange(self):  # type: ignore[override]
        return None


def _floor_tick(p, tick=0.1):
    return f"{math.floor(round(float(p) / tick, 9)) * tick:.1f}"


@pytest.fixture
def ex():
    b = _StubExchange.__new__(_StubExchange)
    b.exchange = MagicMock()
    # amount step 0.01, price tick 0.1 — plain dicts, as real ccxt returns.
    b.exchange.markets = {"ETH/USDT": {"precision": {"amount": 0.01, "price": 0.1}}}
    b.exchange.load_markets.return_value = b.exchange.markets
    b.exchange.price_to_precision.side_effect = lambda s, p: _floor_tick(p)
    b.exchange.create_order.return_value = {"id": "o1"}
    b._ready = lambda: True
    return b


def _sent(ex):
    """(symbol, type, side, amount, price, params) of the ccxt call."""
    assert ex.exchange.create_order.call_count == 1
    return ex.exchange.create_order.call_args[0]


# ── amount quantization ──────────────────────────────────────────────────────
def test_amount_floored_to_lot_step(ex):
    ex.create_order("ETH/USDT", "market", "buy", 0.119)
    _s, _t, _side, amount, _p, _params = _sent(ex)
    assert amount == pytest.approx(0.11)


def test_already_rounded_amount_unchanged(ex):
    ex.create_order("ETH/USDT", "market", "buy", 0.25)
    assert _sent(ex)[3] == pytest.approx(0.25)


def test_amount_flooring_never_rounds_up(ex):
    ex.create_order("ETH/USDT", "market", "sell", 0.2999999)
    assert _sent(ex)[3] == pytest.approx(0.29)


def test_zero_after_rounding_raises_and_never_hits_venue(ex):
    # 0.004 floors to 0 at step 0.01 — dust must fail loudly HERE, not with a
    # cryptic venue rejection (or worse, a silent ccxt pass-through).
    with pytest.raises(ValueError, match="lot step"):
        ex.create_order("ETH/USDT", "market", "buy", 0.004)
    ex.exchange.create_order.assert_not_called()


def test_non_numeric_amount_passes_through(ex):
    # Never crash the boundary on shapes we don't understand — venue decides.
    ex.create_order("ETH/USDT", "market", "buy", "not-a-number")
    assert _sent(ex)[3] == "not-a-number"


# ── price quantization ───────────────────────────────────────────────────────
def test_limit_price_quantized_to_tick(ex):
    ex.create_order("ETH/USDT", "limit", "buy", 0.5, price=100.16)
    assert _sent(ex)[4] == pytest.approx(100.1)


def test_market_order_price_none_stays_none(ex):
    ex.create_order("ETH/USDT", "market", "buy", 0.5)
    assert _sent(ex)[4] is None


# ── trigger-param quantization (the SL/TP surface) ───────────────────────────
def test_trigger_params_quantized(ex):
    ex.create_order(
        "ETH/USDT",
        "market",
        "sell",
        0.5,
        params={
            "stopPrice": 99.97,
            "triggerPrice": 99.97,
            "stopLossPrice": 99.97,
            "takeProfitPrice": 110.16,
            "activationPrice": 105.03,
            "reduceOnly": True,
        },
    )
    params = _sent(ex)[5]
    assert params["stopPrice"] == pytest.approx(99.9)
    assert params["triggerPrice"] == pytest.approx(99.9)
    assert params["stopLossPrice"] == pytest.approx(99.9)
    assert params["takeProfitPrice"] == pytest.approx(110.1)
    assert params["activationPrice"] == pytest.approx(105.0)
    assert params["reduceOnly"] is True  # untouched


def test_string_trigger_price_quantized(ex):
    # Venues/callers sometimes carry prices as strings.
    ex.create_order("ETH/USDT", "market", "sell", 0.5, params={"stopPrice": "99.97"})
    assert _sent(ex)[5]["stopPrice"] == pytest.approx(99.9)


def test_non_numeric_trigger_params_left_alone(ex):
    ex.create_order(
        "ETH/USDT", "market", "sell", 0.5, params={"stopPrice": None, "triggerDirection": "below"}
    )
    params = _sent(ex)[5]
    assert params["stopPrice"] is None
    assert params["triggerDirection"] == "below"


def test_client_order_id_invariant_preserved(ex):
    ex.create_order("ETH/USDT", "market", "buy", 0.5)
    assert _sent(ex)[5].get("clientOrderId")


def test_ambiguous_create_is_reconciled_by_same_client_order_id(ex):
    ex.exchange.create_order.side_effect = TimeoutError("network timeout")
    ex.exchange.fetch_order_by_client_order_id.return_value = {
        "id": "recovered-1",
        "status": "closed",
    }

    order = ex.create_order("ETH/USDT", "market", "buy", 0.5)

    sent_client_id = ex.exchange.create_order.call_args[0][5]["clientOrderId"]
    assert ex.exchange.create_order.call_count == 1
    assert ex.exchange.fetch_order_by_client_order_id.call_args[0][0] == sent_client_id
    assert order["id"] == "recovered-1"
    assert order["_client_order_id"] == sent_client_id
    assert order["_submission_reconciled"] is True


def test_unresolved_create_timeout_is_never_blind_retried(ex):
    ex.exchange.create_order.side_effect = TimeoutError("network timeout")
    ex.exchange.fetch_order_by_client_order_id.side_effect = TimeoutError(
        "reconciliation unavailable"
    )

    with pytest.raises(OrderSubmissionUncertain) as caught:
        ex.create_order("ETH/USDT", "market", "buy", 0.5)

    assert ex.exchange.create_order.call_count == 1
    assert caught.value.client_order_id


# ── partial_close_position rounding (the tracker-drift fix) ──────────────────
def _om(dry_run):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = dry_run
    om.wallet = MagicMock()
    om.sim = MagicMock()
    om.sim.paper_fill_price.return_value = 0.0  # keep caller-provided price
    om.risk._start_balance = 1000.0
    return om


def _pos(size=1.0):
    return Position(
        id="p1",
        exchange="Bitget",
        symbol="XRP/USDT",
        side="buy",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=100.0,
        size=size,
        stop_loss=92.0,
        take_profit=110.0,
        leverage=3,
        open_time=1000.0,
    )


def _step_ex(step=0.1):
    ex = MagicMock()
    ex.name = "Bitget"
    ex.round_quantity.side_effect = lambda s, q, market_type=None: round(math.floor(round(q / step, 9)) * step, 8)
    ex.create_order.return_value = {"id": "pc1"}
    return ex


def test_partial_close_paper_size_rounded_and_tracker_consistent():
    om = _om(dry_run=True)
    pos = _pos(size=1.0)
    ex = _step_ex(step=0.1)
    # 33% of 1.0 -> raw 0.33 -> floors to 0.3 at the 0.1 contract step.
    ok = om.partial_close_position(ex, pos, close_pct=0.33, reason="partial_tp", price=100.0)
    assert ok is True
    # Tracker decremented by the ROUNDED size, not the raw fraction.
    assert pos.size == pytest.approx(0.7)


def test_partial_close_live_sends_rounded_size_to_venue():
    om = _om(dry_run=False)
    om._replace_exchange_sl = MagicMock()
    pos = _pos(size=1.0)
    ex = _step_ex(step=0.1)
    ok = om.partial_close_position(ex, pos, close_pct=0.33, reason="partial_tp", price=100.0)
    assert ok is True
    sent_size = ex.create_order.call_args[0][3]
    assert sent_size == pytest.approx(0.3)  # venue got the rounded size
    assert pos.size == pytest.approx(1.0 - sent_size)  # tracker matches venue


def test_partial_close_zero_after_rounding_refused():
    om = _om(dry_run=True)
    pos = _pos(size=0.5)
    ex = _step_ex(step=1.0)  # coarse contract: 30% of 0.5 -> 0.15 -> floors to 0
    ok = om.partial_close_position(ex, pos, close_pct=0.30, reason="partial_tp", price=100.0)
    assert ok is False
    assert pos.size == pytest.approx(0.5)  # untouched


def test_partial_close_survives_exchange_without_round_quantity():
    # Fakes/stubs (and any nonconforming client) must not break the partial
    # path — rounding is best-effort, falling back to the raw fraction.
    om = _om(dry_run=True)
    pos = _pos(size=1.0)
    ex = MagicMock()
    ex.name = "Binance"
    ex.round_quantity.side_effect = RuntimeError("no metadata")
    ok = om.partial_close_position(ex, pos, close_pct=0.30, reason="partial_tp", price=100.0)
    assert ok is True
    assert pos.size == pytest.approx(0.7)


# ── review 2026-07-09: production-path regression tests ──────────────────────
# The original suite's _step_ex stub implemented the epsilon guard the
# production code LACKED, and 0.25 was a lucky float constant (25.0 divides
# exactly). These run the REAL round_quantity/_market_row on float-trap
# values and the dual spot/swap market rows the review proved diverge.


def _dual_row_ex():
    b = _StubExchange.__new__(_StubExchange)
    b.exchange = MagicMock()
    b.exchange.markets = {
        "SOL/USDT": {"precision": {"amount": 0.0001, "price": 0.01}},
        "SOL/USDT:USDT": {"precision": {"amount": 0.1, "price": 0.1}},
    }
    b.exchange.load_markets.return_value = b.exchange.markets
    b.exchange.price_to_precision.side_effect = lambda s, p: str(p)
    b.exchange.create_order.return_value = {"id": "o1"}
    b._ready = lambda: True
    return b


@pytest.mark.parametrize(
    "qty,step,expected",
    [
        (0.3, 0.1, 0.3),  # 0.3/0.1 = 2.999... -> a bare floor shaved to 0.2
        (2.3, 0.1, 2.3),
        (0.29, 0.01, 0.29),  # 28.999...
        (7.0, 0.0001, 7.0),  # 69999.999...
        (0.25, 0.01, 0.25),  # the old suite's lucky constant still holds
        (0.2999999, 0.1, 0.2),  # genuinely off-grid still floors DOWN
        (0.35, 0.1, 0.3),
    ],
)
def test_round_quantity_epsilon_guard_production_path(qty, step, expected):
    b = _StubExchange.__new__(_StubExchange)
    b.exchange = MagicMock()
    b.exchange.markets = {"X/USDT": {"precision": {"amount": step}}}
    b.exchange.load_markets.return_value = b.exchange.markets
    b._ready = lambda: True
    got = b.round_quantity("X/USDT", qty)
    assert got == pytest.approx(expected, abs=1e-9)
    # idempotency: a rounded value must be a fixed point
    assert b.round_quantity("X/USDT", got) == pytest.approx(expected, abs=1e-9)


def test_futures_amount_quantizes_at_swap_step():
    b = _dual_row_ex()
    # futures resolves the swap row (step 0.1); spot keeps 0.0001
    assert b.round_quantity("SOL/USDT", 0.35, "futures") == pytest.approx(0.3)
    assert b.round_quantity("SOL/USDT", 0.35) == pytest.approx(0.35)


def test_futures_create_order_binds_swap_row():
    b = _dual_row_ex()
    b.create_order("SOL/USDT", "market", "sell", 0.35, market_type="futures")
    assert b.exchange.create_order.call_args[0][3] == pytest.approx(0.3)


def test_futures_price_uses_swap_symbol_for_ccxt():
    b = _dual_row_ex()
    b.round_price("SOL/USDT", 100.16, "futures")
    assert b.exchange.price_to_precision.call_args[0][0] == "SOL/USDT:USDT"


def test_futures_close_full_size_is_fixed_point():
    # The CRITICAL review scenario: closing a 0.3-contract swap position must
    # send 0.3 — the pre-fix code sent 0.2 and left a naked 0.1 remainder.
    b = _dual_row_ex()
    b.create_order("SOL/USDT", "market", "sell", 0.3,
                   params={"reduceOnly": True}, market_type="futures")
    assert b.exchange.create_order.call_args[0][3] == pytest.approx(0.3)
