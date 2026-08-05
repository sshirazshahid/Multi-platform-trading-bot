# 58 — Fable independent verdict: `zfade_4h_cfg365` (2026-08-02)

**Status: INDEPENDENT LEG, SEALED.** Written 2026-08-02 ~04:20Z from `58_s1_dossier_zfade_4h_cfg365.md` only. Codex leg NOT run — usage limit exhausted at probe time (resets 2026-08-08 09:31 local); per protocol §Codex-mechanics the adjudication is PARKED and this verdict is not actionable single-model. Do not include this file in the Codex prompt when the leg runs.

## Q1 — PROMOTE-WORTHY: **NO**

Frozen gate fails on AUC: 0.4018 < 0.6, computed per binding C5 from `shadow_bundle_mr_probe.score` (70/70 coverage) and matching the funnel snapshot. This is a real measurement, not the rsi2-era placeholder. The gate is arithmetic and fail-closed; six passing gates (including net +52.00 after cost, PF 1.519, DSR proxy 0.9297) cannot override one failure. No reviewer argues past a failed threshold.

## Q2 — Disposition: **CONTINUE accruing (option b), log-only, with defined triggers**

Reasons, in order of weight:

1. **This lane is not rsi2.** rsi2 was retired while bleeding (net −73.91, expectancy −0.54); zfade is net-positive after cost with PF 1.52. Retirement-on-first-adjudication was justified there by confirmed forward bleed; here the equivalent forward question — does the after-cost positive expectancy persist? — is still open and cheap to answer.
2. **The expectancy CI includes zero** (t = 1.474, 95% CI [−0.245, +1.731]). n=70 cannot distinguish +0.74/outcome from luck. Continued accrual is the only way this resolves, and it costs zero capital (log-only).
3. **Honest purpose of continuation is measurement, not promotion.** With the frozen score anti-predictive (AUC 0.4018), this lane as-registered almost certainly can never clear the AUC gate — continuation is NOT a path to promoting cfg365. If after-cost expectancy stays positive at materially larger n, that is the "evidence surprises" event that would justify a NEW pre-registered design (rescored) through the reopen bar — a separate, future decision. If it decays, the family record closes cleanly. Either outcome is informative.
4. **Known fragilities are disclosed now** so a future bleed is not re-litigated as a surprise: buy side is −24.26 (the edge is entirely short-side in a downtape window); majors-5 basket is negative (−6.53), profit concentrated in widened alts; all 70 outcomes are post-widen and cross-symbol correlated; WR CI lower bound (0.6399) sits below the 0.6724 breakeven WR. A regime flip to up-tape plausibly kills it.

**Triggers (binding on the loop, not the owner):**
- Re-adjudicate at **≥120 resolved** or on **2026-09-01**, whichever first.
- Early re-adjudication if lane cumulative net after cost turns **negative**, or a risk incident latch fires.
- No probe-config changes (no side filter, no universe change, no re-scoring) without a fresh both-agree — that would be in-flight redesign of a registered measurement.

**Consequence for the shared flag:** CONTINUE keeps `SHADOW_BUNDLE_MR_PROBE_ENABLED` as-is (both arms logging); the deferred rsi2 physical de-registration (48 §2, C3: ADD `=false`, attended restart) is postponed until zfade's own retirement. The rsi2 verdict itself stays final and closed.

## Q3 — Ledger

Tracker row stays OPEN with updated stats. Proposed row text:

> **zfade_4h_cfg365 (bundle-MR shadow, log-only)** — S1 adjudication #1 (2026-08-02): NO-PROMOTE, frozen gate failed 1/7 (score AUC 0.4018 < 0.6, C5-computed, 70/70 coverage; score anti-predictive: mean winner score 0.55 < loser 0.5882). Economics at n=70 (window 2026-07-20→08-01, all post-widen, correlated): net +52.00 after cost, PF 1.519, WR 0.7571 (CP95 [0.6399, 0.8517]) vs breakeven 0.6724, expectancy +0.7429 t=1.47 (CI includes 0). Short-side carries it (+76.26); buys −24.26; majors-5 −6.53. Disposition: CONTINUE accruing to ≥120 resolved or 2026-09-01; measurement, not endorsement; promotion path for cfg365-as-registered is effectively closed by the anti-predictive frozen score. Mean-reversion family status UNCHANGED (refuted for live edge; reopen bar unmet).

## Honesty framing

Forward measurement of a refuted-family probe; expectation stays NO-PROMOTE. The interesting fact is a 70-sample after-cost positive window with an anti-predictive entry score — i.e., any realized value came from the bracket/exit geometry and the short-side tape, not from the score's ranking skill. That is precisely the pattern that decays; the triggers above bound how long we wait to find out.
