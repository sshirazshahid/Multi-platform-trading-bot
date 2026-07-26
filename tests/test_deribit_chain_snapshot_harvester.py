"""Parser/archive tests for the Deribit chain snapshot harvester (C2 prereg 33_*)."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harvest_deribit_chain_snapshots import (  # noqa: E402
    append_snapshot,
    atm_open_interest,
    parse_instrument_name,
)


def test_parse_instrument_name_call_and_put():
    c = parse_instrument_name("BTC-25JUL26-118000-C")
    assert c == {
        "currency": "BTC",
        "expiry_date": "2026-07-25",
        "strike": 118000.0,
        "opt_type": "call",
    }
    p = parse_instrument_name("BTC-5AUG26-95000-P")
    assert p["expiry_date"] == "2026-08-05"
    assert p["opt_type"] == "put"


def test_parse_instrument_name_rejects_non_options():
    assert parse_instrument_name("BTC-PERPETUAL") is None
    assert parse_instrument_name("BTC-25JUL26") is None
    assert parse_instrument_name("") is None
    assert parse_instrument_name("BTC-99XXX26-1000-C") is None


def test_atm_open_interest_filters_expiry_and_band():
    rows = [
        {"instrument_name": "BTC-25JUL26-100000-C", "open_interest": 10.0},
        {"instrument_name": "BTC-25JUL26-102000-P", "open_interest": 7.0},
        # outside +/-2.5% of 100k
        {"instrument_name": "BTC-25JUL26-110000-C", "open_interest": 99.0},
        # wrong expiry
        {"instrument_name": "BTC-26JUL26-100000-C", "open_interest": 50.0},
        # non-option row ignored
        {"instrument_name": "BTC-PERPETUAL", "open_interest": 123.0},
    ]
    total = atm_open_interest(rows, spot=100_000.0, expiry_date="2026-07-25", band_pct=2.5)
    assert total == 17.0
    assert atm_open_interest(rows, spot=0.0, expiry_date="2026-07-25") == 0.0


def test_append_snapshot_writes_monthly_jsonl(tmp_path):
    now = dt.datetime(2026, 7, 24, 7, 30, tzinfo=dt.timezone.utc)
    rows = [{"instrument_name": "BTC-25JUL26-118000-C", "open_interest": 1.5}]
    path = append_snapshot(tmp_path, "BTC", rows, now)
    assert path.name == "BTC_2026-07.jsonl"
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["currency"] == "BTC"
    assert rec["n_instruments"] == 1
    assert rec["ts_utc"] == "2026-07-24T07:30:00Z"
    assert rec["rows"][0]["open_interest"] == 1.5
    # second append -> second line, same file
    append_snapshot(tmp_path, "BTC", rows, now)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
