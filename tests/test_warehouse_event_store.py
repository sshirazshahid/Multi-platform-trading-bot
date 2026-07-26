from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.contracts import (
    DecisionRecord,
    ExecutionEvent,
    FundingSettlement,
    InstrumentId,
    MarketDatum,
    MarketSnapshot,
    OrderIntent,
)
from core.warehouse import EventConflictError, Warehouse

NOW = datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc)


@pytest.fixture
def instrument() -> InstrumentId:
    return InstrumentId("bybit", "perpetual", "ETH/USDT", "ETHUSDT")


@pytest.fixture
def wh(tmp_path) -> Warehouse:
    return Warehouse(tmp_path / "event-store.sqlite")


def _snapshot(
    instrument: InstrumentId,
    value: str = "3100.25",
    *,
    snapshot_id: str = "snapshot-001",
    observed_at: datetime = NOW,
) -> MarketSnapshot:
    datum = MarketDatum(
        instrument=instrument,
        kind="mark_price",
        value=value,
        observed_at=observed_at,
        received_at=observed_at + timedelta(milliseconds=2),
        source="ticker",
        sequence=7,
    )
    return MarketSnapshot(
        snapshot_id=snapshot_id,
        instrument=instrument,
        observed_at=observed_at,
        received_at=observed_at + timedelta(milliseconds=3),
        data=(datum,),
        source="bybit-ws",
        sequence=7,
        context={"run_id": "run-001"},
    )


def _decision(instrument: InstrumentId) -> DecisionRecord:
    return DecisionRecord(
        decision_id="decision-001",
        instrument=instrument,
        strategy_id="carry-v1",
        action="enter_short",
        decided_at=NOW + timedelta(milliseconds=4),
        snapshot_id="snapshot-001",
        side="sell",
        confidence="0.65",
        model_manifest_id="manifest-001",
        reason_codes=("funding_high",),
        context={"config_version": "cfg-12"},
    )


def _intent(instrument: InstrumentId) -> OrderIntent:
    return OrderIntent(
        intent_id="intent-001",
        decision_id="decision-001",
        instrument=instrument,
        created_at=NOW + timedelta(milliseconds=5),
        side="sell",
        order_type="limit",
        quantity="0.10",
        limit_price="3100.25",
        client_order_id="client-001",
    )


def test_versioned_migration_is_idempotent_and_preserves_legacy_rows(tmp_path):
    path = tmp_path / "legacy.sqlite"
    first = Warehouse(path)
    candidate_id = first.record_candidate(
        exchange="bybit", symbol="ETH/USDT:USDT", decision="SKIP",
    )
    first._conn().close()
    if hasattr(Warehouse._tls, "conn"):
        delattr(Warehouse._tls, "conn")

    second = Warehouse(path)
    tables = {
        row["name"] for row in second.query(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "schema_migrations", "decision_events", "market_snapshots",
        "order_events", "funding_settlements",
    } <= tables
    assert second.query("SELECT id FROM candidates") == [{"id": candidate_id}]
    migrations = second.query(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    )
    assert migrations == [
        {"version": 1, "name": "immutable_event_store"},
        {"version": 2, "name": "immutable_event_store_replace_guards"},
    ]


def test_snapshot_append_is_retry_safe_and_rejects_id_collision(
    wh: Warehouse, instrument: InstrumentId,
):
    snapshot = _snapshot(instrument)
    assert wh.append_market_snapshot(snapshot) == snapshot.snapshot_id
    assert wh.append_market_snapshot(snapshot) == snapshot.snapshot_id
    assert wh.query("SELECT COUNT(*) AS n FROM market_snapshots")[0]["n"] == 1
    assert wh.query_market_snapshots(instrument=instrument) == [snapshot]
    assert wh.query_market_snapshots(snapshot_id=snapshot.snapshot_id) == [snapshot]

    with pytest.raises(EventConflictError, match="snapshot-001"):
        wh.append_market_snapshot(_snapshot(instrument, value="3101.00"))


def test_snapshot_query_orders_fractional_timestamps_chronologically(
    wh: Warehouse, instrument: InstrumentId,
):
    later = _snapshot(
        instrument,
        snapshot_id="snapshot-later",
        observed_at=NOW + timedelta(microseconds=500_000),
    )
    earlier = _snapshot(
        instrument,
        snapshot_id="snapshot-earlier",
        observed_at=NOW,
    )
    wh.append_market_snapshot(later)
    wh.append_market_snapshot(earlier)

    assert wh.query_market_snapshots(instrument=instrument) == [earlier, later]


def test_decision_and_order_event_queries_return_typed_contracts(
    wh: Warehouse, instrument: InstrumentId,
):
    snapshot = _snapshot(instrument)
    decision = _decision(instrument)
    intent = _intent(instrument)
    execution = ExecutionEvent(
        event_id="execution-001",
        intent_id=intent.intent_id,
        decision_id=decision.decision_id,
        instrument=instrument,
        event_type="acknowledged",
        occurred_at=NOW + timedelta(milliseconds=10),
        received_at=NOW + timedelta(milliseconds=11),
        venue_order_id="bybit-order-1",
        client_order_id="client-001",
        sequence=8,
    )

    wh.append_market_snapshot(snapshot)
    assert wh.append_decision_event(decision) == decision.decision_id
    assert wh.append_order_intent(intent) == intent.intent_id
    assert wh.append_execution_event(execution) == execution.event_id

    assert wh.query_decision_events(
        strategy_id="carry-v1", instrument=instrument,
    ) == [decision]
    assert wh.query_decision_events(decision_id=decision.decision_id) == [decision]
    assert wh.query_order_events(
        intent_id=intent.intent_id, instrument=instrument,
    ) == [intent, execution]
    assert wh.query_order_events(event_id=execution.event_id) == [execution]


def test_funding_settlements_are_append_only_and_queryable(
    wh: Warehouse, instrument: InstrumentId,
):
    settlement = FundingSettlement(
        settlement_id="funding-001",
        instrument=instrument,
        position_id="position-001",
        occurred_at=NOW,
        received_at=NOW + timedelta(milliseconds=50),
        rate="0.0001",
        amount="-0.031",
        asset="USDT",
        context={"run_id": "run-001"},
    )

    assert wh.append_funding_settlement(settlement) == settlement.settlement_id
    assert wh.append_funding_settlement(settlement) == settlement.settlement_id
    assert wh.query_funding_settlements(
        instrument=instrument, position_id="position-001",
    ) == [settlement]
    assert wh.query_funding_settlements(
        settlement_id=settlement.settlement_id,
    ) == [settlement]


@pytest.mark.parametrize(
    "table,id_column,id_value",
    [
        ("market_snapshots", "snapshot_id", "snapshot-001"),
        ("decision_events", "event_id", "decision-001"),
        ("order_events", "event_id", "intent-001"),
        ("funding_settlements", "settlement_id", "funding-001"),
    ],
)
def test_event_tables_reject_update_and_delete(
    wh: Warehouse,
    instrument: InstrumentId,
    table: str,
    id_column: str,
    id_value: str,
):
    wh.append_market_snapshot(_snapshot(instrument))
    wh.append_decision_event(_decision(instrument))
    wh.append_order_intent(_intent(instrument))
    wh.append_funding_settlement(FundingSettlement(
        settlement_id="funding-001",
        instrument=instrument,
        position_id="position-001",
        occurred_at=NOW,
        received_at=NOW,
        rate="0.0001",
        amount="-0.031",
        asset="USDT",
    ))

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        wh._conn().execute(
            f"UPDATE {table} SET schema_version=2 WHERE {id_column}=?",
            (id_value,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        wh._conn().execute(
            f"DELETE FROM {table} WHERE {id_column}=?", (id_value,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        wh._conn().execute(
            f"INSERT OR REPLACE INTO {table} "
            f"SELECT * FROM {table} WHERE {id_column}=?",
            (id_value,),
        )
