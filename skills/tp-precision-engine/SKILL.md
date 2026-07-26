---
name: tp-precision-engine
description: Design, audit, test, and reconcile take-profit, stop-loss, and trailing protection for crypto futures across Binance, Bybit, and Bitget. Use whenever code computes exit levels or quantities, places attached or conditional TP/SL orders, handles partial fills, persists protection intent, verifies separate conditional-order ledgers, repairs missing coverage after reconnect/restart, models intrabar fills, or investigates a touched-but-unfilled target or stop slippage. Also use to reject unsafe claims of atomic protection, guaranteed fills, or live readiness without venue evidence.
---

# TP/SL Precision Engine

## Overview

Separate two questions:

- **Protection integrity:** does durable intent match verified reduce-only venue coverage for the
  actual filled quantity?
- **Level quality:** did a cost-aware, out-of-sample process support the chosen exit geometry?

Neither attached orders nor a successful placement response proves atomic protection. A stop-market
trigger prioritizes execution attempts over price control, but liquidity gaps, venue outages,
liquidation, and slippage mean an exit price or fill is never guaranteed.

## When to Use

- Calculate, quantize, place, amend, cancel, trail, or reconcile a TP/SL.
- Handle partial entry/exit fills or resize protection coverage.
- Diagnose missing conditional orders, touched-but-unfilled limits, trigger-source differences, or
  unexpected stop slippage.
- Review restart recovery, naked-position exposure, or protection-intent persistence.
- Backtest an ATR/structure/trailing exit or compare limit versus market exits after costs.

## Prerequisites

- Exact venue instrument metadata: price tick, amount step, contract size, minimum amount/notional,
  and supported conditional-order semantics.
- Verified account/position mode and normalized long/short representation.
- Entry fill quantity and average price from venue evidence, not merely requested order values.
- Source-observed and locally received UTC timestamps for market inputs and venue events.
- Fee, spread, slippage, funding, latency, and liquidation assumptions appropriate to the market.
- A durable store for intent, acknowledgements, reconciliation state, and client order IDs.

Read `references/protection-evidence.md` before changing live protection state transitions.

## Workflow

### 1. Define risk before targets

Choose the invalidation level first and define `R = abs(entry - initial_stop)`. Size the position from
the permitted account risk and executable stop distance, including estimated costs and contract
size. Reject zero/negative risk, a stop on the wrong side, or a quantity below venue minima.

Treat ATR, swing levels, prior extremes, basis/funding, order-book walls, and round numbers as
hypotheses. Select parameters only with chronological walk-forward tests, multiple symbols/regimes,
and stressed costs; do not tune by eye on recent winners.

### 2. Build and validate a quantized plan

Use decimal arithmetic. Quantize each price and quantity to venue metadata, then assign any safe
quantity remainder to the final rung. Require:

- total exit quantity equals the filled position quantity after quantization;
- every exit is reduce-only or the venue's equivalent;
- stop and targets are on the correct side of entry;
- client IDs are present and unique;
- no rung violates amount/notional or contract-size rules.

Validate a serialized candidate plan offline:

```bash
python scripts/validate_protection_plan.py path/to/plan.json --json
```

This validator checks plan invariants only; it cannot prove that a venue holds the orders.

### 3. Select explicit trigger and execution semantics

Prefer exchange-native conditional protection over a bot-only price watcher. Select the trigger
source deliberately (`mark`, `last`, or `index`) based on tested venue behavior and record it in the
intent. There is no universally correct source for every strategy or venue.

Use a trigger-market stop when the policy prioritizes exit probability, while modeling unbounded
slippage. Choose limit or market TP rung-by-rung from depth, urgency, fees, and touch-no-fill data.
For conditional limits, distinguish trigger price from limit price and model queue position; a touch
is not a fill.

### 4. Persist intent before venue placement

Durably write a versioned protection intent before sending an order. Include position/fill identity,
desired quantity, trigger source, trigger and limit prices, client IDs, `observed_at`, `received_at`,
and state `pending`.

Advance state only from evidence:

`pending -> acknowledged -> verified`, or `pending/acknowledged -> repair_required`.

An API acknowledgement may advance to `acknowledged`; only a private event or successful venue query
that sees the expected live order and coverage advances to `verified`. Persist the state transition
and evidence cursor atomically with local bookkeeping.

### 5. Cover fills and partial fills

On an entry fill, reconcile cumulative filled quantity before placing or resizing protection. Attached
TP/SL can reduce the unprotected window on supported venues, but do not label the operation atomic.
Track `protection_pending` until venue coverage is verified.

On every partial entry or exit fill:

1. recompute remaining position quantity from venue truth;
2. compare intended versus live reduce-only coverage;
3. resize or replace using bot-owned IDs without blind retries;
4. persist and re-query until verified or escalated.

Block additional entries when protection is unknown or under-covered. Follow the configured emergency
policy for reduce/close decisions; the skill must not invent a live liquidation action.

### 6. Reconcile continuously and on restart

For every venue position, query both regular and conditional/plan-order ledgers. Match by position,
side, client ID, order role, and remaining quantity. A fetch error means unknown - not absent.

Run reconciliation after startup authorization, private-stream gaps, reconnects, partial fills,
amendments, and periodically while positions exist. Repair verified gaps, cancel bot-owned orphans
only after adoption checks, and alert with redacted evidence. Before admitting new entries on restart,
adopt or resolve every venue position and persisted `protection_pending` intent.

### 7. Trail without widening risk

Ratchet stops only toward lower risk. Persist the replacement intent before amendment, rate-limit
updates, reconcile ambiguous responses by client ID, and verify the new order before treating the old
coverage as safely replaced. Prefer an overlap or venue-native amend path when supported and tested.

### 8. Test execution and level logic separately

Execution tests must cover quantization, partial fills, ambiguous acknowledgements, disconnected
private streams, unknown conditional ledgers, restart recovery, stale event timestamps, orphan
adoption, stop gaps, and limit touch-without-fill.

Backtests must use chronological decisions, conservative intrabar ordering when both TP and SL are
reachable, realistic fees/spread/slippage/funding, walk-forward parameter selection, and holdout
promotion criteria. Report results by venue, symbol liquidity, volatility regime, and rung.

Run the deterministic skill tests:

```bash
python -m pytest scripts/tests -q
```

## Output

Return a protection evidence report containing:

- quantized intent and invariant-validation result;
- venue acknowledgement and independent coverage-verification state;
- remaining position quantity versus verified reduce-only coverage;
- protection latency, under-covered seconds, touch-no-fill rate, stop slippage, and repair count;
- backtest/forward-test assumptions and out-of-sample results for level logic;
- unresolved unknowns and the gate that prevents new or controlled-live entries.

Do not report "fully protected," "atomic," or "guaranteed" without qualifying the exact observed
venue state and timestamp.

## Resources

- `scripts/validate_protection_plan.py`: standard-library validator for quantization, direction,
  coverage, reduce-only, IDs, and durable intent timestamps.
- `scripts/tests/test_validate_protection_plan.py`: deterministic valid/invalid plan tests.
- `references/protection-evidence.md`: evidence state machine, restart/reconcile rules, and metrics.
- Repository implementation: `core/order_manager.py`, `core/position_tracker.py`,
  `core/realtime_streams.py`, `exchanges/base.py`, and venue adapters.
