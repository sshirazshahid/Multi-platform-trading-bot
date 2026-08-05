# 56 — Deep research: Complementary Outcome Accumulation (COA)

*Generated: 2026-08-01 | Venue assumption: Polymarket international CLOB | Confidence: high on mechanics, low on unobserved fill edge*

## Decision

**MECHANICALLY VALID, EDGE UNPROVEN, RESEARCH/PAPER ONLY.**

The intended strategy is not CEX buy/sell market making and "$1" is not a
portfolio-size limit. It is staged accumulation of equal **UP and DOWN outcome
shares**. A proposed marginal acquisition is admitted only when its guarded,
fee-aware cost is strictly below the pair's $1 payout; every actual fill is still
reconciled if execution defeats that estimate. A small model-directed surplus is
permitted, but it is explicitly unhedged risk and never gets counted as locked
arbitrage profit.

The repository has no prediction-market venue adapter or outcome-token model.
The existing `55_*` MTSI experiment is therefore preserved as a separate,
falsified CEX interpretation. This branch adds only a venue-neutral research
ledger and order planner; it cannot place orders.

## Binding owner doctrine

1. Buy UP and DOWN at different times.
2. Balance in **shares**, admitting only prospective marginal pairs whose guarded
   all-in estimate remains below their $1 payout, then reconcile all actual
   outcomes without hiding a loss.
3. When a calibrated model identifies an undervalued outcome, allow one small,
   capped surplus clip on that side.
4. No martingale, catch-up sizing, or single large trade. The process must earn
   many small edges through repeated, systematic execution.

## Why Polymarket is the likely venue

The wording maps directly to Polymarket's recurring BTC/ETH/SOL/XRP 5-minute and
15-minute UP/DOWN markets. Each standard binary condition has two complementary
tokens; an equal pair can be merged for $1 pUSD, and after resolution only the
winning token redeems for $1. Official documentation confirms the complete-set
mechanic and equal-token merge: [positions and tokens](https://docs.polymarket.com/concepts/positions-tokens),
[position management](https://docs.polymarket.com/trading/positions/manage).

An **unarchived live observation** of the BTC 15-minute market at 2026-08-01
04:35 UTC had:

| Field | Observed value |
|---|---:|
| Condition | `0x8298832ed93f2a62e608e17fc8d0b694c2e1ad2a31cb06e5738fb6c951e0bdba` |
| Best UP bid / ask | 0.48 / 0.49 |
| Best DOWN bid / ask | 0.51 / 0.52 |
| Minimum size | 5 shares |
| Tick | 0.01 |
| Fees | enabled; taker-only, rate 0.07, exponent 1 |
| Maker rebate pool | 20% |

Dynamic metadata endpoints: [Gamma event](https://gamma-api.polymarket.com/events?slug=btc-updown-15m-1785558600)
and [CLOB market](https://clob.polymarket.com/clob-markets/0x8298832ed93f2a62e608e17fc8d0b694c2e1ad2a31cb06e5738fb6c951e0bdba).
The quoted L2 was not saved as an immutable raw payload, so these mutable links
do not make the bid/ask observation reproducible and it receives no evidentiary
weight for edge. All values must be fetched and archived per condition. A
just-closed market changed from a 0.01 to 0.001 tick and stopped accepting
orders, confirming that tick and lifecycle events cannot be hardcoded. The
official market stream publishes book, price, tick-size, new-market, and
resolution events: [market stream](https://docs.polymarket.com/market-data/realtime-data#market-stream).

Before any order, atomically bind the `conditionId` to both token IDs and their
expected UP/DOWN outcome labels. Also bind `negRisk`, `acceptingOrders` and
lifecycle state, minimum size, tick, `itode`, and the condition's current fee
rate, exponent, and taker-only setting. Any missing or changed binding is a hard
stop, not something inferred from token order or a stale market slug.

If a different venue was intended, this assumption must be revisited because
fees, contract payoff, minimum sizes, settlement, and legal availability differ.

## Exact economics

For a prospective marginal matched quantity `q`:

```text
estimated_fee_i = q × fee_rate_i × (p_i × (1 - p_i)) ^ fee_exponent_i
estimated_unit_cost_i = p_i + estimated_fee_i / q

guarded_pair_cost = estimated_unit_cost_UP
                  + estimated_unit_cost_DOWN
                  + operations_reserve
                  + required_locked_edge

prospective_order_admissible = (guarded_pair_cost < 1.00)
```

The sampled condition reported rate `0.07`, exponent `1`, and taker-only fees;
none of those values may be hardcoded for another condition. The official fee
formula, maker/taker treatment, five-decimal fee precision, and per-market
schedule are documented at [Trading fees](https://docs.polymarket.com/trading/fees).
Rounding an estimated taker fee upward to five decimals is a conservative
**preflight estimate only**. Standard account-trade/user-stream events expose a
fee rate, not necessarily the exact charged amount. Reconciliation must obtain
the exact fee from documented builder-attributed `fee`/`feeUsdc` fields or an
isolated authoritative settlement/balance delta, plus the actual liquidity
role. Until then the fill remains fee-unresolved and blocks P&L and new orders.
Compare the standard [real-time order updates](https://docs.polymarket.com/trading/realtime-order-updates)
with the exact fields in [builder trades](https://docs.polymarket.com/api-reference/trade/get-builder-trades).

Builder attribution can add separate maker/taker fees. This freeze requires
both builder fee rates to be zero; otherwise preflight stops until those costs
are explicitly modeled and preregistered. Archive `mbf`/`tbf` alongside the
platform `fd` fields. See [Builder Fees](https://docs.polymarket.com/builders/fees).

The `< $1` test is an **admission gate for proposed orders**, never a filter on
reconciliation or recovery. Every actual fill is booked at its reported price
and fee, including a fill that makes the realized pair lose money. Once equal
confirmed UP and DOWN shares form an actual complete set, record the locked
profit or loss and merge that set when operationally available regardless
of its sunk acquisition cost. Do not report merge proceeds as realized or reuse
the collateral until confirmation. Stranding a complete set because it failed
the original gate would add exposure without undoing the loss.

Consequences:

- `p_UP + p_DOWN < 1` is necessary but not sufficient when either leg is a taker.
- Two taker legs at 0.49 + 0.49 cost roughly **1.01499 per pair** after fees,
  despite the apparent two-cent nominal discount.
- Two taker legs at 0.48 + 0.48 cost about **0.994944 per pair**, leaving only
  0.005056 before operations, latency, capital, and rounding reserves.
- A maker UP fill at 0.48 followed later by a maker DOWN fill at 0.51 costs 0.99
  and has one cent gross locked edge. It is profitable only if both fills occur
  and all remaining costs fit inside that cent.
- Maker rebates are variable, pro-rata, paid later, and subject to a minimum;
  they are tracked only when realized and never used to make a pre-trade loss
  look profitable. See [Maker Rebates](https://docs.polymarket.com/programs/maker-rebates).

Every pair is tested on its **marginal fill lots**. Averaging a new losing pair
into older cheap inventory is forbidden. Buying equal dollars is also wrong:
the guaranteed payout comes from equal shares.

## Two-lane policy

### LOCK lane — always higher priority

LOCK takes priority whenever **confirmed** inventory is imbalanced. A delayed or
live order, uncertain cancel, or `MATCHED`/`MINED` trade is a reservation, not a
confirmed pairable token: count its worst-case fill against risk and stage
nothing else until it reconciles. With no unresolved reservation, quote only the
missing outcome. Solve its bid ceiling from the oldest unmatched opposing lot,
the condition's current fee schedule, tick, operations reserve, and minimum
edge; do not exceed the confirmed pairable share quantity.

From balanced inventory, LOCK-only may also stage a deterministic **pair
candidate** before consulting the model: calculate exact 5-share post-only maker
prices for both outcomes from the same fresh snapshot, require both parent
orders to satisfy minimum/precision/$3/risk constraints, and require their
guarded sum to be strictly below $1. Submit only the cheaper side first (UP on a
tie). This makes the preregistered LOCK-only comparison executable, but the two
quotes are not atomic and the second price is not reserved. The first fill is
leg risk—not arbitrage P&L—until an admissible opposite fill confirms.

Equal confirmed balances first become **unmerged complete-set inventory**.
Submit merges in controlled batches, but do not count pUSD as released or reuse
the capital until the merge transaction is confirmed. Do not wait for resolution
merely to preserve a cosmetic position.

### MODEL lane — optional and strictly capped

Only when confirmed inventory is balanced, no LOCK pair candidate has priority,
no complete set is awaiting merge, and there are zero
delayed/live/cancel-uncertain/`MATCHED`/`MINED` reservations may a fresh,
calibrated probability model start one small UP or DOWN clip. A suitable target
is:

```text
P(Chainlink_end >= Chainlink_start |
  time remaining, distance from start, realized volatility,
  Chainlink/CEX basis, and order-flow features)
```

The official UP rule uses the specified Chainlink stream; a tie is UP. CEX
prices are features, not the settlement oracle. Parse and persist each market's
rules rather than hardcoding them. Polymarket exposes Chainlink and Binance
crypto streams through its real-time data service:
[real-time data](https://docs.polymarket.com/market-data/realtime-data).

The model changes **participation and price**, not clip size. An unfitted,
uncalibrated, future-dated, or stale signal creates zero directional exposure.
After a model-side fill, the state immediately returns to LOCK/balance-only.

## State machine

```text
DISCOVER -> BIND CONDITION/TOKENS -> SNAPSHOT BOTH BOOKS
    |
    +-- confirmed balanced + zero reservations + guarded two-quote candidate
    |       -> PAIR_START: STAGE CHEAPER 5-SHARE POST-ONLY LEG
    |
    +-- no pair candidate + confirmed balanced + calibrated model edge
    |       -> MODEL: STAGE ONE SMALL POST-ONLY ORDER
    |
    +-- ACK/live/delayed/MATCHED/MINED/uncertain
    |       -> RESERVED: RECONCILE BEFORE ANOTHER ORDER
    |
    +-- confirmed one-sided lot + zero unresolved reservations
    |       -> LOCK: STAGE ONE MISSING-SIDE ORDER
    |
    +-- equal confirmed shares -> UNMERGED COMPLETE SET
            -> MERGE SUBMITTED -> MERGE CONFIRMED -> pUSD RELEASED

Stale/regressive timestamp, inconsistent book hash, reconnect, uncertain cancel,
lifecycle/token-binding change, or cap breach -> CANCEL + REST/USER RECONCILE
```

There is at most one live staged order per condition. Every ACK creates a
reservation, and partial fills reduce that reservation rather than authorizing a
second order. A production preflight must include confirmed inventory plus the
worst-case fill of delayed, `MATCHED`/`MINED`, still-live, cancel-pending, and
otherwise uncertain orders before admitting anything new. Only `CONFIRMED`
tokens enter FIFO pair economics or a merge.

Persist each planner decision as an immutable admitted-order record before
submission: order ID, condition/token/outcome, limit, maximum cumulative shares,
expected liquidity role, post-only flag, and lane/reason. A real fill from an
unknown or mismatched order is still reconciled—it cannot be wished away—but it
latches an order-admission incident and authorizes nothing further.

“At different times” is an execution-state rule—separate staged orders with
reconciliation between them—not a requirement that two venue timestamps have
different displayed values. Timestamp precision can place sequential events in
the same bucket.

FIFO accounting must nevertheless be replay-deterministic. Persist a durable
local `reconciliation_index` after a frozen canonical sort of lifecycle phase,
source timestamp, transaction/trade identity, and tie-break fields. The public
stream does not document a venue sequence number. If reconciliation discovers a
new event earlier than the last applied index, stop and rebuild that condition's
ledger from its canonical journal rather than appending arrival-order economics.

These are per-condition controls, not a portfolio risk budget. Overlapping
5-minute/15-minute windows and correlated crypto assets can aggregate many small
clips into one large directional bet. A live allocator therefore also needs
owner-approved bankroll-relative global and per-asset caps over confirmed,
live, pending, uncertain, and paired-unmerged reservations. Those caps are not
set here, which independently keeps live authorization closed.

### Cutoff and terminal exposure policy

At the no-new-first-leg cutoff, cancel any unfilled PAIR_START or model-opening
quote and reconcile its ACK, fills, and cancel result; LOCK completion may
continue only inside its separate completion window. At the final completion
cutoff, cancel **every**
condition order and stop all new submissions. A cancel request is not a fact:
wait for its ACK and verify authoritative open-order and user-trade state while
holding the uncertain quantity at worst case.

This research implementation has **no taker completion or taker unwind path**,
so it must never improvise an emergency market order at the cutoff. Continue
reconciliation, merge every equal confirmed complete set and await merge
confirmation, then carry any residual confirmed imbalance through resolution
under the existing hard caps. Report its payoff as terminal directional P&L,
separate from locked-pair P&L. No new order is permitted after the final cutoff.

## Execution constraints from primary documentation

- Use post-only limit orders for the normal path. FOK/FAK legs are not atomic
  with each other; FAK can leave a partial first leg. See
  [place orders](https://docs.polymarket.com/trading/place-orders).
- Do not use GTD as the 60-second or 15-second cutoff mechanism. The venue
  subtracts a one-minute security threshold, rejects expirations less than
  three minutes out, and therefore has an effective minimum lifetime of about
  two minutes. Use explicit cancellation, heartbeat, and authoritative
  reconciliation for these short cutoffs: [place orders](https://docs.polymarket.com/trading/place-orders).
- Fetch `itode` for each condition. When enabled, a marketable taker order is
  held for **250 ms** before matching and cannot be cancelled during that hold;
  top-of-book is therefore not a fill guarantee. See
  [CLOB market info](https://docs.polymarket.com/api-reference/markets/get-clob-market-info).
- Trade states progress through `MATCHED → MINED → CONFIRMED`, with retry/failure
  branches. Pending fills reserve risk but are not reusable confirmed balance:
  [order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle).
- Matching-engine restart modes are explicit failure states. Treat HTTP `425` as
  a restart, honor the two-minute post-restart post-only period, and treat HTTP
  `503` according to its response body/`Retry-After` as cancel-only or post-only
  restricted mode. Cancel or reconcile first; never blindly retry a stale quote:
  [matching engine](https://docs.polymarket.com/trading/matching-engine).
- A heartbeat lapse cancels open orders. Use it as a dead-man switch, alongside
  explicit per-condition cancellation:
  [heartbeat](https://docs.polymarket.com/api-reference/trade/send-heartbeat).
- Polling is inadequate for execution. Stream both books and user events, and
  use the documented event timestamp and book hash; the public contract does not
  document a sequence number. After reconnect, stale/regressive timestamp, or
  hash inconsistency, suppress decisions, fetch fresh REST snapshots for both
  tokens, and rebind them before resuming. Current limits are documented at
  [rate limits](https://docs.polymarket.com/api-reference/rate-limits) and
  [per-signer trading limits](https://docs.polymarket.com/api-reference/trading-rate-limits).
- Merge/redeem actions must await confirmation. Keep equal confirmed tokens in
  an `UNMERGED` state and their capital reserved until the merge receipt is
  confirmed. Batch small pairs because the relayer has a much lower submission
  rate than the order API.
- Check the deployment location at runtime. Geographic availability changes and
  VPN bypass is prohibited: [geoblock API](https://docs.polymarket.com/api-reference/geoblock),
  [Geographic Restrictions help article](https://help.polymarket.com/en/articles/13364163-geographic-restrictions).

## What “thousands of times” can honestly mean

Four assets across 288 five-minute windows per day imply 1,152 scheduled market
windows/day before considering 15-minute markets or multiple fills. That makes
large sample collection operationally feasible; it does **not** imply 1,152
profitable opportunities or fills. Liquidity and executable edge must be
screened per condition.

Recent research reinforces the caution:

- A 2026 study of 75 million book snapshots found single-market executable
  anomalies rare and short-lived (seven episodes, median 3.6 seconds) in its NBA
  sample; shallow depth constrained most combinatorial opportunities:
  [Cheng, Yang & Zou](https://arxiv.org/abs/2605.00864).
- A 2026 Polymarket microstructure study found multi-second ingestion tails and
  warned that public-feed trade-direction inference poorly matched on-chain
  ground truth. Reconcile fills from authoritative user/on-chain events:
  [Dubach](https://arxiv.org/abs/2604.24366).
- A 2025 historical study found realized complementary-condition arbitrage, but
  its sample predates the current crypto fee regime, so its profit totals do not
  establish present-day edge:
  [Saguillo et al.](https://arxiv.org/abs/2508.03474).

Thousands of fills from adjacent five-minute windows are serially dependent.
Inference must aggregate or block-bootstrap by market window/day; treating every
clip as an independent observation would manufacture confidence.

## Data and validation plan

Historical midpoint data cannot reconstruct queue position, cancel races,
partial maker fills, or the taker delay. Before any promotion, prospectively
capture:

1. an immutable condition registry: `conditionId`, both token IDs and explicit
   outcome mapping, `negRisk`, `acceptingOrders`/lifecycle, start/end and
   measurement-window rules, settlement source, minimum size, tick, `itode`, and
   the then-current fee rate, exponent, and maker/taker treatment;
2. raw payloads for both outcome L2 books with source timestamp, local monotonic
   receive time, book hash, tick changes, disconnects, and resnapshot markers;
3. Chainlink settlement-source ticks and CEX auxiliary features;
4. every immutable admitted-order decision, order ACK and reservation, cancel
   request/result, partial/cumulative fill, and
   `MATCHED/MINED/CONFIRMED/FAILED` transition. Persist actual liquidity
   role and fee rate, then obtain the exact fee from builder-attributed trade
   fields or isolated settlement/balance reconciliation; the standard user
   event alone is insufficient. Also persist `UNMERGED`, merge
   submitted/confirmed, redeem, resolution, and capital-release transitions;
5. simulated queue-ahead and through-fill assumptions for shadow orders; and
6. all rejections, restarts, cutoff reconciliations, and other incidents, not
   only completed winners.

The preregistration's local SHA-256 test detects accidental Markdown/metadata
drift; it is not independent proof of freeze time. Commit or externally
timestamp the frozen artifact before the first prospective observation.

Report locked-pair profit separately from model-directional P&L, plus maker/taker
mix, fee burden, realized rebates, PAIR_START attempts/first fills/completion
rate, subminimum residuals, time/area unpaired, maximum worst-case imbalance,
fill-to-cancel ratio, conditional post-first-fill adverse selection, merge/redeem
latency, and stale-feed/legging incidents. Validate model calibration and Brier
score by asset and time-to-close before considering directional P&L.

## Repository implementation

Added:

- `research/complementary_outcome_inventory.py` — conservative `Decimal`
  preflight fee estimates, required authoritatively sourced exact taker fees for
  confirmed accounting, strict FIFO reconciliation of all actual complete sets,
  canonical replay indices, immutable admitted-order binding, idempotent
  fill/merge confirmations, incident-latched cap breaches, stale/timing checks,
  and a one-order maker planner.
- `tests/test_complementary_outcome_inventory.py` — 39 invariant tests, including
  fee traps, condition/token binding, cumulative parent-order caps,
  duplicate/conflicting fills, lifecycle reservations, model/balance priority,
  merge confirmation, stale/cutoff handling, and 2,000 small fills.

Not added:

- credentials, SDK dependency, venue writes, live runner, merge/redeem calls, or
  integration into the existing Binance/Bybit/Bitget engine;
- bankroll-relative global/per-asset risk caps or a multi-condition capital
  allocator;
- taker completion, taker unwind, or an emergency market-order fallback;
- any claim that a top-of-book touch would have filled; or
- a fabricated backtest from synthetic prices.

The clean production boundary is a separate prediction-market venue adapter,
complete-set ledger/state reducer, paired executor with reservations and
reconciliation, and calibrated fair-value model. Existing CCXT instruments and
single-position P&L contracts should not be overloaded.

## Verdict and next gate

The payoff identity is real and the requested control policy is now specified
and mechanically tested. Profitability is **INSUFFICIENT_DATA** until a
prospective L2 plus shadow-order archive demonstrates repeatable, after-cost
fills. No live or paper-order allowlist should be opened from this report.
