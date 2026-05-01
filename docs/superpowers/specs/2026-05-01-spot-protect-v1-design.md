# SPOT-PROTECT-V1 — Peak-Drawdown Spot Strategy

**Date:** 2026-05-01
**Status:** Design — approved, implementation pending
**Branch:** `fix/stop-loss-streak-live-risk-trim`

## Problem

`$1,052` is sitting in 32 spot holdings across 3 exchanges, doing
nothing the bot can verify. `data/spot_recommendations.jsonl` shows 1
entry over the bot's lifetime. The existing `SpotPortfolioManager`
runs technical-analysis logic (EMA20/EMA50 + RSI(14)) that has never
been validated against realized outcomes — and at $1,052 across 32
positions averaging $30, generic TA generates fee-eaten whipsaws.

The user's stated goal is "profitable on a daily basis" on the
**futures** path. Spot is currently a passive long-crypto exposure
that the bot looks at but doesn't manage meaningfully.

## Goal

A spot strategy that:
1. **Does not lose money to scale-inappropriate strategies** —
   funding-rate arb, day-trading signals, ML-based spot scoring all
   die on the math at $1,052 with 0.1% taker fees.
2. **Caps spot drawdown losses** asymmetrically — limit downside,
   preserve upside.
3. **Cleans up dust positions** that pollute aggregates and tie up
   tiny capital fragments.
4. **Reuses existing `SpotPortfolioManager` infrastructure** — no
   new model artifacts, no new ML retrains.

## Non-goals

- No buy-side spot logic (no DCA, no signal-based entries).
- No HEDGE actions (funding-rate arb math doesn't clear fees at
  $1,052 scale — see Risks section for details).
- No replacement of futures cell-filter / model-gate work.
- No automatic STAR-set adoption on the spot side (separate evidence
  base required).

## Strategy: SPOT-PROTECT-V1

Two independent components. Either can be disabled without affecting
the other.

### Component A — Dust Consolidation (one-shot)

Sell every spot position with USD value < `dust_cutoff_usd` (default
$25) to USDT. Mark consolidated in
`data/spot_dust_consolidated.json`. Future runs use the marker to
avoid re-selling positions that drift back under the threshold (which
would chase any small position that just cooled off).

**Why $25 cutoff?**
- < $25: even a 10% gain is $2.50 gross → ~$2.30 net of $0.05 fees;
  < $0.20 net of slippage. Below the noise floor.
- $25-$50: borderline; kept (might compound).
- $50+: meaningful position, gets active rules from Component B.

**Why one-shot?**
- Re-running on every cycle would create a perverse incentive: a
  good position that pulled back to $24 gets sold, even though the
  rule's intent is "clean up positions that have decayed to dust,"
  not "sell anything below this number."
- Marker file records `(exchange, coin, ts, price, qty, usd_value)`
  per consolidated position. Idempotent: a second `--commit` run
  no-ops on already-marked rows.

**Out of scope here:**
- The script does NOT redeploy the freed USDT. That's the operator's
  call: leave it as cash buffer, route to futures margin via
  `auto_transfer`, etc.

**Implementation:** new operator script `scripts/consolidate_spot_dust.py`
(dry-run by default, `--commit` applies).

### Component B — Peak-Drawdown Stop (live rule)

Replace the EMA20/EMA50 + RSI exit logic in
`SpotPortfolioManager.evaluate_holding` with peak-drawdown logic.

For every spot holding with `value_usdt >= min_position_usd`:
- Track `peak_price` (already done — `HoldingInfo.peak_price` line 119).
- Compute `drawdown = (peak - current) / peak`.
- Decision rules:
  ```
  if drawdown >= drawdown_full_pct:   action = SELL       (full exit)
  elif drawdown >= drawdown_half_pct: action = SCALE_OUT  (half-exit, peak resets)
  else:                               action = HOLD
  ```

Default thresholds:
- `drawdown_half_pct = 0.25` — 25% from peak triggers a half-exit
- `drawdown_full_pct = 0.40` — 40% from peak triggers full exit
- `min_position_usd = 50.0` — below this no rules fire (avoids the
  same fee-floor problem dust consolidation is solving)

**Why peak-based, not signal-based?**
- No regime-detection error. Works in any regime.
- Cannot whipsaw — peak only moves up, so the trigger only fires when
  current is meaningfully off the high.
- Mechanical and explicable: "you're 25% off the peak from the last
  30 days."

**Why these thresholds?**
- 25%: statistically real (alt vol is 5-10% daily, so -25% is ~3-5σ
  not noise).
- 40%: regime change (BTC -40% from ATH = bear market by convention).
- Half/full ladder: half-exit limits regret if it bounces, full exit
  prevents averaging into capitulation.

**Peak reset on half-exit:**
After a SCALE_OUT, the next trigger must come from a **new** peak.
The HoldingInfo `peak_price` is reset to `current_price`. This
prevents repeat half-exits on the same drawdown.

**Tiering example:**
- Holding bought at $100, climbs to $150 (peak), falls to $112.50
  (-25% from peak). → SCALE_OUT half. Peak resets to $112.50.
- Position rallies to $130 (new peak), falls back to $97.50
  (-25% from new peak). → SCALE_OUT half again. Peak resets.
- Position falls from $130 directly to $78 (-40% from peak). → SELL all.

### Excluded (deliberate)

| Action | Why dropped |
|---|---|
| HEDGE (long spot + short perp) | Math: $100 hedge → $0.30 round-trip fees, $0.03/day at +0.01%/8h funding. Break-even at 10 days, profitable only after 43 days. Funding stays positive that long ~30% of the time. EV ~$0.09/hedge. Not worth the wiring at this scale. |
| SCALE_OUT on profits | "Sell winners" anti-pattern. Without a re-entry rule (which would need timing skill we haven't validated), it caps upside. |
| SCALE_IN on dips | Buy-side logic doesn't exist in `SpotPortfolioManager`. Adding it requires evidence (n>200 spot trades). Premature. |
| Stop-loss based on cost basis (not peak) | Cost basis is fragile (90-day `fetch_my_trades`); peaks are observable. Cost-based exits also penalize dollar-cost-averaged positions where avg cost is artificially low. |

## Config (`config.py`)

```python
SPOT_STRATEGY = {
    "enabled":              True,
    "dust_cutoff_usd":      25.0,
    "min_position_usd":     50.0,
    "drawdown_half_pct":    0.25,
    "drawdown_full_pct":    0.40,
}
```

Set `enabled=False` for full rollback (Component B becomes a no-op,
falls through to existing TA logic).

## Files modified

| File | Change |
|---|---|
| `config.py` | Add `SPOT_STRATEGY` dict (~10 lines). |
| `core/spot_manager.py` | Replace `evaluate_holding`'s EMA/RSI section with peak-drawdown rules. Add `_reset_peak_on_half_exit` helper. |
| `scripts/consolidate_spot_dust.py` | New operator script (~120 lines), dry-run + commit, atomic, heartbeat-guarded. |
| `tests/test_spot_protect_v1.py` | New (~10 invariants). |

## Tests (`tests/test_spot_protect_v1.py`)

1. `test_below_min_position_skipped` — value < $50 → HOLD (no rules)
2. `test_drawdown_below_half_threshold_holds` — -24% → HOLD
3. `test_drawdown_at_half_threshold_inclusive` — -25% exactly → SCALE_OUT
4. `test_drawdown_at_full_threshold_inclusive` — -40% → SELL
5. `test_drawdown_between_thresholds` — -30% → SCALE_OUT (not SELL)
6. `test_full_exit_dominates_half` — -50% → SELL (not double-trigger)
7. `test_no_drawdown_holds` — current = peak → HOLD
8. `test_disabled_falls_through` — `enabled=False` → returns to existing TA path
9. `test_half_exit_resets_peak` — after SCALE_OUT, peak == current_price
10. `test_dust_consolidation_threshold` — $24.99 in, $25.01 out

## Operational verification

Post-deploy, after 14 days:
```sql
-- Spot recommendation log shows expected action distribution
SELECT action, COUNT(*) FROM spot_recommendations_jsonl GROUP BY action;
-- Expected: lots of HOLD, occasional SCALE_OUT, rare SELL.
-- Zero SCALE_OUT/SELL with -drawdown < 25% (rule violations).
```

Monitor the holdings list — Component A should have removed ~17 dust
rows on first commit; subsequent days should show stable holding
count unless a drawdown trigger fires.

## Rollback

| What to roll back | How |
|---|---|
| Component A (dust consolidation) | Restore `data/positions.json.bak.*` from prior backup. Marker file is informational. |
| Component B (peak drawdown) | `SPOT_STRATEGY = {"enabled": False, ...}` in `config.py`. |
| Both | Set `enabled=False` AND restore backup. |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cuts a holding right before a recovery | Medium | Half-exit (not full) on first trigger. Peak resets on half-exit. Worst-case: bot exits 50% of one position at -25%, holds the other 50% for the bounce. |
| `fetch_my_trades` cost-basis fails on a coin | Low | `_fetch_cost_basis` already returns 0 on error and falls back to current price. Doesn't crash the cycle. |
| Heartbeat is fresh during dust-consolidation script run | Medium | Script refuses to run when `heartbeat.json` is < 120s old unless `--force`. Same guard as `trim_ghost_positions.py`. |
| Drawdown logic is too tight in a high-vol regime | Low | Default 25% / 40% are conservative. Easily tuned via config without code change. |
| Spot peak data loss on bot restart | Low | `peak_price` is persisted in `data/spot_portfolio.json` via `_save_state`. Bot restart preserves history. |

## Honest expected impact

This is **insurance, not alpha.**

- In a sideways or up year: ~zero impact. The bot holds, no triggers
  fire, dust gets cleaned once.
- In a 2022-style bear: -25%/-40% staircase exits ~half of $400 in
  alt positions before they hit -60%, saving ~$100-200 versus
  passive holding.
- In a flash crash: exits at the threshold, may re-enter at lower or
  miss the recovery. Asymmetric — protects capital, gives up some
  rebound.

If the user wants spot **alpha** (not protection), the path is one of:
1. DCA into BTC/ETH on a fixed schedule (mechanical, no skill required).
2. Convert all spot to USDT, deploy to futures margin (doubles
   futures bankroll, halves halt-trigger sensitivity).
3. Wait for n>200 attributed spot trades, then fit a spot-specific
   model.

Each is a separate spec. SPOT-PROTECT-V1 is independent of all three.
