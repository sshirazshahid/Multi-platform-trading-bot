"""Offline unit tests for scripts/fetch_binance_funding_oi.py pure helpers.

No network: only the deterministic normalisation / dedup / CSV-writer logic is
exercised. The ccxt-backed fetch_* functions are validated by running the
script against the live public API, not here.
"""

import csv
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_binance_funding_oi.py"
_spec = importlib.util.spec_from_file_location("_fetch_binance_funding_oi", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_to_perp_normalises_all_forms():
    assert mod.to_perp("BTC") == "BTC/USDT:USDT"
    assert mod.to_perp("eth") == "ETH/USDT:USDT"
    assert mod.to_perp("SOLUSDT") == "SOL/USDT:USDT"
    assert mod.to_perp("XRP/USDT") == "XRP/USDT:USDT"
    assert mod.to_perp("BNB/USDT:USDT") == "BNB/USDT:USDT"  # already a perp id


def test_dedup_by_timestamp_sorts_and_uniquifies():
    rows = [
        {"timestamp": 30, "fundingRate": 0.3},
        {"timestamp": 10, "fundingRate": 0.1},
        {"timestamp": 30, "fundingRate": 0.99},  # later dup wins
        {"timestamp": 20, "fundingRate": 0.2},
        {"fundingRate": 0.0},  # no timestamp -> dropped
    ]
    out = mod.dedup_by_timestamp(rows)
    assert [r["timestamp"] for r in out] == [10, 20, 30]
    assert out[-1]["fundingRate"] == 0.99


def test_write_csv_selects_columns(tmp_path):
    path = tmp_path / "BTC_funding.csv"
    rows = [
        {"timestamp": 1, "datetime": "2019-09-10T08:00:00.000Z", "fundingRate": 0.0001},
        {"timestamp": 2, "datetime": "2019-09-10T16:00:00.000Z", "fundingRate": -0.0002},
    ]
    n = mod.write_csv(
        path,
        rows,
        [
            ("timestamp", "timestamp"),
            ("datetime", "datetime"),
            ("funding_rate", "fundingRate"),
        ],
    )
    assert n == 2
    with path.open(newline="", encoding="utf-8") as fh:
        read = list(csv.reader(fh))
    assert read[0] == ["timestamp", "datetime", "funding_rate"]
    assert read[1] == ["1", "2019-09-10T08:00:00.000Z", "0.0001"]
    assert read[2][2] == "-0.0002"


def test_write_csv_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "sub" / "ETH_oi.csv"
    mod.write_csv(
        path,
        [{"timestamp": 1, "datetime": "x", "openInterestAmount": 5, "openInterestValue": 50}],
        [("timestamp", "timestamp"), ("open_interest_base", "openInterestAmount")],
    )
    assert path.exists()


_OI_COLS = [
    ("timestamp", "timestamp"),
    ("datetime", "datetime"),
    ("open_interest_base", "openInterestAmount"),
    ("open_interest_usd", "openInterestValue"),
]


def _oi_row(ts, base):
    return {
        "timestamp": ts,
        "datetime": f"d{ts}",
        "openInterestAmount": base,
        "openInterestValue": base * 10,
    }


def test_write_csv_merge_accumulates_and_fresh_wins(tmp_path):
    """A second merged write keeps old timestamps and overwrites overlaps.

    This is the behaviour that lets the scheduled OI run accumulate history
    past the exchange's ~30-day live window.
    """
    path = tmp_path / "BTC_oi.csv"
    # First run: ts 1,2 (simulating an older 30d window).
    mod.write_csv(path, [_oi_row(1, 100), _oi_row(2, 200)], _OI_COLS, merge=True)
    # Second run: ts 2 (refreshed) + ts 3 (new) — ts 1 is now outside the live
    # window and absent from the fetch, but must survive in the CSV.
    n = mod.write_csv(path, [_oi_row(2, 999), _oi_row(3, 300)], _OI_COLS, merge=True)
    assert n == 3
    rows = mod.read_existing_rows(path, _OI_COLS)
    assert [r["timestamp"] for r in rows] == [1, 2, 3]
    by_ts = {r["timestamp"]: r for r in rows}
    assert by_ts[1]["openInterestAmount"] == "100"  # old row preserved
    assert by_ts[2]["openInterestAmount"] == "999"  # fresh fetch wins on overlap


def test_read_existing_rows_absent_file_is_empty(tmp_path):
    assert mod.read_existing_rows(tmp_path / "nope.csv", _OI_COLS) == []
