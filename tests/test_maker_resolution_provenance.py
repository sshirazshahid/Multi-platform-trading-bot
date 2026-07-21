"""Maker-first resolution must hand the INTENT to the terminal recorder.

2026-07-21 incident (first fill after the economic-gate unblock):
``_finalize_maker_intent`` built ``_maker_first_ctx`` WITHOUT the
``maker_intent`` key, so ``open_position`` passed an EMPTY dict to
``_record_maker_resolution_decision``. With ``enforce_event_provenance`` on,
``record_terminal_decision`` raised "candidate symbol is missing" and
``risk.latch_incident`` HALTED all new entries after the very first maker
fill (log 07:15:58Z, INJ/USDT).

Harness mirrors tests/test_maker_first_paper.py (its module-level helpers are
imported; the fixture is redefined locally because pytest fixtures do not
cross modules outside conftest).
"""

from __future__ import annotations

import pytest

from tests.test_maker_first_paper import SYM, FakeExchange, _om, _open, _ticker


@pytest.fixture
def mf_on(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "MAKER_FIRST_PAPER",
        {"enabled": True, "timeout_sec": 45, "max_chase": 1},
        raising=False,
    )


def _spy_terminal_recorder(om, monkeypatch):
    seen = []

    def spy(intent, **kwargs):
        seen.append((dict(intent), kwargs))
        return True

    monkeypatch.setattr(om, "_record_maker_resolution_decision", spy)
    return seen


def test_maker_fill_terminal_provenance_receives_the_intent(
    mf_on, tmp_path, monkeypatch
):
    om = _om(tmp_path)
    seen = _spy_terminal_recorder(om, monkeypatch)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}  # sold through 99.9

    om._resolve_pending_maker_entries(ex)

    assert om.tracker.add.called, "maker fill did not open a position"
    assert seen, "maker fill must write a terminal resolution decision"
    intent, kwargs = seen[-1]
    assert intent.get("symbol") == SYM, (
        "the FILLED terminal decision must receive the maker intent — an "
        "empty dict raises 'candidate symbol is missing' and latches an "
        "execution incident that halts all new entries"
    )
    assert intent.get("exchange") == ex.name
    assert kwargs.get("outcome") == "filled"
    assert kwargs.get("reason") == "maker_first_maker_fill"


def test_taker_fallback_terminal_provenance_receives_the_intent(
    mf_on, tmp_path, monkeypatch
):
    import time as _time

    om = _om(tmp_path)
    seen = _spy_terminal_recorder(om, monkeypatch)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = _time.time() - 120  # past timeout

    om._resolve_pending_maker_entries(ex)

    assert om.tracker.add.called, "taker fallback did not open a position"
    assert seen, "taker fallback must write a terminal resolution decision"
    intent, kwargs = seen[-1]
    assert intent.get("symbol") == SYM
    assert kwargs.get("outcome") == "filled"
    assert kwargs.get("reason") == "maker_first_taker_fallback_fill"
