from datetime import datetime, timezone

from core.contracts import DecisionOutcome
from core.decision_provenance import (
    outcome_for_rejection,
    record_terminal_decision,
)
from core.execution_guard import validate_execution_book
from core.warehouse import Warehouse


def _action():
    return {
        "type": "OPEN",
        "symbol": "BTC/USDT",
        "exchange": "bybit",
        "market_type": "futures",
        "side": "buy",
        "confidence": 0.7,
        "strategy_id": "F1",
        "strategy_version": "rev5.2",
        "decision_id": "decision-terminal-1",
        "candidate_id": 19,
        "entry_policy": "APPROVED_PAPER",
    }


def test_terminal_rejection_has_explicit_nonfresh_snapshot(tmp_path):
    warehouse = Warehouse(tmp_path / "warehouse.sqlite")
    action = _action()

    record_terminal_decision(
        warehouse,
        action,
        outcome=DecisionOutcome.REJECTED,
        reason="risk_halted",
        stage="execute_open",
        repo_root=tmp_path,
    )

    decisions = warehouse.query_decision_events(decision_id=action["decision_id"])
    snapshots = warehouse.query_market_snapshots(
        snapshot_id=decisions[0].snapshot_id
    )
    assert len(decisions) == 1
    assert decisions[0].outcome is DecisionOutcome.REJECTED
    assert decisions[0].is_terminal
    assert snapshots[0].context["is_fresh"] is False
    assert snapshots[0].data[0].kind.value == "availability"
    assert snapshots[0].data[0].value == 0


def test_terminal_fill_links_real_venue_snapshot_and_is_idempotent(tmp_path):
    warehouse = Warehouse(tmp_path / "warehouse.sqlite")
    action = _action()
    observed_at = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    book = validate_execution_book(
        {
            "bids": [[99, 3]],
            "asks": [[101, 3]],
            "nonce": 8,
            "timestamp": int(observed_at.timestamp() * 1000),
        },
        venue="bybit",
        market_type="futures",
        canonical_symbol="BTC/USDT:USDT",
        exchange_symbol="BTC/USDT:USDT",
        side="buy",
        requested_quantity=1,
        max_slippage_bps=200,
        received_at=observed_at,
    )
    action["execution_snapshot"] = book.to_action_dict()

    first = record_terminal_decision(
        warehouse,
        action,
        outcome="filled",
        reason="paper_fill",
        stage="order_manager",
        repo_root=tmp_path,
    )
    second = record_terminal_decision(
        warehouse,
        action,
        outcome="rejected",
        reason="late_duplicate",
        stage="retry",
        repo_root=tmp_path,
    )

    assert first == second
    decision = warehouse.query_decision_events(decision_id=action["decision_id"])[0]
    assert decision.outcome is DecisionOutcome.FILLED
    snapshot = warehouse.query_market_snapshots(snapshot_id=decision.snapshot_id)[0]
    assert snapshot.instrument.venue.value == "bybit"
    assert snapshot.context["event_time_known"] is True


def test_rejection_reason_maps_to_terminal_outcome():
    assert outcome_for_rejection("cycle_cap", "cycle_cap") is DecisionOutcome.EXPIRED
    assert outcome_for_rejection("maker_first_pending", "execute_open") is DecisionOutcome.DEFERRED
    assert outcome_for_rejection("execution_book_guard_error", "execute_open") is DecisionOutcome.ERROR
    assert outcome_for_rejection("risk_halted", "execute_open") is DecisionOutcome.REJECTED
