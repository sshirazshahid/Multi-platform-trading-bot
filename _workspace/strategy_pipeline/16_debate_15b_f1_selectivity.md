# 16 — Multi-model debate record: 15b F1 funding-percentile persistence selectivity

Run: strategy-evidence-pipeline 2026-07-16 (record written 2026-07-17 00:xx local).
Screen on trial: `_workspace/strategy_pipeline/15b_screen_f1_selectivity.md` / `.json`,
code `research/screen_f1_percentile_selectivity.py`. Screener verdict on trial: **NO_GO**.
Auditors: Sonnet 4.6, Opus 4.8, Fable 5 (independent attack sets). Reconciler: Fable (this document).
All adjudications below were made against the primary artifacts with independent read-only
recomputation via `venv/Scripts/python.exe` (script in session scratchpad, not committed).

## FINAL STATUS: **CONFIRMED_NO_GO**

All three auditors independently returned `verdict_stands: true` at high confidence, and the
reconciler's recomputation reproduces every controlling number bit-exact. The NO_GO is
overdetermined by two regime- and dependency-independent failures (harvest guard 0.21x,
2x-cost stress negative) that no valid finding touches. The VALID findings below change how
the result must be **recorded and reused**, not what it is.

---

## 1. Reconciler recomputation (independent, read-only)

| quantity | screen JSON | recomputed | match |
|---|---|---|---|
| episodes (inc / V1) | 95 / 46 | 95 / 46 | exact |
| total net bps (inc / V1) | 33,097.377 / 6,964.4633 | 33,097.377 / 6,964.4633 | exact |
| harvest ratio | 0.21x | 0.2104 | exact |
| eff bps/settle (inc / V1) | 8.6012 / 10.7642 | 8.60119 / 10.76424 | exact |
| pooled delta | +2.1631 | +2.16305 | exact |
| bootstrap delta CI-LB | −0.18182 bps | −0.18182 bps | exact |
| fold deltas | [+2.718, −0.529, ∅, ∅, +0.801] | identical | exact |

Additional facts established (not in the screen report):
- Episode entry years: incumbent {2021: 92, 2024: 3}; V1 {2021: 42, 2024: 4}. **Zero episodes
  in 2022, 2023, 2025, 2026 in either arm.** Last entry either arm: **2024-12-07 16:00 UTC**
  (bybit ZEC). 96.8% / 91.3% of episodes are calendar-2021.
- Fold boundaries: f0 2021-01-08..2021-10-20 (inc 82 / V1 36), f1 2021-10-20..2022-08-02
  (10 / 6), f2 and f3 EMPTY both arms, f4 2024-02-25..2024-12-07 (3 / 4).
- Delta CI-LB under alternative dependency models (same episodes, seed 7, 2000 resamples):
  independent per-arm (the screen's frozen design) = **−0.18** (FAIL);
  coin-(venue,base)-clustered joint = **+0.53** (would PASS);
  calendar-quarter-block joint = **−4.17** (fails hard; Opus got −3.14 with a different
  block construction — same sign, same conclusion). The gate's pass/fail flips with the
  dependency model.
- Scout's own stated metric (net carry per unit round-trip cost): incumbent 6.850 vs
  V1 2.976 — the variant loses 57% on the metric the scout registered.
- Git: all four artifacts (`15b*.md`, `15b*.json`, screen script, tests) are untracked
  (`??`), no commit history — the "frozen before results" claim is self-attested only.

---

## 2. Per-model attack summaries

**Sonnet 4.6** — verdict stands, high confidence. 2 MAJOR: (M1) episode mass concentrated in
Jan–Oct 2021 across many coins simultaneously → effective independent n overstated for
DSR/OOS-WR/MC; (M2) zero episodes in the trailing ~19 months despite the data-currency
headline; incumbent F1 itself appears to generate no new entries — undisclosed, owner-relevant.
4 MINOR: 90.3%-zero PBO/DSR grid; unpaired bootstrap may over-penalize (pro-strategy);
venue-sign gate weaker bar than CI gate; freeze unverifiable in git.

**Opus 4.8** — verdict stands, high confidence. 4 MAJOR: (M1) NO_GO rests on ONE robust leg
(harvest_guard, arithmetic/structural); the two statistical co-indictments are low-power
single-regime artifacts; (M2) ~96% single-2021-regime evidence base, undisclosed — every
passing gate is a 2021 measurement; (M3) flat "refuted" ledger row over-claims — mechanism
claim structurally refuted, efficiency-edge sub-claim UNTESTABLE in the target 2025-26
compressed regime → row needs a regime-scope caveat; (M4) delta CI-LB unidentified — flips
sign with the dependency model (independent −0.18 / clustered +0.53 / quarter-block −3.14).
2 MINOR: screen's primary endpoint deviates (generously) from the scout's stated metric;
freeze unverifiable in git.

**Fable 5 (auditor)** — verdict stands, high confidence. 2 MAJOR: (M1) single-regime 2021
study, last entry 2024-12-07, undisclosed in the report; incumbent-idle corroboration of
carry compression; (M2) fold-sign gate structurally unsatisfiable for ANY variant (folds 2–3
empty both arms fail closed; empty-fold semantics never pre-defined) — must be fixed before
any re-registration or that screen is pre-decided. 4 MINOR: entry-print approximation is
pro-variant (not delta-neutral as prereg asserts; delta +2.16 → +1.81 excluding it); iid
treatment too generous everywhere but all affected gates failed anyway or passed
non-bindingly; unpaired bootstrap possibly over-penalizing (cannot flip arithmetic harvest
fail); freeze unverifiable in git.

---

## 3. Adjudication of FATAL and MAJOR findings

No FATAL findings were raised. MAJOR findings, deduplicated where the same defect was found
independently:

| # | finding (auditor) | adjudication | evidence (recomputed) |
|---|---|---|---|
| 1 | 2021 regime concentration, undisclosed; effective independent n overstated (Sonnet M1, Opus M2, Fable M1) | **VALID** | inc 92/95 and V1 42/46 enter in 2021; zero episodes 2022/2023/2025/2026; fold reconstruction matches Sonnet bit-exact (82/36, 10/6, 0/0, 0/0, 3/4). The .md discloses only "folds 3–4 empty"; it never states 96% single-regime or the effective-n consequence for DSR (sr_var=1/n on 46), OOS-WR, MC. Direction: strengthens NO_GO; indicts the *passing* gates' apparent strength, not the failing ones. |
| 2 | Zero data in trailing ~19 months despite "verified current to 2026-07-16"; incumbent F1 itself produces no qualifying replay entries after 2024-12-07 (Sonnet M2, Fable M1) | **VALID** | Last entry either arm 2024-12-07 16:00 UTC (bybit ZEC). Data currency claim is technically true (CSV tails = 2026-07-16) but the report never says the last *episode* is 19 months old. Owner-relevant beyond this screen: the in-soak F1 incumbent gate appears structurally idle in the current compressed-funding regime — consistent with the scout-cited Borri et al. 2025 carry-compression finding. |
| 3 | NO_GO rests on harvest_guard as the sole robust leg; the two statistical failing gates are low-power/single-regime and should not be recorded as co-equal indictments (Opus M1) | **VALID** (with one addition) | harvest_guard is arithmetic on reproduced episodes: 6,964.4633 / 33,097.377 = 0.2104 vs 0.75 floor; driven by construction (decay exit fires 42/46, mean hold 14.1 vs 40.5) — regime- and dependency-independent. Addition: the 2x-cost stress row (V1 total −49 bps, NEGATIVE; incumbent +5,708) is a SECOND structural indictment of the same mechanism, so the verdict stands on two robust legs, not one. The CI-LB (finding 5) and fold-sign (finding 6) gates are confirmed fragile and are hereby demoted to supporting, not controlling, reasons. |
| 4 | Flat "refuted" ledger row over-claims scope; needs regime-scope caveat (Opus M3) | **PARTIALLY VALID** | The registered mechanism claim ("fewer, LONGER, richer episodes ... without gutting the absolute harvest") is refuted structurally and in DIRECTION (episodes came out shorter, 14.1 vs 40.5, and poorer, 151 vs 348 bps) — that refutation is regime-independent and the row is legitimate. But the efficiency-EDGE sub-claim in the candidate's own target regime (2025-26 compression) has zero testable episodes → UNTESTABLE, not refuted. Binding on the ledger row wording (see §5); does not change the verdict. |
| 5 | Delta CI-LB is unidentified — independent per-arm bootstrap ignores subset/cluster structure; pass/fail flips with the dependency model (Opus M4; pro-strategy halves of Sonnet minor-4 / Fable minor-4 fold in here) | **VALID** | Reconciler reproduction: independent −0.18 (FAIL, exact); coin-clustered joint +0.53 (PASS — matches Opus to 2dp); quarter-block joint −4.17 (hard fail; Opus −3.14 with different blocks, same sign). The −0.18 number is real under the *frozen pre-registered* design (no protocol violation) but must not be cited as an independent statistical refutation. Cannot flip the verdict: findings 3's two structural legs are untouched by any CI methodology. |
| 6 | Fold-sign gate structurally unsatisfiable for ANY variant on this data; empty-fold semantics not pre-defined (Fable M2) | **VALID** (non-binding here) | Folds 2–3 confirmed empty in BOTH arms → `signs=[1,−1,None,None,1]`, `all_positive` unreachable regardless of variant quality. The prereg text "positive in ALL 5 calendar folds" reads naturally as fail-closed (an empty fold cannot be positive), so this is a design defect, not a post-hoc manipulation — but the gate as constructed was pre-decided and MUST be re-specified (min-episode fold floor or evaluable-folds-only rule, pre-registered) before any reuse. Non-binding for this verdict: fold 1 is sign-negative outright (−0.529 bps) — though per findings 1/3 that fold (10 vs 6 episodes) carries little evidential weight either way. |

**MINOR findings:** all six auditor-distinct minors (zero-heavy PBO/DSR grid; venue-gate
weaker bar than CI gate; entry-print pro-variant bias, delta +2.16 → +1.81 excluded;
endpoint deviation from the scout's stated metric — recomputed: net/RT-cost 6.850 vs 2.976,
V1 −57%, so the screen's endpoint choice FAVORED the variant and it still failed; iid
generosity toward the passing gates; git-unverifiable freeze, confirmed `??` on all four
artifacts) are **recorded as stated**. None is disputed by the artifacts, none flips any
controlling gate, and each either cuts toward leniency-on-the-variant or touches only
gates that passed non-bindingly.

---

## 4. Material dissent, preserved verbatim

The one genuine inter-auditor disagreement is the evidential meaning of fold 1
(2021-10-20..2022-08-02, the only non-empty fold outside the 2021 mania peak):

> **Sonnet 4.6:** "if anything it strengthens the fold-sign-instability failure, since fold1
> (the one fold outside the mania cluster with a meaningful sample) is genuinely negative
> (-0.53bps), and the 'passing' confirmatory gates (DSR=1.000, MC P=1.000) plausibly look
> stronger than the true regime-adjusted uncertainty supports."

> **Opus 4.8:** "delta_ci_lb (-0.18) and fold_sign ([+,-,null,null,+]) are marginal/noisy ...
> the fold-sign gate is decided by fold2 (6 V1 vs 10 inc) and fold5 (4 vs 3)." [Opus's
> 1-based fold2 = Sonnet's 0-based fold1 — same fold.]

**Reconciler ruling:** Opus is right that 10-vs-6 episodes cannot support a confident sign
claim in either direction; Sonnet is right that no evidence anywhere in the sample shows the
edge surviving outside the 2021 regime. Both readings converge on the same operational
conclusion: the fold-sign and CI-LB gates are demoted to supporting evidence, and the NO_GO
is recorded as standing on the harvest guard + 2x-cost stress (structural) with the
statistical gates as corroboration only. The dissent is immaterial to the outcome.

---

## 5. Final reasoning and binding follow-ups

**CONFIRMED_NO_GO.** The verdict survives its strongest attack: every auditor tried to break
it and instead reproduced it. The two controlling failures are arithmetic consequences of the
variant's own construction (the trailing-median decay exit guts 79% of the harvest and
de-amortizes round-trip costs to the point of negativity under 2x stress) and are invariant
to regime weighting, dependency modeling, or bootstrap design — the three genuine
methodological soft spots found. The pooled +2.16 bps/settlement efficiency delta remains
unproven (unidentified CI, single-regime sample) and irrelevant while the harvest guard fails.

**Binding follow-ups (owner-visible):**
1. **Ledger row wording (required):** add the row scoped as — "F1 percentile+persistence
   selectivity (P75, trailing 270/90-settle windows, persistence 3/2) WITH trailing-median
   decay exit: mechanism claim REFUTED (structural — harvest 0.21x, 2x-cost negative;
   ~96% of evidence 2021 funding-bull regime); efficiency-edge sub-claim UNTESTABLE in the
   target 2025-26 compressed regime (zero qualifying episodes after 2024-12-07)." Not a flat
   unqualified "refuted".
2. **Owner alert (orthogonal to this screen, more urgent):** the incumbent F1 replay gate
   itself produces ZERO qualifying entries after 2024-12-07 on 30/30 current series — the
   validated F1 carry lane in PAPER soak may be structurally idle in the current funding
   regime. This corroborates the external carry-compression finding the scout cited and
   should be checked against the live F1 runner's actual entry log.
3. **Before any entry-only re-registration** (the screen's own named follow-up): (a) fix the
   fold-sign gate's empty-fold semantics (pre-registered min-episode fold floor or
   evaluable-folds-only rule) — as constructed it is unpassable on this data for any variant;
   (b) pre-register a paired/clustered delta bootstrap (episodes share coins, venues, and
   regime); (c) disclose regime concentration and last-episode date up front; (d) commit the
   frozen pre-registration to git BEFORE the run so the freeze claim is verifiable.

— Reconciler (Fable), strategy-evidence-pipeline debate close, 2026-07-17.
