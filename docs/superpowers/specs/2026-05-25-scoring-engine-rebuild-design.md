# Scoring-Engine Rebuild — Validate & Promote the ML Signal to Primary Entry

**Date:** 2026-05-25
**Status:** Design (approved in brainstorm; pending spec review)
**Author:** Claude (Opus 4.7) + operator

## Problem

The 5-agent forensic swarm (memory: `no-edge-forensics-2026-05-25`) proved on
284 clean trades that the **live entry signal has no exploitable edge**:

- WR 45.8% vs 65.5% break-even; win/loss ratio 0.526; PF 0.444.
- The deterministic rule score (`mcp_brain._score_coin`) is **anti-monotonic**
  — the 80-84 band wins 30% vs 70-74's 47%; higher score → *lower* WR.
- 81% of trades carry no score at all.

Separately, a **full ML substrate already exists** and is mostly unused for
entry decisions:

- Warehouse tables: `candidates` (23,755 rows, with `features_json`),
  `features` (272,216 rows), `labels` (5,582 rows, binary `y` over a
  96-bar forward horizon), `predictions`, `shadow_decisions` (571),
  `model_versions`, `attribution`.
- Trainer `scripts/train_models.py`: anchored walk-forward CV
  (`core/walk_forward.py`), PBO + deflated-Sharpe stats
  (`core/stat_tests.py`), LR + GBM ensemble with isotonic calibration.
- A live ensemble `data/models/ensemble_futures_latest.json` is loaded by
  `mcp_brain._load_model_bundle` / model-gate (`mcp_brain.py:843+`).

### Three core flaws (what "rebuild" actually fixes)

1. **The promotion gate rubber-stamps overfit models.** The live ensemble's
   own diagnostics: `oos_wr 0.80`, `auc 0.759`, but **`pbo 1.0`** (max overfit)
   and **`deflated_sharpe 0.0008`** (≈ zero). It was promoted because the gate
   is `max_pbo: 1.0`, `min_dsr: 0.0` — permissive enough to allow a maximally
   overfit model.
2. **Probable label leakage.** `embargo_bars=24 < label_horizon_bars=96`.
   Walk-forward embargo shorter than the label's forward horizon lets training
   samples within 96 bars of a test fold carry labels that peek into the test
   period (de Prado purging requires embargo ≥ horizon). This neatly explains
   the contradiction: leakage inflates OOS-WR to 0.80 while PBO=1.0 correctly
   flags overfit.
3. **The model is sidelined.** Its `p_win` only nudges position size ±30%
   (`bot_engine.py:2007`) and a <40% refuse gate. The anti-monotonic rule
   score still drives every entry.

## Goal

Get a **validated** ML signal driving live entries, fast but not recklessly.
Operator decisions from the brainstorm:

- **Pace:** fast — but clearing a minimum safety floor (a model flagged
  PBO=1.0 must not drive real entries unchecked).
- **Deploy path:** leak-check → shadow → live, behind a one-flip kill-switch.
- **Integration:** the validated model **replaces** the rule score as the
  entry gate (the rule score has negative edge; blending drags the model
  down). Rule score is kept computed for logging + fallback only.

## Non-goals

- Greenfield ML rebuild (the substrate works — fix and leverage it).
- New feature engineering beyond what exists (OI was just wired to the
  Claude prompt 2026-05-25; promoting microstructure to scored features is a
  *future* effort, not this one).
- Changing the label definition (keep the 96-bar horizon; fix the embargo to
  match it).

## Design

### Phase A — Leakage check (read-only, hours)

Confirm/kill the `embargo(24) < horizon(96)` hypothesis.

1. Re-run the walk-forward OOS-WR with `embargo ≥ 96` and compare to the
   claimed 0.80. **Expected if leakage:** collapses toward the 0.14 base rate.
2. Audit the feature/label temporal join (`features.ts` vs `labels.ts +
   horizon`) for forward bleed.
3. Verify the PBO fold matrix isn't degenerate.

**Deliverable:** `reports/leak_check_2026-05-25.md` — verdict (real vs
leakage) + corrected honest OOS metrics.

### Phase B — Model readiness (branches on Phase A)

- **Leak confirmed (expected):** set `embargo_bars ≥ label_horizon_bars` in
  the trainer; tighten the promotion gate — `max_pbo 1.0 → 0.5`,
  `min_dsr 0.0 → > 0` (small positive, e.g. 0.10), keep `min_oos_wr 0.55`,
  `min_auc 0.60`. Re-train. The honest metrics decide promotion.
- **80% is real (unlikely):** use the existing ensemble unchanged.
- **Honest stop:** if no model clears the tightened gate, the verdict is
  "current features lack validated edge." Report it; do **not** promote a
  mirage. (Operator chose fast but explicitly accepts a surfaced null result.)

### Phase C — Shadow integration (short window)

- Reuse the `shadow_decisions` table + shadow-predictor path.
- For every entry decision, log what the model *would* do (`p_win`,
  would-enter, threshold) alongside the live rule-score decision.
  **Live behaviour unchanged in this phase.**
- **Advance gate:** ~30-50 shadow decisions where the realized WR of
  would-be entries tracks predicted `p_win` within a calibration tolerance
  (e.g. |realized − predicted| < 0.10 over the window).

### Phase D — Live promotion + kill-switch

- New config flag `MODEL_PRIMARY_ENTRY_ENABLED` (env-driven, default
  **False**). Flipped True only after Phase C passes.
- When True: entry gate = `p_win ≥ MODEL_GATE.threshold_futures` (existing
  key), **replacing** the `score ≥ 65` rule gate. Rule score still computed
  for logging/fallback; if the model bundle is unavailable, fall back to the
  rule gate (fail-safe, never naked).
- **Kill-switch:** flip `MODEL_PRIMARY_ENTRY_ENABLED=false` → instant revert
  to rule-score (the proven `SCALP_TIER_ENABLED` pattern).

## Components & boundaries

| Unit | Responsibility | Touches |
|---|---|---|
| Leak-check harness | Re-run walk-forward with embargo≥horizon; emit verdict | `scripts/` (new), reads warehouse |
| Trainer fix | embargo≥horizon; honest gate thresholds | `core/walk_forward.py`, `scripts/train_models.py`, gate config |
| Shadow logger | Log model would-do per decision | `core/mcp_brain.py` model-gate, `shadow_decisions` table |
| Entry-gate switch | p_win≥thr replaces score≥65 when flag on; fail-safe fallback | `core/bot_engine.py` `_execute_open`, `config.py` |
| Kill-switch | One env flag → revert | `config.py` |

## Error handling / fail-safe

- Model bundle missing/unloadable → fall back to rule gate, log warning, never
  block-naked. (Existing `_load_model_bundle` already returns a NaN-safe stub.)
- `p_win` NaN → treat as below threshold (no entry), log.
- Kill-switch default False means the live path is unchanged until an explicit,
  validated, operator-acknowledged flip.

## Testing (TDD)

- Leak-check: unit test that embargo<horizon produces leakage and embargo≥
  horizon removes it (synthetic labeled series).
- Gate threshold config pins (honest `max_pbo`/`min_dsr`).
- Shadow logger: a decision produces a `shadow_decisions` row with p_win +
  would-enter, and does NOT alter live behaviour.
- Entry switch: with flag True, p_win≥thr enters and rule-score is bypassed;
  with flag False, rule-score path is used; model-unavailable falls back.
- Kill-switch: flag flip reverts in one place.

## Success / stop criteria

- **Success:** a model clears the *tightened* honest gate (PBO<0.5, DSR>0,
  OOS-WR≥0.55, AUC≥0.60), passes the shadow calibration gate, and is promoted
  behind the kill-switch.
- **Stop (honest null):** no model clears the tightened gate → report
  "no validated edge in current features," leave rule-score driving entries,
  and recommend the future feature-engineering effort (microstructure/OI as
  scored signals). Do not promote.

## Risks

- **The likely outcome is the null result.** Once the leak is fixed, OOS-WR may
  collapse to ~base-rate and no model clears an honest gate. That is a *correct*
  finding, not a failure — it means edge requires new features, not tuning.
- Shadow window is short (fast path) → calibration estimate is noisy; the
  kill-switch is the backstop.
- Thin labeled data for some markets; keep models simple + regularized.

## Rollback

- Every phase is behind a flag or is read-only. Live promotion reverts via
  `MODEL_PRIMARY_ENTRY_ENABLED=false`. Trainer/gate changes don't affect the
  live model until a new model is explicitly promoted.
