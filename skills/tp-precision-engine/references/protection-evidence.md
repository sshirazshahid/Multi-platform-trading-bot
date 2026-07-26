# Protection Evidence and Reconciliation

Protection is a continuously verified relationship among a venue position, durable bot intent, and
live reduce-only conditional orders. It is not a one-time API response.

## Evidence states

| State | Minimum evidence | Entry admission |
|---|---|---|
| `pending` | Intent durably stored before placement | Block |
| `acknowledged` | Venue accepted the request or returned an order ID | Block until queried/observed |
| `verified` | Fresh private event or successful venue query sees correct role, side, price, and remaining coverage | Policy may admit |
| `repair_required` | Missing, under-covered, conflicting, expired, rejected, or unknown-after-deadline state | Block and reconcile |

An acknowledgement is not verification. A REST/stream error is unknown, not evidence that an order
is absent. Record venue event/source time separately from local receipt time.

A serialized `verified` state must retain its evidence source, fresh verification timestamp, and
venue order identifiers. An offline validator can check that evidence is present and internally
consistent, but cannot independently prove that the recorded venue observation is true.

## Durable intent fields

Persist at least:

- venue/account domain, canonical symbol, market type, position side/mode, and position/fill ID;
- entry average price and cumulative filled/remaining quantity;
- initial risk stop, current desired stop, target ladder, order roles, and trigger sources;
- price tick, amount step, contract size, and metadata version/time;
- bot-owned client IDs, venue order IDs when known, and replacement generation;
- `observed_at`, `received_at`, state, last verification time, and reconciliation cursor.

Never store credentials or unredacted signed requests in intent/evidence records.

## Coverage calculation

For each open venue position:

1. Determine remaining quantity from a fresh position/fill view.
2. Query regular and conditional/plan ledgers; venues may separate them.
3. Keep only live orders matching venue/account, symbol, position side, close side, reduce-only
   semantics, and bot ownership/adoption rules.
4. Group by role. Verify one effective loss-side rail for the remaining quantity and ensure target
   quantities cannot reverse the position.
5. Treat over-coverage, overlapping replacement generations, or an unverifiable ledger as a
   conflict requiring reconciliation, not as “extra safety.”

Partial fills change coverage immediately. Resize from cumulative venue truth, not the requested
entry amount or a stale local position.

An average fill price can fall between ticks after multiple fills; quantize outbound order prices,
not the computed average. Venue minimum-notional exemptions for reduce-only closing orders vary, so
encode and test that rule explicitly rather than assuming the entry-order minimum always applies.

## Replacement and restart rules

- Persist the next generation before amend/cancel/create.
- Reconcile an ambiguous acknowledgement by client ID before retrying.
- Do not mark the replacement verified until venue evidence sees it.
- Do not cancel the old rail until the replacement path has the venue-specific coverage required by
  the tested transition policy.
- On restart, run authorization gates first, then read-only venue preflight, then reconcile every
  venue position, conditional ledger, recent fill cursor, and pending intent before new entries.
- Adopt only orders that satisfy explicit ownership/identity rules. Never cancel unrelated manual
  orders merely because their price resembles a bot target.

## Fill-model requirements

- A limit touch is not a fill. Model trade-through, queue uncertainty, spread, and latency.
- When OHLC bars contain both TP and SL, use conservative or explicitly sampled intrabar ordering;
  do not award the favorable path by default.
- Stop-market means no limit price, not guaranteed execution or bounded slippage.
- Attached TP/SL may reduce a protection window but can still fail validation, cover the wrong
  quantity/mode, or require separate venue reconciliation. Do not call it atomic without a venue
  transaction guarantee that has been verified for the exact API path.

## Operational metrics

- `protection_pending_ms`: fill observation to independently verified coverage.
- `undercovered_seconds`: time remaining position quantity exceeded verified loss-side coverage.
- `coverage_ratio`: verified applicable loss-side quantity / remaining position quantity.
- `repair_attempts` and `repair_success_rate`, separated by venue/reason.
- `touch_no_fill_rate` per target rung and liquidity bucket.
- stop trigger-to-fill slippage, gap loss, fee/funding drag, and rejected/amended order counts.
- restart adoption duration and unresolved/orphan counts.

Metrics are diagnostic evidence, not profitability guarantees. Promote level logic only from
chronological, cost-stressed, out-of-sample results; keep protection integrity as an independent hard
gate.

## Primary venue references

- [Binance USD-M new order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order)
- [Bybit V5 create order](https://bybit-exchange.github.io/docs/v5/order/create-order)
- [Bybit V5 trading stop](https://bybit-exchange.github.io/docs/v5/position/trading-stop)
- [Bitget API documentation](https://www.bitget.com/api-doc/common/intro)

Re-check supported order fields, trigger sources, order-ledger queries, and rate limits in the current
official docs and installed ccxt version before changing adapter mappings.
