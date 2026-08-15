# 76 — Why zero trades for 38h: the scorer and the veto want opposite tapes

**Type:** DIAGNOSIS. Read-only. **No trading config changed.**

**Trigger:** owner — "more than 24 hours and no trades … identify and fix it."

## The finding

Two gates, both individually defensible, are **structurally opposed**:

| Gate | Rule | Where |
|---|---|---|
| MCP scorer | requires trend: `adx_4h >= 20`, EMA alignment; rejects `adx_4h < 15` as chop | `core/scoring/entry_score.py:65` |
| Band regime veto | rejects `adx_4h > 30` as toxic | `core/engine/gate_health.py:49` |

The scorer is a **trend-seeker**. The veto is a **trend-avoider**. So the
scorer's own best candidates land exactly where the veto fires.

**Measured over the last 24h:**

- Candidates scored: **36,270** → `ALLOW` **2,531**, `SKIP` 33,739
- Of the 2,531 that PASSED scoring, **ADX median = 34.2** (p25 26.6, p75 37.7)
- **65.4% of approved candidates had ADX > 30** → vetoed at execution
- Post-approval block reasons: **1,743 of 1,786 = `band_regime_filter`**
  (1,440 adx>30 + 303 btc_vol<0.7); only 43 were anything else
- Opens: **0**. Last open 2026-08-14 08:51 (38h before this diagnosis).

The universe itself is not the problem — across all symbols only 35.7% of
ADX readings exceed 30. The problem is **selection**: the scorer concentrates
its output into the vetoed band (65.4% vs a 35.7% base rate).

## Why this is NOT simply "turn the veto off"

The veto is not arbitrary. Pre-registered screen `13_band_conditional`
(14,555 resolved outcomes, Bonferroni m=16) measured:

- `adx_4h > 30` → band WR **59.0%** (n=5,352) vs 65.7% baseline
- `btc_vol < 0.7` → band WR **55.6%** (n=3,203)

And the 2026-08-15 replay (`73_*`) resolved 312 of the actually-blocked
entries: **−19.9 bps (buy) / −25.3 bps (sell)**, both CIs excluding zero.
The veto is refusing trades that were measurably losing.

**Both gates are right about their own question.** The scorer correctly
identifies trend setups; the veto correctly reports that this system loses
money on strong-trend tapes. Their conjunction is what's broken — and the
honest reading is that **the scorer is selecting for the exact regime in
which it has no edge.** That is a signal-quality finding, not a plumbing bug.

## Options, with what each costs

1. **Leave it.** Zero trades while ADX stays elevated. Costs: cohort stalls
   (n=36, needs ~140 for power). Gains: no measurably-negative trades taken.
2. **Disable the veto.** Restores flow immediately. Costs: −19.9/−25.3 bps
   per replayed entry; contaminates the n=36 cohort; contradicts a
   pre-registered screen. **Not recommended** — evidence points the other way.
3. **Narrow the scorer instead** (require `20 <= adx_4h <= 30` at scoring
   time). Aligns the two gates so the scorer stops proposing trades that will
   be vetoed. Costs: a NEW pre-registration — this changes entry selection,
   which is exactly the kind of change the ledger requires evidence for.
   Honest expectation: it raises flow, not edge.
4. **Accept that the tape is the constraint.** ADX>30 is a market state, not
   a defect. It will pass.

## Recommendation

**Option 1 (leave it) until the tape turns, and pre-register option 3 if the
owner wants flow restored on evidence rather than by flag-flip.** Nothing in
the measurement supports disabling a veto that is demonstrably saving money.

## Related alert fix (same day)

`model_gate_starving` was emailing hourly for 38h claiming "the model gate may
be starving for signal" — a mis-diagnosis: the gate was fine, a deliberate
measured veto was doing its job. The watchdog now names the dominant blocker
and stays silent when it is a `DELIBERATE_ENTRY_BLOCK`, while still alerting
loudly on **unexplained** idleness (the genuinely diagnostic case).
