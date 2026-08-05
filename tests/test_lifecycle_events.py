"""C6 (tpbot retrofit): mid-trade lifecycle rows in trade_events.

Gap (survey 2026-07-08): trade_events recorded ONLY 'OPEN' and 'CLOSE' —
the signal->order->fill->TP->close chain had no intermediate rows, so any
future TP-authority dispute (the Jun-4 attribution-corruption class: three
competing TP authorities, corrupt attribution, dollars correct) is
unauditable after the fact. Partial TP survives only as a trades column;
SL moves only in loguru logs that rotate away.

New surface:
  * Warehouse.record_lifecycle_event(exchange, symbol, side, ts_entry,
    event_type, payload) — resolves the OPEN row via trade_id_by_key
    (same idempotency key as the close path); missing row -> False no-op;
    dedup via the existing UNIQUE(trade_id, event_type, event_ts).
  * OrderManager._record_lifecycle(pos, event_type, payload) — best-effort,
    never raises, wired at the sites that already loguru-log the
    transitions: ORDER_PLACED (SL/TP legs in _place_exchange_sl_tp),
    FILL (open_position, with fill_type), PARTIAL_TP + SL_MOVE
    (partial_close_position; breakeven move).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def wh(tmp_path, monkeypatch):
    """Fresh isolated warehouse under tmp (never the production data/)."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    import core.warehouse

    core.warehouse._default = None
    from core.warehouse import get_warehouse

    yield get_warehouse()
    core.warehouse._default = None


def _seed_open(wh, symbol="ETH/USDT", ts_entry=1000.0):
    # NOTE: the warehouse thread-local sqlite connection can survive across
    # tests in one process (same idiom-caveat as test_bybit_trigger_direction),
    # so every test seeds a UNIQUE symbol and scopes its assertions per-trade —
    # never absolute COUNT(*) over the whole table.
    tid = wh.record_trade_open(
        exchange="Bybit",
        symbol=symbol,
        side="buy",
        ts_entry=ts_entry,
        entry_px=100.0,
        size=1.0,
        leverage=3,
        mode="PAPER",
    )
    assert tid and tid > 0
    return tid


def _events(wh, tid, etype=None):
    q = "SELECT event_type, event_ts, payload_json FROM trade_events WHERE trade_id=?"
    args = [tid]
    if etype:
        q += " AND event_type=?"
        args.append(etype)
    return wh.query(q + " ORDER BY event_ts", tuple(args))


# ── warehouse-level ──────────────────────────────────────────────────────────
def test_lifecycle_event_recorded_with_payload(wh):
    tid = _seed_open(wh, symbol="LCA/USDT", ts_entry=1200.0)
    ok = wh.record_lifecycle_event(
        exchange="Bybit",
        symbol="LCA/USDT",
        side="buy",
        ts_entry=1200.0,
        event_type="SL_MOVE",
        payload={"from": 92.0, "to": 100.0, "trigger": "breakeven"},
    )
    assert ok is True
    rows = _events(wh, tid, "SL_MOVE")
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["to"] == 100.0


def test_lifecycle_event_missing_trade_is_noop_false(wh):
    ok = wh.record_lifecycle_event(
        exchange="Bybit",
        symbol="NOPE/USDT",
        side="buy",
        ts_entry=999.0,
        event_type="SL_MOVE",
        payload={},
    )
    assert ok is False
    rows = wh.query("SELECT COUNT(*) AS n FROM trade_events WHERE symbol='NOPE/USDT'")
    assert rows[0]["n"] == 0


def test_multiple_sl_moves_at_distinct_ts_all_recorded(wh):
    tid = _seed_open(wh, symbol="SLM/USDT", ts_entry=1100.0)
    for i, ts in enumerate((2000.0, 2010.0, 2020.0)):
        assert wh.record_lifecycle_event(
            exchange="Bybit",
            symbol="SLM/USDT",
            side="buy",
            ts_entry=1100.0,
            event_type="SL_MOVE",
            payload={"to": 93.0 + i},
            event_ts=ts,
        )
    assert len(_events(wh, tid, "SL_MOVE")) == 3


def test_duplicate_type_and_ts_deduped_by_unique_constraint(wh):
    tid = _seed_open(wh, symbol="LCB/USDT", ts_entry=1300.0)
    for _ in range(2):
        wh.record_lifecycle_event(
            exchange="Bybit",
            symbol="LCB/USDT",
            side="buy",
            ts_entry=1300.0,
            event_type="ORDER_PLACED",
            payload={"leg": "sl"},
            event_ts=2000.0,
        )
    assert len(_events(wh, tid, "ORDER_PLACED")) == 1  # INSERT OR IGNORE


def test_close_once_index_untouched_by_lifecycle_rows(wh):
    tid = _seed_open(wh, symbol="LCC/USDT", ts_entry=1400.0)
    wh.record_lifecycle_event(
        exchange="Bybit",
        symbol="LCC/USDT",
        side="buy",
        ts_entry=1400.0,
        event_type="PARTIAL_TP",
        payload={"size": 0.3},
        event_ts=2000.0,
    )
    # A lifecycle row must never satisfy/violate uq_trade_events_close_once.
    rows = wh.query(
        "SELECT COUNT(*) AS n FROM trade_events WHERE trade_id=? AND event_type='CLOSE'", (tid,)
    )
    assert rows[0]["n"] == 0


def test_default_event_ts_is_now(wh):
    tid = _seed_open(wh, symbol="LCD/USDT", ts_entry=1500.0)
    before = time.time()
    wh.record_lifecycle_event(
        exchange="Bybit",
        symbol="LCD/USDT",
        side="buy",
        ts_entry=1500.0,
        event_type="FILL",
        payload={"px": 100.0},
    )
    row = _events(wh, tid, "FILL")[0]
    assert before - 1 <= row["event_ts"] <= time.time() + 1


# ── order-manager helper ─────────────────────────────────────────────────────
def test_om_record_lifecycle_best_effort_never_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    import core.warehouse

    core.warehouse._default = None
    from core.order_manager import OrderManager

    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    pos = MagicMock()
    pos.exchange = "Bybit"
    pos.symbol = "ETH/USDT"
    pos.side = "buy"
    pos.open_time = 1000.0
    # No trade row exists — must be a silent no-op, never a raise.
    om._record_lifecycle(pos, "SL_MOVE", {"to": 100.0})
    # Even a poisoned position object must not raise.
    om._record_lifecycle(object(), "SL_MOVE", {"to": 100.0})
    core.warehouse._default = None


# ── wire-in pins (source-scan, matching repo scan-test style) ────────────────
def _om_src() -> str:
    from tests.order_manager_source import order_manager_impl_source

    return order_manager_impl_source()


def test_order_placed_wired_in_sl_tp_placement():
    src = _om_src()
    i = src.index("def _place_exchange_sl_tp")
    block = src[i : i + 14000]
    assert block.count('"ORDER_PLACED"') >= 2, (
        "both SL and TP legs must write ORDER_PLACED lifecycle rows"
    )


def test_fill_wired_in_open_position():
    src = _om_src()
    i = src.index("def open_position")
    j = src.index("def _place_exchange_sl_tp")  # next method = window end
    assert '"FILL"' in src[i:j], (
        "entry fill must write a FILL lifecycle row (fill_type/price payload)"
    )


def test_partial_tp_and_sl_move_wired_in_partial_close():
    src = _om_src()
    i = src.index("def partial_close_position")
    # 7000->9000 when the 2026-07-09 review fixes (market_type threading)
    # grew the function head; guarded behavior unchanged.
    block = src[i : i + 9000]
    assert '"PARTIAL_TP"' in block
    assert '"SL_MOVE"' in block, "the breakeven move must write an SL_MOVE lifecycle row"
