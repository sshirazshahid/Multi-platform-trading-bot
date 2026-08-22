"""A research-only feed must never gate live entries.

2026-08-22, measured on the live warehouse: `soft_stale_entry_block` accounted
for 219 of 465 entry decisions in one day (42.3% across the week). Its reason
was `forward_feeds_stale`, and the unhealthy feed was `skew`:

    data/skew_status.json
      {"updated": 1787386558.1, "connected": false,
       "open_hours": 0, "total_polls": 0}

Sampled twice 95s apart, `updated` advanced 62.4s -- the harvester is alive and
retrying every minute, and has NEVER once succeeded (`total_polls: 0`). Its
`fresh: true` is misleading: it times the failure record, not any data.

The decisive fact: **nothing in the live decision path consumes skew.** Every
`skew` reference under core/ is either CLOCK skew (`max_future_skew_seconds`,
`max_clock_skew_ms`) or statistical SKEWNESS (`_skew` feeding the deflated
Sharpe). The options-skew feed exists for `research/screen_skew_shock_drift.py`.

So a research feed that has never worked was blocking ~42% of live trading
decisions. `open_hours: 0` compounds it: an options feed with market hours is
*expected* to sit disconnected off-hours, which makes gating 24/7 crypto entries
on it wrong by construction, not merely unlucky.

The fix separates two questions that `unhealthy_forward_feeds` had conflated:
  "is this feed healthy?"        -> monitoring/alerting, unchanged
  "may this feed block entries?" -> new, opt-out, DEFAULT ON (fail-closed)

Run: venv/Scripts/python.exe -m pytest tests/test_feed_entry_gating.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.feed_health import (  # noqa: E402
    FORWARD_FEEDS,
    ForwardFeedSpec,
    entry_gating_feeds,
    unhealthy_forward_feeds,
)


def _rec(name: str, *, connected: bool = True, fresh: bool = True, error=None) -> dict:
    """A health record shaped like read_forward_feed_status() emits."""
    return {
        "name": name,
        "exists": True,
        "connected": connected,
        "fresh": fresh,
        "age_sec": 74.0,
        "error": error,
    }


# ------------------------------------------------------- the reported defect
def test_dead_skew_feed_does_not_gate_entries():
    """THE fix: skew may be unhealthy without blocking a single trade."""
    records = [
        _rec("liquidations"),
        _rec("skew", connected=False),  # the live payload, verbatim in shape
        _rec("l2"),
        _rec("tv"),
    ]
    assert entry_gating_feeds(records) == [], (
        "a research-only feed blocked entries; 219 of 465 decisions died here "
        "on 2026-08-22 for a feed nothing in the live path reads"
    )


def test_skew_is_still_reported_unhealthy_for_monitoring():
    """Ungating is not silencing. The operator must still see it is broken.

    The 2026-08-17 lesson: a suppression that hides a broken component is how an
    80h outage went unnoticed. Alerting keeps its old behaviour exactly.
    """
    records = [_rec("liquidations"), _rec("skew", connected=False), _rec("l2"), _rec("tv")]
    assert unhealthy_forward_feeds(records) == ["skew"], (
        "monitoring must still flag skew; only ENTRY GATING changes"
    )


# --------------------------------------------------- the gate still protects
@pytest.mark.parametrize("feed", ["liquidations", "l2", "tv"])
@pytest.mark.parametrize(
    "breakage", [{"connected": False}, {"fresh": False}, {"error": "boom"}]
)
def test_a_genuinely_load_bearing_feed_still_blocks_entries(feed, breakage):
    """Ungating skew must not ungate anything else, by any failure mode.

    If this passes for skew but also for l2, the fix has removed the protection
    instead of narrowing it.
    """
    records = [_rec(n) for n in ("liquidations", "skew", "l2", "tv")]
    for r in records:
        if r["name"] == feed:
            r.update(breakage)
    assert feed in entry_gating_feeds(records), (
        f"{feed} broke with {breakage} and no longer blocks entries -- the fix "
        "removed protection rather than narrowing it"
    )


def test_missing_feed_record_still_blocks():
    """`exists: False` is the harvester never having run at all."""
    records = [_rec("liquidations"), _rec("l2"), _rec("tv")]
    records[1]["exists"] = False
    assert "l2" in entry_gating_feeds(records)


# ------------------------------------------------------------- fail-closed
def test_gating_defaults_to_ON_for_any_new_feed():
    """A feed added later must gate unless someone opts it out DELIBERATELY.

    Default-off would mean the next feed silently stops protecting entries.
    """
    spec = ForwardFeedSpec(
        name="future_feed",
        script="scripts\\x.py",
        status_path="data\\x.json",
        max_age_sec=1200,
    )
    assert spec.gates_entries is True, "new feeds must gate by default (fail-closed)"


def test_exactly_one_feed_is_opted_out_and_it_is_skew():
    """Pin the blast radius. If a second feed gets ungated, this fails loudly."""
    opted_out = sorted(s.name for s in FORWARD_FEEDS if not s.gates_entries)
    assert opted_out == ["skew"], (
        f"expected only skew to be ungated, found {opted_out}. Every additional "
        "opt-out removes a live-entry protection and needs its own justification."
    )


def test_unknown_feed_name_in_records_still_gates():
    """A record whose name is not in FORWARD_FEEDS must NOT be silently ignored.

    Unknown -> treated as gating, so a renamed feed fails closed rather than
    quietly losing its protection.
    """
    records = [_rec("brand_new_feed", connected=False)]
    assert entry_gating_feeds(records) == ["brand_new_feed"]


# ------------------------------------------- the WIRING, not just the helper
# Adversarial review caught this gap: reverting health_watchdog's latch branch
# from `gating_bad` back to `bad` -- restoring the entire defect -- left every
# test above green, because they only exercised entry_gating_feeds() in
# isolation. A helper that is correct but unused fixes nothing. These pin the
# watchdog actually calling it.
def _wd(tmp_path, monkeypatch, records):
    import core.feed_health as fh
    import core.health_watchdog as hw
    import core.soft_stale_latch as ssl

    latch = tmp_path / "soft_stale_latch.json"
    monkeypatch.setattr(ssl, "SOFT_STALE_LATCH_PATH", latch)
    monkeypatch.setattr(hw, "SOFT_STALE_LATCH_PATH", latch, raising=False)
    monkeypatch.setattr(fh, "read_forward_feed_status", lambda *a, **k: records)

    engine = type("E", (), {})()
    wd = hw.HealthWatchdog(engine, notifier=None, warehouse_path=tmp_path / "wh.sqlite")
    return wd, latch


def test_watchdog_does_not_latch_when_only_skew_is_down(tmp_path, monkeypatch):
    """THE wiring test. Revert `gating_bad` -> `bad` and this must fail."""
    wd, latch = _wd(
        tmp_path, monkeypatch,
        [_rec("liquidations"), _rec("skew", connected=False), _rec("l2"), _rec("tv")],
    )
    wd._check_forward_feeds()
    assert not latch.exists(), (
        "the watchdog latched entries for a research-only feed -- the helper is "
        "correct but the wiring still uses the wide list"
    )


def test_watchdog_still_latches_when_a_gating_feed_is_down(tmp_path, monkeypatch):
    """The other half: protection must survive the narrowing."""
    wd, latch = _wd(
        tmp_path, monkeypatch,
        [_rec("liquidations"), _rec("skew"), _rec("l2", connected=False), _rec("tv")],
    )
    wd._check_forward_feeds()
    assert latch.exists(), "l2 died and entries were NOT blocked"
    import json as _json

    doc = _json.loads(latch.read_text(encoding="utf-8"))
    assert doc.get("active") is True
    assert doc["detail"]["feeds"] == ["l2"], "latch must name the gating feed only"


def test_feed_staleness_does_not_clobber_another_subsystems_latch(tmp_path, monkeypatch):
    """Observed live 2026-08-22: an `exchange_outage:binance` latch (fails=9)
    was overwritten by `forward_feeds_stale/[skew]` four minutes later. The
    clear branch matches startswith("forward_feeds"), so the next skew recovery
    would DELETE a block a real exchange outage was holding."""
    import json as _json

    wd, latch = _wd(
        tmp_path, monkeypatch,
        [_rec("liquidations"), _rec("skew"), _rec("l2", connected=False), _rec("tv")],
    )
    latch.write_text(
        _json.dumps({"active": True, "reason": "exchange_outage:binance",
                     "ts_unix": 1.0, "detail": {"fails": 9}}),
        encoding="utf-8",
    )
    wd._check_forward_feeds()
    doc = _json.loads(latch.read_text(encoding="utf-8"))
    assert doc["reason"] == "exchange_outage:binance", (
        "feed staleness overwrote an exchange-outage latch; a later feed "
        "recovery would then clear a block the outage still needed"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
