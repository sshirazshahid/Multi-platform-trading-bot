"""Offline tests for scripts/backfill_unlock_calendar.py parsing logic.

Fixture = REAL DefiLlama bucket payload for arbitrum (captured 2026-07-11),
trimmed (12 cliff events, documentedData downsampled) — not synthesized.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_unlock_calendar import (  # noqa: E402
    WIN_HI,
    WIN_LO,
    build_circ_series,
    circ_at,
    extract_cliff_events,
    index_protocols_in_window,
    map_gecko_to_symbol,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "emissions_arbitrum_trimmed.json")


def _payload():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def test_extract_cliff_events_window_and_merge():
    events = extract_cliff_events(_payload())
    # 2023-03 TGE events (ts 1679533271) are BEFORE the frozen 2023-06 window.
    assert all(WIN_LO <= e["ts"] < WIN_HI for e in events)
    assert 1679533271 not in {e["ts"] for e in events}
    # the 2024-03-16 insiders+privateSale cluster merges into ONE event:
    merged = [e for e in events if e["ts"] == 1710550800]
    assert len(merged) == 1
    m = merged[0]
    assert m["n_allocations"] == 2
    # privateSale counts toward event size; insiders excluded but recorded.
    assert abs(m["tokens"] - 474770833.3333333) < 1.0
    assert abs(m["excluded_tokens"] - 729625000) < 1.0
    assert m["excluded_categories"] == ["insiders"]
    assert m["categories"] == ["privateSale"]


def test_extract_cliff_events_noncirculating_excluded_from_size():
    events = extract_cliff_events(_payload())
    nc = [e for e in events if e["ts"] == 1697623820]  # treasury-only cliff
    assert len(nc) == 1
    assert nc[0]["tokens"] == 0.0
    assert nc[0]["excluded_tokens"] > 0
    # zero-size events exist in the calendar; the SCREEN's >=10% filter drops them.


def test_build_circ_series_excludes_noncirculating_label():
    p = _payload()
    series = build_circ_series(p)
    assert series == sorted(series, key=lambda t: t[0])
    # Treasury (noncirculating) must not be included: total documented circ at the
    # end must be strictly below the all-label sum.
    all_labels_end = sum(a["data"][-1]["unlocked"] for a in p["documentedData"]["data"])
    treasury_end = next(a["data"][-1]["unlocked"] for a in p["documentedData"]["data"]
                        if a["label"] == "Arbitrum DAO Treasury")
    assert abs(series[-1][1] - (all_labels_end - treasury_end)) < 1.0


def test_circ_at_asof_semantics():
    series = [[100, 10.0], [200, 20.0], [300, 30.0]]
    assert circ_at(series, 50) is None
    assert circ_at(series, 100) == 10.0
    assert circ_at(series, 250) == 20.0
    assert circ_at(series, 999) == 30.0


def test_ratio_denominator_is_positive_at_merged_event():
    p = _payload()
    series = build_circ_series(p)
    c = circ_at(series, 1710550800)
    assert c is not None and c > 0
    # sanity: the 474.8M privateSale unlock is a two-digit % of documented circ
    ratio = 474770833.3333333 / c
    assert 0.01 < ratio < 1.0


def test_map_gecko_to_symbol():
    rows = [{"id": "arbitrum", "symbol": "arb", "name": "Arbitrum"},
            {"id": "aptos", "symbol": "apt", "name": "Aptos"},
            {"id": "junk"}]
    m = map_gecko_to_symbol(rows)
    assert m == {"arbitrum": "ARB", "aptos": "APT"}


def test_index_protocols_in_window_prefilter():
    idx = [
        {"protocolSlug": "a", "gecko_id": "a", "name": "A", "circSupply": 1, "maxSupply": 2,
         "events": [{"unlockType": "cliff", "timestamp": WIN_LO + 10}]},
        {"protocolSlug": "b", "gecko_id": "b", "name": "B", "circSupply": 1, "maxSupply": 2,
         "events": [{"unlockType": "linear", "timestamp": WIN_LO + 10}]},
        {"protocolSlug": "c", "gecko_id": "c", "name": "C", "circSupply": 1, "maxSupply": 2,
         "events": [{"unlockType": "cliff", "timestamp": WIN_LO - 10}]},
    ]
    out = index_protocols_in_window(idx)
    assert [p["slug"] for p in out] == ["a"]
