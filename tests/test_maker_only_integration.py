"""Maker-only <-> order_manager integration (2026-05-29).

Makes MAKER_ONLY safe to enable (it is NOT, by default, until this lands —
see config.MAKER_ONLY note). Two halves:

  1. Executor: a maker limit that PARTIALLY fills then times out must be
     reported as 'partial_maker' with the actual filled qty — never silently
     returned as 'skipped_maker_only' while a real (SL-less) partial sits on
     the exchange.
  2. order_manager._interpret_execution_result: maps an executor return into
     (outcome, fill_size) so a skip/uncertain outcome opens NO position (no
     phantom — the old order_manager.py:839 keyed only on order id), and a
     partial fill is sized to what actually filled (so the SL covers the real
     position, since size_rounded is recomputed from `size` before SL placement).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ── Executor: partial vs zero fill under maker-only ───────────────────────


def _executor():
    from core.smart_executor import SmartExecutor

    se = SmartExecutor()
    se._record_stat = MagicMock()
    se._market_order = MagicMock(return_value={"status": "closed", "id": "MKT", "average": 100.0})
    se.get_entry_price = MagicMock(return_value=100.0)
    return se


def _exchange(filled, after_status="canceled"):
    ex = MagicMock()
    ex.create_order = MagicMock(return_value={"id": "LIM", "status": "open"})
    ex.cancel_order = MagicMock(return_value={"status": "canceled"})
    verification = {
        "id": "LIM",
        "status": after_status,
        "filled": filled,
        "average": 100.0,
        "price": 100.0,
    }
    ex.exchange = MagicMock()
    ex.exchange.fetch_order = MagicMock(side_effect=[verification] * 12)
    return ex


@pytest.fixture
def maker_on(monkeypatch):
    # max_wait_sec=0 -> instant timeout, no 120s real wait in the test.
    monkeypatch.setattr("config.MAKER_ONLY", {"enabled": True, "max_wait_sec": 0}, raising=False)


def test_maker_partial_fill_reported_not_skipped(maker_on):
    se = _executor()
    ex = _exchange(filled=4.0)  # 4 of 10 intended filled before timeout
    result = se.execute_limit_with_fallback(
        ex, "DOGE/USDT:USDT", "buy", 10.0, market_type="futures"
    )
    assert result["status"] != "skipped_maker_only", (
        "a naked partial fill must NOT be reported as a skip"
    )
    assert float(result.get("filled") or 0) == 4.0
    assert not se._market_order.called, "maker-only must not market-top-up a partial"


def test_maker_zero_fill_reports_skip(maker_on):
    se = _executor()
    ex = _exchange(filled=0.0)
    result = se.execute_limit_with_fallback(
        ex, "DOGE/USDT:USDT", "buy", 10.0, market_type="futures"
    )
    assert result["status"] == "skipped_maker_only"
    assert float(result.get("filled") or 0) == 0.0
    assert not se._market_order.called


# ── OrderManager._interpret_execution_result (pure staticmethod) ──────────


def _interp(order, size):
    from core.order_manager import OrderManager

    return OrderManager._interpret_execution_result(order, size)


def test_interpret_none_is_no_fill():
    assert _interp(None, 10.0) == ("no_fill", 0.0)


def test_interpret_skip_is_no_fill():
    assert _interp({"status": "skipped_maker_only", "id": "LIM"}, 10.0) == ("no_fill", 0.0)


def test_interpret_uncertain_is_no_fill():
    out, sz = _interp({"status": "uncertain", "id": "LIM"}, 10.0)
    assert out == "uncertain" and sz == 0.0


def test_interpret_missing_id_is_no_fill():
    assert _interp({"status": "closed", "id": None}, 10.0) == ("no_fill", 0.0)


def test_interpret_full_fill_uses_intended_size():
    assert _interp({"status": "closed", "id": "X"}, 10.0) == ("filled", 10.0)


def test_interpret_closed_uses_reported_filled_or_amount():
    assert _interp({"status": "closed", "id": "X", "filled": 7.5}, 10.0) == ("filled", 7.5)
    assert _interp({"status": "closed", "id": "Y", "amount": 6.0}, 10.0) == ("filled", 6.0)


def test_interpret_partial_maker_sizes_to_fill():
    assert _interp({"status": "partial_maker", "id": "X", "filled": 4.0}, 10.0) == ("filled", 4.0)


def test_interpret_partial_maker_zero_fill_is_no_fill():
    assert _interp({"status": "partial_maker", "id": "X", "filled": 0.0}, 10.0) == ("no_fill", 0.0)


def test_twap_aggregate_sums_slices_and_vwaps():
    from core.order_manager import OrderManager

    order = OrderManager._aggregate_execution_results([
        {"status": "closed", "id": "A", "filled": 2.0, "average": 100.0, "_fill_type": "maker"},
        {"status": "closed", "id": "B", "filled": 3.0, "average": 110.0, "_fill_type": "maker"},
    ], intended_size=5.0)

    assert order["id"] == "A"
    assert order["filled"] == 5.0
    assert order["average"] == pytest.approx(106.0)
    assert order["_twap_slice_ids"] == ["A", "B"]


def test_twap_aggregate_uncertain_without_fill():
    from core.order_manager import OrderManager

    order = OrderManager._aggregate_execution_results([
        {"status": "uncertain", "id": "A"},
        {"status": "skipped_maker_only", "id": "B"},
    ], intended_size=5.0)

    assert order["status"] == "uncertain"
