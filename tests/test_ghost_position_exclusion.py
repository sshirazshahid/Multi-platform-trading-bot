"""Ghost-position exclusion pin (2026-07-20 AXS forensics).

2026-07-18 05:08 incident: the futures monitor iterated an AXS/USDT:USDT
position while data/positions.json's open list read empty. Forensics proved it
was NOT a persistence ghost — the position was genuinely opened in-session
(maker-first taker fallback, id DRY-359d8a43) and closed 4s later as
data_feed_failure by the mark-price freshness bug; the recurring boot symptom
was the PERSISTED risk incident latch, not a resurrected position.

These tests pin the invariant the incident hypothesis assumed could break:
a record in the persisted `closed` list — including the exact shape of the
AXS data_feed_failure row — must NEVER be resurrected into the open set by
PositionTracker._load(), on the main file or the backup-recovery path.

Run: venv/Scripts/python.exe -m pytest tests/test_ghost_position_exclusion.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.position_tracker import PositionTracker

# The exact persisted shape of the incident row (data/positions.json closed[],
# id DRY-359d8a43), trimmed to the fields Position(**d) consumes.
AXS_CLOSED_ROW = {
    "id": "DRY-359d8a43",
    "exchange": "Binance",
    "symbol": "AXS/USDT:USDT",
    "side": "sell",
    "market_type": "futures",
    "strategy": "algo_det",
    "entry_price": 0.9365315,
    "size": 66.0,
    "stop_loss": 0.9410268512,
    "take_profit": 0.9243565905,
    "leverage": 1,
    "open_time": 1784333297.7585196,
    "close_time": 1784333301.6483033,
    "exit_price": 0.9374685,
    "entry_fee": 0.0309055395,
    "exit_fee": 0.0309364605,
    "total_fees": 0.061842,
    "gross_pnl": -0.061842,
    "pnl": -0.123684,
    "pnl_pct": -0.2001,
    "close_reason": "data_feed_failure",
    "order_id": "DRY-359d8a43",
    "paper_trade": True,
}

OPEN_ROW = {
    "id": "DRY-live0001",
    "exchange": "Bybit",
    "symbol": "BTC/USDT:USDT",
    "side": "buy",
    "market_type": "futures",
    "strategy": "algo_det",
    "entry_price": 50_000.0,
    "size": 0.001,
    "stop_loss": 49_000.0,
    "take_profit": 52_000.0,
    "leverage": 1,
    "paper_trade": True,
}


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _write_positions(payload: dict, path: str = "data/positions.json") -> None:
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def test_closed_record_never_resurfaces_in_open(caplog):
    """The AXS incident row lives in closed[] — _load must route it to the
    closed deque only; get_open() stays empty for every scope."""
    _write_positions({"open": [], "closed": [AXS_CLOSED_ROW]})
    tracker = PositionTracker()

    assert tracker.get_open() == []
    assert tracker.get_open(exchange="Binance") == []
    assert tracker.get_open(exchange="Binance", symbol="AXS/USDT:USDT") == []
    assert tracker.count_open() == 0
    closed_ids = [p.id for p in tracker._closed]
    assert "DRY-359d8a43" in closed_ids


def test_open_and_closed_sections_route_independently():
    """Sanity for the same loader: open[] loads into the open set while the
    closed AXS row still stays out of it."""
    _write_positions({"open": [OPEN_ROW], "closed": [AXS_CLOSED_ROW]})
    tracker = PositionTracker()

    open_ids = [p.id for p in tracker.get_open()]
    assert open_ids == ["DRY-live0001"]
    assert tracker.get_open(symbol="AXS/USDT:USDT") == []


def test_backup_recovery_also_excludes_closed_records():
    """The corrupt-main -> backup recovery path re-implements the routing;
    pin it to the same invariant."""
    Path("data/positions.json").write_text("{not json", encoding="utf-8")
    _write_positions(
        {"open": [], "closed": [AXS_CLOSED_ROW]},
        path="data/positions.json.bak",
    )
    tracker = PositionTracker()

    assert tracker.get_open() == []
    assert tracker.count_open() == 0
    assert "DRY-359d8a43" in [p.id for p in tracker._closed]


# ── sync_with_exchanges: flat exchange rows must never inject ghosts ────────
# The one genuine ghost-injection hole found by the 2026-07-20 audit:
# `ep.get("contracts") or ep.get("contractSize")` sized exchange rows.
# `contractSize` is a per-contract multiplier (a market constant, ~1.0), NOT a
# position size — a FLAT row (contracts=0/None, which Binance positionRisk and
# Bybit v5 can serve with side/avgPrice still populated) fell through to
# contractSize and (a) imported as a MANUAL ghost the monitor then iterates
# while the real account is flat, and (b) shielded real ghosts from closure by
# keeping their (exchange, symbol, side) snapshot key alive. These pins must
# hold WITHOUT the .env INCIDENT_QUARANTINE_BASES=AXS workaround — nothing
# here reads quarantine state.


def _flat_axs_row(side_raw: str = "short") -> dict:
    return {
        "symbol": "AXS/USDT:USDT",
        "contracts": 0.0,
        "contractSize": 1.0,
        "side": side_raw,
        "entryPrice": 0.9365,
        "leverage": 3,
        "liquidationPrice": 0.0,
    }


def _fake_exchange(rows: list):
    from unittest.mock import MagicMock

    fake_ex = MagicMock()
    fake_ex.name = "binance"
    fake_ex.fetch_positions.return_value = rows
    fake_ex._last_positions_fetch_ok = True
    fake_ex.fetch_closed_pnl.return_value = []
    return fake_ex


def test_flat_row_with_contract_size_is_not_imported():
    """contracts=0 means FLAT even when contractSize=1.0 would satisfy the
    truthiness fallback — no MANUAL ghost import."""
    tracker = PositionTracker()
    imported = tracker.sync_with_exchanges({"binance": _fake_exchange([_flat_axs_row()])})

    assert imported == []
    assert tracker.get_open() == []


def test_none_contracts_row_is_not_imported():
    """contracts=None (missing) is also flat — contractSize is never a
    position-size substitute."""
    row = _flat_axs_row()
    row["contracts"] = None
    tracker = PositionTracker()
    imported = tracker.sync_with_exchanges({"binance": _fake_exchange([row])})

    assert imported == []
    assert tracker.get_open() == []


def test_real_position_row_still_imports():
    """Guard the guard: a genuine external position (contracts>0) must still
    import as MANUAL — the fix only excludes flat rows."""
    row = _flat_axs_row()
    row["contracts"] = 66.0
    tracker = PositionTracker()
    imported = tracker.sync_with_exchanges({"binance": _fake_exchange([row])})

    assert len(imported) == 1
    assert imported[0].symbol == "AXS/USDT:USDT"
    assert imported[0].size == pytest.approx(66.0)
    assert imported[0].side == "sell"


def test_flat_row_does_not_shield_ghost_close():
    """A tracked LIVE position whose exchange row is FLAT must be treated as
    a ghost (enter the two-pass reconcile) — not kept 'alive' by the
    contractSize fallthrough."""
    from core.position_tracker import Position

    tracker = PositionTracker()
    p = Position(
        id="GHOST-AXS-001",
        exchange="binance",
        symbol="AXS/USDT:USDT",
        side="sell",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=0.9365,
        size=66.0,
        stop_loss=0.9410,
        take_profit=0.9243,
        paper_trade=False,
    )
    tracker._open[p.id] = p

    tracker.sync_with_exchanges({"binance": _fake_exchange([_flat_axs_row("short")])})

    assert "GHOST-AXS-001" in tracker._pending_ghost_reconcile, (
        "flat exchange row (contracts=0, contractSize=1.0) kept the tracked "
        "position 'alive' — ghost close was shielded"
    )
