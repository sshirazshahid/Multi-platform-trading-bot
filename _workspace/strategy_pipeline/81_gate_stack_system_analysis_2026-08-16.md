# 81 — The gate stack as a system: 71 rejection paths, 1 trade per 2,826 candidates

**Type:** SYSTEM ANALYSIS. Read-only. **No config changed.**

**Trigger:** owner — "Examine it ultrathink", after three *different* blockers
were diagnosed in three days (stale latch -> ADX conflict -> BTC-vol veto).
The question: coincidence, or a system property?

**Relation to prior work:** `46_decision_funnel_27_of_7943` audited a 6h
SCALP window (n≈7,969) and found labeling bugs. This examines the whole
stack over 859,117 candidates / 30 days, and asks a different question: what
does the conjunction cost, and what does it buy?

## 1. The gate surface, counted from source

| stage | file | distinct rejection paths |
|---|---|---|
| scoring | `core/scoring/entry_score.py` | **15** |
| execution | `core/engine/entry_exec.py` | **56** |
| **total** | | **71** |

## 2. The measured funnel (30 days)

| stage | count | survival |
|---|---|---|
| candidates scored | 859,117 | — |
| -> ALLOW | 34,306 | **3.99%** |
| -> OPENED | 304 | **0.89% of ALLOWs** |
| -> winning trades | 147 | 48.4% of opens |

**End-to-end 0.0354% — one trade per 2,826 candidates.** One *winning* trade
per 5,845.

## 3. The arithmetic fits almost exactly

Solving `p^N = 0.000354` for independent gates each passing with prob `p`:

| gate permissiveness | implied N |
|---|---|
| 90% | **75.4** |
| 80% | 35.6 |
| 70% | 22.3 |
| 50% | 11.5 |

**71 coded rejection paths ≈ 75 effective 90%-permissive gates.** No single
gate is pathological; the funnel is the *product* of many individually
reasonable filters.

## 4. The gates are INDEPENDENT, not conspiring (tested)

The natural hypothesis — gates anti-correlated, so satisfying one makes
another harder — is **false**. Over 417,224 feature-carrying candidates:

| | |
|---|---|
| pass ADX window 15-30 | 54.6% |
| pass vol_ratio >= 0.7 | 56.2% |
| **pass BOTH (observed)** | **30.5%** |
| pass BOTH (if independent) | **30.7%** |

Indistinguishable. They also fire on genuinely different market states
(median ADX: chop 13.3, req_fail 20.4, analysis_only 25.9), so they are not
one condition wearing several names.

**This matters:** the stack is not broken. It behaves exactly as a
conjunction of independent filters must. There is no redundancy to remove.

## 5. The finding that actually matters

A multiplicative funnel is only worth its cost if each gate **selects**.
Tested on the trades that survived all 71:

| window | n | WR | mean PnL |
|---|---|---|---|
| 30d | 304 | 48.4% | -0.1765 |
| 90d | 2,230 | 35.1% | -0.2756 |
| all | 2,604 | 35.3% | -0.2761 |

The bracket geometry (TP ≈ 0.35x SL) needs **~74-80% WR** to break even.
Survivors of all 71 gates win **35-48%**.

**The stack costs 99.96% of flow and delivers a win rate far below
breakeven** — an enormous selectivity price for negative selection value.

## 6. Interpretation (the honest one)

This is **not** an argument for removing gates. Every gate examined is
individually evidence-backed: `band_regime_filter` refuses trades measured at
-19.9/-25.3 bps (artifact 73), the ADX bounds come from screen 13, the budget
cap bounds research cost. Removing them makes losses *faster* — artifact 73
demonstrated that empirically.

It is an argument that **the gate stack is compensating for a signal with no
edge.** 71 filters exist because each was added to stop a specific observed
bleed. That is what building rails around a non-predictive signal looks like
from the inside: every rail justified, the sum a system that almost never
trades and still loses when it does.

The three blockers in three days were therefore neither coincidence nor bugs
— they are what a 71-deep conjunction looks like sampled on three different
days. **On any given day something is binding.** Fixing the binding one
promotes the next.

## 7. What follows

- **Do NOT loosen gates to restore flow.** Measured: the blocked trades lose.
- **Do NOT read a future flow recovery as improvement.** It only means the
  market moved into the narrow window the conjunction allows.
- **The only exit is a signal with positive expectancy.** With one, the stack
  could be *simplified*, because rails would stop doing the work of
  compensating for absent edge. Without one, no arrangement of 71 gates
  produces profit — this artifact is the quantitative proof.
- Corollary for prioritisation: work that adds gates or tunes thresholds has
  a measured ceiling of zero. Work that tests for edge does not.
