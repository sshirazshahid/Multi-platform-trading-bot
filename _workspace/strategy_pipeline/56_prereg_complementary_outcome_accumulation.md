# 56 — Preregistration: Complementary Outcome Accumulation

**Status:** FROZEN before prospective outcome/fill collection  
**Date:** 2026-08-01  
**Family:** `complementary_outcome_accumulation_v1`  
**Venue assumption:** Polymarket standard binary crypto UP/DOWN  
**Expected verdict:** `INSUFFICIENT_DATA` until prospective L2 + shadow fills exist

## 1. Hypotheses

- **H0 LOCK:** fee- and reserve-adjusted completed-pair P&L is not positive after
  realistic queue/partial-fill and leg-risk treatment.
- **H1 LOCK:** the lower 95% confidence bound of net locked-pair P&L is positive
  when aggregated by independent market window/day.
- **H0 MODEL:** the calibrated directional lean does not improve net P&L over the
  lock-only policy.
- The LOCK lane may pass with the MODEL lane disabled; model profit may never
  subsidize a negative complete-set edge.

## 2. Frozen core economics

For each marginal FIFO-matched UP/DOWN lot:

```text
preflight_taker_fee_i = shares × 0.07 × price_i × (1-price_i),
                        exponent 1 and rounded conservatively
preflight_pair_cost = price_up + estimated_fee_up/shares
                    + price_down + estimated_fee_down/shares
guarded_pair_cost = preflight_pair_cost
                  + 0.001 operations reserve + 0.005 required edge
admit a new pair-completion order iff guarded_pair_cost < 1.00
```

The frozen expected crypto schedule is rate `0.07`, exponent `1`, and
taker-only platform fees, with both builder maker/taker fee rates frozen at
zero. Fetch and archive the fee schedule for each condition before quoting; a
missing or changed rate, exponent, maker/taker treatment, fee precision, or a
nonzero builder fee fails closed instead of silently using the expected values.
Maker rebate is zero in the pre-trade calculation and enters reporting only
when actually paid.

The preflight curve is an admission estimate, not an observed fee. Every
confirmed taker fill requires an exact fee from a documented authoritative
source: the builder-attributed trade `fee`/`feeUsdc` field or an isolated
settlement/balance reconciliation. Standard account-trade/user-stream fields
such as `fee_rate_bps` do not themselves report the exact charged amount.
Without one of the exact sources, keep the fill fee-unresolved, reserve its full
risk, and block P&L/new orders. An estimate may never be relabeled as paid.

Admission governs new orders, never whether an authoritative fill is recorded.
Every actual fill is reconciled. If actual confirmed UP and DOWN quantities form
a complete set but fail the frozen admission threshold, merge the complete set
anyway to contain exposure, book its actual loss or shortfall, and latch an
incident that blocks new orders pending review. Never discard a real fill or
leave an available complete set unmerged merely to preserve reported edge.

The LOCK-only arm may start from balanced inventory without a model only when
the same fresh two-token snapshot supplies exact 5-share post-only maker
candidates for both outcomes, each candidate independently satisfies minimum,
precision, $3 parent-order, and risk constraints, and their guarded pair cost is
strictly below $1. Submit only the cheaper candidate first (UP wins an exact
tie), then return to balance-only after any confirmed fill. The unsubmitted
second quote is hypothetical: the first fill creates leg risk and no profit is
locked until equal opposing fills are confirmed.

## 3. Frozen control limits

| Control | Value |
|---|---:|
| Normal/max clip | 5 shares |
| Max clip acquisition cost | $3 |
| Conservative preflight fee quantum | 0.00001 USD |
| New-order size quantum | 0.01 share |
| Max unmatched directional inventory | 20 shares |
| Max unmatched worst-case cost | $12 |
| Aggregate asset/portfolio live cap | UNSET — live blocked |
| Min calibrated model edge | 0.01 probability |
| Model clip after edge gate | exactly 5 shares |
| Minimum assumed future complement price | 0.05 |
| Max model-signal age | 2 s |
| Max live staged orders/condition | 1 |
| Max book age | 2 s |
| Max UP/DOWN book timestamp skew | 0.5 s |
| Stop new PAIR_START or model legs | 60 s before window end |
| Stop pair-completion buys | 15 s before window end |

The market's current minimum size and tick override only in the safer direction.
The model clip is fixed: once edge is at least `0.01`, submit exactly 5 shares or
do not trade. Never scale size with model strength, and do not resize around a
market minimum, notional cap, or precision constraint. A tick, fee, identity, or
lifecycle change invalidates outstanding quote calculations.

The per-condition staged `ExecutionState` must show zero live-order quantity,
zero pending-fill quantity, and zero uncertain-cancel quantity before any new
order is admitted. Submission creates at most one live order. Partial fills,
`MATCHED/MINED` quantities, cancel requests without authoritative confirmation,
and worst-case remaining live quantity continue to reserve their full risk.
“Different times” means separate staged order/lifecycle phases; do not require
unequal venue timestamp values, whose resolution may place sequential events in
the same timestamp bucket.

The frozen dollar/share limits are per condition. They do not authorize many
overlapping conditions: before any live promotion, the owner must freeze
bankroll-relative aggregate and per-asset caps that include every confirmed,
live, pending, uncertain, and paired-unmerged reservation. Until then only
isolated research/shadow evaluation is allowed.

## 4. Model freeze

Target `P(Chainlink_end >= Chainlink_start)` from information timestamped no
later than the decision. Candidate feature families are time remaining,
distance from the Chainlink start anchor, realized volatility, Chainlink/CEX
basis, and order-flow state. Fit/calibrate only on the training segment.

- Chronological 60/20/20 train/validation/test split by complete market window.
- Purge overlapping 5m/15m windows and embargo adjacent feature lookbacks.
- Choose no parameters from the final test segment.
- Uncalibrated or stale model = zero lean.
- Bind every signal to the exact condition ID, token/outcome map, settlement
  source, and measurement-window timestamps used to train and score it. A signal
  for another condition or window is invalid even if its asset label matches.
- Once the chosen outcome's edge is at least `0.01`, the model may start one
  fixed 5-share clip subject to every other gate and only when no LOCK pair
  candidate has priority. Larger edge changes neither clip size nor directional
  cap.

## 5. Fill/replay assumptions

- Post-only maker is the default. A quote touch is **not** a fill.
- Shadow maker fill requires a documented queue-ahead/through-trade rule and is
  sensitivity-tested pessimistically.
- A balanced LOCK pair candidate is two hypothetical passive quotes, not an
  atomic trade or locked edge. Attribute its first-leg fills, completion rate,
  conditional adverse selection, time/area unpaired, and terminal residual loss
  to LOCK. A confirmed partial below the venue order minimum may be impossible
  to complement exactly; never round it into a new directional clip.
- Taker completion is recorded only at walked executable depth, exact fee, and
  observed per-condition `itode` delay; two legs are never modeled as atomic.
  The preflight fee estimate reserves risk, while an exact builder-attributed or
  settlement-reconciled fill fee replaces it for accounting. The normal user
  stream's fee rate alone is insufficient. A confirmed taker fill without an
  exact authoritative fee remains unresolved and cannot produce realized P&L.
- Maintain one condition-bound `ExecutionState` containing authoritative token
  mapping; live, pending, uncertain-cancel, confirmed-unpaired, and
  confirmed-paired-but-unmerged quantities; merge-pending quantity; and a
  latched-incident flag. Zero live/pending/uncertain quantity is required before
  any next staged order.
- Journal confirmed fills in a deterministic canonical replay order and assign
  a durable local `reconciliation_index`. The venue's public stream does not
  promise a sequence number, so derive ordering from archived lifecycle phase,
  source timestamp/transaction/trade identity with a frozen tie-break. A newly
  discovered earlier event requires a full condition-ledger rebuild; never
  append it behind later FIFO matches.
- Confirmed, pending (`MATCHED/MINED`), live, uncertain-cancel, and unmerged
  quantities all consume risk/collateral headroom. Duplicate identical events
  are idempotent; conflicting duplicates fail closed.
- Reconcile every authoritative fill even when it violates the order admission
  estimate or arrives after a cancel/cutoff. Allocate every confirmed complete
  set, including an unprofitable one, to merge; latch any admission, identity,
  fee, lifecycle, or cap breach as an incident and block further orders.
- Before submission, persist an immutable admitted-order record binding order
  ID, condition/token/outcome, BUY side, limit price, maximum cumulative shares,
  expected maker/taker role, post-only flag, and lane/reason. Reconcile an
  unknown or mismatched real fill anyway, then latch an order-admission incident;
  never let an arbitrary order ID inherit a valid planner decision after fact.
- Track `confirmed_paired_unmerged` and `merge_pending` explicitly. Submit merge
  for only `min(confirmed_up, confirmed_down)`. Move equal shares from unmatched
  lots into paired-unmerged accounting and record their guaranteed terminal
  edge, but do not report merge proceeds as realized, release collateral, or
  reuse pUSD until authoritative merge confirmation. Failed/uncertain merge
  state remains reserved and latched.
- At 60 seconds before the measurement-window end, cancel any working
  PAIR_START/model first-leg order and admit no new first leg. LOCK completion
  may continue only after the cancellation is authoritative and state is
  reconciled. At 15 seconds, cancel
  every remaining working completion order and admit no new order. A cancel
  request alone does not release its reservation; late fills still reconcile.
- Do not rely on GTD to implement either short cutoff: its current minimum
  effective lifetime is about two minutes. Use explicit cancel plus heartbeat
  and authoritative open-order/user-event reconciliation.
- Terminal policy is cancel, reconcile, merge every confirmed complete set, then
  hold any residual unmatched confirmed outcome to resolution in the research
  replay. Do not invent an unregistered emergency taker or SELL. Charge the
  residual to failed-leg/model directional P&L and worst-case risk, and keep an
  execution-failure incident latched until final resolution and reconciliation.

## 6. Frozen identity and archive contract

Before the first decision for each market window, archive an immutable discovery
snapshot plus source and local-receive timestamps containing:

1. exact `conditionId` and the authoritative outcome-label-to-token-ID mapping,
   including which token is UP and which is DOWN;
2. resolution question/rules, settlement oracle, tie rule, `negRisk`, and the
   exact Chainlink measurement-window start and end (Gamma creation `startDate`
   is not a substitute);
3. `acceptingOrders`, lifecycle/end state, CLOB `itode` taker-order-delay flag,
   tick size, minimum order size, and all price/size/order precision rules;
4. full fee metadata: platform maker/taker fees, fee-curve rate `0.07`, exponent
   `1`, taker-only flag, builder maker/taker fee rates (both expected zero), fee
   rounding/precision, effective time, and raw response;
5. pUSD contract/address and unit/decimal precision, plus merge/redeem quantity
   precision; and
6. every later identity, lifecycle, tick, order-precision, fee, or measurement-
   window change as a versioned event that cancels current calculations and
   forces reconciliation.

An order, signal, fill, pair allocation, merge, redemption, or resolution event
without the same archived condition/token/outcome identity fails closed. Merely
having two distinct token IDs does not prove they are a complementary pair.

## 7. Required prospective sample

All of the following before a promotion decision:

1. at least 30 calendar days spanning multiple volatility regimes;
2. at least 1,000 fully observed resolved market windows;
3. at least 2,000 completed shadow pairs under the frozen fill rule;
4. no missing losing/unfilled/incident observations; and
5. every market rule, fee schedule, tick, minimum, and settlement source archived.

If the fill count is lower, verdict is `INSUFFICIENT_DATA`, not `NO_GO` and not
permission to loosen the fill model.

## 8. Joint promotion gates

LOCK lane must clear every gate:

1. strict marginal all-in pair invariant has zero violations;
2. lower 95% block-bootstrap confidence bound of after-cost locked P&L > 0;
3. positive net P&L after fees, operations reserve, failed-leg losses, merge
   costs, and capital opportunity cost;
4. zero unresolved one-leg, cancel-uncertainty, settlement, or reconciliation
   incidents, zero unconfirmed merge/redeem proceeds counted as available, and
   every incident latch explained and cleared only by authoritative state;
5. share, cost-at-risk, per-clip, stale-data, and lifecycle caps have zero
   violations; and
6. result remains positive under pessimistic queue and one-tick adverse
   sensitivity.

MODEL lane additionally requires calibrated Brier/reliability evidence on the
untouched test set and non-negative lower-bound incremental P&L. Otherwise ship
LOCK-only with model lean hard-disabled, subject to owner approval.

## 9. Forbidden

- Counting nominal `p_up+p_down<1` without exact fees.
- Counting a two-quote PAIR_START candidate as filled, atomic, or locked profit.
- Averaging a losing marginal pair into an older winning cost basis.
- Counting maker rebates before cash receipt.
- Equal-dollar balancing; balances are equal shares.
- Market fallback, martingale, catch-up sizing, or raising clips after a miss.
- Treating FOK/FAK legs as atomic or cancel requests as confirmed cancellations.
- Pairing fills without exact condition/token/outcome identity, or discarding an
  actual fill because it failed the intended-order admission gate.
- Relabeling a preflight fee estimate as an observed fee, or recording a
  confirmed taker fill as realized from `fee_rate_bps` alone without its exact
  builder-attributed or settlement-reconciled fee.
- Treating paired-unmerged tokens as destroyed, reporting merge proceeds as
  realized, or reusing their collateral before merge confirmation.
- Scaling the fixed 5-share model clip up or down with model strength.
- Treating per-condition caps as an aggregate portfolio authorization.
- Using Gamma creation `startDate` as the Chainlink comparison-window start.
- Promoting from synthetic/midpoint-only data or from correlated fill count.
- Wiring live venue writes from this preregistration.

## 10. Current status

The accounting/control implementation has 39 passing invariant tests, including
a deterministic 2,000-fill sequence. That proves implementation geometry only.
It is not market evidence. The companion SHA-256 guard proves local file/metadata
consistency, not that a third party observed this freeze time; commit or
externally timestamp it before prospective collection. Current verdict remains
`INSUFFICIENT_DATA`.
