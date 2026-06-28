from __future__ import annotations

import sqlite3


def test_trade_close_event_is_idempotent(tmp_path):
    from core.warehouse import Warehouse

    if hasattr(Warehouse._tls, "conn"):
        delattr(Warehouse._tls, "conn")
    db = tmp_path / "warehouse.sqlite"
    wh = Warehouse(db)

    trade_id = wh.record_trade_open(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        side="buy",
        ts_entry=1000.0,
        entry_px=100.0,
        size=1.0,
        strategy_family="machine",
        mode="PAPER",
    )
    wh.record_trade_close(
        trade_id=trade_id,
        ts_exit=1100.0,
        exit_px=101.0,
        realized_pnl=1.0,
        exit_reason="take_profit",
    )
    wh.record_trade_close(
        trade_id=trade_id,
        ts_exit=1200.0,
        exit_px=102.0,
        realized_pnl=2.0,
        exit_reason="duplicate_retry",
    )

    con = sqlite3.connect(db)
    try:
        close_events = con.execute(
            "SELECT COUNT(*) FROM trade_events WHERE trade_id=? AND event_type='CLOSE'",
            (trade_id,),
        ).fetchone()[0]
        open_events = con.execute(
            "SELECT COUNT(*) FROM trade_events WHERE trade_id=? AND event_type='OPEN'",
            (trade_id,),
        ).fetchone()[0]
    finally:
        con.close()

    assert open_events == 1
    assert close_events == 1
