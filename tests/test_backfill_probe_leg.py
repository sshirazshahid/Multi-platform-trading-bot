"""The funding backfill must cover the SHADOW-PROBE universe, not just F1.

2026-08-20 incident: every probe tick logged
``[probe] bybit <SYM>: no usable realized funding history — probe funding
accrual DEFERRED (run scripts/backfill_funding_history.py)`` for 17 bybit
symbols. The remedy the warning names could not work: the script's only legs
were `dispersion` (the F1 15-coin carry universe) and `listing` (binance
new-listing bases), and NONE of the 17 was in either set.

Root cause: the bundle-MR probe universe was widened on 2026-07-20 (frozen
5-major basket -> spec-derived bybit universe, now ~144 symbols) and the
funding backfill was never extended to match. Consequence measured on the
live warehouse: 27 of 82 pending bybit probe rows (33%) sat on symbols with
no realized funding history, so their after-cost net — the number the
promotion gate reads — was missing its funding component.

Fix under test: a `probe` leg whose bases come from the SAME spec artifact the
probes themselves resolve (`bundle_mr_probe_agent.resolve_universe`), so the
backfill stays in sync with any future universe change automatically instead
of drifting again.

Run: venv/Scripts/python.exe -m pytest tests/test_backfill_probe_leg.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.backfill_funding_history as bf  # noqa: E402


class _FakeEx:
    """Markets contain every requested perp, so scope is the only variable."""

    def __init__(self):
        self.markets = {}

    def load_markets(self):
        self.markets = {f"{b}/USDT:USDT": {} for b in
                        ("ALLO", "ZAMA", "XLM", "1000PEPE", "BTC", "ETH")}
        return self.markets


def test_probe_leg_is_exposed_on_the_cli():
    """The warning names this script; a `probe` leg must exist to run."""
    import inspect

    src = inspect.getsource(bf.main)
    assert '"probe"' in src or "'probe'" in src, (
        "backfill must expose a --leg probe covering the shadow-probe universe"
    )


def test_probe_leg_bases_come_from_the_probe_universe(monkeypatch):
    """Scope must be the SPEC-derived probe universe — the same source the
    probes resolve — so it cannot drift out of sync again."""
    seen: list = []

    def fake_fetch(ex, sym, since_ms, limit=None):
        seen.append(sym)
        return []

    monkeypatch.setattr(bf, "make_exchange", lambda venue: _FakeEx())
    monkeypatch.setattr(bf, "fetch_funding_history_paginated", fake_fetch)
    monkeypatch.setattr(
        bf, "_probe_bases",
        lambda venue="bybit": ("ALLO", "ZAMA", "XLM", "1000PEPE"))

    report = bf.backfill_probe(0)
    assert {r["base"] for r in report} == {"ALLO", "ZAMA", "XLM", "1000PEPE"}
    assert all(r["leg"] == "probe" for r in report)
    assert all(r["venue"] == "bybit" for r in report), (
        "the bundle-MR probes trade bybit; that is the venue needing history"
    )


def test_probe_bases_resolve_from_the_shared_spec_helper():
    """_probe_bases must delegate to the probes' own resolver, not a copy."""
    import inspect

    src = inspect.getsource(bf._probe_bases)
    assert "resolve_universe" in src, (
        "probe bases must come from bundle_mr_probe_agent.resolve_universe so "
        "a universe change propagates to the backfill automatically"
    )


def test_probe_leg_survives_a_venue_outage(monkeypatch):
    """Fail-soft like the other legs: an outage reports, never raises."""

    def boom(venue):
        raise RuntimeError("venue down")

    monkeypatch.setattr(bf, "make_exchange", boom)
    monkeypatch.setattr(bf, "_probe_bases", lambda venue="bybit": ("ALLO",))
    report = bf.backfill_probe(0)
    assert report and all(r["status"] == "venue_down" for r in report)


def test_unlisted_base_is_recorded_not_fetched(monkeypatch):
    """A base with no live perp is skipped honestly, never silently dropped."""
    calls: list = []
    monkeypatch.setattr(bf, "make_exchange", lambda venue: _FakeEx())
    monkeypatch.setattr(
        bf, "fetch_funding_history_paginated",
        lambda ex, sym, since, limit=None: calls.append(sym) or [])
    monkeypatch.setattr(bf, "_probe_bases", lambda venue="bybit": ("ALLO", "NOSUCH"))
    report = bf.backfill_probe(0)
    statuses = {r["base"]: r["status"] for r in report}
    assert statuses["NOSUCH"] == "not_listed"
    assert "NOSUCH/USDT:USDT" not in calls


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
