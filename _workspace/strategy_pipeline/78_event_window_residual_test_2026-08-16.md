# 78 — Event-window residual test: INSUFFICIENT_DATA (structural, n=0)

**Type:** MEASUREMENT (the council's falsifier for a qualitative/event layer).
Read-only. **No config changed.**

**Trigger:** council 2026-08-16 (TradingAgents adoption). The Pragmatist's
stated falsifier for its own "adopt nothing" position:

> "If the missing edge is event context the numeric scorer cannot see —
> listing, unlock, exploit — I'm wrong. The falsifier is specific: measure
> residual return inside those event windows against the scorer's own
> decisions. Materially non-zero residual earns the qualitative layer a
> hearing."

## Result: the test cannot run. Not "no effect" — **no observations.**

| | |
|---|---|
| Closed trades available | **2,547** |
| Event anchors (unlock + listing) | **514** across 164 bases |
| Trades INSIDE a ±7d event window | **0** |
| Trades OUTSIDE any event window | 2,547 (mean **−38.6 bps**, WR 36.1%) |

**Verified this is not a join bug.** 8 bases genuinely overlap (APT, ARB,
GRASS, HBAR, JUP, MANTA, SEI, SUI) and the date ranges intersect. The cause is
proximity, not matching:

| base | events | trades | closest trade-to-event gap |
|---|---|---|---|
| GRASS | 4 | 3 | **153.6 days** |
| JUP | 4 | 63 | 526.0 days |
| HBAR | 2 | 19 | 551.1 days |
| SUI | 16 | 8 | 700.5 days |
| ARB | 2 | 134 | 747.0 days |
| MANTA | 2 | 1 | 809.7 days |
| APT | 4 | 93 | 846.6 days |
| SEI | 2 | 2 | 962.8 days |

The nearest approach in the entire dataset is **153.6 days**. The unlock
calendar is overwhelmingly *historical* (2023-06 → 2024) while live trading
only began **2026-03-29**.

## Why forward accrual will not rescue it soon

- **Future unlock events on the calendar: 6.** On bases the bot actually
  trades: **0**.
- Listings accrue at ~1.36/day, but the listing probe's universe barely
  intersects the 44-base trading spec, and listing events are precisely where
  the bot's `tradfi_asset` / thin-book gates reject entries.

So the qualitative layer's hearing is not *denied* — it is **unscheduled**.
Nothing can be concluded either way until trades and events coincide.

## Verdict

**INSUFFICIENT_DATA (structural).** Per pipeline convention this earns NO
refutation row — recorded here and in the ledger's "Open" section only.

The Pragmatist's falsifier stands unfalsified and unconfirmed. It does not
license adopting an LLM/event layer (the 2026-08-16 ledger row governs that),
and it does not refute one either.

**Reopen condition (frozen):** ≥30 closed trades whose entry falls within ±7d
of a logged unlock or listing event on a traded base. At the current
zero-overlap rate that requires either (a) the unlock calendar backfilled
forward onto the 44-base trading spec, or (b) the trading universe widened to
the event-probe universe. **Neither is authorized by this measurement** —
both are entry-selection changes needing their own pre-registration.

## Honest note on the control arm

The 2,547 "outside" trades average **−38.6 bps** with 36.1% WR. That is the
already-known no-edge baseline restated on a different slice. It is not
evidence about events; it is a control with nothing to compare against.
