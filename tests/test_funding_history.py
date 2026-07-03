"""core/funding_history — per-venue rolling funding series for the F1 gate."""

import csv

from core.funding_history import MIN_PERIODS_FOR_AVG, avg_7d, record

NOW = 1_783_100_000.0


def _frame(rate=0.0001, next_ts=NOW + 3600.0, interval=8.0, stale=False):
    return {"rate": rate, "next_funding_ts": next_ts,
            "interval_hours": interval, "stale": stale}


def test_record_writes_header_and_row(tmp_path):
    assert record("binance", "BTC", _frame(), base_dir=tmp_path, now_fn=lambda: NOW)
    rows = list(csv.DictReader(open(tmp_path / "binance_BTC.csv", encoding="utf-8")))
    assert len(rows) == 1
    assert float(rows[0]["rate"]) == 0.0001
    assert float(rows[0]["interval_hours"]) == 8.0


def test_record_dedupes_same_funding_period(tmp_path):
    assert record("bybit", "ETH", _frame(next_ts=123.0), base_dir=tmp_path)
    # same period again (hourly harvest of an 8h interval) -> no new row
    assert not record("bybit", "ETH", _frame(rate=0.0002, next_ts=123.0),
                      base_dir=tmp_path)
    # new period -> appended
    assert record("bybit", "ETH", _frame(rate=0.0002, next_ts=456.0),
                  base_dir=tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "bybit_ETH.csv", encoding="utf-8")))
    assert len(rows) == 2


def test_record_refuses_stale_or_empty_frames(tmp_path):
    assert not record("bitget", "BTC", _frame(stale=True), base_dir=tmp_path)
    assert not record("bitget", "BTC", {"rate": None}, base_dir=tmp_path)
    assert not record("bitget", "BTC", {}, base_dir=tmp_path)
    assert not (tmp_path / "bitget_BTC.csv").exists()


def test_avg_7d_returns_none_without_enough_history(tmp_path):
    assert avg_7d("binance", "BTC", base_dir=tmp_path, now=NOW) is None  # no file
    for i in range(MIN_PERIODS_FOR_AVG - 1):
        record("binance", "BTC", _frame(next_ts=float(i)), base_dir=tmp_path,
               now_fn=lambda: NOW)
    assert avg_7d("binance", "BTC", base_dir=tmp_path, now=NOW) is None  # too few


def test_avg_7d_windows_and_averages(tmp_path):
    # 6 in-window periods at alternating rates + 2 ancient rows outside 7d
    for i in range(2):
        record("binance", "ETH", _frame(rate=9.9, next_ts=float(i)),
               base_dir=tmp_path, now_fn=lambda: NOW - 8 * 24 * 3600.0)
    for i in range(6):
        rate = 0.0001 if i % 2 == 0 else 0.0003
        record("binance", "ETH", _frame(rate=rate, next_ts=100.0 + i),
               base_dir=tmp_path, now_fn=lambda i=i: NOW - 3600.0 * i)
    got = avg_7d("binance", "ETH", base_dir=tmp_path, now=NOW)
    assert abs(got - 0.0002) < 1e-12  # ancient 9.9 rows excluded


def test_avg_7d_skips_malformed_rows(tmp_path):
    for i in range(MIN_PERIODS_FOR_AVG):
        record("bybit", "BTC", _frame(next_ts=float(i)), base_dir=tmp_path,
               now_fn=lambda: NOW)
    with open(tmp_path / "bybit_BTC.csv", "a", encoding="utf-8", newline="") as f:
        f.write("garbage,not,a,row\n")
    got = avg_7d("bybit", "BTC", base_dir=tmp_path, now=NOW)
    assert got is not None and abs(got - 0.0001) < 1e-12
