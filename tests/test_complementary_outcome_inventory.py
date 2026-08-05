"""Invariant tests for complementary UP/DOWN accumulation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from itertools import count
from pathlib import Path
from typing import Optional

import pytest

from research.complementary_outcome_inventory import (
    AdmittedOrder,
    BinaryBookSnapshot,
    BinaryInventoryLedger,
    ConflictingFillError,
    ConflictingMergeError,
    ExecutionState,
    Fill,
    ModelSignal,
    OutOfOrderFillError,
    StrategyConfig,
    StrategyInvariantError,
    max_complement_price,
    plan_next_order,
    trade_fee_usd,
)

D = Decimal
CONDITION_ID = "condition-1"
UP_TOKEN_ID = "up-token"
DOWN_TOKEN_ID = "down-token"
DEFAULT_NOW = D("100")
RECONCILIATION_INDICES = count()


def _ledger(config: Optional[StrategyConfig] = None) -> BinaryInventoryLedger:
    kwargs = {} if config is None else {"config": config}
    return BinaryInventoryLedger(
        condition_id=CONDITION_ID,
        up_token_id=UP_TOKEN_ID,
        down_token_id=DOWN_TOKEN_ID,
        **kwargs,
    )


def _fill(
    fill_id: str,
    outcome: str,
    price: str,
    *,
    shares: str = "5",
    role: str = "maker",
    timestamp: str = "100",
    reconciliation_index: Optional[int] = None,
    fee_usd=None,
    fee_source: Optional[str] = None,
    order_id: Optional[str] = None,
    condition_id: str = CONDITION_ID,
    token_id: Optional[str] = None,
) -> Fill:
    normalized_outcome = outcome.upper()
    if token_id is None:
        token_id = UP_TOKEN_ID if normalized_outcome == "UP" else DOWN_TOKEN_ID
    return Fill(
        fill_id=fill_id,
        order_id=order_id or f"order-{fill_id}",
        condition_id=condition_id,
        token_id=token_id,
        outcome=outcome,
        shares=D(shares),
        price=D(price),
        role=role,
        timestamp=D(timestamp),
        reconciliation_index=(
            next(RECONCILIATION_INDICES) if reconciliation_index is None else reconciliation_index
        ),
        fee_usd=None if fee_usd is None else D(str(fee_usd)),
        fee_source=(
            "builder_trade_fee_usdc"
            if role == "taker" and fee_usd is not None and fee_source is None
            else fee_source
        ),
    )


def _record_fill(ledger: BinaryInventoryLedger, fill: Fill):
    if fill.order_id not in ledger.admitted_orders_by_id:
        max_shares = ledger.config.max_clip_shares
        while max_shares > 0:
            estimated_cost = max_shares * fill.price + trade_fee_usd(
                max_shares,
                fill.price,
                fill.role,
                taker_fee_rate=ledger.config.taker_fee_rate,
                taker_fee_exponent=ledger.config.taker_fee_exponent,
                fee_quantum=ledger.config.fee_quantum,
            )
            if estimated_cost <= ledger.config.max_clip_notional_usd:
                break
            max_shares -= ledger.config.order_size_quantum
        ledger.register_admitted_order(
            AdmittedOrder(
                order_id=fill.order_id,
                condition_id=fill.condition_id,
                token_id=fill.token_id,
                outcome=fill.outcome,
                side="BUY",
                limit_price=fill.price,
                max_shares=max_shares,
                expected_role=fill.role,
                post_only=fill.role.lower() == "maker",
                reason="TEST_ADMITTED_ORDER",
            )
        )
    return ledger.record_fill(fill)


def _book(
    *,
    now: str = "100",
    end: str = "400",
    up_ts: Optional[str] = None,
    down_ts: Optional[str] = None,
    up_bid: str = "0.48",
    up_ask: str = "0.49",
    down_bid: str = "0.51",
    down_ask: str = "0.52",
    tick: str = "0.01",
    min_size: str = "5",
    fee_rate: str = "0.07",
    fee_quantum: str = "0.00001",
    size_quantum: str = "0.01",
    builder_maker_fee_rate: str = "0",
    builder_taker_fee_rate: str = "0",
    fee_exponent: int = 1,
    fees_taker_only: bool = True,
    accepting_orders: bool = True,
    neg_risk: bool = False,
    itode: bool = True,
    condition_id: str = CONDITION_ID,
    up_token_id: str = UP_TOKEN_ID,
    down_token_id: str = DOWN_TOKEN_ID,
) -> BinaryBookSnapshot:
    return BinaryBookSnapshot(
        condition_id=condition_id,
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        up_best_bid=D(up_bid),
        up_best_ask=D(up_ask),
        down_best_bid=D(down_bid),
        down_best_ask=D(down_ask),
        up_timestamp=D(up_ts or now),
        down_timestamp=D(down_ts or now),
        end_timestamp=D(end),
        tick_size=D(tick),
        min_order_size=D(min_size),
        fee_rate=D(fee_rate),
        fee_quantum=D(fee_quantum),
        size_quantum=D(size_quantum),
        builder_maker_fee_rate=D(builder_maker_fee_rate),
        builder_taker_fee_rate=D(builder_taker_fee_rate),
        fee_exponent=fee_exponent,
        fees_taker_only=fees_taker_only,
        accepting_orders=accepting_orders,
        neg_risk=neg_risk,
        itode=itode,
    )


def _signal(
    fair_up: str = "0.55",
    *,
    timestamp: str = "100",
    calibrated: bool = True,
    condition_id: str = CONDITION_ID,
) -> ModelSignal:
    return ModelSignal(condition_id, D(fair_up), D(timestamp), calibrated)


def _no_lock_book() -> BinaryBookSnapshot:
    # Passive maker candidates are .50 + .50, which fails the guarded < $1 gate.
    return _book(up_bid="0.50", up_ask="0.51", down_bid="0.50", down_ask="0.51")


def _plan(
    ledger: BinaryInventoryLedger,
    book: BinaryBookSnapshot,
    signal: Optional[ModelSignal],
    *,
    now: Decimal = DEFAULT_NOW,
    execution_state: Optional[ExecutionState] = None,
):
    return plan_next_order(
        ledger,
        book,
        signal,
        execution_state=execution_state or ExecutionState(CONDITION_ID),
        now=now,
    )


def _confirm_merge(
    ledger: BinaryInventoryLedger,
    merge_id: str,
    shares: Decimal,
    *,
    cost_usd: Optional[Decimal] = None,
) -> bool:
    return ledger.confirm_merge(
        merge_id,
        shares,
        condition_id=CONDITION_ID,
        up_token_id=UP_TOKEN_ID,
        down_token_id=DOWN_TOKEN_ID,
        cost_usd=D("0") if cost_usd is None else cost_usd,
    )


def test_preregistration_hash_is_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    md = root / "_workspace/strategy_pipeline/56_prereg_complementary_outcome_accumulation.md"
    metadata = json.loads(
        (
            root / "_workspace/strategy_pipeline/56_prereg_complementary_outcome_accumulation.json"
        ).read_text(encoding="utf-8")
    )
    raw = md.read_bytes()
    assert metadata["bytes_md"] == len(raw)
    assert metadata["sha256_md"] == hashlib.sha256(raw).hexdigest()
    assert metadata["live_authorized"] is False


def test_maker_fills_at_different_times_lock_then_confirm_a_sub_dollar_pair() -> None:
    ledger = _ledger()

    first = _record_fill(ledger, _fill("u1", "UP", "0.48", timestamp="100"))
    assert not first.matches
    assert ledger.up_shares == D("5")
    assert ledger.down_shares == 0

    second = _record_fill(ledger, _fill("d1", "DOWN", "0.51", timestamp="140"))
    assert len(second.matches) == 1
    match = second.matches[0]
    assert match.admissible
    assert match.condition_id == CONDITION_ID
    assert match.actual_pair_cost == D("0.99")
    assert match.guarded_pair_cost == D("0.996")
    assert match.actual_locked_edge_per_pair == D("0.01")
    assert ledger.paired_shares == D("5")
    assert ledger.unmerged_paired_shares == D("5")
    assert ledger.merged_shares == 0
    assert ledger.actual_locked_profit_usd == D("0.05")
    assert ledger.conservative_locked_profit_usd == D("0.045")
    assert ledger.up_shares == ledger.down_shares == 0
    assert ledger.worst_case_terminal_pnl_usd == D("0.05")

    blocked = _plan(ledger, _book(), _signal())
    assert blocked.reason == "AWAITING_MERGE_CONFIRMATION"
    with pytest.raises(StrategyInvariantError, match="merge market identity"):
        ledger.confirm_merge(
            "wrong-condition-merge",
            D("5"),
            condition_id="condition-2",
            up_token_id=UP_TOKEN_ID,
            down_token_id=DOWN_TOKEN_ID,
        )
    assert ledger.unmerged_paired_shares == D("5")
    assert not _confirm_merge(ledger, "merge-1", D("5"))
    assert _confirm_merge(ledger, "merge-1", D("5"))
    with pytest.raises(ConflictingMergeError):
        _confirm_merge(ledger, "merge-1", D("4"))
    assert ledger.unmerged_paired_shares == 0
    assert ledger.merged_shares == D("5")


def test_exact_one_dollar_actual_pair_is_reconciled_and_latches_incident() -> None:
    config = replace(
        StrategyConfig(),
        min_locked_edge_per_pair=D("0"),
        operations_reserve_per_pair=D("0"),
    )
    ledger = _ledger(config)
    _record_fill(ledger, _fill("u1", "UP", "0.50"))
    result = _record_fill(ledger, _fill("d1", "DOWN", "0.50"))

    assert len(result.matches) == 1
    assert not result.matches[0].admissible
    assert ledger.paired_shares == D("5")
    assert ledger.unmerged_paired_shares == D("5")
    assert ledger.up_shares == ledger.down_shares == 0
    assert ledger.pair_cost_violation_count == 1
    assert ledger.inadmissible_paired_shares == D("5")
    assert ledger.worst_case_terminal_pnl_usd == 0

    _confirm_merge(ledger, "merge-1", D("5"))
    decision = _plan(ledger, _book(), _signal())
    assert decision.reason == "PAIR_COST_INCIDENT"


def test_merge_cost_above_reserved_operations_budget_latches_incident() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("u1", "UP", "0.48"))
    _record_fill(ledger, _fill("d1", "DOWN", "0.51"))

    _confirm_merge(ledger, "merge-costly", D("5"), cost_usd=D("0.006"))
    assert ledger.operations_cost_violation_count == 1
    assert ledger.actual_locked_profit_usd == D("0.044")
    assert ledger.conservative_locked_profit_usd == D("0.044")
    assert _plan(ledger, _book(), None).reason == "OPERATIONS_COST_INCIDENT"


def test_taker_fee_curve_turns_nominal_discount_into_recorded_loss() -> None:
    ledger = _ledger()
    expected_fee = D("0.08747")
    assert trade_fee_usd(D("5"), D("0.49"), "taker") == expected_fee

    _record_fill(ledger, _fill("u1", "UP", "0.49", role="taker", fee_usd=expected_fee))
    result = _record_fill(ledger, _fill("d1", "DOWN", "0.49", role="taker", fee_usd=expected_fee))

    assert len(result.matches) == 1
    assert not result.matches[0].admissible
    assert result.matches[0].actual_pair_cost == D("1.014988")
    assert ledger.fees_paid_usd == expected_fee * 2
    assert ledger.actual_locked_profit_usd == D("-0.074940")
    assert ledger.pair_cost_violation_count == 1
    assert ledger.unpaired_worst_case_loss_usd == 0


def test_confirmed_taker_fill_requires_explicit_authoritative_fee() -> None:
    ledger = _ledger()
    missing_fee = _fill("trade-1", "UP", "0.49", role="taker")

    with pytest.raises(StrategyInvariantError, match="authoritative fee_usd"):
        _record_fill(ledger, missing_fee)
    assert ledger.fill_count == 0

    fee = trade_fee_usd(D("5"), D("0.49"), "taker")
    fill = replace(missing_fee, fee_usd=fee, fee_source="builder_trade_fee_usdc")
    assert not _record_fill(ledger, fill).duplicate
    assert _record_fill(ledger, fill).duplicate
    assert ledger.fill_count == 1


def test_unexpected_authoritative_maker_fee_is_recorded_and_latched() -> None:
    ledger = _ledger()
    fill = _fill(
        "maker-fee",
        "UP",
        "0.48",
        fee_usd="0.01",
        fee_source="settlement_balance_delta",
    )

    _record_fill(ledger, fill)
    assert ledger.fees_paid_usd == D("0.01")
    assert ledger.up_shares == D("5")
    assert ledger.fee_schedule_violation_count == 1
    assert _plan(ledger, _book(), None).reason == "FEE_SCHEDULE_INCIDENT"


def test_point_48_taker_pair_records_strict_and_permissive_admission() -> None:
    fee = trade_fee_usd(D("5"), D("0.48"), "taker")
    strict = _ledger()
    _record_fill(strict, _fill("u1", "UP", "0.48", role="taker", fee_usd=fee))
    strict_result = _record_fill(strict, _fill("d1", "DOWN", "0.48", role="taker", fee_usd=fee))
    assert len(strict_result.matches) == 1
    assert not strict_result.matches[0].admissible
    assert strict.pair_cost_violation_count == 1

    permissive_config = replace(
        StrategyConfig(),
        min_locked_edge_per_pair=D("0.001"),
        operations_reserve_per_pair=D("0.001"),
    )
    permissive = _ledger(permissive_config)
    _record_fill(permissive, _fill("u2", "UP", "0.48", role="taker", fee_usd=fee))
    result = _record_fill(permissive, _fill("d2", "DOWN", "0.48", role="taker", fee_usd=fee))
    assert len(result.matches) == 1
    assert result.matches[0].admissible
    assert result.matches[0].actual_pair_cost == D("0.994944")
    assert permissive.pair_cost_violation_count == 0


def test_maker_and_taker_complement_ceilings_include_fees() -> None:
    config = StrategyConfig()
    maker = max_complement_price(D("0.48"), D("5"), "maker", D("0.01"), config)
    taker = max_complement_price(D("0.48"), D("5"), "taker", D("0.01"), config)

    assert maker == D("0.51")
    assert taker == D("0.49")


def test_actual_fifo_matching_reconciles_an_inadmissible_pair() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("u-expensive", "UP", "0.60"))
    result = _record_fill(ledger, _fill("d-new", "DOWN", "0.40"))

    assert len(result.matches) == 1
    assert not result.matches[0].admissible
    assert ledger.up_shares == ledger.down_shares == 0
    assert ledger.unmerged_paired_shares == D("5")
    assert ledger.pair_cost_violation_count == 1
    assert ledger.has_execution_incident


def test_duplicate_fill_is_idempotent_but_conflicting_duplicate_fails() -> None:
    ledger = _ledger()
    fill = _fill("trade-1", "UP", "0.48")
    _record_fill(ledger, fill)

    replay = _record_fill(ledger, fill)
    assert replay.duplicate
    assert ledger.fill_count == 1
    assert ledger.up_shares == D("5")

    with pytest.raises(ConflictingFillError):
        _record_fill(ledger, _fill("trade-1", "UP", "0.47"))
    assert ledger.fill_count == 1
    assert ledger.up_shares == D("5")


def test_out_of_order_new_fill_requires_canonical_replay_rebuild() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("later", "UP", "0.48", reconciliation_index=10))

    with pytest.raises(OutOfOrderFillError):
        _record_fill(ledger, _fill("earlier", "DOWN", "0.51", reconciliation_index=9))
    assert ledger.fill_count == 1
    assert ledger.up_shares == D("5")
    assert ledger.down_shares == 0


def test_replay_with_equivalent_explicit_maker_fee_is_idempotent() -> None:
    ledger = _ledger()
    implicit = _fill("trade-1", "UP", "0.49")
    _record_fill(ledger, implicit)

    explicit = replace(implicit, fee_usd=D("0"))
    assert _record_fill(ledger, explicit).duplicate
    assert ledger.fill_count == 1


@pytest.mark.parametrize(
    "fill",
    [
        _fill("wrong-condition", "UP", "0.48", condition_id="condition-2"),
        _fill("wrong-token", "UP", "0.48", token_id=DOWN_TOKEN_ID),
    ],
)
def test_cross_condition_or_token_fill_is_rejected(fill: Fill) -> None:
    ledger = _ledger()
    with pytest.raises(StrategyInvariantError):
        _record_fill(ledger, fill)
    assert ledger.fill_count == 0


def test_unknown_order_fill_is_reconciled_then_latches_admission_incident() -> None:
    ledger = _ledger()
    ledger.record_fill(_fill("external", "UP", "0.48"))

    assert ledger.fill_count == 1
    assert ledger.up_shares == D("5")
    assert ledger.order_admission_violation_count == 1
    assert _plan(ledger, _book(), None).reason == "ORDER_ADMISSION_INCIDENT"
    with pytest.raises(StrategyInvariantError, match="after its first fill"):
        ledger.register_admitted_order(
            AdmittedOrder(
                order_id="order-external",
                condition_id=CONDITION_ID,
                token_id=UP_TOKEN_ID,
                outcome="UP",
                side="BUY",
                limit_price=D("0.48"),
                max_shares=D("5"),
                expected_role="maker",
                post_only=True,
                reason="TOO_LATE",
            )
        )


def test_actual_oversize_fill_is_recorded_but_preview_and_planner_reject() -> None:
    clean = _ledger()
    oversize = _fill("too-big", "UP", "0.10", shares="6")
    with pytest.raises(StrategyInvariantError, match="share cap|clip cap"):
        clean.preview_fill(oversize)
    assert clean.fill_count == 0

    ledger = _ledger()
    result = _record_fill(ledger, oversize)
    assert not result.duplicate
    assert ledger.fill_count == 1
    assert ledger.up_shares == D("6")
    assert ledger.clip_violation_count == 1
    assert ledger.max_parent_order_shares == D("6")
    assert _plan(ledger, _book(), _signal()).reason == "ORDER_ADMISSION_INCIDENT"


def test_cumulative_parent_partial_fills_latch_clip_violation() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("part-1", "UP", "0.10", shares="3", order_id="parent-order"))
    assert ledger.clip_violation_count == 0

    _record_fill(ledger, _fill("part-2", "UP", "0.10", shares="3", order_id="parent-order"))
    assert ledger.fill_count == 2
    assert ledger.up_shares == D("6")
    assert ledger.order_filled_shares["parent-order"] == D("6")
    assert ledger.max_single_fill_shares == D("3")
    assert ledger.max_parent_order_shares == D("6")
    assert ledger.clip_violation_count == 1
    assert _plan(ledger, _book(), _signal()).reason == "ORDER_ADMISSION_INCIDENT"


def test_actual_directional_breach_is_recorded_and_latched() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("huge", "UP", "0.10", shares="21"))

    assert ledger.up_shares == D("21")
    assert ledger.directional_cap_violation_count == 1
    assert ledger.clip_violation_count == 1
    assert ledger.has_execution_incident


def test_lock_only_pair_candidate_stages_cheaper_leg_then_balances() -> None:
    ledger = _ledger()
    book = _book()

    first = _plan(ledger, book, None)
    assert first.intent is not None
    assert first.reason == "PAIR_START_UP"
    assert first.intent.price == D("0.48")
    assert first.intent.shares == D("5")

    _record_fill(ledger, _fill("pair-start", "UP", "0.48"))
    second = _plan(ledger, book, None)
    assert second.intent is not None
    assert second.reason == "COMPLETE_DOWN_PAIR"
    assert second.intent.price == D("0.51")


def test_pair_candidate_rejects_a_complement_over_parent_notional_cap() -> None:
    # The pair itself is cheap enough, but 5 DOWN shares at .78 would cost $3.90.
    book = _book(up_bid="0.19", up_ask="0.20", down_bid="0.78", down_ask="0.79")
    decision = _plan(_ledger(), book, None)
    assert decision.intent is None
    assert decision.reason == "NO_LOCK_OR_MODEL_ENTRY"


def test_model_starts_fixed_clip_when_no_lock_candidate_then_balances_and_merges() -> None:
    ledger = _ledger()
    entry_book = _book(up_bid="0.48", up_ask="0.49", down_bid="0.52", down_ask="0.53")
    balance_book = _book()

    first = _plan(ledger, entry_book, _signal("0.55"))
    assert first.intent is not None
    assert first.reason == "MODEL_LEAN_UP"
    assert first.intent.outcome == "UP"
    assert first.intent.price == D("0.48")
    assert first.intent.shares == D("5")
    assert first.intent.post_only
    up_fill = _fill("up-fill", "UP", str(first.intent.price), shares=str(first.intent.shares))
    ledger.register_admitted_order(first.intent.admitted_order(up_fill.order_id))
    ledger.record_fill(up_fill)

    second = _plan(ledger, balance_book, _signal("0.55"))
    assert second.intent is not None
    assert second.intent.outcome == "DOWN"
    assert second.intent.reason == "COMPLETE_DOWN_PAIR"
    assert second.intent.price == D("0.51")
    down_fill = _fill(
        "down-fill", "DOWN", str(second.intent.price), shares=str(second.intent.shares)
    )
    ledger.register_admitted_order(second.intent.admitted_order(down_fill.order_id))
    ledger.record_fill(down_fill)

    assert ledger.up_shares == ledger.down_shares == 0
    assert ledger.paired_shares == D("5")
    assert ledger.unmerged_paired_shares == D("5")
    assert ledger.actual_locked_profit_usd == D("0.05")
    assert _plan(ledger, balance_book, _signal()).reason == "AWAITING_MERGE_CONFIRMATION"

    _confirm_merge(ledger, "merge-1", D("5"))
    assert ledger.merged_shares == D("5")
    assert _plan(ledger, balance_book, _signal("0.50")).intent is not None


@pytest.mark.parametrize(
    ("signal", "now", "expected"),
    [
        (_signal(calibrated=False), D("100"), "UNCALIBRATED_MODEL"),
        (_signal(timestamp="90"), D("100"), "STALE_MODEL"),
        (None, D("100"), "NO_LOCK_OR_MODEL_ENTRY"),
    ],
)
def test_model_lane_fails_closed(signal, now, expected: str) -> None:
    decision = _plan(_ledger(), _no_lock_book(), signal, now=now)
    assert decision.intent is None
    assert decision.reason == expected


def test_malformed_book_and_model_fail_closed() -> None:
    malformed_book = replace(_book(), up_best_bid="not-a-decimal")
    book_decision = _plan(_ledger(), malformed_book, _signal())
    assert book_decision.intent is None
    assert book_decision.reason == "INVALID_BOOK_NUMBER"

    malformed_signal = ModelSignal(CONDITION_ID, "not-a-decimal", D("100"), True)
    model_decision = _plan(_ledger(), _book(), malformed_signal)
    assert model_decision.intent is None
    assert model_decision.reason == "INVALID_MODEL_SIGNAL"


@pytest.mark.parametrize(
    ("execution_state", "expected"),
    [
        (ExecutionState(CONDITION_ID, live_order_ids=("live-1",)), "LIVE_ORDER_RESERVATION"),
        (
            ExecutionState(CONDITION_ID, pending_trade_ids=("pending-1",)),
            "PENDING_FILL_RESERVATION",
        ),
        (
            ExecutionState(CONDITION_ID, uncertain_order_ids=("uncertain-1",)),
            "UNCERTAIN_ORDER_RESERVATION",
        ),
        (ExecutionState(CONDITION_ID, live_order_ids=None), "INVALID_EXECUTION_RESERVATION"),
        (ExecutionState(CONDITION_ID, live_order_ids=7), "INVALID_EXECUTION_RESERVATION"),
    ],
)
def test_live_pending_and_uncertain_reservations_block(execution_state, expected: str) -> None:
    decision = _plan(
        _ledger(),
        _book(),
        _signal(),
        execution_state=execution_state,
    )
    assert decision.intent is None
    assert decision.reason == expected


def test_book_lifecycle_fee_schedule_and_identity_fail_closed() -> None:
    ledger = _ledger()
    signal = _signal()

    assert _plan(ledger, _book(up_ts="90", down_ts="90"), signal).reason == "STALE_UP_BOOK"
    assert _plan(ledger, _book(up_ts="100", down_ts="99"), signal).reason == (
        "CROSS_BOOK_TIMESTAMP_SKEW"
    )
    assert _plan(ledger, replace(_book(), accepting_orders=False), signal).reason == (
        "MARKET_NOT_ACCEPTING_ORDERS"
    )
    assert _plan(ledger, replace(_book(), neg_risk=True), signal).reason == (
        "UNSUPPORTED_NEG_RISK_MARKET"
    )
    assert _plan(ledger, replace(_book(), fee_rate=D("0.05")), signal).reason == (
        "FEE_SCHEDULE_MISMATCH"
    )
    assert _plan(ledger, replace(_book(), fee_quantum=D("0.0001")), signal).reason == (
        "MARKET_PRECISION_MISMATCH"
    )
    assert _plan(ledger, replace(_book(), size_quantum=D("0.001")), signal).reason == (
        "MARKET_PRECISION_MISMATCH"
    )
    assert _plan(ledger, replace(_book(), builder_taker_fee_rate=D("0.01")), signal).reason == (
        "UNSUPPORTED_NONZERO_BUILDER_FEE"
    )
    assert _plan(ledger, _book(condition_id="condition-2"), signal).reason == (
        "LEDGER_BOOK_IDENTITY_MISMATCH"
    )
    assert _plan(ledger, _book(), _signal(condition_id="condition-2")).reason == (
        "MODEL_CONDITION_MISMATCH"
    )
    mismatched_execution = ExecutionState("condition-2")
    assert (
        _plan(
            ledger,
            _book(),
            signal,
            execution_state=mismatched_execution,
        ).reason
        == "EXECUTION_CONDITION_MISMATCH"
    )


def test_cutoff_blocks_new_lean_but_allows_earlier_pair_completion() -> None:
    book = _book(now="100", end="150")
    empty = _plan(_ledger(), book, _signal())
    assert empty.reason == "NEW_LEAN_CUTOFF"

    ledger = _ledger()
    _record_fill(ledger, _fill("u1", "UP", "0.48"))
    completion = _plan(ledger, book, None)
    assert completion.intent is not None
    assert completion.intent.outcome == "DOWN"

    too_late_book = _book(now="100", end="115")
    too_late = _plan(ledger, too_late_book, None)
    assert too_late.reason == "COMPLETION_CUTOFF"


def test_subminimum_partial_first_leg_remains_reserved_for_terminal_policy() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("partial", "UP", "0.48", shares="2"))

    decision = _plan(ledger, _book(), None)
    assert decision.intent is None
    assert decision.reason == "PAIRABLE_SIZE_BELOW_MARKET_MINIMUM"
    assert ledger.up_shares == D("2")
    assert ledger.unpaired_worst_case_loss_usd == D("0.96")


def test_inadmissible_actual_pair_blocks_after_merge_confirmation() -> None:
    ledger = _ledger()
    _record_fill(ledger, _fill("u1", "UP", "0.60"))
    _record_fill(ledger, _fill("d1", "DOWN", "0.40"))
    assert ledger.pair_cost_violation_count == 1

    assert _plan(ledger, _book(), _signal()).reason == "AWAITING_MERGE_CONFIRMATION"
    _confirm_merge(ledger, "merge-1", D("5"))
    assert _plan(ledger, _book(), _signal()).reason == "PAIR_COST_INCIDENT"


def test_market_minimum_larger_than_clip_fails_closed() -> None:
    decision = _plan(
        _ledger(),
        _book(min_size="10"),
        _signal(),
    )
    assert decision.intent is None
    assert decision.reason == "MODEL_FIXED_CLIP_UNAVAILABLE"


def test_model_never_shrinks_fixed_clip_to_fit_notional_cap() -> None:
    book = _book(
        up_bid="0.69",
        up_ask="0.70",
        down_bid="0.31",
        down_ask="0.32",
        min_size="1",
    )
    decision = _plan(_ledger(), book, _signal("0.80"))
    assert decision.intent is None
    assert decision.reason == "MODEL_FIXED_CLIP_UNAVAILABLE"


def test_two_thousand_small_fills_preserve_invariants_and_confirm_each_merge() -> None:
    ledger = _ledger()
    for index in range(1000):
        _record_fill(ledger, _fill(f"u-{index}", "UP", "0.48", timestamp=str(index * 2)))
        result = _record_fill(
            ledger, _fill(f"d-{index}", "DOWN", "0.51", timestamp=str(index * 2 + 1))
        )
        assert len(result.matches) == 1
        assert result.matches[0].admissible
        assert ledger.absolute_directional_shares == 0
        assert ledger.unpaired_worst_case_loss_usd == 0
        assert ledger.unmerged_paired_shares == D("5")
        _confirm_merge(ledger, f"merge-{index}", D("5"))
        assert ledger.unmerged_paired_shares == 0

    assert ledger.fill_count == 2000
    assert ledger.paired_shares == D("5000")
    assert ledger.merged_shares == D("5000")
    assert ledger.actual_locked_profit_usd == D("50.00")
    assert ledger.max_single_fill_shares == D("5")
    assert ledger.max_parent_order_shares == D("5")
    assert ledger.max_single_fill_cost_usd == D("2.55")
    assert ledger.max_abs_directional_shares == D("5")
    assert ledger.max_unpaired_worst_case_loss_usd == D("2.40")
    assert not ledger.has_execution_incident


def test_two_thousand_one_sided_partials_use_incremental_open_lot_totals() -> None:
    ledger = _ledger()
    for index in range(2000):
        _record_fill(
            ledger,
            _fill(
                f"up-partial-{index}",
                "UP",
                "0.48",
                shares="0.01",
                order_id=f"up-parent-{index // 500}",
            ),
        )

    assert ledger.up_shares == D("20.00")
    assert ledger.unpaired_cost_usd == D("9.6000")
    assert ledger.max_parent_order_shares == D("5.00")
    assert not ledger.has_execution_incident

    for index in range(4):
        _record_fill(ledger, _fill(f"down-{index}", "DOWN", "0.51"))

    assert ledger.fill_count == 2004
    assert ledger.paired_shares == D("20.00")
    assert ledger.up_shares == ledger.down_shares == 0
    assert ledger.actual_locked_profit_usd == D("0.2000")
    assert ledger.unmerged_paired_shares == D("20.00")
    _confirm_merge(ledger, "merge-all", D("20"))
    assert ledger.merged_shares == D("20")
    assert not ledger.has_execution_incident
