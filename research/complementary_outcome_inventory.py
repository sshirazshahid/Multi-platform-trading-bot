"""Complementary-outcome accumulation for binary prediction markets.

This module encodes the strategy's *accounting and order-planning* invariants:

* accumulate UP and DOWN at different times;
* admit a prospective complement only when its all-in cost is strictly below
  the $1 payout after fees, an operations reserve, and a required edge;
* reconcile every confirmed fill truthfully and move every actual complete set
  into a merge-pending bucket, even when execution broke the admission gate;
* keep unmatched inventory small, with a model allowed to create only a capped
  directional lean; and
* split the objective into many small maker clips.

It is research-only and models the current Polymarket fee shape.  It does not
sign, submit, cancel, merge, redeem, or otherwise place an order.  A live
adapter must supply authoritative lifecycle reservations, exact confirmed-fill
fees from a documented source, merge confirmations, restart reconciliation,
heartbeats, and settlement finality.
"""

from __future__ import annotations

import copy
import math
from collections import deque
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException
from typing import Deque, Dict, List, Optional, Tuple

D = Decimal
ZERO = D("0")
ONE = D("1")
FEE_QUANTUM = D("0.00001")
# Current Polymarket limit-order sizes use two decimal places. Confirmed partial
# fills remain exact inputs; this quantum is only for newly planned order sizes.
SHARE_QUANTUM = D("0.01")
DEFAULT_TAKER_FEE_RATE = D("0.07")
OUTCOMES = ("UP", "DOWN")
ROLES = ("maker", "taker")
AUTHORITATIVE_FEE_SOURCES = ("builder_trade_fee_usdc", "settlement_balance_delta")
CONFIGURED_MAKER_ZERO_SOURCE = "configured_taker_only_zero"


class StrategyInvariantError(ValueError):
    """Input or state would violate a hard strategy invariant."""


class ConflictingFillError(StrategyInvariantError):
    """A reused fill ID described a different economic event."""


class ConflictingMergeError(StrategyInvariantError):
    """A reused merge ID described a different confirmation."""


class OutOfOrderFillError(StrategyInvariantError):
    """A new fill arrived behind the ledger's canonical reconciliation order."""


class ConflictingOrderAdmissionError(StrategyInvariantError):
    """A reused order ID described a different admitted order."""


def dec(value: object) -> Decimal:
    """Convert API-like numeric input to an exact, finite ``Decimal``."""

    try:
        if isinstance(value, Decimal):
            result = value
        else:
            result = D(str(value))
    except (DecimalException, TypeError, ValueError) as exc:
        raise StrategyInvariantError(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise StrategyInvariantError(f"non-finite decimal: {value!r}")
    return result


def other_outcome(outcome: str) -> str:
    outcome = outcome.upper()
    if outcome == "UP":
        return "DOWN"
    if outcome == "DOWN":
        return "UP"
    raise StrategyInvariantError(f"unknown outcome: {outcome!r}")


def floor_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    value = dec(value)
    tick = dec(tick)
    if tick <= ZERO:
        raise StrategyInvariantError("tick must be positive")
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def floor_shares(value: Decimal, quantum: Decimal = SHARE_QUANTUM) -> Decimal:
    value = dec(value)
    quantum = dec(quantum)
    if quantum <= ZERO:
        raise StrategyInvariantError("share quantum must be positive")
    return (value / quantum).to_integral_value(rounding=ROUND_FLOOR) * quantum


def is_decimal_place_quantum(value: Decimal) -> bool:
    """Whether ``value`` represents one decimal place (1, .1, .01, ...)."""

    value = dec(value).normalize()
    return ZERO < value <= ONE and value.as_tuple().digits == (1,)


@dataclass(frozen=True)
class StrategyConfig:
    """Hard bounds for one binary market.

    Defaults are intentionally tiny and PAPER-oriented.  They are examples,
    not a recommendation to trade or a claim of profitability.
    """

    payout_per_pair: Decimal = D("1")
    min_locked_edge_per_pair: Decimal = D("0.005")
    operations_reserve_per_pair: Decimal = D("0.001")
    taker_fee_rate: Decimal = D("0.07")
    taker_fee_exponent: int = 1
    fees_taker_only: bool = True
    fee_quantum: Decimal = FEE_QUANTUM
    order_size_quantum: Decimal = SHARE_QUANTUM

    clip_shares: Decimal = D("5")
    max_clip_shares: Decimal = D("5")
    max_clip_notional_usd: Decimal = D("3")
    max_directional_shares: Decimal = D("20")
    max_directional_cost_usd: Decimal = D("12")

    min_model_edge: Decimal = D("0.01")
    min_future_complement_price: Decimal = D("0.05")
    signal_stale_after_seconds: Decimal = D("2")
    book_stale_after_seconds: Decimal = D("2")
    max_cross_book_skew_seconds: Decimal = D("0.5")
    new_lean_cutoff_seconds: Decimal = D("60")
    completion_cutoff_seconds: Decimal = D("15")
    max_live_orders_per_market: int = 1

    def __post_init__(self) -> None:
        decimal_fields = (
            "payout_per_pair",
            "min_locked_edge_per_pair",
            "operations_reserve_per_pair",
            "taker_fee_rate",
            "fee_quantum",
            "order_size_quantum",
            "clip_shares",
            "max_clip_shares",
            "max_clip_notional_usd",
            "max_directional_shares",
            "max_directional_cost_usd",
            "min_model_edge",
            "min_future_complement_price",
            "signal_stale_after_seconds",
            "book_stale_after_seconds",
            "max_cross_book_skew_seconds",
            "new_lean_cutoff_seconds",
            "completion_cutoff_seconds",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, dec(getattr(self, name)))

        if self.payout_per_pair != ONE:
            raise StrategyInvariantError("this strategy requires a $1 complete-set payout")
        if self.min_locked_edge_per_pair < ZERO or self.operations_reserve_per_pair < ZERO:
            raise StrategyInvariantError("edge and operations reserve cannot be negative")
        if self.min_locked_edge_per_pair + self.operations_reserve_per_pair >= self.payout_per_pair:
            raise StrategyInvariantError("edge plus reserve must be below the pair payout")
        if not (ZERO <= self.taker_fee_rate < ONE):
            raise StrategyInvariantError("taker_fee_rate must be in [0, 1)")
        if isinstance(self.taker_fee_exponent, bool) or not isinstance(
            self.taker_fee_exponent, int
        ):
            raise StrategyInvariantError("taker_fee_exponent must be an integer")
        if self.taker_fee_exponent <= 0:
            raise StrategyInvariantError("taker_fee_exponent must be positive")
        if self.fees_taker_only is not True:
            raise StrategyInvariantError("only a taker-only fee schedule is supported")
        if not is_decimal_place_quantum(self.fee_quantum):
            raise StrategyInvariantError("fee_quantum must be a power of ten")
        if self.order_size_quantum <= ZERO:
            raise StrategyInvariantError("order_size_quantum must be positive")
        if (
            min(
                self.clip_shares,
                self.max_clip_shares,
                self.max_clip_notional_usd,
                self.max_directional_shares,
                self.max_directional_cost_usd,
            )
            <= ZERO
        ):
            raise StrategyInvariantError("size and risk caps must be positive")
        if self.clip_shares > self.max_clip_shares:
            raise StrategyInvariantError("clip_shares exceeds max_clip_shares")
        if (
            floor_shares(self.clip_shares, self.order_size_quantum) != self.clip_shares
            or floor_shares(self.max_clip_shares, self.order_size_quantum) != self.max_clip_shares
        ):
            raise StrategyInvariantError("clip shares must conform to order_size_quantum")
        # One fill must never be capable of consuming the whole directional budget.
        if self.max_clip_shares * D("4") > self.max_directional_shares:
            raise StrategyInvariantError("max_clip_shares must be <= 25% of directional share cap")
        if self.max_clip_notional_usd * D("4") > self.max_directional_cost_usd:
            raise StrategyInvariantError("max clip notional must be <= 25% of directional cost cap")
        if not (ZERO < self.min_model_edge < ONE):
            raise StrategyInvariantError("min_model_edge must be in (0, 1)")
        if not (ZERO < self.min_future_complement_price < self.payout_per_pair):
            raise StrategyInvariantError("minimum future complement price is invalid")
        if (
            min(
                self.signal_stale_after_seconds,
                self.book_stale_after_seconds,
                self.max_cross_book_skew_seconds,
                self.new_lean_cutoff_seconds,
                self.completion_cutoff_seconds,
            )
            < ZERO
        ):
            raise StrategyInvariantError("timing limits cannot be negative")
        if self.completion_cutoff_seconds > self.new_lean_cutoff_seconds:
            raise StrategyInvariantError("completion cutoff must not exceed new-lean cutoff")
        if self.max_live_orders_per_market != 1:
            raise StrategyInvariantError("the staged strategy requires exactly one live order")


def trade_fee_usd(
    shares: Decimal,
    price: Decimal,
    role: str,
    *,
    taker_fee_rate: Decimal = DEFAULT_TAKER_FEE_RATE,
    taker_fee_exponent: int = 1,
    fee_quantum: Decimal = FEE_QUANTUM,
) -> Decimal:
    """Conservative Polymarket-style fee estimate for one fill.

    The current exponent-one fee curve is ``C * rate * p * (1-p)`` for takers
    and zero for makers.  Upward rounding is this strategy's conservative
    *pre-trade estimate*, not a claim about the venue's rounding rule.  Actual
    taker reconciliation must use an authoritatively sourced exact fee.
    """

    shares = dec(shares)
    price = dec(price)
    taker_fee_rate = dec(taker_fee_rate)
    fee_quantum = dec(fee_quantum)
    role = role.lower()
    if shares <= ZERO:
        raise StrategyInvariantError("shares must be positive")
    if not (ZERO < price < ONE):
        raise StrategyInvariantError("binary-token price must be in (0, 1)")
    if not (ZERO <= taker_fee_rate < ONE):
        raise StrategyInvariantError("taker_fee_rate must be in [0, 1)")
    if not is_decimal_place_quantum(fee_quantum):
        raise StrategyInvariantError("fee_quantum must be a power of ten")
    if role not in ROLES:
        raise StrategyInvariantError(f"unknown liquidity role: {role!r}")
    if isinstance(taker_fee_exponent, bool) or not isinstance(taker_fee_exponent, int):
        raise StrategyInvariantError("taker_fee_exponent must be an integer")
    if taker_fee_exponent <= 0:
        raise StrategyInvariantError("taker_fee_exponent must be positive")
    if role == "maker":
        return ZERO
    raw = shares * taker_fee_rate * (price * (ONE - price)) ** taker_fee_exponent
    return raw.quantize(fee_quantum, rounding=ROUND_CEILING)


def effective_unit_cost(
    shares: Decimal,
    price: Decimal,
    role: str,
    config: StrategyConfig,
    *,
    actual_fee_usd: Optional[Decimal] = None,
) -> Decimal:
    shares = dec(shares)
    price = dec(price)
    fee = (
        trade_fee_usd(
            shares,
            price,
            role,
            taker_fee_rate=config.taker_fee_rate,
            taker_fee_exponent=config.taker_fee_exponent,
            fee_quantum=config.fee_quantum,
        )
        if actual_fee_usd is None
        else dec(actual_fee_usd)
    )
    if fee < ZERO:
        raise StrategyInvariantError("fee cannot be negative")
    return price + fee / shares


def pair_guarded_cost(
    up_unit_cost: Decimal,
    down_unit_cost: Decimal,
    config: StrategyConfig,
) -> Decimal:
    """Cost used by the strict pre-merge gate.

    Maker rebates and other uncertain incentives are deliberately excluded.
    """

    return (
        dec(up_unit_cost)
        + dec(down_unit_cost)
        + config.operations_reserve_per_pair
        + config.min_locked_edge_per_pair
    )


def pair_is_admissible(
    up_unit_cost: Decimal,
    down_unit_cost: Decimal,
    config: StrategyConfig,
) -> bool:
    return pair_guarded_cost(up_unit_cost, down_unit_cost, config) < config.payout_per_pair


def max_complement_price(
    opposing_unit_cost: Decimal,
    shares: Decimal,
    role: str,
    tick_size: Decimal,
    config: StrategyConfig,
) -> Optional[Decimal]:
    """Highest tick whose all-in marginal pair still clears every buffer."""

    opposing_unit_cost = dec(opposing_unit_cost)
    shares = dec(shares)
    tick_size = dec(tick_size)
    if opposing_unit_cost <= ZERO or tick_size <= ZERO:
        raise StrategyInvariantError("opposing cost and tick size must be positive")
    raw_ceiling = (
        config.payout_per_pair
        - config.min_locked_edge_per_pair
        - config.operations_reserve_per_pair
        - opposing_unit_cost
    )
    candidate = floor_to_tick(min(raw_ceiling, ONE - tick_size), tick_size)
    while candidate > ZERO:
        unit_cost = effective_unit_cost(shares, candidate, role, config)
        if pair_is_admissible(opposing_unit_cost, unit_cost, config):
            return candidate
        candidate -= tick_size
    return None


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    condition_id: str
    token_id: str
    outcome: str
    shares: Decimal
    price: Decimal
    role: str
    timestamp: Decimal
    reconciliation_index: int
    fee_usd: Optional[Decimal] = None
    fee_source: Optional[str] = None

    def normalized(self) -> Fill:
        if not isinstance(self.fill_id, str) or not self.fill_id:
            raise StrategyInvariantError("fill_id is required")
        if not isinstance(self.order_id, str) or not self.order_id:
            raise StrategyInvariantError("order_id is required")
        if not isinstance(self.condition_id, str) or not self.condition_id:
            raise StrategyInvariantError("condition_id is required")
        if not isinstance(self.token_id, str) or not self.token_id:
            raise StrategyInvariantError("token_id is required")
        if not isinstance(self.outcome, str):
            raise StrategyInvariantError("fill outcome must be a string")
        if not isinstance(self.role, str):
            raise StrategyInvariantError("fill role must be a string")
        if isinstance(self.reconciliation_index, bool) or not isinstance(
            self.reconciliation_index, int
        ):
            raise StrategyInvariantError("reconciliation_index must be an integer")
        if self.reconciliation_index < 0:
            raise StrategyInvariantError("reconciliation_index cannot be negative")
        if self.fee_source is not None and (
            not isinstance(self.fee_source, str) or not self.fee_source
        ):
            raise StrategyInvariantError("fee_source must be a non-empty string")
        outcome = self.outcome.upper()
        role = self.role.lower()
        if outcome not in OUTCOMES:
            raise StrategyInvariantError(f"unknown outcome: {self.outcome!r}")
        if role not in ROLES:
            raise StrategyInvariantError(f"unknown liquidity role: {self.role!r}")
        fee = None if self.fee_usd is None else dec(self.fee_usd)
        return Fill(
            fill_id=self.fill_id,
            order_id=self.order_id,
            condition_id=self.condition_id,
            token_id=self.token_id,
            outcome=outcome,
            shares=dec(self.shares),
            price=dec(self.price),
            role=role,
            timestamp=dec(self.timestamp),
            reconciliation_index=self.reconciliation_index,
            fee_usd=fee,
            fee_source=self.fee_source,
        )


@dataclass(frozen=True)
class AdmittedOrder:
    """Frozen pre-submission facts used to audit later fills."""

    order_id: str
    condition_id: str
    token_id: str
    outcome: str
    side: str
    limit_price: Decimal
    max_shares: Decimal
    expected_role: str
    post_only: bool
    reason: str

    def normalized(self) -> AdmittedOrder:
        string_fields = (
            self.order_id,
            self.condition_id,
            self.token_id,
            self.outcome,
            self.side,
            self.expected_role,
            self.reason,
        )
        if any(not isinstance(value, str) or not value for value in string_fields):
            raise StrategyInvariantError("admitted order string fields are required")
        outcome = self.outcome.upper()
        side = self.side.upper()
        role = self.expected_role.lower()
        if outcome not in OUTCOMES or side != "BUY" or role not in ROLES:
            raise StrategyInvariantError("unsupported admitted order semantics")
        if not isinstance(self.post_only, bool):
            raise StrategyInvariantError("post_only must be a boolean")
        if self.post_only != (role == "maker"):
            raise StrategyInvariantError("post_only and expected_role are inconsistent")
        return AdmittedOrder(
            order_id=self.order_id,
            condition_id=self.condition_id,
            token_id=self.token_id,
            outcome=outcome,
            side=side,
            limit_price=dec(self.limit_price),
            max_shares=dec(self.max_shares),
            expected_role=role,
            post_only=self.post_only,
            reason=self.reason,
        )


@dataclass
class InventoryLot:
    outcome: str
    shares: Decimal
    unit_cost: Decimal
    fill_id: str
    order_id: str
    timestamp: Decimal


@dataclass(frozen=True)
class PairMatch:
    condition_id: str
    shares: Decimal
    up_unit_cost: Decimal
    down_unit_cost: Decimal
    actual_pair_cost: Decimal
    guarded_pair_cost: Decimal
    actual_locked_edge_per_pair: Decimal
    conservative_locked_edge_per_pair: Decimal
    admissible: bool
    up_fill_id: str
    down_fill_id: str
    up_order_id: str
    down_order_id: str


@dataclass(frozen=True)
class FillRecordResult:
    duplicate: bool
    matches: Tuple[PairMatch, ...]


@dataclass
class BinaryInventoryLedger:
    """Condition-bound FIFO ledger for confirmed fills and complete sets.

    Admission and reconciliation are deliberately separate.  The planner may
    only propose an admissible marginal pair.  Once fills are confirmed, this
    ledger always pairs equal opposing shares, records any admission failure as
    an incident, and waits for an explicit merge confirmation.
    """

    condition_id: str
    up_token_id: str
    down_token_id: str
    config: StrategyConfig = field(default_factory=StrategyConfig)
    lots: Dict[str, Deque[InventoryLot]] = field(
        default_factory=lambda: {"UP": deque(), "DOWN": deque()}
    )
    open_shares_by_outcome: Dict[str, Decimal] = field(
        default_factory=lambda: {"UP": ZERO, "DOWN": ZERO}
    )
    open_cost_by_outcome: Dict[str, Decimal] = field(
        default_factory=lambda: {"UP": ZERO, "DOWN": ZERO}
    )
    fills_by_id: Dict[str, Fill] = field(default_factory=dict)
    admitted_orders_by_id: Dict[str, AdmittedOrder] = field(default_factory=dict)
    order_identity_by_id: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    order_filled_shares: Dict[str, Decimal] = field(default_factory=dict)
    order_filled_cost_usd: Dict[str, Decimal] = field(default_factory=dict)
    merge_confirmations_by_id: Dict[str, Tuple[str, str, str, Decimal, Decimal]] = field(
        default_factory=dict
    )
    matches: List[PairMatch] = field(default_factory=list)
    fill_count: int = 0
    last_reconciliation_index: int = -1
    fill_notional_usd: Decimal = ZERO
    fees_paid_usd: Decimal = ZERO
    paired_shares: Decimal = ZERO
    unmerged_paired_shares: Decimal = ZERO
    merged_shares: Decimal = ZERO
    merge_cost_paid_usd: Decimal = ZERO
    actual_locked_profit_usd: Decimal = ZERO
    conservative_locked_profit_usd: Decimal = ZERO
    pair_cost_violation_count: int = 0
    inadmissible_paired_shares: Decimal = ZERO
    clip_violation_count: int = 0
    directional_cap_violation_count: int = 0
    operations_cost_violation_count: int = 0
    fee_schedule_violation_count: int = 0
    order_admission_violation_count: int = 0
    max_single_fill_shares: Decimal = ZERO
    max_single_fill_cost_usd: Decimal = ZERO
    max_parent_order_shares: Decimal = ZERO
    max_parent_order_cost_usd: Decimal = ZERO
    max_abs_directional_shares: Decimal = ZERO
    max_unpaired_worst_case_loss_usd: Decimal = ZERO

    def __post_init__(self) -> None:
        identities = (self.condition_id, self.up_token_id, self.down_token_id)
        if any(not isinstance(identity, str) or not identity for identity in identities):
            raise StrategyInvariantError("ledger market identity is required")
        if self.up_token_id == self.down_token_id:
            raise StrategyInvariantError("UP and DOWN token IDs must differ")

    def clone(self) -> BinaryInventoryLedger:
        return copy.deepcopy(self)

    def outcome_shares(self, outcome: str) -> Decimal:
        outcome = outcome.upper()
        if outcome not in OUTCOMES:
            raise StrategyInvariantError(f"unknown outcome: {outcome!r}")
        return self.open_shares_by_outcome[outcome]

    def outcome_cost(self, outcome: str) -> Decimal:
        outcome = outcome.upper()
        if outcome not in OUTCOMES:
            raise StrategyInvariantError(f"unknown outcome: {outcome!r}")
        return self.open_cost_by_outcome[outcome]

    @property
    def up_shares(self) -> Decimal:
        return self.outcome_shares("UP")

    @property
    def down_shares(self) -> Decimal:
        return self.outcome_shares("DOWN")

    @property
    def signed_directional_shares(self) -> Decimal:
        return self.up_shares - self.down_shares

    @property
    def absolute_directional_shares(self) -> Decimal:
        return abs(self.signed_directional_shares)

    @property
    def unpaired_cost_usd(self) -> Decimal:
        return self.outcome_cost("UP") + self.outcome_cost("DOWN")

    @property
    def unpaired_worst_case_loss_usd(self) -> Decimal:
        # At resolution at least min(q_up, q_down) pays regardless of outcome.
        guaranteed_payout = min(self.up_shares, self.down_shares) * self.config.payout_per_pair
        return max(ZERO, self.unpaired_cost_usd - guaranteed_payout)

    @property
    def worst_case_terminal_pnl_usd(self) -> Decimal:
        guaranteed_unpaired_payout = (
            min(self.up_shares, self.down_shares) * self.config.payout_per_pair
        )
        return self.actual_locked_profit_usd + guaranteed_unpaired_payout - self.unpaired_cost_usd

    @property
    def has_two_sided_unpaired_inventory(self) -> bool:
        return self.up_shares > ZERO and self.down_shares > ZERO

    @property
    def has_execution_incident(self) -> bool:
        return any(
            (
                self.pair_cost_violation_count,
                self.clip_violation_count,
                self.directional_cap_violation_count,
                self.operations_cost_violation_count,
                self.fee_schedule_violation_count,
                self.order_admission_violation_count,
            )
        )

    def register_admitted_order(self, order: AdmittedOrder) -> bool:
        """Register one pre-submission order contract; identical replays are idempotent."""

        order = order.normalized()
        if order.condition_id != self.condition_id:
            raise StrategyInvariantError("admitted order condition_id does not match ledger")
        expected_token = self.up_token_id if order.outcome == "UP" else self.down_token_id
        if order.token_id != expected_token:
            raise StrategyInvariantError("admitted order token_id does not match outcome mapping")
        if not (ZERO < order.limit_price < self.config.payout_per_pair):
            raise StrategyInvariantError("admitted order limit price is invalid")
        if not (ZERO < order.max_shares <= self.config.max_clip_shares):
            raise StrategyInvariantError("admitted order share cap is invalid")
        estimated_cost = order.max_shares * order.limit_price + trade_fee_usd(
            order.max_shares,
            order.limit_price,
            order.expected_role,
            taker_fee_rate=self.config.taker_fee_rate,
            taker_fee_exponent=self.config.taker_fee_exponent,
            fee_quantum=self.config.fee_quantum,
        )
        if estimated_cost > self.config.max_clip_notional_usd:
            raise StrategyInvariantError("admitted order exceeds acquisition-cost cap")
        prior = self.admitted_orders_by_id.get(order.order_id)
        if prior is not None:
            if prior == order:
                return True
            raise ConflictingOrderAdmissionError(
                f"conflicting duplicate admitted order_id={order.order_id}"
            )
        if order.order_id in self.order_filled_shares:
            raise StrategyInvariantError("cannot admit an order after its first fill")
        self.admitted_orders_by_id[order.order_id] = order
        return False

    def oldest_opposing_unit_cost(self, new_outcome: str) -> Optional[Decimal]:
        opposing = self.lots[other_outcome(new_outcome)]
        return opposing[0].unit_cost if opposing else None

    def pairable_fifo_shares(
        self,
        new_outcome: str,
        new_unit_cost: Decimal,
        *,
        limit: Optional[Decimal] = None,
    ) -> Decimal:
        """Shares pairable without averaging a bad marginal lot into a good one."""

        new_outcome = new_outcome.upper()
        new_unit_cost = dec(new_unit_cost)
        limit = None if limit is None else dec(limit)
        if limit is not None and limit <= ZERO:
            return ZERO
        total = ZERO
        for lot in self.lots[other_outcome(new_outcome)]:
            up_cost = new_unit_cost if new_outcome == "UP" else lot.unit_cost
            down_cost = new_unit_cost if new_outcome == "DOWN" else lot.unit_cost
            if not pair_is_admissible(up_cost, down_cost, self.config):
                break
            accepted = lot.shares if limit is None else min(lot.shares, limit - total)
            total += accepted
            if limit is not None and total >= limit:
                break
        return total

    def record_fill(self, fill: Fill) -> FillRecordResult:
        """Reconcile one authoritative confirmed fill.

        Real fills are never discarded merely because execution exceeded a
        strategy cap.  Such a breach is recorded and latches the planner off.
        Malformed, cross-condition, or fee-incomplete events are rejected
        because they cannot be accounted for truthfully.
        """

        fill = fill.normalized()
        if fill.condition_id != self.condition_id:
            raise StrategyInvariantError("fill condition_id does not match ledger")
        expected_token = self.up_token_id if fill.outcome == "UP" else self.down_token_id
        if fill.token_id != expected_token:
            raise StrategyInvariantError("fill token_id does not match outcome mapping")
        if fill.shares <= ZERO:
            raise StrategyInvariantError("fill shares must be positive")
        if not (ZERO < fill.price < self.config.payout_per_pair):
            raise StrategyInvariantError("fill price must be inside binary payout bounds")
        if fill.fee_usd is not None and fill.fee_usd < ZERO:
            raise StrategyInvariantError("fill fee cannot be negative")
        if fill.role == "taker" and fill.fee_usd is None:
            raise StrategyInvariantError("confirmed taker fill requires authoritative fee_usd")
        if fill.role == "taker" and fill.fee_source not in AUTHORITATIVE_FEE_SOURCES:
            raise StrategyInvariantError("confirmed taker fill requires authoritative fee_source")
        if fill.role == "maker" and fill.fee_usd in (None, ZERO):
            fill = replace(
                fill,
                fee_usd=ZERO,
                fee_source=CONFIGURED_MAKER_ZERO_SOURCE,
            )
        if fill.role == "maker" and fill.fee_usd != ZERO:
            if fill.fee_source not in AUTHORITATIVE_FEE_SOURCES:
                raise StrategyInvariantError("nonzero maker fee requires authoritative fee_source")
        prior = self.fills_by_id.get(fill.fill_id)
        if prior is not None:
            if prior == fill:
                return FillRecordResult(duplicate=True, matches=())
            raise ConflictingFillError(f"conflicting duplicate fill_id={fill.fill_id}")

        prior_order_identity = self.order_identity_by_id.get(fill.order_id)
        order_identity = (fill.outcome, fill.token_id)
        if prior_order_identity is not None and prior_order_identity != order_identity:
            raise StrategyInvariantError("order_id was reused across outcomes or tokens")
        if fill.reconciliation_index <= self.last_reconciliation_index:
            raise OutOfOrderFillError(
                "new fill is not after the ledger's last reconciliation_index"
            )
        return self._record_fill_unchecked(fill)

    def validate_prospective_maker_fill(self, fill: Fill) -> None:
        """Validate a planned maker fill without copying historical lot state."""

        fill = fill.normalized()
        if fill.condition_id != self.condition_id:
            raise StrategyInvariantError("prospective fill condition_id does not match ledger")
        expected_token = self.up_token_id if fill.outcome == "UP" else self.down_token_id
        if fill.token_id != expected_token:
            raise StrategyInvariantError("prospective fill token_id does not match outcome mapping")
        if fill.role != "maker":
            raise StrategyInvariantError("this validator accepts maker fills only")
        if fill.order_id in self.admitted_orders_by_id or fill.order_id in self.order_filled_shares:
            raise StrategyInvariantError("prospective order_id is not new")
        if fill.shares <= ZERO or not (ZERO < fill.price < self.config.payout_per_pair):
            raise StrategyInvariantError("prospective fill size or price is invalid")
        fill_cost = fill.shares * fill.price
        if (
            fill.shares > self.config.max_clip_shares
            or fill_cost > self.config.max_clip_notional_usd
        ):
            raise StrategyInvariantError("prospective parent order violates clip cap")

        remaining = fill.shares
        for lot in self.lots[other_outcome(fill.outcome)]:
            matched = min(remaining, lot.shares)
            up_cost = fill.price if fill.outcome == "UP" else lot.unit_cost
            down_cost = fill.price if fill.outcome == "DOWN" else lot.unit_cost
            if not pair_is_admissible(up_cost, down_cost, self.config):
                raise StrategyInvariantError("prospective fill violates pair-cost admission gate")
            remaining -= matched
            if remaining == ZERO:
                break

        projected_up = self.up_shares + (fill.shares if fill.outcome == "UP" else ZERO)
        projected_down = self.down_shares + (fill.shares if fill.outcome == "DOWN" else ZERO)
        if abs(projected_up - projected_down) > self.config.max_directional_shares:
            raise StrategyInvariantError("prospective fill violates directional share cap")
        projected_cost = self.unpaired_cost_usd + fill_cost
        guaranteed = min(projected_up, projected_down) * self.config.payout_per_pair
        if max(ZERO, projected_cost - guaranteed) > self.config.max_directional_cost_usd:
            raise StrategyInvariantError("prospective fill violates directional cost cap")

    def preview_fill(self, fill: Fill) -> BinaryInventoryLedger:
        """Project a proposed fill and reject any newly introduced incident."""

        self.validate_prospective_maker_fill(fill)
        shadow = copy.copy(self)
        shadow.lots = {
            outcome: deque(copy.copy(lot) for lot in lots) for outcome, lots in self.lots.items()
        }
        shadow.open_shares_by_outcome = self.open_shares_by_outcome.copy()
        shadow.open_cost_by_outcome = self.open_cost_by_outcome.copy()
        shadow.fills_by_id = {}
        shadow.admitted_orders_by_id = self.admitted_orders_by_id.copy()
        shadow.order_identity_by_id = self.order_identity_by_id.copy()
        shadow.order_filled_shares = self.order_filled_shares.copy()
        shadow.order_filled_cost_usd = self.order_filled_cost_usd.copy()
        shadow.merge_confirmations_by_id = self.merge_confirmations_by_id.copy()
        shadow.matches = []
        before = (
            shadow.pair_cost_violation_count,
            shadow.clip_violation_count,
            shadow.directional_cap_violation_count,
        )
        shadow.register_admitted_order(
            AdmittedOrder(
                order_id=fill.order_id,
                condition_id=fill.condition_id,
                token_id=fill.token_id,
                outcome=fill.outcome,
                side="BUY",
                limit_price=fill.price,
                max_shares=fill.shares,
                expected_role=fill.role,
                post_only=fill.role.lower() == "maker",
                reason="PLAN_PREVIEW",
            )
        )
        shadow.record_fill(fill)
        after = (
            shadow.pair_cost_violation_count,
            shadow.clip_violation_count,
            shadow.directional_cap_violation_count,
        )
        if after[0] > before[0]:
            raise StrategyInvariantError("prospective fill violates pair-cost admission gate")
        if after[1] > before[1]:
            raise StrategyInvariantError("prospective parent order violates clip cap")
        if after[2] > before[2]:
            raise StrategyInvariantError("prospective fill violates directional risk cap")
        return shadow

    def _record_fill_unchecked(self, fill: Fill) -> FillRecordResult:
        if fill.fee_usd is None:
            raise StrategyInvariantError("normalized confirmed fill is missing fee_usd")
        fee = fill.fee_usd
        fill_cost = fill.shares * fill.price + fee

        previous_order_shares = self.order_filled_shares.get(fill.order_id, ZERO)
        previous_order_cost = self.order_filled_cost_usd.get(fill.order_id, ZERO)
        parent_order_shares = previous_order_shares + fill.shares
        parent_order_cost = previous_order_cost + fill_cost
        admitted_order = self.admitted_orders_by_id.get(fill.order_id)
        if admitted_order is None:
            self.order_admission_violation_count += 1
        elif (
            admitted_order.condition_id != fill.condition_id
            or admitted_order.token_id != fill.token_id
            or admitted_order.outcome != fill.outcome
            or admitted_order.expected_role != fill.role
            or fill.price > admitted_order.limit_price
            or parent_order_shares > admitted_order.max_shares
        ):
            self.order_admission_violation_count += 1
        if (
            parent_order_shares > self.config.max_clip_shares
            or parent_order_cost > self.config.max_clip_notional_usd
        ):
            self.clip_violation_count += 1

        unit_cost = fill_cost / fill.shares
        remaining = fill.shares
        opposing_outcome = other_outcome(fill.outcome)
        opposing_lots = self.lots[opposing_outcome]
        new_matches: List[PairMatch] = []

        # Actual complete sets must be reconciled even when their historical
        # fills fail the prospective admission rule.  Never strand opposing
        # collateral to make the strategy look safer than execution really was.
        while remaining > ZERO and opposing_lots:
            opposing_lot = opposing_lots[0]
            up_cost = unit_cost if fill.outcome == "UP" else opposing_lot.unit_cost
            down_cost = unit_cost if fill.outcome == "DOWN" else opposing_lot.unit_cost
            matched = min(remaining, opposing_lot.shares)
            actual_cost = up_cost + down_cost
            guarded_cost = pair_guarded_cost(up_cost, down_cost, self.config)
            admissible = guarded_cost < self.config.payout_per_pair
            up_fill_id = fill.fill_id if fill.outcome == "UP" else opposing_lot.fill_id
            down_fill_id = fill.fill_id if fill.outcome == "DOWN" else opposing_lot.fill_id
            up_order_id = fill.order_id if fill.outcome == "UP" else opposing_lot.order_id
            down_order_id = fill.order_id if fill.outcome == "DOWN" else opposing_lot.order_id
            match = PairMatch(
                condition_id=self.condition_id,
                shares=matched,
                up_unit_cost=up_cost,
                down_unit_cost=down_cost,
                actual_pair_cost=actual_cost,
                guarded_pair_cost=guarded_cost,
                actual_locked_edge_per_pair=self.config.payout_per_pair - actual_cost,
                conservative_locked_edge_per_pair=(
                    self.config.payout_per_pair
                    - actual_cost
                    - self.config.operations_reserve_per_pair
                ),
                admissible=admissible,
                up_fill_id=up_fill_id,
                down_fill_id=down_fill_id,
                up_order_id=up_order_id,
                down_order_id=down_order_id,
            )
            new_matches.append(match)
            self.matches.append(match)
            self.paired_shares += matched
            self.unmerged_paired_shares += matched
            self.actual_locked_profit_usd += matched * match.actual_locked_edge_per_pair
            self.conservative_locked_profit_usd += matched * match.conservative_locked_edge_per_pair
            if not admissible:
                self.pair_cost_violation_count += 1
                self.inadmissible_paired_shares += matched
            remaining -= matched
            opposing_lot.shares -= matched
            self.open_shares_by_outcome[opposing_outcome] -= matched
            self.open_cost_by_outcome[opposing_outcome] -= matched * opposing_lot.unit_cost
            if opposing_lot.shares == ZERO:
                opposing_lots.popleft()

        if remaining > ZERO:
            self.lots[fill.outcome].append(
                InventoryLot(
                    outcome=fill.outcome,
                    shares=remaining,
                    unit_cost=unit_cost,
                    fill_id=fill.fill_id,
                    order_id=fill.order_id,
                    timestamp=fill.timestamp,
                )
            )
            self.open_shares_by_outcome[fill.outcome] += remaining
            self.open_cost_by_outcome[fill.outcome] += remaining * unit_cost

        self.fills_by_id[fill.fill_id] = fill
        self.order_identity_by_id[fill.order_id] = (fill.outcome, fill.token_id)
        self.order_filled_shares[fill.order_id] = parent_order_shares
        self.order_filled_cost_usd[fill.order_id] = parent_order_cost
        self.fill_count += 1
        self.last_reconciliation_index = fill.reconciliation_index
        self.fill_notional_usd += fill.shares * fill.price
        self.fees_paid_usd += fee
        if fill.role == "maker" and fee != ZERO:
            self.fee_schedule_violation_count += 1
        self.max_single_fill_shares = max(self.max_single_fill_shares, fill.shares)
        self.max_single_fill_cost_usd = max(self.max_single_fill_cost_usd, fill_cost)
        self.max_parent_order_shares = max(self.max_parent_order_shares, parent_order_shares)
        self.max_parent_order_cost_usd = max(self.max_parent_order_cost_usd, parent_order_cost)
        if (
            self.absolute_directional_shares > self.config.max_directional_shares
            or self.unpaired_worst_case_loss_usd > self.config.max_directional_cost_usd
        ):
            self.directional_cap_violation_count += 1
        self.max_abs_directional_shares = max(
            self.max_abs_directional_shares, self.absolute_directional_shares
        )
        self.max_unpaired_worst_case_loss_usd = max(
            self.max_unpaired_worst_case_loss_usd,
            self.unpaired_worst_case_loss_usd,
        )
        return FillRecordResult(duplicate=False, matches=tuple(new_matches))

    def confirm_merge(
        self,
        merge_id: str,
        shares: Decimal,
        *,
        condition_id: str,
        up_token_id: str,
        down_token_id: str,
        cost_usd: Decimal = ZERO,
    ) -> bool:
        """Apply an authoritative complete-set merge confirmation.

        Pair accounting proves the $1 terminal claim; confirmation proves that
        collateral was actually released and may be reused by the next cycle.
        Identical event replays are idempotent; conflicting IDs fail closed.

        Returns ``True`` for an identical replay and ``False`` for a new event.
        """

        if not isinstance(merge_id, str) or not merge_id:
            raise StrategyInvariantError("merge_id is required")
        if (
            condition_id != self.condition_id
            or up_token_id != self.up_token_id
            or down_token_id != self.down_token_id
        ):
            raise StrategyInvariantError("merge market identity does not match ledger")
        shares = dec(shares)
        cost_usd = dec(cost_usd)
        if shares <= ZERO:
            raise StrategyInvariantError("merge shares must be positive")
        if cost_usd < ZERO:
            raise StrategyInvariantError("merge cost cannot be negative")
        confirmation = (condition_id, up_token_id, down_token_id, shares, cost_usd)
        prior = self.merge_confirmations_by_id.get(merge_id)
        if prior is not None:
            if prior == confirmation:
                return True
            raise ConflictingMergeError(f"conflicting duplicate merge_id={merge_id}")
        if shares > self.unmerged_paired_shares:
            raise StrategyInvariantError("merge exceeds confirmed unmerged complete sets")
        reserved_cost = shares * self.config.operations_reserve_per_pair
        self.unmerged_paired_shares -= shares
        self.merged_shares += shares
        self.merge_cost_paid_usd += cost_usd
        self.actual_locked_profit_usd -= cost_usd
        self.conservative_locked_profit_usd -= max(ZERO, cost_usd - reserved_cost)
        if cost_usd > reserved_cost:
            self.operations_cost_violation_count += 1
        self.merge_confirmations_by_id[merge_id] = confirmation
        return False

    def metrics(self) -> Dict[str, object]:
        return {
            "condition_id": self.condition_id,
            "fill_count": self.fill_count,
            "last_reconciliation_index": self.last_reconciliation_index,
            "fill_notional_usd": str(self.fill_notional_usd),
            "paired_shares": str(self.paired_shares),
            "unmerged_paired_shares": str(self.unmerged_paired_shares),
            "merged_shares": str(self.merged_shares),
            "merge_confirmation_count": len(self.merge_confirmations_by_id),
            "merge_cost_paid_usd": str(self.merge_cost_paid_usd),
            "actual_locked_profit_usd": str(self.actual_locked_profit_usd),
            "conservative_locked_profit_usd": str(self.conservative_locked_profit_usd),
            "pair_cost_violation_count": self.pair_cost_violation_count,
            "inadmissible_paired_shares": str(self.inadmissible_paired_shares),
            "clip_violation_count": self.clip_violation_count,
            "directional_cap_violation_count": self.directional_cap_violation_count,
            "operations_cost_violation_count": self.operations_cost_violation_count,
            "fee_schedule_violation_count": self.fee_schedule_violation_count,
            "order_admission_violation_count": self.order_admission_violation_count,
            "has_execution_incident": self.has_execution_incident,
            "fees_paid_usd": str(self.fees_paid_usd),
            "unpaired_up_shares": str(self.up_shares),
            "unpaired_down_shares": str(self.down_shares),
            "unpaired_cost_usd": str(self.unpaired_cost_usd),
            "unpaired_worst_case_loss_usd": str(self.unpaired_worst_case_loss_usd),
            "worst_case_terminal_pnl_usd": str(self.worst_case_terminal_pnl_usd),
            "max_single_fill_shares": str(self.max_single_fill_shares),
            "max_single_fill_cost_usd": str(self.max_single_fill_cost_usd),
            "max_parent_order_shares": str(self.max_parent_order_shares),
            "max_parent_order_cost_usd": str(self.max_parent_order_cost_usd),
            "max_abs_directional_shares": str(self.max_abs_directional_shares),
            "max_unpaired_worst_case_loss_usd": str(self.max_unpaired_worst_case_loss_usd),
        }


@dataclass(frozen=True)
class BinaryBookSnapshot:
    condition_id: str
    up_token_id: str
    down_token_id: str
    up_best_bid: Decimal
    up_best_ask: Decimal
    down_best_bid: Decimal
    down_best_ask: Decimal
    up_timestamp: Decimal
    down_timestamp: Decimal
    end_timestamp: Decimal
    tick_size: Decimal
    min_order_size: Decimal
    fee_rate: Decimal
    fee_quantum: Decimal
    size_quantum: Decimal
    builder_maker_fee_rate: Decimal
    builder_taker_fee_rate: Decimal
    fee_exponent: int
    fees_taker_only: bool
    accepting_orders: bool
    neg_risk: bool
    itode: bool

    def normalized(self) -> BinaryBookSnapshot:
        exponent = dec(self.fee_exponent)
        if exponent != exponent.to_integral_value():
            raise StrategyInvariantError("fee_exponent must be an integer")
        numeric = {
            name: dec(getattr(self, name))
            for name in (
                "up_best_bid",
                "up_best_ask",
                "down_best_bid",
                "down_best_ask",
                "up_timestamp",
                "down_timestamp",
                "end_timestamp",
                "tick_size",
                "min_order_size",
                "fee_rate",
                "fee_quantum",
                "size_quantum",
                "builder_maker_fee_rate",
                "builder_taker_fee_rate",
            )
        }
        return BinaryBookSnapshot(
            condition_id=self.condition_id,
            up_token_id=self.up_token_id,
            down_token_id=self.down_token_id,
            fee_exponent=int(exponent),
            fees_taker_only=self.fees_taker_only,
            accepting_orders=self.accepting_orders,
            neg_risk=self.neg_risk,
            itode=self.itode,
            **numeric,
        )

    def validate(self, now: Decimal, config: StrategyConfig) -> Optional[str]:
        try:
            book = self.normalized()
            now = dec(now)
        except StrategyInvariantError:
            return "INVALID_BOOK_NUMBER"
        identities = (book.condition_id, book.up_token_id, book.down_token_id)
        if any(not isinstance(identity, str) or not identity for identity in identities):
            return "MISSING_MARKET_IDENTITY"
        if book.up_token_id == book.down_token_id:
            return "NON_COMPLEMENTARY_TOKEN_IDS"
        if not all(
            isinstance(value, bool)
            for value in (
                book.fees_taker_only,
                book.accepting_orders,
                book.neg_risk,
                book.itode,
            )
        ):
            return "INVALID_MARKET_FLAGS"
        if not book.accepting_orders:
            return "MARKET_NOT_ACCEPTING_ORDERS"
        if book.neg_risk:
            return "UNSUPPORTED_NEG_RISK_MARKET"
        if not (ZERO <= book.fee_rate < ONE) or book.fee_exponent <= 0:
            return "INVALID_FEE_SCHEDULE"
        if book.fee_quantum <= ZERO or book.size_quantum <= ZERO:
            return "INVALID_MARKET_PRECISION"
        if book.builder_maker_fee_rate < ZERO or book.builder_taker_fee_rate < ZERO:
            return "INVALID_BUILDER_FEE_SCHEDULE"
        if book.builder_maker_fee_rate != ZERO or book.builder_taker_fee_rate != ZERO:
            return "UNSUPPORTED_NONZERO_BUILDER_FEE"
        if (
            book.fee_rate != config.taker_fee_rate
            or book.fee_exponent != config.taker_fee_exponent
            or book.fees_taker_only != config.fees_taker_only
        ):
            return "FEE_SCHEDULE_MISMATCH"
        if book.fee_quantum != config.fee_quantum or book.size_quantum != config.order_size_quantum:
            return "MARKET_PRECISION_MISMATCH"
        if book.tick_size <= ZERO or book.min_order_size <= ZERO:
            return "INVALID_MARKET_LIMITS"
        if floor_shares(book.min_order_size, book.size_quantum) != book.min_order_size:
            return "MARKET_SIZE_PRECISION_MISMATCH"
        for bid, ask in (
            (book.up_best_bid, book.up_best_ask),
            (book.down_best_bid, book.down_best_ask),
        ):
            if not (ZERO < bid < ask < config.payout_per_pair):
                return "CROSSED_OR_INVALID_BOOK"
            if (
                floor_to_tick(bid, book.tick_size) != bid
                or floor_to_tick(ask, book.tick_size) != ask
            ):
                return "BOOK_PRICE_TICK_MISMATCH"
        if book.up_timestamp > now or book.down_timestamp > now:
            return "FUTURE_DATED_BOOK"
        if now - book.up_timestamp > config.book_stale_after_seconds:
            return "STALE_UP_BOOK"
        if now - book.down_timestamp > config.book_stale_after_seconds:
            return "STALE_DOWN_BOOK"
        if abs(book.up_timestamp - book.down_timestamp) > config.max_cross_book_skew_seconds:
            return "CROSS_BOOK_TIMESTAMP_SKEW"
        if book.end_timestamp <= now:
            return "MARKET_ENDED"
        return None

    def midpoint(self, outcome: str) -> Decimal:
        outcome = outcome.upper()
        if outcome == "UP":
            return (dec(self.up_best_bid) + dec(self.up_best_ask)) / D("2")
        if outcome == "DOWN":
            return (dec(self.down_best_bid) + dec(self.down_best_ask)) / D("2")
        raise StrategyInvariantError(f"unknown outcome: {outcome!r}")

    def best_bid(self, outcome: str) -> Decimal:
        outcome = outcome.upper()
        if outcome == "UP":
            return dec(self.up_best_bid)
        if outcome == "DOWN":
            return dec(self.down_best_bid)
        raise StrategyInvariantError(f"unknown outcome: {outcome!r}")

    def best_ask(self, outcome: str) -> Decimal:
        outcome = outcome.upper()
        if outcome == "UP":
            return dec(self.up_best_ask)
        if outcome == "DOWN":
            return dec(self.down_best_ask)
        raise StrategyInvariantError(f"unknown outcome: {outcome!r}")


@dataclass(frozen=True)
class ExecutionState:
    """Authoritative reservations for one condition.

    The strategy is intentionally staged: a new order is forbidden until the
    prior order is gone, every MATCHED/MINED trade is CONFIRMED and reconciled,
    and every uncertain cancellation has been resolved.
    """

    condition_id: str
    live_order_ids: Tuple[str, ...] = ()
    pending_trade_ids: Tuple[str, ...] = ()
    uncertain_order_ids: Tuple[str, ...] = ()

    def blocking_reason(self, expected_condition_id: str) -> Optional[str]:
        if not isinstance(self.condition_id, str) or not self.condition_id:
            return "MISSING_EXECUTION_CONDITION"
        if self.condition_id != expected_condition_id:
            return "EXECUTION_CONDITION_MISMATCH"
        for identifiers in (
            self.live_order_ids,
            self.pending_trade_ids,
            self.uncertain_order_ids,
        ):
            if not isinstance(identifiers, tuple):
                return "INVALID_EXECUTION_RESERVATION"
            if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
                return "INVALID_EXECUTION_RESERVATION"
            if len(set(identifiers)) != len(identifiers):
                return "DUPLICATE_EXECUTION_RESERVATION"
        if self.uncertain_order_ids:
            return "UNCERTAIN_ORDER_RESERVATION"
        if self.pending_trade_ids:
            return "PENDING_FILL_RESERVATION"
        if self.live_order_ids:
            return "LIVE_ORDER_RESERVATION"
        return None


@dataclass(frozen=True)
class ModelSignal:
    condition_id: str
    fair_up_probability: Decimal
    timestamp: Decimal
    calibrated: bool

    def normalized(self) -> ModelSignal:
        if not isinstance(self.condition_id, str) or not self.condition_id:
            raise StrategyInvariantError("model condition_id is required")
        if not isinstance(self.calibrated, bool):
            raise StrategyInvariantError("calibrated must be a boolean")
        fair = dec(self.fair_up_probability)
        if not (ZERO <= fair <= ONE):
            raise StrategyInvariantError("fair_up_probability must be in [0, 1]")
        return ModelSignal(
            condition_id=self.condition_id,
            fair_up_probability=fair,
            timestamp=dec(self.timestamp),
            calibrated=self.calibrated,
        )


@dataclass(frozen=True)
class OrderIntent:
    condition_id: str
    token_id: str
    outcome: str
    side: str
    price: Decimal
    shares: Decimal
    post_only: bool
    reason: str

    def admitted_order(self, order_id: str) -> AdmittedOrder:
        """Bind a venue/client order ID to this frozen planner intent."""

        return AdmittedOrder(
            order_id=order_id,
            condition_id=self.condition_id,
            token_id=self.token_id,
            outcome=self.outcome,
            side=self.side,
            limit_price=self.price,
            max_shares=self.shares,
            expected_role="maker" if self.post_only else "taker",
            post_only=self.post_only,
            reason=self.reason,
        )


@dataclass(frozen=True)
class PlanDecision:
    intent: Optional[OrderIntent]
    reason: str


def _passive_price_ceiling(book: BinaryBookSnapshot, outcome: str) -> Decimal:
    return book.best_ask(outcome) - dec(book.tick_size)


def _size_with_clip_caps(
    requested_shares: Decimal,
    price: Decimal,
    book: BinaryBookSnapshot,
    config: StrategyConfig,
) -> Decimal:
    shares = min(dec(requested_shares), config.clip_shares, config.max_clip_shares)
    shares = min(shares, config.max_clip_notional_usd / price)
    shares = floor_shares(shares, config.order_size_quantum)
    if shares < dec(book.min_order_size):
        return ZERO
    return shares


def _pair_start_candidate(
    book: BinaryBookSnapshot,
    config: StrategyConfig,
) -> Optional[Tuple[str, Decimal, Decimal]]:
    """Return one deterministic first leg for a two-quote LOCK candidate.

    Both hypothetical maker legs must support the exact frozen clip and pass
    the marginal pair gate.  Only the cheaper leg is returned (UP wins a tie).
    The unsubmitted second quote is not reserved and no edge is yet locked.
    """

    prices: Dict[str, Decimal] = {}
    for outcome in OUTCOMES:
        price = floor_to_tick(_passive_price_ceiling(book, outcome), book.tick_size)
        if price <= ZERO:
            return None
        if _size_with_clip_caps(config.clip_shares, price, book, config) != config.clip_shares:
            return None
        prices[outcome] = price

    up_cost = effective_unit_cost(config.clip_shares, prices["UP"], "maker", config)
    down_cost = effective_unit_cost(config.clip_shares, prices["DOWN"], "maker", config)
    if not pair_is_admissible(up_cost, down_cost, config):
        return None
    outcome = "UP" if prices["UP"] <= prices["DOWN"] else "DOWN"
    return outcome, prices[outcome], config.clip_shares


def plan_next_order(
    ledger: BinaryInventoryLedger,
    book: BinaryBookSnapshot,
    signal: Optional[ModelSignal],
    *,
    execution_state: ExecutionState,
    now: Decimal,
) -> PlanDecision:
    """Plan at most one post-only BUY clip for a staged accumulation cycle."""

    config = ledger.config
    try:
        now = dec(now)
    except StrategyInvariantError:
        return PlanDecision(None, "INVALID_NOW")
    invalid_book = book.validate(now, config)
    if invalid_book:
        return PlanDecision(None, invalid_book)
    book = book.normalized()
    if (
        ledger.condition_id != book.condition_id
        or ledger.up_token_id != book.up_token_id
        or ledger.down_token_id != book.down_token_id
    ):
        return PlanDecision(None, "LEDGER_BOOK_IDENTITY_MISMATCH")
    if not isinstance(execution_state, ExecutionState):
        return PlanDecision(None, "INVALID_EXECUTION_STATE")
    execution_block = execution_state.blocking_reason(book.condition_id)
    if execution_block:
        return PlanDecision(None, execution_block)
    if ledger.unmerged_paired_shares > ZERO:
        return PlanDecision(None, "AWAITING_MERGE_CONFIRMATION")
    if ledger.pair_cost_violation_count:
        return PlanDecision(None, "PAIR_COST_INCIDENT")
    if ledger.operations_cost_violation_count:
        return PlanDecision(None, "OPERATIONS_COST_INCIDENT")
    if ledger.fee_schedule_violation_count:
        return PlanDecision(None, "FEE_SCHEDULE_INCIDENT")
    if ledger.order_admission_violation_count:
        return PlanDecision(None, "ORDER_ADMISSION_INCIDENT")
    if ledger.clip_violation_count or ledger.directional_cap_violation_count:
        return PlanDecision(None, "RISK_CAP_INCIDENT")
    if ledger.has_two_sided_unpaired_inventory:
        return PlanDecision(None, "LEDGER_RECONCILIATION_ERROR")

    seconds_to_end = book.end_timestamp - now
    signed_inventory = ledger.signed_directional_shares
    balancing = signed_inventory != ZERO

    if balancing:
        if seconds_to_end <= config.completion_cutoff_seconds:
            return PlanDecision(None, "COMPLETION_CUTOFF")
        outcome = "DOWN" if signed_inventory > ZERO else "UP"
        opposing_cost = ledger.oldest_opposing_unit_cost(outcome)
        if opposing_cost is None:
            return PlanDecision(None, "MISSING_OPPOSING_LOT")
        desired = min(abs(signed_inventory), config.clip_shares)
        ceiling = max_complement_price(
            opposing_cost,
            desired,
            "maker",
            book.tick_size,
            config,
        )
        if ceiling is None:
            return PlanDecision(None, "NO_PROFITABLE_COMPLEMENT_PRICE")
        price = floor_to_tick(
            min(ceiling, _passive_price_ceiling(book, outcome)),
            book.tick_size,
        )
        if price <= ZERO:
            return PlanDecision(None, "NO_VALID_POST_ONLY_PRICE")
        new_unit_cost = effective_unit_cost(desired, price, "maker", config)
        pairable = ledger.pairable_fifo_shares(outcome, new_unit_cost, limit=desired)
        desired = min(desired, pairable)
        size = _size_with_clip_caps(desired, price, book, config)
        if size == ZERO:
            return PlanDecision(None, "PAIRABLE_SIZE_BELOW_MARKET_MINIMUM")
        reason = "COMPLETE_DOWN_PAIR" if outcome == "DOWN" else "COMPLETE_UP_PAIR"
    else:
        if seconds_to_end <= config.new_lean_cutoff_seconds:
            return PlanDecision(None, "NEW_LEAN_CUTOFF")
        normalized_signal: Optional[ModelSignal] = None
        if signal is not None:
            try:
                normalized_signal = signal.normalized()
            except StrategyInvariantError:
                return PlanDecision(None, "INVALID_MODEL_SIGNAL")
            if normalized_signal.condition_id != book.condition_id:
                return PlanDecision(None, "MODEL_CONDITION_MISMATCH")
        pair_start = _pair_start_candidate(book, config)
        if pair_start is not None:
            outcome, price, size = pair_start
            reason = "PAIR_START_UP" if outcome == "UP" else "PAIR_START_DOWN"
        else:
            if normalized_signal is None:
                return PlanDecision(None, "NO_LOCK_OR_MODEL_ENTRY")
            signal = normalized_signal
            if not signal.calibrated:
                return PlanDecision(None, "UNCALIBRATED_MODEL")
            if signal.timestamp > now:
                return PlanDecision(None, "FUTURE_DATED_MODEL")
            if now - signal.timestamp > config.signal_stale_after_seconds:
                return PlanDecision(None, "STALE_MODEL")

            up_edge = signal.fair_up_probability - book.midpoint("UP")
            down_fair = ONE - signal.fair_up_probability
            down_edge = down_fair - book.midpoint("DOWN")
            if max(up_edge, down_edge) < config.min_model_edge:
                return PlanDecision(None, "NO_LOCK_OR_MODEL_ENTRY")
            outcome = "UP" if up_edge >= down_edge else "DOWN"
            fair_value = signal.fair_up_probability if outcome == "UP" else down_fair

            future_pair_ceiling = (
                config.payout_per_pair
                - config.min_locked_edge_per_pair
                - config.operations_reserve_per_pair
                - config.min_future_complement_price
            )
            price = floor_to_tick(
                min(
                    _passive_price_ceiling(book, outcome),
                    fair_value - config.min_model_edge,
                    future_pair_ceiling,
                ),
                book.tick_size,
            )
            if price <= ZERO:
                return PlanDecision(None, "NO_VALID_POST_ONLY_PRICE")
            risk_room = config.max_directional_cost_usd - ledger.unpaired_worst_case_loss_usd
            if risk_room < config.clip_shares * price:
                return PlanDecision(None, "DIRECTIONAL_COST_CAP")
            share_room = config.max_directional_shares - ledger.absolute_directional_shares
            if share_room < config.clip_shares:
                return PlanDecision(None, "DIRECTIONAL_SHARE_CAP")
            # Model magnitude changes participation and the price ceiling, never
            # the clip. Once the frozen edge threshold clears, request one clip.
            size = _size_with_clip_caps(config.clip_shares, price, book, config)
            if size != config.clip_shares:
                return PlanDecision(None, "MODEL_FIXED_CLIP_UNAVAILABLE")
            reason = "MODEL_LEAN_UP" if outcome == "UP" else "MODEL_LEAN_DOWN"

    # Final exact preview: the planner must never emit an intent whose complete
    # fill would violate the same ledger used for reconciliation.
    token_id = book.up_token_id if outcome == "UP" else book.down_token_id
    preview = Fill(
        fill_id=f"__PLAN_PREVIEW_FILL__:{ledger.fill_count}",
        order_id=f"__PLAN_PREVIEW_ORDER__:{ledger.fill_count}",
        condition_id=book.condition_id,
        token_id=token_id,
        outcome=outcome,
        shares=size,
        price=price,
        role="maker",
        timestamp=now,
        reconciliation_index=ledger.last_reconciliation_index + 1,
    )
    try:
        ledger.validate_prospective_maker_fill(preview)
    except StrategyInvariantError as exc:
        return PlanDecision(None, f"PREVIEW_REJECTED:{exc}")

    return PlanDecision(
        OrderIntent(
            condition_id=book.condition_id,
            token_id=token_id,
            outcome=outcome,
            side="BUY",
            price=price,
            shares=size,
            post_only=True,
            reason=reason,
        ),
        reason,
    )


def float_is_finite(value: object) -> bool:
    """Compatibility helper for callers validating JSON numbers before ``dec``."""

    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
