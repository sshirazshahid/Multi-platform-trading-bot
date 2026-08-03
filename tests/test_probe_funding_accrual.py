"""probe_common.accrue_funding — realized-settlement accrual for shadow probes.

The defect these pin: accrual booked the CURRENT live funding print once per 8h
WALL-CLOCK window, which is neither a settlement nor an interval the venue uses.
The perp funding interval is time-varying per symbol (bases have switched 8h ->
4h mid-history), so no constant is correct. Accrual must replay the venue's
realized settlements and defer when they are unavailable.

Log-only lane: these rows never touch money. The warehouse here is an in-memory
sqlite double and the funding history is a tmp_path fixture — nothing in this
file may open data/warehouse.sqlite or data/funding_history/.
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

from core.agents.probe_common import accrue_funding

TABLE = "shadow_probe_under_test"
VENUE = "bybit"
SYMBOL = "ONDO/USDT:USDT"
ENTRY_TS = 1_785_000_000  # a 4h boundary in the fixtures below
NOW = ENTRY_TS + 24 * 3600


class _Warehouse:
    """Minimal stand-in exposing the single `_conn()` accrue_funding uses."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            f"CREATE TABLE {TABLE} (proposal_id TEXT PRIMARY KEY, symbol TEXT, "
            "realized_funding_rate_sum REAL, last_funding_bucket INTEGER)"
        )

    def _conn(self):
        return self.conn

    def state(self, pid="p1"):
        r = self.conn.execute(
            f"SELECT realized_funding_rate_sum, last_funding_bucket FROM {TABLE} "
            "WHERE proposal_id=?", (pid,)
        ).fetchone()
        return r


def _row(**over):
    row = {
        "proposal_id": "p1", "symbol": SYMBOL, "signal_bar_ts": ENTRY_TS,
        "realized_funding_rate_sum": 0.0, "last_funding_bucket": None,
    }
    row.update(over)
    return row


def _seed(wh, row):
    wh.conn.execute(
        f"INSERT INTO {TABLE} VALUES (?,?,?,?)",
        (row["proposal_id"], row["symbol"], row["realized_funding_rate_sum"],
         row["last_funding_bucket"]),
    )
    wh.conn.commit()


def _history(tmp_path, settlements, *, venue=VENUE, coin="ONDO"):
    p = tmp_path / f"{venue}_{coin}.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(("ts", "funding_rate", "venue", "symbol"))
        for ts, rate in settlements:
            w.writerow((ts, rate, venue, SYMBOL))
    return tmp_path


def _live_print(rate):
    def market_data(_venue, _symbol):
        return {"funding_rate": rate}
    return market_data


def _accrue(wh, row, tmp_path, *, now=NOW, live=0.5):
    accrue_funding(wh, table=TABLE, market_data=_live_print(live), venue=VENUE,
                   row=row, now=now, history_dir=tmp_path)


# ── the core defect ─────────────────────────────────────────────────────────
def test_books_every_realized_settlement_in_the_window(tmp_path):
    """A 4h base settles 6x/day; the 8h wall-clock bucket booked at most 3."""
    _history(tmp_path, [(ENTRY_TS + 14400 * i, 0.0001) for i in range(1, 7)])
    wh, row = _Warehouse(), _row()
    _seed(wh, row)
    _accrue(wh, row, tmp_path)
    total, cursor = wh.state()
    assert total == pytest.approx(6 * 0.0001)
    assert cursor == ENTRY_TS + 14400 * 6


def test_each_settlement_is_booked_exactly_once_across_passes(tmp_path):
    _history(tmp_path, [(ENTRY_TS + 14400, 0.0001), (ENTRY_TS + 28800, 0.0002)])
    wh, row = _Warehouse(), _row()
    _seed(wh, row)
    _accrue(wh, row, tmp_path)
    first = wh.state()
    row = _row(realized_funding_rate_sum=first[0], last_funding_bucket=first[1])
    _accrue(wh, row, tmp_path)
    assert wh.state() == first, "re-running must not double-book a settlement"


def test_the_live_spot_print_is_never_booked(tmp_path):
    """No settlement in range => nothing accrues, whatever the ticker says."""
    _history(tmp_path, [(ENTRY_TS - 14400, 0.0001)])
    wh, row = _Warehouse(), _row()
    _seed(wh, row)
    _accrue(wh, row, tmp_path, live=0.9)
    assert wh.state() == (0.0, None)


def test_defers_when_the_realized_history_is_unavailable(tmp_path):
    wh, row = _Warehouse(), _row()
    _seed(wh, row)
    _accrue(wh, row, tmp_path, live=0.9)  # no CSV written at all
    assert wh.state() == (0.0, None), "missing history must defer, never guess"


# ── migration: pre-fix rows hold an 8h bucket INDEX, not a timestamp ────────
def test_legacy_eight_hour_bucket_index_is_treated_as_unset(tmp_path):
    """61987 is an 8h index (~6.2e4), not a settlement ts (~1.78e9)."""
    _history(tmp_path, [
        (ENTRY_TS - 28800, 9.9),          # pre-entry: must never be booked
        (ENTRY_TS + 14400, 0.0001),
        (ENTRY_TS + 28800, 0.0002),
    ])
    wh, row = _Warehouse(), _row(last_funding_bucket=61987)
    _seed(wh, row)
    _accrue(wh, row, tmp_path)
    total, cursor = wh.state()
    assert total == pytest.approx(0.0003), "stale index must bound accrual by entry"
    assert cursor == ENTRY_TS + 28800


def test_a_settlement_at_the_entry_instant_is_not_booked(tmp_path):
    _history(tmp_path, [(ENTRY_TS, 9.9), (ENTRY_TS + 14400, 0.0001)])
    wh, row = _Warehouse(), _row()
    _seed(wh, row)
    _accrue(wh, row, tmp_path)
    assert wh.state()[0] == pytest.approx(0.0001)


def test_settlements_after_now_are_not_booked_early(tmp_path):
    _history(tmp_path, [(ENTRY_TS + 14400, 0.0001), (NOW + 14400, 9.9)])
    wh, row = _Warehouse(), _row()
    _seed(wh, row)
    _accrue(wh, row, tmp_path)
    assert wh.state()[0] == pytest.approx(0.0001)


def test_a_row_without_an_entry_timestamp_defers(tmp_path):
    _history(tmp_path, [(ENTRY_TS + 14400, 0.0001)])
    wh, row = _Warehouse(), _row(signal_bar_ts=None)
    _seed(wh, row)
    _accrue(wh, row, tmp_path)
    assert wh.state() == (0.0, None)
