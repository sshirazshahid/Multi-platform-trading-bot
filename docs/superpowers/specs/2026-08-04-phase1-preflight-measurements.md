# Phase 1 pre-flight measurements (de-emotion surgery)

**Date:** 2026-08-04 · **Status:** measured, pre-implementation
**Plan:** De-Emotion Overhaul + Full Decomposition, Phase 1

Phase 1 deletes the sentiment layer from the entry path. Two objections were
raised in design review that had to be answered with data, not assumption,
*before* the code is cut:

1. **The vetoes BLOCK entries.** On a lane with no validated edge, removing a
   block admits more negative-expectancy trades. Deletion could be a
   measurable regression, not an improvement.
2. **`sources_ok` counts news and fear/greed.** Removing two sources from the
   tally could push cycles under the fail-closed floor, producing zero trades
   with no error and no crash.

Both were measured on this box against the live warehouse and a live fetch.

## Result 1 — the sentiment paths are inert, measured over 30 days

Read-only query over `candidates.features_json`, 30-day window:

| Path | Recorded? | Firings | Verdict |
|---|---|---|---|
| V2 news veto (`news_veto`) | yes — 14,048 of 40,000 sampled rows carry the key | **0** of 690,079 candidates | inert |
| B13 panic branch (`crowd_signal == "panic"`) | yes — 618,135 rows | **0** | inert |
| V4 FOMO size multiplier (`crowd_signal == "fomo"`) | signal recorded; `_fomo_mult` itself never persisted | **0** — `crowd_signal` is `neutral` in **618,135 / 618,135** rows (100%) | inert |

`crowd_signal` has exactly one observed value in 30 days: `neutral`. Both the
panic branch and the FOMO multiplier are gated on other values, so neither has
ever changed a decision or a size.

`news_sentiment` does vary — 610,704 rows are exactly 0.0 (99.3%), with a
~1.2% non-zero tail (1.0, 0.5, −1.0, 0.333, 0.25). But it feeds only (a) the
veto, which never fired, and (b) the warehouse feature log. No promoted model
consumes it (no model pointer passes the gate), so dropping the column costs
nothing that is currently read.

**Honest expectation to record at Phase 1 commit:** deleting these three paths
is expected to be **behaviourally inert** — it does not admit more trades, and
it does not change sizing. It is dead-weight removal, not a strategy change.
This is the claim to falsify later, not a claim of improvement.

Caveat on scope: `_fomo_mult` is a **sizing** multiplier, and the standing
WR-floor rule requires flagging anything that can change sizing. It is flagged
here and cleared on the evidence above — `crowd_signal` never left `neutral`,
so the multiplier was always exactly 1.0.

## Result 2 — `sources_ok` has ample headroom

Live fetch at 2026-08-04 01:35 (`_fetch_all_data(["BTC","ETH"])`):

```
sources_ok = 7      crypto ✓  gecko ✓  news ✓  fng ✓  funding ✓  orderbook ✓  oi ✓
```

All seven sources succeeded. Removing `news` and `fng` leaves **five**, against
an abort threshold of **`< 2`**. The failure mode (dropping under the floor and
silently trading nothing) does **not** apply.

Two things this measurement also settled:

- The gate is **live**, not dormant: `core/mcp_strategy_scorer.py:143` applies
  `if data.get("sources_ok", 0) < 2` on the `SIGNAL_SOURCE=mcp` path the bot is
  actually running. It was worth verifying rather than assuming.
- Phase 1 must still **re-derive the tally consciously** (5 sources, threshold
  unchanged at 2) and keep the abort. Never delete the abort.

Cosmetic defect noticed in passing: the fetch log prints `7-source fetch: 7/6
OK` — the denominator is stale/off by one. Harmless, worth fixing while in the
file.

## What is still NOT measured

The contamination guard in the SP1 verdict agent is **inert**: `trades` stores
`entry_px` and `entry_stop_px` but no planned target price, so planned R:R
cannot be recomputed per trade. Persisting a planned-target column on entry
would make that guard live, and would also let the geometry studies verify
their own cohorts. Out of scope for Phase 1; recorded so it is not forgotten.
