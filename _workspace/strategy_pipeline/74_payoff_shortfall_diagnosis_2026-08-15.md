# 74 — Payoff-shortfall diagnosis: 0.243 realized vs 0.35 "design"

**Type:** MEASUREMENT (no prereg — this decomposes an existing cohort, it
decides nothing). Read-only. **No config changed.**

**Trigger:** owner directive "wins land ~30% smaller than the geometry
specifies — FIX IT", after the 2026-08-14 council flagged realized payoff
0.243.

## Verdict: there is no execution defect. The comparison was mis-specified.

### What was actually measured (n=36 cohort, epoch 2026-08-12 03:03Z)

| Component | Measured | Read |
|---|---|---|
| **TP execution vs design** | **98.2%** (median 98.9%, n=30) | TPs fill essentially AT design. **Not the leak.** |
| **Stop overshoot** | mean **1.064×** intended | ~5 bps past the stop; 2 of 6 exited exactly at stop |
| **Payoff ratio, GROSS (pre-fee)** | **0.371** | ABOVE the 0.35 design |
| **Payoff ratio, NET (post-fee)** | **0.243** | the whole gap is fees |
| Fee as % of avg **win** | **29.0%** | small win, fixed-ish cost |
| Fee as % of avg **loss** | **8.1%** | large loss, same cost |

### Root cause

The "0.35 design" is a **pre-cost price ratio**; 0.243 is a **post-cost
dollar ratio**. Comparing them was guaranteed to show a gap. Gross payoff is
0.371 — the geometry delivers slightly BETTER than spec. A round-trip fee
consumes 29% of a 0.315%-of-notional win but only 8.1% of a
0.95%-of-notional loss, and that asymmetry alone carries 0.371 → 0.243.

**A fee is not a defect.** Nothing in the execution path is broken.

### Defect candidates checked and ELIMINATED

- **Slippage double-charge on stops:** `net == gross − fee` exactly on all 6
  stop exits (residual 0.00000); the `slippage` column is 0.0 and the slipped
  fill is already inside `exit_px`. No double charge.
- **Sizing asymmetry:** wins avg $94.44 notional vs losses $76.65. At n=6
  losses this is noise, and it *flatters* the gross ratio — excluded from any
  conclusion.
- **TP under-fill:** refuted at 98.2% of design.

## What must NOT be done about it

1. **Do not widen TP.** Screen 68 (prereg `4a848c84…`, ledger row
   2026-08-12) already swept k ∈ {0.35…3.0} with full costs: **0/10 arms
   positive, monotone decay, no optimum inside or outside the grid.** That
   ledger row's own terms require a NEW hashed prereg for any TP change.
2. **Do not lower `FEE_BPS_PER_SIDE`.** It is a PAPER assumption; editing it
   improves the printout and changes nothing real.

## The one genuine lever (NOT taken here — needs owner sign-off)

**Maker-first execution.** At zero fees the gross ratio 0.371 implies
breakeven **72.9%** vs the observed 83.3% — a **+10.4pp** margin instead of
the current +2.9pp. That is by far the largest available improvement, and the
F1 lane already has a maker-first implementation to model on.

It is a real execution change (fill risk, partial fills, queue position), it
would **reset the cohort**, and it must be measured as a separate labeled
arm. Recorded as a candidate, not implemented.

## Bearing on the live-switch question

The +2.9pp margin over realized breakeven (80.4%) is **0.44 SE** and, per the
2026-08-14 council arithmetic, does not resolve at n=100/140/200. This
diagnosis does not change that: the cohort is still not evidence of
profitability.
