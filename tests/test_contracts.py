from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.contracts import (
    DecisionAction,
    DecisionOutcome,
    DecisionRecord,
    ExecutionEvent,
    ExecutionEventType,
    FundingSettlement,
    InstrumentId,
    MarketDataKind,
    MarketDatum,
    MarketSnapshot,
    MarketType,
    ModelManifest,
    OrderIntent,
    OrderSide,
    OrderType,
    StrategySpec,
    TimeInForce,
    Venue,
)

NOW = datetime(2026, 7, 13, 10, 30, tzinfo=timezone.utc)


def _instrument(exchange_symbol: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(
        venue="BINANCE",
        market="perp",
        canonical_symbol="btc/usdt",
        exchange_symbol=exchange_symbol,
    )


def _datum(kind: MarketDataKind, value: str) -> MarketDatum:
    return MarketDatum(
        instrument=_instrument(),
        kind=kind,
        value=value,
        observed_at=NOW,
        received_at=NOW + timedelta(milliseconds=2),
        source="bookTicker",
        sequence=42,
        context={"stream": {"partition": 1}},
    )


def test_instrument_identity_is_normalized_validated_and_frozen():
    instrument = _instrument()

    assert instrument.venue is Venue.BINANCE
    assert instrument.market is MarketType.PERPETUAL
    assert instrument.canonical_symbol == "BTC/USDT"
    assert instrument.exchange_symbol == "BTCUSDT"
    with pytest.raises(FrozenInstanceError):
        instrument.exchange_symbol = "BTC-USDT"  # type: ignore[misc]

    with pytest.raises(ValueError, match="venue"):
        InstrumentId("unknown", "spot", "BTC/USDT", "BTCUSDT")
    with pytest.raises(ValueError, match="canonical_symbol"):
        InstrumentId("binance", "spot", "BTCUSDT", "BTCUSDT")
    with pytest.raises(ValueError, match="exchange_symbol"):
        InstrumentId("binance", "spot", "BTC/USDT", "")


def test_market_contracts_are_deeply_immutable_and_round_trip():
    bid = _datum(MarketDataKind.BID_PRICE, "60234.10")
    ask = _datum(MarketDataKind.ASK_PRICE, "60234.20")
    snapshot = MarketSnapshot(
        snapshot_id="snap-001",
        instrument=_instrument(),
        observed_at=NOW,
        received_at=NOW + timedelta(milliseconds=3),
        data=(bid, ask),
        source="binance-ws",
        sequence=42,
        context={"quality": {"crossed": False}},
    )

    assert bid.value == Decimal("60234.10")
    assert MarketSnapshot.from_dict(snapshot.to_dict()) == snapshot
    with pytest.raises(TypeError):
        snapshot.context["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.context["quality"]["crossed"] = True  # type: ignore[index]

    with pytest.raises(ValueError, match="timezone-aware"):
        MarketDatum(
            instrument=_instrument(), kind="last_price", value="1",
            observed_at=datetime(2026, 1, 1), received_at=NOW,
        )


def test_strategy_spec_is_deeply_immutable_and_round_trips():
    spec = StrategySpec(
        strategy_id="F1",
        strategy_version="correctness-v1",
        family="delta_neutral_carry",
        market_types=("spot", "perpetual"),
        venues=("binance", "bybit", "bitget"),
        entry_rules={"funding": {"trailing_settlements": 21}},
        exit_rules={"max_settlements": 42},
        sizing={"leverage": 1.0},
        risk_limits={"paper_only": True},
        validation_status="edge_unproven",
        promotion_status="PAPER_CANDIDATE",
    )

    assert StrategySpec.from_dict(spec.to_dict()) == spec
    with pytest.raises(FrozenInstanceError):
        spec.strategy_version = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        spec.entry_rules["funding"]["trailing_settlements"] = 1  # type: ignore[index]
    with pytest.raises(ValueError, match="same instrument"):
        MarketSnapshot(
            snapshot_id="snap-bad",
            instrument=_instrument(),
            observed_at=NOW,
            received_at=NOW,
            data=(MarketDatum(
                instrument=_instrument("XBTUSDT"),
                kind="last_price",
                value="1",
                observed_at=NOW,
                received_at=NOW,
            ),),
        )


def test_decision_order_and_execution_contracts_validate_and_round_trip():
    decision = DecisionRecord(
        decision_id="decision-001",
        instrument=_instrument(),
        strategy_id="momentum-v2",
        action="enter_long",
        decided_at=NOW,
        snapshot_id="snap-001",
        side="buy",
        confidence="0.73",
        model_manifest_id="manifest-001",
        outcome="submitted",
        reason_codes=("trend", "liquidity_ok"),
        context={"run_id": "run-9"},
    )
    intent = OrderIntent(
        intent_id="intent-001",
        decision_id=decision.decision_id,
        instrument=_instrument(),
        created_at=NOW + timedelta(milliseconds=5),
        side="buy",
        order_type="limit",
        quantity="0.002",
        limit_price="60234.10",
        time_in_force="gtc",
        post_only=True,
        client_order_id="client-001",
        context={"risk_version": "risk-v3"},
    )
    fill = ExecutionEvent(
        event_id="exec-001",
        intent_id=intent.intent_id,
        decision_id=decision.decision_id,
        instrument=_instrument(),
        event_type="filled",
        occurred_at=NOW + timedelta(milliseconds=20),
        received_at=NOW + timedelta(milliseconds=22),
        venue_order_id="venue-123",
        client_order_id="client-001",
        sequence=100,
        quantity="0.002",
        price="60234.10",
        cumulative_quantity="0.002",
        fee="0.01",
        fee_asset="USDT",
    )

    assert decision.action is DecisionAction.ENTER_LONG
    assert decision.side is OrderSide.BUY
    assert decision.confidence == Decimal("0.73")
    assert intent.order_type is OrderType.LIMIT
    assert intent.time_in_force is TimeInForce.GTC
    assert fill.event_type is ExecutionEventType.FILLED
    assert DecisionRecord.from_dict(decision.to_dict()) == decision
    assert decision.outcome is DecisionOutcome.SUBMITTED
    assert decision.is_terminal
    assert OrderIntent.from_dict(intent.to_dict()) == intent
    assert ExecutionEvent.from_dict(fill.to_dict()) == fill

    with pytest.raises(ValueError, match="limit_price"):
        OrderIntent(
            intent_id="bad", decision_id="decision-001",
            instrument=_instrument(), created_at=NOW, side="buy",
            order_type="limit", quantity="1",
        )
    with pytest.raises(ValueError, match="quantity and price"):
        ExecutionEvent(
            event_id="bad-fill", intent_id="intent-001",
            instrument=_instrument(), event_type="filled",
            occurred_at=NOW, received_at=NOW,
        )


def test_model_manifest_and_funding_settlement_are_exact_and_serializable():
    manifest = ModelManifest(
        manifest_id="manifest-001",
        model_name="entry-probability",
        model_version="2026.07.13.1",
        created_at=NOW,
        artifact_uri="models/entry-probability.pkl",
        artifact_sha256="a" * 64,
        feature_schema_version="features-v4",
        training_data_version="warehouse-2026-07-12",
        code_version="git:abc123",
        metrics={"oos_auc": 0.61},
        parameters={"seed": 42},
        context={"promotion": "shadow"},
    )
    settlement = FundingSettlement(
        settlement_id="funding-001",
        instrument=_instrument(),
        position_id="position-001",
        occurred_at=NOW,
        received_at=NOW + timedelta(seconds=1),
        rate="0.0001",
        amount="-0.024",
        asset="USDT",
        context={"source": "income-history"},
    )

    assert ModelManifest.from_dict(manifest.to_dict()) == manifest
    assert FundingSettlement.from_dict(settlement.to_dict()) == settlement
    assert settlement.amount == Decimal("-0.024")
    with pytest.raises(ValueError, match="artifact_sha256"):
        ModelManifest(
            manifest_id="bad", model_name="m", model_version="v",
            created_at=NOW, artifact_uri="m.pkl", artifact_sha256="abc",
            feature_schema_version="f1", training_data_version="d1",
            code_version="c1",
        )
