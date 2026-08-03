"""core/funding_history — F1 gate observations and realized settlements."""

import csv

from core.funding_history import (
    MIN_PERIODS_FOR_AVG,
    FundingSettlement,
    avg_7d,
    load_recent_realized_settlements,
    load_realized_settlements,
    record,
)

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


def test_load_realized_settlements_reads_exact_range_in_timestamp_order(tmp_path):
    path = tmp_path / "binance_BTC.csv"
    path.write_text(
        "ts,funding_rate,venue,symbol\n"
        f"{NOW + 8 * 3600},-0.0002,binance,BTC/USDT:USDT\n"
        f"{NOW},0.0001,binance,BTC/USDT:USDT\n"
        f"{NOW + 8 * 3600},-0.0002,binance,BTC/USDT:USDT\n"
        f"{NOW + 16 * 3600},0.0003,binance,BTC/USDT:USDT\n",
        encoding="utf-8",
    )

    got = load_realized_settlements(
        "binance", "BTC", start_ts=NOW, end_ts=NOW + 8 * 3600,
        base_dir=tmp_path,
    )

    assert got == (
        FundingSettlement(settlement_ts=NOW, rate=0.0001),
        FundingSettlement(settlement_ts=NOW + 8 * 3600, rate=-0.0002),
    )


def test_load_realized_settlements_fails_closed_on_non_realized_schema(tmp_path):
    # Forward observations are predictions for next_funding_ts, not settlement
    # records, and must never be accepted by reconciliation.
    (tmp_path / "binance_BTC.csv").write_text(
        "ts,rate,interval_hours,next_funding_ts\n"
        f"{NOW},0.009,8,{NOW + 8 * 3600}\n",
        encoding="utf-8",
    )

    assert load_realized_settlements(
        "binance", "BTC", start_ts=NOW, end_ts=NOW,
        base_dir=tmp_path,
    ) is None


def test_load_realized_settlements_fails_closed_on_conflicting_duplicate(tmp_path):
    (tmp_path / "bybit_ETH.csv").write_text(
        "ts,funding_rate,venue,symbol\n"
        f"{NOW},0.0001,bybit,ETH/USDT:USDT\n"
        f"{NOW},0.0002,bybit,ETH/USDT:USDT\n",
        encoding="utf-8",
    )

    assert load_realized_settlements(
        "bybit", "ETH", start_ts=NOW, end_ts=NOW,
        base_dir=tmp_path,
    ) is None


def test_recent_realized_window_requires_all_21_authoritative_rows(tmp_path):
    path = tmp_path / "binance_BTC.csv"
    rows = [
        f"{NOW + i * 3600},{0.0001 + i * 0.000001},binance,BTC/USDT:USDT"
        for i in range(22)
    ]
    path.write_text(
        "ts,funding_rate,venue,symbol\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    recent = load_recent_realized_settlements(
        "binance",
        "BTC",
        limit=21,
        before_ts=NOW + 30 * 3600,
        base_dir=tmp_path,
    )
    assert recent is not None
    assert len(recent) == 21
    assert recent[0].settlement_ts == NOW + 3600

    assert load_recent_realized_settlements(
        "binance",
        "BTC",
        limit=23,
        before_ts=NOW + 30 * 3600,
        base_dir=tmp_path,
    ) is None


# ── record() must never fabricate the funding interval ──────────────────────
# The interval is time-varying per symbol (bases have switched 8h -> 4h
# mid-history), so a missing value must be recorded as absent, exactly like
# next_funding_ts. Writing a literal 8.0 puts an unobserved number into the
# permanent historical record.
def test_record_leaves_interval_blank_when_the_venue_did_not_report_one(tmp_path):
    frame = {"rate": 0.0001, "next_funding_ts": NOW + 3600.0, "interval_hours": None}
    assert record("binance", "SOL", frame, base_dir=tmp_path, now_fn=lambda: NOW)
    rows = list(csv.DictReader(open(tmp_path / "binance_SOL.csv", encoding="utf-8")))
    assert rows[0]["interval_hours"] == "", "absent interval must not be fabricated as 8.0"


def test_record_preserves_a_legitimate_short_interval(tmp_path):
    """`or` fires on a real 0-ish value; 4h venues must survive verbatim."""
    assert record("bybit", "TAO", _frame(interval=4.0), base_dir=tmp_path,
                  now_fn=lambda: NOW)
    rows = list(csv.DictReader(open(tmp_path / "bybit_TAO.csv", encoding="utf-8")))
    assert float(rows[0]["interval_hours"]) == 4.0


def test_record_rejects_a_nonpositive_or_unparsable_interval_as_absent(tmp_path):
    for coin, bad in (("AAA", 0.0), ("BBB", -8.0), ("CCC", "n/a")):
        frame = {"rate": 0.0001, "next_funding_ts": NOW, "interval_hours": bad}
        assert record("binance", coin, frame, base_dir=tmp_path, now_fn=lambda: NOW)
        rows = list(csv.DictReader(
            open(tmp_path / f"binance_{coin}.csv", encoding="utf-8")))
        assert rows[0]["interval_hours"] == "", f"{bad!r} must record as absent"
