"""Polymarket harvester — parsing, PIT stamping, and append-only storage.

This feeds a FUTURE pre-registered screen ("do prediction-market probabilities
lead spot?"). Nothing here touches the trade path. The properties tested are
the ones that would silently corrupt that screen's evidence:

  * PIT stamping. Every row carries available_at_utc at harvest time. Without
    it a screen cannot prove it wasn't reading a probability that only existed
    after the outcome — the look-ahead failure this repo has hit before.
  * Probability provenance. outcomePrices is LAST TRADE and can be stale;
    bestBid/bestAsk is the live book. A screen must know which one it got, so
    the source is recorded rather than the two silently blended.
  * Resolved markets are kept, not dropped. When a market closes its
    outcomePrices become ["1","0"] or ["0","1"] — that IS the ground-truth
    label a predictive screen needs. Dropping them throws away the y variable.
  * Append-only. Snapshots accrue a forward time series; an overwrite destroys
    exactly the history that makes the screen possible.

Filtering is deliberately NOT the harvester's job (COLLECT -> STORE, per the
whale/liquidation harvesters). Baking a liquidity or open/closed filter in here
would pre-decide a question the prereg is supposed to fix.

Run: venv/Scripts/python.exe -m pytest tests/test_harvest_polymarket.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harvest_polymarket import (  # noqa: E402
    derive_probability,
    parse_market,
    write_snapshot,
)

EVENT = {"title": "When will Bitcoin hit $150k?", "id": "evt-1"}
NOW = "2026-08-18T02:55:00+00:00"


def _market(**over):
    m = {
        "conditionId": "0xabc",
        "slug": "will-bitcoin-hit-150k-by-september-30",
        "question": "Will Bitcoin hit $150k by September 30?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.62", "0.38"]',
        "bestBid": 0.61,
        "bestAsk": 0.63,
        "spread": 0.02,
        "lastTradePrice": 0.62,
        "volumeNum": 27150645.0,
        "liquidityNum": 480000.0,
        "endDate": "2026-09-30T04:00:00Z",
        "closed": False,
        "active": True,
    }
    m.update(over)
    return m


# ── probability provenance ──────────────────────────────────────────────────

def test_probability_prefers_the_live_book():
    """bestBid/bestAsk is live; outcomePrices is last trade and can be stale."""
    p, src = derive_probability(_market(bestBid=0.40, bestAsk=0.44,
                                        outcomePrices='["0.90", "0.10"]'))
    assert p == pytest.approx(0.42)
    assert src == "book_mid"


def test_probability_falls_back_to_outcome_prices():
    p, src = derive_probability(_market(bestBid=None, bestAsk=None))
    assert p == pytest.approx(0.62)
    assert src == "outcome_prices"


def test_probability_none_when_nothing_usable():
    p, src = derive_probability(_market(bestBid=None, bestAsk=None,
                                        outcomePrices="[]"))
    assert p is None and src == "none"


def test_probability_survives_malformed_json():
    """The API returns these as JSON STRINGS; a bad one must not kill a run."""
    p, src = derive_probability(_market(bestBid=None, bestAsk=None,
                                        outcomePrices="not json"))
    assert p is None and src == "none"


def test_one_sided_book_is_not_treated_as_a_mid():
    """A single quote is not a midpoint — fall back rather than invent one."""
    _p, src = derive_probability(_market(bestBid=None, bestAsk=0.001))
    assert src == "outcome_prices"


# ── PIT stamping and shape ──────────────────────────────────────────────────

def test_row_carries_pit_stamp():
    row = parse_market(EVENT, _market(), now=NOW)
    assert row["available_at_utc"] == NOW
    assert row["condition_id"] == "0xabc"
    assert row["event_title"] == "When will Bitcoin hit $150k?"
    assert row["prob_yes"] == pytest.approx(0.62)
    assert row["volume_num"] == pytest.approx(27150645.0)


def test_resolved_market_is_kept_with_its_outcome():
    """A closed market's prices ARE the label. Dropping it discards y."""
    row = parse_market(EVENT, _market(closed=True, bestBid=None, bestAsk=None,
                                      outcomePrices='["1", "0"]'), now=NOW)
    assert row is not None, "resolved markets must be harvested, not skipped"
    assert row["closed"] is True
    assert row["prob_yes"] == pytest.approx(1.0)


def test_market_without_identity_is_skipped():
    """No conditionId means rows cannot be joined across snapshots."""
    assert parse_market(EVENT, _market(conditionId=None), now=NOW) is None


# ── storage ─────────────────────────────────────────────────────────────────

def test_write_is_append_only(tmp_path):
    rows = [parse_market(EVENT, _market(), now=NOW)]
    p1 = write_snapshot(rows, out_dir=tmp_path, day="2026-08-18")
    p2 = write_snapshot(rows, out_dir=tmp_path, day="2026-08-18")
    assert p1 == p2
    lines = p1.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "a second harvest must APPEND, not overwrite"
    assert json.loads(lines[0])["condition_id"] == "0xabc"


def test_dry_run_writes_nothing(tmp_path):
    rows = [parse_market(EVENT, _market(), now=NOW)]
    assert write_snapshot(rows, out_dir=tmp_path, day="2026-08-18",
                          dry_run=True) is None
    assert list(tmp_path.glob("*.jsonl")) == []


def test_empty_harvest_writes_nothing(tmp_path):
    """An empty fetch must not create a file that looks like a real snapshot."""
    assert write_snapshot([], out_dir=tmp_path, day="2026-08-18") is None


# ── query strategy ──────────────────────────────────────────────────────────

def test_harvest_targets_open_and_resolved_separately():
    """Blind pagination buries the live set.

    Measured 2026-08-18: paginating the untargeted crypto tag returned 3,442
    markets of which only 5 were OPEN; closed=false alone returned 443 open.
    Both passes are required — open markets are the accruing series (X),
    recent closes carry the resolution label (y). Losing the open query kills
    the forward series silently, which is why it is pinned here.
    """
    from scripts.harvest_polymarket import EVENT_QUERIES, MAX_PAGES

    queries = dict(EVENT_QUERIES)
    assert "closed=false" in queries.get("open", "")
    assert "closed=true" in queries.get("resolved", "")
    assert MAX_PAGES <= 10, "targeted queries should not need deep pagination"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
