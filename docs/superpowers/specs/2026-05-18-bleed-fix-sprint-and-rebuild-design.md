# Bleed-Fix Sprint + Quant Rebuild Swarm — Design

**Date:** 2026-05-18 (ultrathink revision: 2026-05-19)
**Branch (sprint):** `feat/profitability-upgrade`
**Branch (rebuild):** `feat/quant-rebuild-shadow` (new, from sprint head after sprint lands)
**Author:** Claude Code (Opus 4.7 1M) — brainstorming session with user
**Status:** Pending user review before plan generation
**Revision history:**
- 2026-05-18 initial — three patches in parallel sprint + parallel rebuild swarm
- 2026-05-19 ultrathink — ship-order re-sequenced; Patch #0 instrumentation added; effect estimates rebased on 30d data; numerical errors corrected

---

## 1. Diagnostic and Goal

### 1.1 Diagnostic (30-day attribution from `data/warehouse.sqlite`)

| Bucket | $ |
|---|---:|
| WIN exits (`mcp_take_profit` + `trailing_stop`) | **+$26.76** |
| LOSS exits (`ghost_sync`, `ghost_reconciled`, `STALE`, `stop_loss`, `sl_placement_failed`, `systematic_close`) | **−$26.36** |
| AGE exits (clean code) | −$2.88 |
| AGE exits (free-text reasons like "STAR age 188m>90m cutoff") | −$9.48 |
| Other | −$3.77 |
| **30d total** | **−$15.72** |

30d rollup: n=234, WR 44.9%, realized R:R 0.88:1, avg_win +$0.44, avg_loss −$0.50.

7d window detail (claude_portfolio is the only active `strategy_family` post-Phase-10 triage, so 7d all-trades = 7d claude_portfolio = n=75, net −$9.28, WR 48.0%):
- `mcp_take_profit`: n=11, +$16.67, avg +$1.51
- `ghost_reconciled`: n=25, −$11.62, avg −$0.47
- `ghost_sync`: n=19, −$7.23, avg −$0.38
- `STALE`: n=7, −$0.72
- Free-text age-cutoff reasons (5 distinct, single-row each): aggregate −$3.20

### 1.2 The structural finding

The bot has clear edge in the `mcp_take_profit` path (+$1.51 per win, n=11 over 7d). It gives every cent back through ghost-class exits and premature age-cutoff exits — paths that close positions BEFORE TP can fire. **The leak is execution-side, not signal-side.**

Confirmation today (post phantom-ghost fix shipped 2026-05-15 + TP-drift detection shipped this session): 8 closed claude_portfolio trades, +$2.03, 87.5% WR — every winner via `mcp_take_profit`. Recent patches are already moving the needle in the right direction.

### 1.2.1 Time-to-fire distribution of `mcp_take_profit` (load-bearing for Patch #2)

```
mcp_take_profit time-to-fire (n=25 over 30d):
  min/p25/median/p75/max = 23 / 71 / 119 / 202 / 678 min
  past 60min:  19/25 (76%)
  past 120min: 12/25 (48%)
  past 240min:  4/25 (16%)
```

**Median winner takes 119 minutes to deliver.** Current Phase-14 cutoffs (AGGRESSIVE=90m, CONVICTION=120m, STANDARD=240m) kill 48% of winner candidates before they fire. This single statistic justifies Patch #2 as the highest-confidence change in the sprint.

### 1.2.2 Ghost-class realized PnL distribution (relevant to Patch #1 thesis)

```
ghost_reconciled (30d): n=33  sum=-$15.52  positive=18/33 (55%)  sum_positive=+$12.33
ghost_sync       (30d): n=42  sum=- $7.10  positive=14/42 (33%)  sum_positive=+ $2.45
```

**43% (32/75) of ghost-class trades close at positive PnL.** However, a positive ghost_reconciled close most likely means the venue's TP order fired and the ledger search caught it — the bot already books the win at the correct value. Patch #1's `verify_exchange_tp_alive` would return False (TP filled, not pending) for those, so the patch would correctly proceed-to-close and net effect on the visible 18 positive rows is approximately zero.

Patch #1 only helps **Case A**: bot tracker incorrectly thinks position gone while venue still has it open with SL+TP orders still pending. Case A frequency is **not directly measurable from warehouse rows** and requires pre-deployment instrumentation (Patch #0).

### 1.3 Goal

1. **Sprint stream (2–3 days):** Ship three surgical patches that stop the largest bleed sources and amplify the working path. Live bot stays running on `CONTROLLED_LIVE`.
2. **Rebuild stream (weeks, parallel multi-agent swarm):** Execute Phases 2–6 of the existing Quant Trading Transformation plan (`C:\Users\SyedShirazShahid\.claude\plans\compiled-pondering-key.md`). Phase 7 (shadow runner) gated on reviewer-agent approval.

### 1.4 Constraints (binding)

- Always `CONTROLLED_LIVE`. Never suggest OBSERVATION/PAPER as a safety fallback — stop the process instead.
- WR floor ≥ 65% (memory directive; rebuild promotion gate enforces).
- $562 account size; minimum-notional thresholds matter.
- No personal info / API keys in committed files.
- Pre-commit hooks (ruff, codespell, detect-secrets, pre-push pytest) must pass on every commit.
- Sprint must not pre-empt rebuild's feature-store schema; rebuild must not touch any sprint-stream file.

---

## 2. Architecture

```
┌─── SPRINT STREAM (2–3 days, single-developer edits in existing files) ────────────────────┐
│                                                                                           │
│   core/position_tracker.py   ── Patch #1: ghost reroute (verify alive, hold if profit)    │
│   core/order_manager.py      ── Patch #1: _finalize_close ghost branch                    │
│   core/order_manager.py      ── Patch #3: STALE/AGE wrap with _try_soft_close             │
│   core/mcp_brain.py          ── Patch #3: score_take_profit_proximity()                   │
│   config.py                  ── Patches #1/#2/#3: feature flags + new constants           │
│   scripts/refit_age_cutoffs.py ── NEW: empirical age-cutoff fit                           │
│   scripts/sprint_kpi.py      ── NEW: baseline-vs-current bucket reporting                 │
│   tests/test_ghost_reroute.py ── NEW                                                      │
│   tests/test_age_cutoffs_refit.py ── NEW                                                  │
│   tests/test_mcp_tp_amplify.py ── NEW                                                     │
│                                                                                           │
└───────────────────────────────────────────────────────────────────────────────────────────┘

┌─── REBUILD STREAM (weeks, Ruflo swarm in background — pure additive code) ────────────────┐
│                                                                                           │
│   core/feature_store.py      ── NEW (Phase 2)                                             │
│   core/regime_hmm.py         ── NEW (Phase 2)                                             │
│   core/garch_vol.py          ── NEW (Phase 2)                                             │
│   core/labeler.py            ── NEW (Phase 3)                                             │
│   core/models.py             ── NEW (Phase 3)                                             │
│   core/calibration.py        ── NEW (Phase 3)                                             │
│   core/dist_fit_sl.py        ── NEW (Phase 4) — supplements existing impl                 │
│   core/vol_target.py         ── NEW (Phase 4)                                             │
│   core/decay_detector.py     ── NEW (Phase 6)                                             │
│   core/walk_forward.py       ── NEW (Phase 0)                                             │
│   core/stat_tests.py         ── NEW (Phase 0)                                             │
│   core/shadow_runner.py      ── NEW (Phase 7, GATED)                                      │
│   core/promotion_gate.py     ── NEW (Phase 6)                                             │
│   tests/test_*               ── ~60 new tests (per coverage targets in master plan)       │
│                                                                                           │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Why the streams don't collide

- Sprint edits **existing** files in `core/order_manager.py`, `core/position_tracker.py`, `core/mcp_brain.py`, `config.py`. Rebuild **creates new** files in `core/`.
- Only shared file is `core/warehouse.py` (rebuild adds tables via `CREATE TABLE IF NOT EXISTS`, sprint does not touch). Coordination: rebuild's first task lands the schema migration; sprint proceeds in parallel.
- Live bot stays running on `CONTROLLED_LIVE` throughout; sprint patches picked up at user-initiated restart; rebuild work is purely additive and never affects live until promotion gate passes.

### 2.2 Sync points (two)

1. **Sprint Patch #2 reads from `attribution` table.** If the rebuild's Phase 1 attribution backfill hasn't landed, the refit falls back to raw `trades.realized_pnl` + `trades.ts_entry`. Independent — no blocking dependency.
2. **Shadow runner go-live (rebuild Phase 7) requires sprint patches in production.** The shadow path must observe the same execution behavior as live. Naturally sequenced: sprint ships in days, rebuild Phase 7 is weeks away.

---

## 2A. Ship order (revised 2026-05-19 ultrathink)

| Day | Action | Confidence | Notes |
|---|---|---|---|
| Day 1 | Patch #2 (age refit) ships; Patch #0 instrumentation ships in same commit | **Strong** | Median winner = 119min vs current 90/120/240 cutoffs; direct empirical justification |
| Day 2 | Patch #3 (mcp_TP amplify) ships; **rebuild swarm spawned** (single ONE-MESSAGE spawn of 5 named agents, run_in_background=true) | Medium | Sprint patches and rebuild swarm touch disjoint files |
| Day 3 | (soak day for Patches #2 and #3; observe sprint_kpi.py output) | — | No new code; verify Spec §12 still trips on synthetic 5-loss scenario |
| Day 4–5 | Review Patch #0 instrumentation log output (72h window) | — | Decision point: enable Patch #1 only if `would_reroute=True` events ≥ 5/24h AND mean expected-saved-pnl > $0 |
| Day 6+ | Patch #1 (ghost reroute) ships **only if Day-4–5 gate passes** | Conditional | If gate fails, Patch #1 is deprioritized and instrumentation findings inform a different fix |

**Why this re-ordering vs the original parallel-three pitch:**
- Patch #2 has the strongest data support (76% of winners past 60min) — ship the high-confidence win first.
- Patch #1's expected effect was conjecture; instrumentation reveals it before commitment.
- Patch #3 is small and safe; ships independently.
- Rebuild swarm kicks off after first patch is observed for 24h, so swarm sees a stable head.

## 3. Sprint Patch #0 — Pre-deployment instrumentation for ghost-class events

### 3.0.1 Purpose

The Patch #1 ghost-reroute idea targets a "Case A" scenario (bot tracker drift while venue still has position open + SL+TP pending) that is **not directly measurable** from warehouse rows. Before committing 50-100 LOC + 10 tests + a feature flag to the production path, we instrument what Patch #1 *would have done* and count the actual frequency in production.

### 3.0.2 Implementation (log-only, no behavior change)

Insert at the top of the ghost-class close emitter in `core/position_tracker.py`:

```python
# Patch #0: instrumentation only — does not gate the close
if config.GHOST_REROUTE_INSTRUMENT:
    try:
        upnl_pct = self._compute_unrealized_pnl_pct(position)
        sl_alive = order_manager.verify_exchange_sl_alive(exchange, position) if upnl_pct > 0 else None
        tp_alive = order_manager.verify_exchange_tp_alive(exchange, position) if upnl_pct > 0 else None
        would_reroute = (upnl_pct > 0) and bool(sl_alive) and bool(tp_alive)
        log_info(
            f"GHOST_REROUTE_INSTRUMENT: symbol={position.symbol} "
            f"upnl_pct={upnl_pct:.4f} sl_alive={sl_alive} tp_alive={tp_alive} "
            f"would_reroute={would_reroute} reason={ghost_class_reason}"
        )
    except Exception as e:
        log_warning(f"GHOST_REROUTE_INSTRUMENT: failed to compute hypothetical: {e}")
# ... existing ghost-class close logic continues unchanged
```

### 3.0.3 Files touched

- `core/position_tracker.py` — add the instrumentation block (~15 LOC, log-only).
- `config.py` — add `GHOST_REROUTE_INSTRUMENT = True` (separate flag from `GHOST_REROUTE_ENABLED`).
- `scripts/ghost_reroute_report.py` — NEW, ~40 LOC. Reads logs over a window, counts `would_reroute=True` events, sums expected-saved-pnl (a rough proxy: `upnl_pct * notional`).
- `tests/test_ghost_reroute_instrument.py` — NEW, ~4 tests: instrumentation fires, log line format is parseable, flag-off skips the block, exception during compute doesn't crash close path.

### 3.0.4 Decision gate (Day 4–5)

Run `scripts/ghost_reroute_report.py --since <ts> --until <ts>` over the 72h instrumentation window.

| Outcome | Action |
|---|---|
| ≥ 5 `would_reroute=True` events/24h AND mean expected-saved-pnl > $0 | Ship Patch #1 as designed |
| < 5 events/24h | Case A is rare; deprioritize Patch #1; investigate a different bleed source |
| ≥ 5 events/24h BUT mean expected-saved-pnl ≤ $0 | The candidates exist but holding wouldn't help; deprioritize Patch #1 |

### 3.0.5 Rollback

`GHOST_REROUTE_INSTRUMENT = False`, restart. <1 minute. Log-only means there's nothing to break.

---

## 3. Sprint Patch #1 — Ghost-class reroute

### 3.1 Problem

`ghost_sync` (n=19, −$7.23) + `ghost_reconciled` (n=25, −$11.62) = **−$18.85 / 7d**, the largest single bleed. The 2026-05-15 phantom-ghost fix added a 45s pending-then-confirm grace, addressing DNS-outage false positives. It does not handle the harder case: a real exchange-side close that books a small loss when a longer hold would have fired `mcp_take_profit` (+$1.51 avg).

### 3.2 Idea

When ghost-class detection fires, ask "is this real?" The bot has live exchange-side SL/TP order verification (`verify_exchange_sl_alive`, `verify_exchange_tp_alive`, shipped this session). If both are alive AND the position is in profit, the ghost detection is most likely tracker drift, not a real close. Hold instead of booking.

### 3.3 Decision logic

Inserted into `core/position_tracker.py` ghost path, gating the existing `ghost_*` close-emission:

```
on ghost_class_detection(position):
    if not GHOST_REROUTE_ENABLED:
        proceed_with_close()
        return

    if position.unrealized_pnl_pct <= 0:
        proceed_with_close()   # existing behavior — cut losers fast
        return

    sl_alive = order_manager.verify_exchange_sl_alive(exchange, position)
    tp_alive = order_manager.verify_exchange_tp_alive(exchange, position)

    if sl_alive and tp_alive:
        position._ghost_reroute_count = (position._ghost_reroute_count or 0) + 1
        if position._ghost_reroute_count > MAX_GHOST_REROUTES_PER_POSITION:
            proceed_with_close(reason="ghost_reroute_exhausted")
            return
        log_info(f"GHOST_REROUTE: {position.symbol} uPnL>0 + SL+TP alive, holding (#{position._ghost_reroute_count}/{MAX_GHOST_REROUTES_PER_POSITION})")
        # Soft re-sync from venue; do not close
        return

    proceed_with_close()
```

### 3.4 Files touched

- `core/position_tracker.py` — add `_ghost_reroute_count` attribute; gate existing ghost-close branch with the decision logic above.
- `core/order_manager.py` — ensure `verify_exchange_sl_alive` and `verify_exchange_tp_alive` are importable from `position_tracker` (they exist per memory; may need a small re-export).
- `config.py` — add:
  - `GHOST_REROUTE_ENABLED = True`
  - `MAX_GHOST_REROUTES_PER_POSITION = 3`
- `tests/test_ghost_reroute.py` — NEW, ~10 tests.

### 3.5 Tests

1. `test_position_in_loss_proceeds_to_close` — uPnL ≤ 0 → original ghost-close fires.
2. `test_position_in_profit_with_both_alive_holds` — uPnL > 0, both verify True → no close, count = 1.
3. `test_position_in_profit_with_sl_dead_closes` — SL verify False → real close.
4. `test_position_in_profit_with_tp_dead_closes` — TP verify False → real close.
5. `test_reroute_cap_exhausts_then_closes` — fire 4 times → 4th closes with `ghost_reroute_exhausted`.
6. `test_flag_off_preserves_original_behavior` — GHOST_REROUTE_ENABLED=False → original ghost-close fires.
7. `test_ghost_class_detection_emits_log_on_reroute` — log line `GHOST_REROUTE:` present.
8. `test_spot_position_unaffected` — spot positions don't have `_ghost_reroute_count`; close proceeds.
9. `test_dry_run_short_circuits` — DRY_RUN=True → no verify call attempted.
10. `test_invariant_sl_close_path_unchanged` — actual SL trigger close (non-ghost) is not gated.

### 3.6 Expected effect (revised 2026-05-19 ultrathink)

**Honest estimate:** unknown without Patch #0 instrumentation data.

Warehouse data shows 43% of ghost-class trades close at positive realized_pnl over 30d. However, those positive closes most likely mean the venue's TP fired (ledger search caught it) — the bot already books the correct value and Patch #1 would correctly proceed-to-close (verify_tp_alive=False on filled orders). The patch's real target is **Case A** (tracker drift on still-alive position with pending SL+TP) which warehouse data cannot directly count.

**Bound based on 30d data:**
- Total ghost-class bleed (loss subset): -$22.62 / 30d. Patch #1 cannot recover more than this.
- Realistic ceiling if Case A is 30% of those: +$6 to +$8 / 30d.
- Realistic floor if Case A is rare: +$0 to +$1 / 30d.
- **Actual figure determined by Patch #0 instrumentation Day 4–5.**

### 3.7 Rollback

`GHOST_REROUTE_ENABLED = False` in `config.py`, restart. <1 minute.

---

## 4. Sprint Patch #2 — Age-cutoff retune

### 4.1 Problem

Phase 14 (2026-04-29) fit age cutoffs at 4h/2h/1.5h for STANDARD/CONVICTION/AGGRESSIVE on 60d data showing "edge expires at 60min." Two changes since: phantom-ghost fix (2026-05-15) keeps more winners alive past 60min, and TP-drift detection prevents silent TP eviction. The 7d data shows clean AGE_EXITS (−$2.88) + free-text AGE_TEXT (−$9.48) = **−$12.36 / 7d** — second-largest bleed.

Today's manual ARB and ETC SHORTs at 257m, +1.1% and +2.0% with hard TPs at +4.1% and +3.4%, show that 4h+ holds with proper TPs work. The bot's old cutoff would have closed these positions hours ago.

### 4.2 Refit procedure

New script `scripts/refit_age_cutoffs.py`:

```
1. Load all CLOSED trades with status, ts_entry, ts_exit, realized_pnl, mcp_score, exit_reason
   from warehouse.sqlite WHERE ts_entry >= now() - 60 days AND status = 'CLOSED'.
2. Split 60d → 45d fit / 15d holdout by ts_entry.
3. For each tier (STANDARD=mcp_score 65–74, CONVICTION=75–84, AGGRESSIVE=85+):
   a. Filter fit-set rows to this tier.
   b. Compute age_at_exit_minutes for each.
   c. For each candidate cutoff c in {30, 45, 60, 90, 120, 180, 240, 300, 360}:
      - Replay: for trades that closed by minute c, use their realized_pnl;
        for trades closed after c, simulate close at c using linear interpolation
        of (entry_px, current_px_at_c). Sum tier pnl under cutoff c.
   d. Pick c* maximizing fit-set sum. Validate on holdout: holdout_sum(c*) ≥ holdout_sum(current).
   e. If validation fails, keep current cutoff for that tier and log.
4. STAR symbols: do not refit cutoff — keep TP-extender × 1.25 (Phase 14 evidence).
5. Write to data/models/age_cutoffs.json: {STANDARD: c1, CONVICTION: c2, AGGRESSIVE: c3, fitted_at: ts, fit_sample_size: n}
6. Print suggested config.py diff and stop short of writing config — operator decides whether to commit.
```

### 4.3 Files touched

- `scripts/refit_age_cutoffs.py` — NEW, ~120 LOC.
- `config.py` — update `AGE_CUTOFF_STANDARD_MIN`, `AGE_CUTOFF_CONVICTION_MIN`, `AGE_CUTOFF_AGGRESSIVE_MIN` to fitted values (after operator review).
- `core/risk_manager.py` (or wherever age cutoffs are read) — load `data/models/age_cutoffs.json` if present, else fall back to config constants.
- `tests/test_age_cutoffs_refit.py` — NEW, ~6 tests.

### 4.4 Tests

1. `test_fit_picks_argmax_on_synthetic_pnl_curve` — synthetic monotone pnl(cutoff) → fit picks the right c*.
2. `test_holdout_validation_rejects_non_improving_fit` — fit-set best fails holdout → keep current.
3. `test_star_symbols_excluded_from_refit` — STAR tier in cutoffs.json absent.
4. `test_runtime_loads_json_when_present` — risk_manager reads cutoff from json.
5. `test_runtime_falls_back_to_config_when_missing` — no json → config constants used.
6. `test_no_change_when_fit_sample_too_small` — fit-set < 20 rows per tier → keep current.

### 4.5 Expected effect (revised 2026-05-19 ultrathink)

**Primary effect: expand the winning set, not cut the losing one.**

The 30d AGE-class bleed totals -$12.36 (clean AGE_LIMIT/AGE_LOSS = -$2.88 + free-text age-cutoff reasons = -$9.48). Refit can reduce this — but the dominant value comes from positions currently age-cut that *would have* hit `mcp_take_profit` if held longer.

Empirical anchor (Section 1.2.1): median winner fires at **119 min**. Current cutoffs:
- AGGRESSIVE 90m → kills 76% of winner-class trajectories
- CONVICTION 120m → kills 48%
- STANDARD 240m → kills 16%

If refit lifts AGGRESSIVE → 120m and CONVICTION → 180m (conservative shift), expected effect:
- **Cuts age-bleed by 30–50%:** save +$4 to +$6 / 30d on the loss-cut leg.
- **Expands the winning set:** of ~12 currently-age-cut candidates / 30d, perhaps 30–50% would have eventually fired TP (+$1.50 avg). Add +$4 to +$9 / 30d.
- **Total expected:** **+$8 to +$15 / 30d**, point estimate $11. Highest-confidence patch in the sprint.

Holdout validation (45d-fit / 15d-test) gates deployment — if holdout doesn't strictly improve, refit reverts to current values.

### 4.6 Rollback

`rm data/models/age_cutoffs.json`, restart. Falls back to config constants. <1 minute.

---

## 5. Sprint Patch #3 — `mcp_take_profit` path amplification

### 5.1 Problem

`mcp_take_profit` produces +$1.51/win × 11 = +$16.67 / 7d. Competing soft-close paths (`STALE`, `AGE_*`, `systematic_close`) cut off positions that are close to firing TP. Extend their runway slightly and the winning bucket grows without changing entry quality.

### 5.2 Idea

Before any soft close (NOT SL, NOT real ghost), check if the position is close to firing `mcp_take_profit`. If proximity ≥ threshold, grant a 30-min grace window before the soft close is allowed to fire.

### 5.3 Proximity scoring

Added to `core/mcp_brain.py`:

```
def score_take_profit_proximity(position) -> float:
    """0.0 (far) to 1.0 (about to fire).

    Combines: distance to TP in ATR units (50%), pnl progress (30%), 
    momentum alignment (20%).
    """
    if not position.tp_price or not position.entry_atr:
        return 0.0

    dist_atr = abs(position.current_px - position.tp_price) / position.entry_atr
    dist_score = max(0.0, 1.0 - dist_atr / 2.0)

    pnl_score = clip(position.unrealized_pnl_pct / position.target_pnl_pct, 0, 1)
    momentum_score = 1.0 if mcp_brain.ema_aligned(position) else 0.5

    return 0.5 * dist_score + 0.3 * pnl_score + 0.2 * momentum_score
```

### 5.4 Decision gate

Added at top of each soft-close path in `core/order_manager.py`:

```
def _try_soft_close(position, soft_reason):
    if not MCP_TP_AMPLIFY_ENABLED:
        return proceed_with_close(soft_reason)

    proximity = mcp_brain.score_take_profit_proximity(position)
    if proximity < MCP_TP_PROXIMITY_THRESHOLD:
        return proceed_with_close(soft_reason)

    now = time.time()
    grace_until = getattr(position, "_mcp_tp_grace_until", 0)
    if grace_until == 0:
        position._mcp_tp_grace_until = now + MCP_TP_GRACE_SEC
        log_info(f"MCP_TP_AMPLIFY: deferring {soft_reason} on {position.symbol} (prox={proximity:.2f})")
        return

    if now < grace_until:
        return  # still in grace window

    return proceed_with_close(f"{soft_reason}_post_grace")
```

### 5.5 Files touched

- `core/mcp_brain.py` — add `score_take_profit_proximity()` method (~25 LOC).
- `core/order_manager.py` — wrap STALE, AGE_LIMIT, AGE_LOSS, systematic_close paths with `_try_soft_close` (~5 call sites).
- `config.py` — add:
  - `MCP_TP_AMPLIFY_ENABLED = True`
  - `MCP_TP_PROXIMITY_THRESHOLD = 0.7`
  - `MCP_TP_GRACE_SEC = 1800`
- `tests/test_mcp_tp_amplify.py` — NEW, ~8 tests.

### 5.6 Tests

1. `test_proximity_below_threshold_closes_normally` — score 0.5 → STALE fires.
2. `test_proximity_above_threshold_first_call_defers` — score 0.8, grace_until=0 → grace set, no close.
3. `test_within_grace_window_still_defers` — second call inside window → no close.
4. `test_grace_expired_closes_with_post_grace_suffix` — third call past window → close fires with reason suffix.
5. `test_sl_path_unaffected` — invariant: SL close not gated, fires immediately.
6. `test_real_ghost_close_unaffected` — invariant: ghost-class real close not gated.
7. `test_flag_off_preserves_original_behavior`.
8. `test_proximity_zero_when_no_tp_price` — guard against div-by-zero / missing TP.

### 5.7 Expected effect (revised 2026-05-19 ultrathink)

Soft-close 30d totals: AGE-class -$12.36 + STALE -$1.19 + systematic_close (unmeasured, small) ≈ -$14 / 30d. The amplification grace targets the subset close to firing TP at soft-close trigger.

Subset estimation: of 23 soft-close events / 30d, roughly 30–40% would meet `proximity ≥ 0.7`. Of those, perhaps half would actually reach TP in the 30-min grace window (the other half time out and close anyway with `_post_grace` suffix).

**Expected effect: +$3 to +$6 / 30d**, point estimate $4. Small but cheap; ships independently of Patch #1's gate.

Material overlap with Patch #2: a refitted age cutoff already handles part of this case. Patch #3 catches the residual — cases where the position is close to TP but the cutoff has been exceeded *and* the soft-close still wants to fire.

### 5.8 Rollback

`MCP_TP_AMPLIFY_ENABLED = False`, restart. <1 minute.

---

## 6. Rebuild swarm orchestration

### 6.1 Pattern

SendMessage-first named-agent pipeline per CLAUDE.md Ruflo integration. ALL agents spawned in ONE message with `run_in_background: true`. Single SendMessage kicks off the pipeline. I stop, surface what's running, and resume only on results.

### 6.2 Roster

| Agent name | subagent_type | Scope | Next |
|---|---|---|---|
| `researcher` | `researcher` | Map existing code: warehouse schema, sim_execution interface, feature touchpoints. Identify reusable code in `probability_calibrator.py`, `correlation_manager.py`, `news_scanner.py` | architect |
| `architect` | `system-architect` | Design feature_store schema, model interface, calibration contract, walk-forward CV interface. Produce module boundaries doc | coder |
| `coder` | `coder` | Implement Phase 2 (feature_store + regime_hmm + garch_vol), Phase 3 (labeler + LR + isotonic), Phase 4 (dist_fit_sl + vol_target). Pure additive new files | tester |
| `tester` | `tester` | TDD discipline: failing tests first, green minimal impl, refactor. Coverage targets per master plan (each new module ≥80%) | reviewer |
| `reviewer` | `reviewer` | Code quality + security + check coverage. Produce go/no-go for Phase 7 (shadow_runner) | (terminal) |

### 6.3 Kickoff timing

Spawn the swarm on **Day 2** — after Patch #2 (age refit) lands and has been observed in production for 24h. This ensures:
- The swarm sees a stable sprint-stream head.
- The first patch's effect is partially measurable before committing context to a multi-week parallel workstream.
- Sprint-stream and rebuild-stream stay file-disjoint from the swarm's first commit.

### 6.4 Plan-file accessibility (added 2026-05-19 ultrathink)

Before spawning, copy the master plan content from `C:\Users\SyedShirazShahid\.claude\plans\compiled-pondering-key.md` into the repo as `docs/superpowers/specs/2026-05-18-quant-rebuild-plan-source.md`. Agents may not have read access to user-home paths on Windows; a checked-in artifact in the repo is reachable via standard Read tool semantics.

### 6.5 Kickoff payload (single SendMessage to `researcher`)

```
TASK: Phases 2-6 of the quant rebuild plan. Pure additive build.
PLAN FILE: docs/superpowers/specs/2026-05-18-quant-rebuild-plan-source.md
DESIGN FILE: docs/superpowers/specs/2026-05-18-bleed-fix-sprint-and-rebuild-design.md

CONSTRAINTS:
  - DO NOT modify ANY file in core/ that the sprint stream may also touch:
    position_tracker.py, order_manager.py, mcp_brain.py, risk_manager.py, config.py
  - Add NEW tables to core/warehouse.py via CREATE TABLE IF NOT EXISTS only
  - Live bot is RUNNING on CONTROLLED_LIVE — do not stop it, do not break imports
  - All commits on branch feat/quant-rebuild-shadow (branched from current sprint head AT DAY 2)
  - Pre-commit hooks must pass (ruff, codespell, detect-secrets, pytest)
  - At each phase boundary, SendMessage to the next agent with:
    files changed, tests added, test pass/fail count, any deviation from plan

PRIORITY ORDER: Phase 2 (feature_store + regime + garch) → Phase 3 (model + calib) → Phase 4 (sizing + dist-fit SL) → Phase 6 (decay + promotion)

DEFERRED:
  - Phase 5 (cointegration) — skip; revisit only if fresh diagnostic shows EDGE_PRESENT on a new strategy
  - Phase 7 (shadow_runner) — GATED on reviewer green-light from this batch

INVARIANT (HARD STOP):
  - If pytest -q drops below baseline pass count (~1077), STOP and SendMessage to me
  - If ATOM 30d PnL drops below current baseline, STOP and SendMessage to me
  - If any sprint-stream file is touched in a commit, STOP and SendMessage to me
```

### 6.6 Background mode and resumption

The swarm runs autonomously. I do not poll. When reviewer returns its summary (final message bubbles back to the main thread), I review the pull-request-shape output and decide on Phase 7 entry.

### 6.7 Risk

Swarm could drift from plan. Mitigations: explicit plan-file path and phase scope in kickoff payload; tester agent enforces test-suite invariant; reviewer agent is the final gate; I have manual final review before any merge into working tree.

---

## 7. Success criteria and measurement

### 7.1 Sprint baseline (locked at sprint kickoff, all 30d windows)

- 30d net PnL: −$15.72
- 30d WR: 44.9%
- 30d realized R:R: 0.88:1
- 30d ghost-class loss bucket: −$22.62 (ghost_reconciled −$15.52 + ghost_sync −$7.10)
- 30d age-class loss bucket: −$12.36 (clean −$2.88 + free-text −$9.48)
- 30d mcp_take_profit win bucket: +$26.76 (with trailing_stop)
- 30d soft-close (STALE + systematic_close) bucket: ≈ −$1.5
- Daily PnL today at design time: +$1.67 / +$2.03 closed
- mcp_take_profit median time-to-fire: 119 min (load-bearing for Patch #2)

### 7.2 Sprint targets (measured 30 days after final patch lands)

| Metric | Baseline (30d) | Target (30d) | Stop-gap if missed |
|---|---:|---:|---|
| Net PnL | −$15.72 | **≥ +$5** | If still net-negative → re-baseline; consider Patch #1 even if instrumentation was borderline |
| Ghost-class loss bucket | −$22.62 | **≥ −$15** (gated by Patch #1 deployment) | If Patch #1 gates open and bucket unchanged → roll back; if gates closed, ignore |
| Age-class loss bucket | −$12.36 | **≥ −$4** | If unchanged → confirm `data/models/age_cutoffs.json` is being read; restart bot |
| `mcp_take_profit` win bucket | +$26.76 | **≥ +$32** (more candidates reach TP) | If lower → amplification threshold too aggressive or refit too liberal; raise thresholds |
| WR | 44.9% | **≥ 50%** | Structural; soft target. 65% remains the long-term floor under rebuild promotion gate |
| Realized R:R | 0.88:1 | **≥ 1.2:1** | Primary signal that the winning runway is being preserved |

### 7.2.1 Interim 7d signals (first read on Day 8 after Patch #2 ships)

| Metric | Target |
|---|---:|
| 7d net PnL | ≥ 0 |
| `GHOST_REROUTE_INSTRUMENT` events with `would_reroute=True` | ≥ 15 over 72h (Patch #0 gate input) |
| New `_post_grace` exit_reason rows | ≥ 3 over 7d (proves Patch #3 wired) |
| New trades hitting `mcp_take_profit` past 120min | ≥ 2 (proves Patch #2 reached production) |

### 7.3 Rebuild targets (at Phase 7 shadow-runner go-live + 30 days of shadow data)

| Metric | Promotion gate |
|---|---|
| Shadow Sharpe LB(95%) | LB > Live UB |
| Shadow rolling 30d WR | ≥ 65% (memory floor) |
| Shadow PBO | < 0.5 |
| Shadow Deflated SR p-value | < 0.05 |

### 7.4 Measurement instrumentation

- `scripts/sprint_kpi.py` — NEW, ~80 LOC, read-only. Prints baseline-vs-current bucket table and exit-reason rollup over user-supplied window. No schema changes.
- Operator-side cron (not committed): `python scripts/sprint_kpi.py --since YYYY-MM-DD --markdown >> reports/sprint_progress.md`

---

## 8. Testing strategy and rollback

### 8.1 Test budget

- Sprint: ~30 new tests (10 ghost-reroute, 6 age-refit, 8 mcp_tp_amplify, 6 sprint_kpi).
- Rebuild: ~60 new tests per master plan coverage targets.
- All TDD: failing test first, minimal green, refactor.

### 8.2 Sprint test invariants (hold after every patch)

1. `pytest tests/ -q` ≥ 1077 prior-baseline pass count, 0 failures.
2. ATOM 30d PnL > 0 (proven-edge guard from master plan verification block).
3. `python main.py --status` exits clean.
4. `config.GHOST_REROUTE_ENABLED` and `config.MCP_TP_AMPLIFY_ENABLED` are boolean flags.

### 8.3 Per-patch dry-run

After each patch lands, run `scripts/sprint_kpi.py --dry-run` to confirm new exit-reason buckets are populating. For Patch #1: tail logs for `GHOST_REROUTE:` lines after restart — first occurrence is the smoke test.

### 8.4 Rollback table

| Patch | Rollback action | Time |
|---|---|---:|
| #1 Ghost reroute | `GHOST_REROUTE_ENABLED = False` in `config.py`, restart | <1m |
| #2 Age cutoffs | `rm data/models/age_cutoffs.json`, restart | <1m |
| #3 Mcp_TP amplify | `MCP_TP_AMPLIFY_ENABLED = False`, restart | <1m |
| All three | `git revert` the three sprint commits, restart | <5m |
| Rebuild | Abandon `feat/quant-rebuild-shadow` branch (additive-only, never merged until promotion gate passes) | n/a |

### 8.5 Risk register

| Risk | Mitigation |
|---|---|
| Ghost reroute holds a position into a bigger loss | Position must be in profit to qualify; exchange-side SL still active; MAX_REROUTES cap = 3 |
| Age-cutoff refit overfits 60d window | 45d-fit / 15d-holdout split; deploy only on strict improvement |
| Mcp_TP amplify defers a close, market reverses against grace window | Grace bounded at 30 min; exchange-side SL unchanged; bounded per-trade downside |
| Swarm produces non-mergeable PR | Reviewer agent is final gate; manual final review before any merge |
| Two streams produce conflicting `core/warehouse.py` edits | Sprint forbidden from touching warehouse.py; rebuild's edits are CREATE TABLE IF NOT EXISTS only |
| Live engine still bleeding during rebuild weeks | Sprint patches land in 2–3 days and are the primary recovery vector; rebuild is the ceiling-lift, not the bleed-stop |
| **Patches collectively bias bot toward holding → Spec §12 (5-loss halt) might mis-fire or fail to fire** | **Add a Day-3 soak test (in `tests/test_spec12_post_sprint.py`): synthesize 5 sequential loss rows in a temp warehouse; assert risk_manager flips to halt; assert auto-resume cooldown writes `data/review_required.json`. Must pass before Day-4 instrumentation gate decision.** |
| Rebuild swarm agents can't access `C:\Users\…\compiled-pondering-key.md` (Windows user-path) | **Copy plan content into `docs/superpowers/specs/2026-05-18-quant-rebuild-plan-source.md` as a checked-in artifact; kickoff payload references the relative path** |

---

## 9. Out of scope (explicit)

- **New strategies, new alpha sources, multi-agent debate frameworks** — sprint is not a strategy redesign; rebuild Phase 5 (cointegration) deferred until fresh diagnostic shows EDGE_PRESENT on something new.
- **Tuning `PARTIAL_TP` thresholds** — already enabled and working at 50%/50%; no current data supports re-tuning.
- **Refactoring the 3 phantom-pattern fixes from prior session** — stable, refactor would trade tested surface for churn surface.
- **Promoting any rebuild artifact to live** — requires the promotion gate (Sharpe LB + WR ≥ 65% + PBO < 0.5 + Deflated SR p < 0.05). Until then, rebuild is shadow-only and additive.

---

## 10. Decision summary

| Question | Decision |
|---|---|
| Focus | Both bleed-stop AND structural overhaul |
| Timeline | Mixed: Sprint NOW + Rebuild in background |
| Approach | Tight Sprint (Patch #0 instrument + #2 ship + #3 ship + #1 gated on #0 data) + Plan-led Rebuild Swarm |
| Sprint scope | Age refit + mcp_TP amplify + (conditionally) ghost reroute |
| Sprint ship order | Day 1: Patches #0 + #2 — Day 2: Patch #3 + spawn rebuild swarm — Day 3: soak + Spec §12 test — Day 4–5: decide on Patch #1 |
| Rebuild scope | Phases 2–4 and 6 of `docs/superpowers/specs/2026-05-18-quant-rebuild-plan-source.md`; Phase 7 gated by reviewer agent |
| Live bot | Stays running on CONTROLLED_LIVE throughout |
| Rollback | Per-patch config flags, sub-1-minute revert |
| Expected effect (30d, point estimates) | Patch #2: +$11 — Patch #3: +$4 — Patch #1: $0 to +$7 (gated). **Sprint total: +$15 to +$22 / 30d.** Net trajectory: −$15.72 → between +$0 and +$6 / 30d. |
| Highest-confidence change | Patch #2 (age refit) — supported by mcp_take_profit median time-to-fire = 119 min vs current 90/120/240 cutoffs |
| Lowest-confidence change | Patch #1 (ghost reroute) — empirical support depends on Patch #0 instrumentation findings |
