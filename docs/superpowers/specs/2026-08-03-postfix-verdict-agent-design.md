# Post-Fix Verdict Agent — Design

**Date:** 2026-08-03 · **Status:** awaiting owner approval · **Sub-project 1 of 3**
(SP2 reliability repairs and SP3 microstructure harvester get their own specs.)

## Purpose

On 2026-08-02 17:25:14 the bot restarted with `ACCURACY_TARGET_MODE=false`,
ending a geometry that compressed take-profit to ~0.39x the stop and required a
72% win rate to break even. Every trade since gets tier geometry (measured:
R:R 1.33 on the first fills, needing ~43%).

This agent answers one question daily, automatically, in plain language:
**is the new geometry working, not working, or still too early to call?**
It reports to the owner; it never acts. Humans decide.

## Cohort definition (the correctness-critical part)

- A trade belongs to the cohort iff its **entry** occurred at or after the fix
  epoch. Implementation MUST call `performance_summary(...,
  window_column="ts_entry", since_epoch=FIX_EPOCH)`. The default exit-time
  window would leak pre-fix trades that closed post-fix into the cohort —
  caught in design review; pinned by a test.
- `FIX_EPOCH` is a named constant documenting its provenance (supervisor
  respawn logged 2026-08-02 17:25:14 local). A test pins its value.
- **EPOCH UPDATE (2026-08-03, owner-directed):** the tier-geometry time-exit
  hold (`TIER_GEOMETRY_TIME_EXIT_HOLD`, config.py) activated at the
  2026-08-03 22:42:57 +05:00 respawn (~unix 1785778977) changed exit policy
  mid-stream. `FIX_EPOCH` MUST be that second respawn: geometry-v1 trades
  (2026-08-02 17:25 → 2026-08-03 22:42, resolved n=10: 3W/7L, −$1.12, 4
  STALE, 0 full TP) are a CLOSED mini-cohort and must never be pooled with
  v2. The `TIMEOUT_INTERFERENCE` flag stays: v2's prediction is that STALE
  share collapses; if it does not, the hold's R:R/scalp exemptions leak.
- **Self-validating cohort:** the script independently recomputes planned R:R
  for every cohort trade from entry/stop/target. If any trade shows R:R < 1.0
  (compressed geometry), verdict becomes `COHORT_CONTAMINATED` with the
  offending trade ids listed — protection against a future regression that
  re-enables compression mid-cohort.
- Provenance inheritance: `performance_summary` already excludes rows without
  `decision_id` and the manual/reconcile/reconciled_exchange families, so
  adopted ("fake") exchange trades cannot contaminate the verdict.

## Verdict rules (frozen; no post-hoc reinterpretation)

Evaluated in order; first match wins:

| verdict | condition |
|---|---|
| `COHORT_CONTAMINATED` | any cohort trade with planned R:R < 1.0 |
| `TOO_EARLY` | resolved n < 30 — report n, accrual rate/day, ETA to 30 |
| `WORKING` | n >= 30 AND net_after_cost_pnl > 0 AND realized payoff >= (1-w)/w, where w = decisive win rate wins/(wins+losses) |
| `NOT_WORKING` | n >= 30 AND net_after_cost_pnl <= 0 |
| `MIXED` | n >= 30, remaining cases (net > 0 but payoff below requirement) |

Modifier flag, independent of verdict: `TIMEOUT_INTERFERENCE` when resolved
n >= 10 AND STALE-exit share > 40% — the wider targets need longer to reach,
and early evidence (first 2 post-fix closes were both STALE) suggests the
stale timeout may harvest positions before either barrier is hit. The flag
reports; retuning any timeout is out of scope and needs its own evidence.

Every verdict ships with the Wilson 95% CI on win rate (already computed by
`performance_summary`) so a marginal call reads as marginal.

## Architecture

One new script, `scripts/report_postfix_verdict.py`, deliberately thin:

1. Open warehouse read-only (`mode=ro` + `PRAGMA query_only=ON`).
2. Call the existing `performance_summary()` (reuses this week's breakeven,
   payoff, drawdown, and CI machinery — no re-implementation).
3. Add: exit-path breakdown (STALE share), cohort R:R validation, verdict.
4. Write `data/postfix_verdict.json` via existing `atomic_write_json()`.
5. Deck reads it through the optional-feed pattern (as `mtsi` does): a
   missing/stale file renders a quiet placeholder, never an error state.
6. Email via existing `utils/notifier` ONLY on verdict change vs the previous
   JSON, plus one weekly heartbeat (Monday run) — no daily TOO_EARLY spam.

Scheduling: daily Windows scheduled task cloned from the existing
`TradingBot-GoalProgress` registration pattern.

## Safety

Read-only everywhere. No imports from order/execution paths. Script failure
can never affect trading. Public repo: JSON goes under gitignored `data/`;
no secrets touched.

## Testing (`tests/test_postfix_verdict.py`)

- verdict table: synthetic cohorts driving each verdict + the modifier flag
- **window-column regression:** a trade opened pre-epoch but closed
  post-epoch must be EXCLUDED (the design-review catch)
- contamination: one compressed trade flips verdict to COHORT_CONTAMINATED
- STALE flag requires n >= 10 (n=2 all-STALE must NOT fire it)
- email-on-change only: same verdict twice -> one send
- FIX_EPOCH value pinned

## Out of scope (YAGNI)

No prediction, no parameter changes, no auto-reaction, no strategy logic.
SP2 (ops repairs) and SP3 (microstructure harvest) are separate specs.
