# Cell-Filter Entry Gate

**Date:** 2026-05-01
**Status:** Design — pending user review
**Author:** Claude Opus 4.7 (with Syed Shiraz Shahid)
**Branch:** `fix/stop-loss-streak-live-risk-trim`

## Problem

The bot operates at $221, takes ~10-15 trades/day across the full
`claude_portfolio` setup universe, and is roughly breakeven (last 7 days
+$6.80 / 47% WR / R:R 3.4:1). All-time PnL is -$53.90 across 262 trades.

Per-cell warehouse attribution (`claude_portfolio` strategy_family,
all-time, post-fix-bug-poison filtered):

| Cell | n | sum | avg/trade | WR |
|------|---|-----|-----------|-----|
| Score 65–74 | 18 | +$2.41 | +$0.134 | 44% |
| Score 75–84 | 20 | +$4.86 | +$0.243 | 40% |
| **Score 85–100** | **16** | **−$2.98** | **−$0.186** | **31%** |
| ATOM (STAR) | 14 | +$2.06 | +$0.147 | 43% |
| ARB (STAR) | 18 | +$1.20 | +$0.067 | 44% |
| DOGE (STAR) | 10 | +$4.83 | +$0.483 | 50% |
| All other symbols | varied | net-negative or fee-eaten | | |

The bot is profitable in proven cells (STAR + score 70–84) and net-
negative everywhere else. Trade volume from non-edge cells dilutes the
profitable ones.

## Goal

Add a hard entry gate that fires only on proven-edge cells, anchored
in the table above. Specifically:

> Take entry **IFF** `(symbol ∈ STAR_SYMBOLS) OR (70 ≤ mcp_score ≤ 84)`.

Expected impact: **trade count −50%, expected EV +$4–7 / 30 days**
versus the current path. Approach 1 from the 2026-05-01 brainstorm.

## Non-goals

- No new strategy or ML retrain (sample size n=262 doesn't support).
- No change to existing exits (AGE_LOSS, trailing, mcp_take_profit).
- No change to the score-85 tier-cap (commit `86acef3`) — that stays
  as defense-in-depth on STAR symbols at high score.
- No automatic STAR-set re-derivation (kept manual, evidence-anchored).

## Design

### Single-line rule

```
allow = (symbol ∈ STAR_SYMBOLS) OR (70.0 ≤ mcp_score ≤ 84.0)
```

That's the whole filter.

### Where it goes

`core/bot_engine.py::_execute_open` — a new gate inserted **between**:

- (E) Meta-filter quality gate (already exists)
- (a) Symbol blacklist (already exists)

So the flow becomes: `mode → universe → meta_filter → CELL_FILTER (new) →
blacklist → tier → sizing → execute`.

Reasoning for placement:
- After meta-filter so we don't waste percentile-cache lookups on
  rejected candidates.
- Before blacklist so the cell decision is clean and observable
  (a STAR symbol on the dynamic blacklist is still a blacklist
  rejection, not a cell rejection).
- Before tier so the score-85 case is rejected once with the right
  reason rather than capped twice.

### Config

`config.py` adds:

```python
# 2026-05-01 — Cell-Filter Entry Gate (Approach 1 from brainstorm).
# Hard entry gate: only fire on proven-edge cells.
#   STAR symbols (ATOM/ARB/DOGE): always allowed.
#   Non-STAR: allowed only when 70 <= mcp_score <= 84.
# All other cells (score < 70, score > 84 on non-STAR) are blocked.
# The score-85 tier cap (commit 86acef3) is unchanged; STAR symbols
# at score >= 85 are still allowed but tier-capped to STANDARD size.
CELL_FILTER = {
    "enabled":         True,
    "score_band_min":  70.0,
    "score_band_max":  84.0,
    "star_overrides":  True,   # STAR ∈ allowed regardless of score
}
```

### Skip reasons (warehouse-observable)

When the filter rejects, write the candidate to the `candidates` table
with `decision='SKIP'` and one of:

- `cell_filter:score_below_band` — non-STAR, score < 70
- `cell_filter:score_above_band` — non-STAR, score > 84 (the anti-EV
  zone; this is the load-bearing rejection)
- `cell_filter:disabled` — never written (filter off → no rejection)

These join with the existing skip-reason taxonomy used elsewhere in
the warehouse.

### Edge cases

| Symbol class | Score | Action | Reason |
|---|---|---|---|
| STAR (ATOM/ARB/DOGE) | any | ALLOW | star override |
| non-STAR | < 70 | SKIP | `score_below_band` |
| non-STAR | 70–84 | ALLOW | in band |
| non-STAR | 85+ | SKIP | `score_above_band` |
| STAR | 85+ | ALLOW | star override (existing tier-cap forces STANDARD) |
| STAR | < 70 | ALLOW | star override (rare; STAR setups usually score 70+) |

`star_overrides=False` flips the STAR rows to "must also be in the
band" (kept as a tunable for future tightening if STAR-at-high-score
proves anti-EV with more data).

### Failure modes

- **Filter blocks all entries on a low-volatility day** — acceptable.
  Bot does nothing, accumulates no fee drag. Recovers when score
  distribution returns to normal.
- **Score field missing** (`mcp_score is None`) — treat as "below band"
  → SKIP. Better than letting an unscored candidate through.
- **`CELL_FILTER` config key missing** (legacy config without the
  block) — defaults to `enabled=True` via `.get(...).get("enabled", True)`
  so the safety filter applies even on old config files.
- **`CELL_FILTER.enabled=False`** — gate becomes a no-op, full path
  reverts to pre-fix behavior. One-line rollback.

## Tests

`tests/test_cell_filter_entry_gate.py` — 8 invariants:

1. STAR symbol + any score → ALLOW
2. Non-STAR + score 70 → ALLOW (boundary inclusive)
3. Non-STAR + score 84 → ALLOW (boundary inclusive)
4. Non-STAR + score 69.99 → SKIP `score_below_band`
5. Non-STAR + score 85 → SKIP `score_above_band`
6. Non-STAR + score=None → SKIP `score_below_band`
7. `enabled=False` → ALLOW everything (rollback path)
8. `star_overrides=False` + STAR + score 90 → SKIP `score_above_band`

Each test exercises a self-contained predicate replica, not the full
`_execute_open` path (heavy deps).

## Operational checks (post-merge)

After 50 trades under the new filter, query warehouse:

```sql
SELECT
  CASE
    WHEN symbol IN ('ATOM/USDT:USDT','ARB/USDT:USDT','DOGE/USDT:USDT')
      THEN 'STAR'
    WHEN mcp_score BETWEEN 70 AND 84 THEN 'BAND'
    ELSE 'OTHER'
  END AS cell,
  COUNT(*), ROUND(SUM(realized_pnl), 2)
FROM trades
WHERE status='CLOSED' AND ts_entry >= '<filter-deploy-ts>'
GROUP BY cell;
```

Expected: zero rows in `OTHER` (the filter is doing its job),
`STAR` and `BAND` both net-positive in the new sample.

If `STAR` flips negative or `BAND` shows < +$1 over 50 trades, raise
the score band lower bound to 75 (more selective) and review.

## Rollback

Single config flag in `config.py`:

```python
CELL_FILTER = {"enabled": False, ...}
```

No data migration. Bot resumes pre-filter behavior on the next entry
cycle.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Cuts trade count below sustainability threshold | Medium | Acceptable — non-edge cells were net-negative. Worst case bot does nothing for a day. |
| STAR membership becomes stale (DOGE could revert) | Medium | Manual evidence-based STAR refresh, anchored in claude_portfolio data, has been the explicit pattern (Phase 12, today's audit). Add to ops runbook. |
| Score field has a hidden bug producing artificially-low values | Low | Phase 13.5b's mcp_score warehouse fix is in place. Operational check above catches this within 50 trades. |
| Filter contradicts a future "Don't block any trades" directive | Surfaceable | This is a literal entry gate. The user has approved Approach 1 explicitly; conflict surfaced at design time. |

## Out of scope

- The +$3 daily profit-lock (Approach 2 hybrid) — defer; can be added
  in a follow-up commit if Approach 1 alone moves the needle but
  give-back-on-good-days remains a problem.
- BTC regime alignment (Approach 3) — defer; data shows it's already
  partially in place via tier `requires_btc_aligned`.
- Adaptive STAR membership (refit weekly) — out of scope, manual.
