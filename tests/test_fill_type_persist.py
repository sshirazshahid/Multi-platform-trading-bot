"""Phase 2 — per-trade fill_type instrumentation (forward-only).

Covers the two additive behaviors:
  1. warehouse.record_trade_open persists the fill_type column (round-trips).
  2. smart_executor tags _fill_type on its return paths ('taker' on the market
     fallback) so order_manager can record ground-truth maker/taker per trade.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _query_fill_type(wh, trade_id):
    row = wh._conn().execute("SELECT fill_type FROM trades WHERE id=?", (trade_id,)).fetchone()
    return row["fill_type"]


def test_record_trade_open_persists_fill_type():
    from core.warehouse import Warehouse

    wh = Warehouse(Path("data/wh_test.sqlite"))
    tid = wh.record_trade_open(
        exchange="bybit",
        symbol="BTC/USDT:USDT",
        side="buy",
        ts_entry=1_700_000_000.0,
        entry_px=100.0,
        size=1.0,
        mode="CONTROLLED_LIVE",
        fill_type="maker",
    )
    assert tid > 0
    assert _query_fill_type(wh, tid) == "maker"


def test_record_trade_open_fill_type_defaults_none():
    """Legacy callers that omit fill_type still work (NULL persisted)."""
    from core.warehouse import Warehouse

    wh = Warehouse(Path("data/wh_test2.sqlite"))
    tid = wh.record_trade_open(
        exchange="bybit",
        symbol="ETH/USDT:USDT",
        side="sell",
        ts_entry=1_700_000_100.0,
        entry_px=50.0,
        size=2.0,
        mode="PAPER",
    )
    assert _query_fill_type(wh, tid) is None


def test_market_fallback_tags_taker():
    from core.smart_executor import SmartExecutor

    ex = MagicMock()
    ex.create_order.return_value = {"id": "o1", "status": "closed", "average": 100.0}
    se = SmartExecutor()
    out = se._market_order(ex, "BTC/USDT:USDT", "buy", 1.0, "futures", {})
    assert out is not None
    assert out["_fill_type"] == "taker"


def test_partial_maker_payload_tagged():
    """The partial-fill return path carries _fill_type='maker_partial'."""
    import inspect

    from core import smart_executor

    src = inspect.getsource(smart_executor)
    assert '"_fill_type": "maker_partial"' in src
    # and the two limit-fill paths tag 'maker'
    assert src.count('["_fill_type"] = "maker"') >= 2
