# 39 — Pre-registration v2: clamp-print zero-information screen (F1 measurement hygiene)

**Status:** FROZEN_PREREG-DRAFT (hash at screen run time, per pipeline rule 2026-07-17)
**Date:** 2026-07-28 · **Class:** measurement-correctness overlay on F1 — NOT a strategy family
**Approval:** ai-reviewer APPROVE 2026-07-28 (Candidate 2), with binding revisions R1/R2 incorporated
**Proposes NO trade, NO entry/exit rule, NO order path. Success case: "measurement confirmed, no new trades."**

## 0. Supersession and burn disclosure (read first)

This document supersedes `38_prereg_clamp_print_information.md`, which is **BURNED** and must
never be hashed or cited as a prereg: it tabulated per-cell outcome statistics (unconditional
next-settlement sign rates and per-cell clamp WR) before any hash existed. 38_ is retained
byte-intact as an audit artifact and is hereby designated a **disclosed design set**.

What that burn means for THIS document, stated honestly:
- It is KNOWN from the design set that unconditional next-settlement-positive base rates are
  high (majority-positive) in most populated cells, and therefore that any bare
  `WR_clamp >= 0.55` rule is a threshold artifact that would "falsify" on base rate alone.
  That knowledge motivated the corrected decision rule in §6 — it does not decide it.
- The sole discriminating statistic in §6 (the stratified clamp-vs-control excess) has
  **never been computed**. It is the only quantity this prereg is judged on.
- No per-cell outcome number from the design set appears in this document, and none may be
  quoted in the screen report except as a reproduction of the screen's own fresh computation.

## 1. Null hypothesis

Funding prints equal to the venue baseline ("clamp prints") on thin alt perps carry **zero
incremental positioning information**: conditional on time and market state, a clamp print
predicts the SIGN of its own next settlement **no better than contemporaneous non-clamp
positive prints**.

Falsification is exclusively via the stratified excess test in §6. Motivating evidence
(verified 3-0, 2026-07-28 corpus): +0.0100% printed identically across six venues for XMR
simultaneously; Hyperliquid natively reports 0.00125%/1h = exactly 0.01% renormalized to 8h —
an aggregator rescaling a floor, not independent premia.

## 2. Data (frozen)

- Path: `data/funding_history/*.csv` — local, already harvested. **No fetch, no backfill.**
- Files: 510 (binance 217 / bybit 158 / bitget 135); schema `{ts, funding_rate, venue, symbol}`,
  `ts` = epoch seconds of settlement.
- Staleness: most files 74–418h behind now, plus 15 dead/delisted files up to ~28,000h stale.
  This bounds **recency**, not validity — the measurement concerns settlements that already
  occurred. **Delisted files are INCLUDED**: excluding them would inject survivorship bias
  into a measurement-hygiene screen.

## 3. R1 — regime-aware baseline (binding)

The funding interval is time-varying per symbol (design-set structure: bybit TAO's own file
contains 1,053 consecutive 8h deltas and 3,338 4h deltas around a single switch at
2025-01-16T04:00Z). Therefore:

- **Regime per row:** trailing median of the previous 10 consecutive-ts deltas, snapped to the
  nearest of {1h, 2h, 4h, 8h}. Rows with <10 predecessors in-file: use all available (min 3);
  fewer than 3 → row excluded from both arms.
- **Baseline per row:** `baseline(regime) = 1e-4 × (regime_hours / 8)` — i.e. 8h→1.0e-4,
  4h→5.0e-5, 2h→2.5e-5, 1h→1.25e-5. Design-set verified as the modal printed rate in every
  populated (venue, regime) cell; recorded here as predictor-side characterization only.
- **Clamp print:** `abs(funding_rate − baseline(regime)) < 1e-9`.
- **No global constant. No live-metadata lookup.** Today's `fundingInterval` does not describe
  historical rows and is not consulted.
- Worked example (structure only): bybit TAO 2024-02-10T00:00Z row → trailing deltas 8h →
  baseline 1.0e-4; 2025-01-17T00:00Z row → trailing deltas 4h → baseline 5.0e-5. A global
  `abs(rate − 1e-4)` rule would misclassify every post-switch baseline print.

## 4. R2 — baseline-clamp only (binding)

This screen detects **baseline** clamps only. Venue **cap** clamps
(`upperFundingRate` / `lowerFundingRate` / `adjustedFundingRateCap`) are excluded: those bounds
are current-only and per-symbol; no local archive of historical bounds exists, so historical
cap-clamp detection is unsupported by the data and is NOT claimed.

## 5. Cells, multiplicity, Stage-0

- **Cells:** (venue ∈ {binance, bybit, bitget}) × (regime ∈ {1h, 2h, 4h, 8h}) = **m = 12, FIXED**.
- Bonferroni α per cell = 0.05 / 12 ≈ 0.004167. **Stage-0 attrition does NOT shrink m.**
- **Stage-0 feasibility (stopping rule only):** a cell is testable only with ≥ 30 **informative
  strata** — settlement timestamps at which BOTH arms (§6) are non-empty, counted on distinct
  timestamps. Fewer → that cell is INSUFFICIENT_DATA. (A "≥30 clamp timestamps" count is NOT
  sufficient — it passes on arithmetic in many-symbol cells while leaving the control arm empty.)
- Design-set structure already indicates bitget-1h and bitget-2h are structurally absent and
  binance-2h has no informative strata; they remain in m regardless.

## 6. Decision rule (the only outcome computation this prereg authorizes)

Per cell, per settlement timestamp t (stratum):
- **Clamp arm:** symbols whose print at t is a clamp print (§3) with positive sign.
- **Control arm:** symbols whose print at t is positive and NOT a clamp print.
- Outcome per symbol: sign of that symbol's own NEXT settlement print (positive = success).

Test: **Cochran–Mantel–Haenszel** common odds ratio across strata, clamp vs control,
two-sided, α = 0.004167 per cell.

- **Null RETAINED** (expected): clamp arm does not differ from control → clamp prints carry no
  incremental information. Operational meaning: a clamp-aware filter is justified as
  measurement hygiene in F1 telemetry and any future funding-conditioned screen — still not a
  trade, and no F1 parameter changes.
- **Null FALSIFIED** (surprise): clamp prints predict differently from matched controls
  (either direction). Operational meaning: a genuine anomaly requiring its OWN separately
  pre-registered screen before any use. Nothing auto-promotes.

The prior draft's `WR_clamp ≥ 0.55` floor is **deliberately dropped**: the design set shows it
is non-discriminating here (base-rate-satisfied), and a known-satisfied condition inside a
falsification rule is exactly the outcome-burn the pipeline forbids.

**Pre-registered confound note (binding):** 1h-regime cells are dominated by hot new listings;
any 1h-cell falsification must be attributed to volatility-state persistence unless that
explanation is separately excluded. Named now so it is a finding, not a post-hoc excuse.

## 7. Not authorized

No probe. No order-path or paper-order change. No promotion. No F1 parameter change. No edit
to 38_ (audit artifact). Screen executes via `strategy-evidence-pipeline` (edge-screener ↔
honesty-auditor, both-agree verdict); this file is content-hashed immediately before that run,
and the hash recorded in the screen artifact.

## 8. Expectation

**Null RETAINED.** Stated plainly per pipeline discipline: the expected outcome is that clamp
prints carry no information, the ledger gains a measurement row (not a strategy row), and no
trades result.
