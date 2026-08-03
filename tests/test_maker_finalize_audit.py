"""Deep-audit fixes for maker-first finalize (2026-07-23).

Bugs confirmed in paper-path audit:
1. Finalize omitted authorization_strategy_id → entry policy rejects after
   virtual fill when allowlist has mcp_registry but strategy is algo_det.
2. Intent was popped BEFORE open success → silent drop on reject / book fail
   without _record_maker_nonfill.
3. Chase abandon never recorded a terminal nonfill (parent stuck DEFERRED).
4. Finalize ctx omitted provenance_intent → duplicate order intents.
"""
from __future__ import annotations

from unittest.mock import MagicMock

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


def test_finalize_passes_authorization_strategy_id(mf_on, tmp_path, monkeypatch):
    om = _om(tmp_path)
    om.enforce_entry_policy = True
    seen = {}

    real_open = om.open_position

    def spy_open(*args, **kwargs):
        seen["authorization_strategy_id"] = kwargs.get("authorization_strategy_id")
        # Bypass entry policy for the fill itself — we only assert the kwarg.
        om.enforce_entry_policy = False
        return real_open(*args, **kwargs)

    monkeypatch.setattr(om, "open_position", spy_open)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex, strategy="algo_det")
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["strategy_id"] = "mcp_registry"
    om._pending_maker[key]["provenance_intent"] = {"id": "intent-1"}
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}
    om._resolve_pending_maker_entries(ex)
    assert seen.get("authorization_strategy_id") == "mcp_registry"


def test_finalize_carries_provenance_intent_in_ctx(mf_on, tmp_path, monkeypatch):
    om = _om(tmp_path)
    seen = {}

    real_open = om.open_position

    def spy_open(*args, **kwargs):
        ctx = kwargs.get("_maker_first_ctx") or {}
        seen["provenance_intent"] = ctx.get("provenance_intent")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(om, "open_position", spy_open)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["provenance_intent"] = {"id": "prov-abc"}
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}
    om._resolve_pending_maker_entries(ex)
    assert seen.get("provenance_intent") == {"id": "prov-abc"}


def test_finalize_persists_filled_child_decision_and_preserves_parent_lineage(
    mf_on, tmp_path, monkeypatch
):
    import core.warehouse as wh

    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    intent = om._pending_maker[key]
    parent_id = "decision-deferred-parent-1"
    child_id = "maker-resolution-filled-child-1"
    intent["decision_id"] = parent_id
    intent["parent_decision_id"] = parent_id
    intent["resolution_decision_id"] = child_id

    warehouse = MagicMock()
    monkeypatch.setattr(wh, "get_warehouse", lambda: warehouse)
    seen = {}
    resolutions = []
    real_open = om.open_position

    def spy_open(*args, **kwargs):
        seen["decision_id"] = kwargs.get("decision_id")
        seen["decision_parent_id"] = kwargs.get("decision_parent_id")
        return real_open(*args, **kwargs)

    def spy_resolution(resolved_intent, **kwargs):
        resolutions.append((om._stable_maker_resolution_id(resolved_intent), kwargs))
        return True

    monkeypatch.setattr(om, "open_position", spy_open)
    monkeypatch.setattr(om, "_record_maker_resolution_decision", spy_resolution)
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}
    om._resolve_pending_maker_entries(ex)

    assert om.tracker.add.called, "maker fill did not open a position"
    assert seen == {
        "decision_id": child_id,
        "decision_parent_id": parent_id,
    }
    assert intent["decision_id"] == parent_id, "parent decision must stay immutable"
    assert resolutions[-1][0] == child_id
    assert resolutions[-1][1]["outcome"] == "filled"
    assert warehouse.record_trade_open.call_args.kwargs["decision_id"] == child_id


def test_finalize_records_nonfill_when_open_rejects(mf_on, tmp_path, monkeypatch):
    om = _om(tmp_path)
    recorded = []
    monkeypatch.setattr(
        om,
        "_record_maker_nonfill",
        lambda intent, **kw: recorded.append((dict(intent), kw)),
    )
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)  # registers pending via real open_position
    key = f"{ex.name}:{SYM}"
    assert key in om._pending_maker
    monkeypatch.setattr(om, "open_position", lambda *a, **k: None)
    om.last_open_reject = "entry_policy_denied"
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}
    om._resolve_pending_maker_entries(ex)
    assert key not in om._pending_maker
    assert recorded, "reject after virtual fill must record nonfill"
    assert recorded[0][1]["outcome"] in {"rejected", "cancelled", "error"}


def test_chase_abandon_records_nonfill(mf_on, tmp_path, monkeypatch):
    import time

    om = _om(tmp_path)
    recorded = []
    monkeypatch.setattr(
        om,
        "_record_maker_nonfill",
        lambda intent, **kw: recorded.append(kw),
    )
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 46
    ex.ticker = {"last": 100.45, "bid": 100.4, "ask": 100.5}
    om._resolve_pending_maker_entries(ex)
    assert om.last_open_reject == "maker_chase_abandoned"
    assert recorded
    assert recorded[0]["reason"] == "maker_chase_abandoned"


def _real_provenance_payload():
    """A REAL OrderIntent.to_dict() payload — what registration persists."""
    from datetime import datetime, timezone

    from core.contracts import InstrumentId, OrderIntent

    intent = OrderIntent(
        intent_id="intent-halt-repro-1",
        decision_id="dec-halt-repro-1",
        instrument=InstrumentId(
            venue="binance",
            market="perpetual",
            canonical_symbol=SYM,
            exchange_symbol=SYM,
        ),
        created_at=datetime.now(timezone.utc),
        side="buy",
        order_type="limit",
        quantity="1.0",
        limit_price="99.9",
        post_only=True,
    )
    return intent.to_dict()


def test_finalize_rehydrates_provenance_dict_and_never_latches(
    mf_on, tmp_path, monkeypatch
):
    """2026-07-23 12:05Z halt regression: ctx carried provenance_intent as a
    to_dict() payload; open_position handed the raw dict to
    _append_execution_event, whose .intent_id access raised AttributeError
    and latched an execution-incident HALT on the first maker resolution."""
    import core.warehouse as wh
    from core.contracts import OrderIntent

    om = _om(tmp_path)
    monkeypatch.setattr(wh, "get_warehouse", lambda: MagicMock())
    seen = []
    real_append = om._append_execution_event

    def spy_append(intent, *a, **k):
        seen.append(intent)
        return real_append(intent, *a, **k)

    monkeypatch.setattr(om, "_append_execution_event", spy_append)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["provenance_intent"] = _real_provenance_payload()
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}
    om._resolve_pending_maker_entries(ex)

    assert not om.risk.latch_incident.called, (
        "maker finalize latched an execution incident: "
        f"{om.risk.latch_incident.call_args}"
    )
    assert not any(isinstance(x, dict) for x in seen), (
        "raw provenance dict reached _append_execution_event"
    )
    assert any(isinstance(x, OrderIntent) for x in seen), (
        "no rehydrated OrderIntent reached _append_execution_event"
    )
    assert om.tracker.add.called, "position must still open after rehydration"
