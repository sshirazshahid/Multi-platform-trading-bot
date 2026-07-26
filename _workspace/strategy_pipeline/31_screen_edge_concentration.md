# 31 — SCREEN RESULT: is the 0.031 OOS AUC excess UNIFORM or CONCENTRATED?

**Executed:** 2026-07-26T14:45:48.935232+00:00 → 2026-07-26T15:45:30.167173+00:00  
**Pre-registration:** `_workspace\strategy_pipeline\31_prereg_edge_concentration.md` — hash re-verified: **True** (`48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1`)  
**Read-only run.** Warehouse opened `mode=ro`; no `core/`, `config.py`, `.env` or process touched.

## MECHANICAL VERDICT: `INDETERMINATE`

**Computed mechanically from the frozen §11 rule, not from judgement. Decision trace:**

1. **Not CONCENTRATED** — **zero** of the six buckets clear conditions 2–7. Not one bucket
   passes even a single one of the three primary hurdles: no `A_b` reaches the 0.560 floor at
   `p ≤ 0.003125` (best raw p = 0.438), no `D_b` reaches +0.030 at `p ≤ 0.003125`
   (best raw p = 0.386), and no `LCB_b` reaches 0.60 (best = 0.507, i.e. 0.09 short).
   Neither omnibus fires (`p_P1 = 0.454`, `p_P2 = 0.245`), so the partition-level condition
   fails independently.
2. **Not NON_UNIFORM_NOT_NARROW** — that needs ≥2 qualifying buckets; there are 0.
3. **Not UNIFORM** — UNIFORM is an *equivalence* result (§11 STATE 3, adopted from Codex at D9)
   and must be **earned**. It requires all six simultaneous 99.375% intervals on `D_b` to lie
   wholly inside ±0.030. They do not: the widest is `P2_ALT` at **[−0.1291, +0.0531]** and
   four of the six exceed the band. At 30 day-clusters the intervals are simply wider than the
   equivalence margin.
4. ⇒ **INDETERMINATE.**

**What this does and does not say.** It is *not* "we found a pocket" and it is *not* "we
proved uniformity". The measured content is:

- **No bucket comes anywhere near qualifying, and the misses are not near-misses.** The
  strongest bucket, `P2_MAJOR` at `A_b` = 0.5677, misses the frozen `LCB > 0.60` gate by
  **0.12** and carries raw p-values of **0.438 / 0.386** against a threshold of **0.003125**.
  No plausible standard error makes that a marginal call.
- **The measured MDE is 0.65, but it is a LOWER BOUND, not the detectable effect against a
  realistic pocket** — see SG8 below. The injected alternative is i.i.d. per row and therefore
  carries far less day-to-day dispersion in discrimination than the real score does (the real
  `P1_ADXhi_ATRlo` swings 0.463–0.598 across thirds while averaging 0.5565). The day-block
  bootstrap measures precisely that dispersion, so the synthetic alternative is easier to detect
  than a genuine pocket would be. Honest statement: the study detects an i.i.d.-clean 0.65
  pocket with certainty; the detectable effect against a pocket with realistic day-level
  dispersion is larger and **was not measured**.
- **This does not change the answer, and must not be used to re-open it.** The §10 gate fires
  `NO_ANSWER: underpowered` only above MDE 0.80. If the honest MDE were >0.80 the state would
  flip INDETERMINATE → `NO_ANSWER`, and §11's binding clause treats the two identically:
  conditioning work prohibited. No correction to the power curve can reach CONCENTRATED or
  UNIFORM, because those states are decided by the observed statistics, not by the MDE.
- **Homogeneity could not be positively certified** to the ±0.030 margin, because 270,870 rows
  are only **30 independent day-clusters**. This is the honest failure mode the prereg
  anticipated (§10, §12 item 1) and deliberately refused to paper over.

Operationally the two states are identical: **INDETERMINATE and UNIFORM both prohibit further
conditioning work.** Only CONCENTRATED would have permitted one future screen, and it did not
occur. The difference is epistemic, not operational — this run buys the same NO-GO with less
scientific closure than a clean equivalence would have bought.

**Binding on the ledger row.** The row may say the conditioning family is closed
**operationally** — no filter, conditioner, meta-label or regime gate on this signal is
permitted. It may **not** say uniformity was proven. The equivalence certification failed at 30
day-clusters; homogeneity to ±0.030 is **uncertified**, not established. Any ledger text
claiming "uniformity demonstrated on our own data" overclaims this run.

Binding interpretation clause (§11, verbatim): *"Do not relabel failure to find a pocket as scientific uniformity. Both UNIFORM and INDETERMINATE prohibit further conditioning work; only CONCENTRATED permits one future screen."*

## Gates (frozen order)

| Stage | Result |
|---|---|
| S1 reproduction — pooled OOS AUC in [0.520, 0.541] | 0.5346184611639582 → **PASS** |
| S2 machinery (day-block Mann-Whitney == `roc_auc_score`) | max abs err 5.55e-17 → **PASS** |
| S3 unit check (offset-only synthetic, 200 draws) | P(p≤0.05)=0.020 → **PASS** |
| S4 measured MDE (§10) | reported MDE = 0.65 → **ADEQUATE** |

## Measured geometry

- OOS rows **270870** over **30 populated UTC days** and 28 symbols — window 2026-06-10T21:15:07.450302+00:00 → 2026-07-18T10:28:24.553000+00:00.
- Base rate 0.3612.
- **The binding sample size is 30 day-clusters, not 270k rows.** Every interval below is a panel-synchronous circular moving-block bootstrap over those day blocks (B=20,000). The LCB's resolution is limited by the 30 clusters, not by B: the 0.3125th percentile is read off a distribution whose support comes from 30 day draws. §12 item 1 acknowledges this; §11 cond 7 (2-day blocks) mitigates it.

| Bucket | n | share | days | symbols | max sym share | floors |
|---|---:|---:|---:|---:|---:|---|
| P1_ADXlo_ATRlo | 93631 | 0.346 | 27 | 25 | 0.090 | QUALIFIES |
| P1_ADXlo_ATRhi | 56354 | 0.208 | 30 | 26 | 0.117 | QUALIFIES |
| P1_ADXhi_ATRlo | 55634 | 0.205 | 30 | 25 | 0.139 | QUALIFIES |
| P1_ADXhi_ATRhi | 65251 | 0.241 | 30 | 27 | 0.102 | QUALIFIES |
| P2_MAJOR | 54206 | 0.200 | 30 | 5 | 0.228 | QUALIFIES |
| P2_ALT | 216664 | 0.800 | 30 | 23 | 0.069 | QUALIFIES |

## Pooled

Pooled OOS AUC **0.534618**; day-block-clustered 95% CI **[0.5096, 0.5654]** on 30 clusters. Contains 0.500: **False**.

## Per-bucket result

| Bucket | n | A_b | A_not_b | D_b | LCB(1d) | p(A) | p(D) | D 99.375% CI | pocket |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| P1_ADXlo_ATRlo | 93631 | 0.5213 | 0.5410 | -0.0197 | 0.4675 | 0.9702 | 0.9817 | [-0.0855, +0.0428] | no |
| P1_ADXlo_ATRhi | 56354 | 0.5269 | 0.5383 | -0.0115 | 0.4862 | 0.9851 | 0.9899 | [-0.0612, +0.0312] | no |
| P1_ADXhi_ATRlo | 55634 | 0.5565 | 0.5293 | +0.0271 | 0.4898 | 0.5839 | 0.5652 | [-0.0374, +0.1019] | no |
| P1_ADXhi_ATRhi | 65251 | 0.5427 | 0.5307 | +0.0120 | 0.5070 | 0.9074 | 0.8356 | [-0.0354, +0.0528] | no |
| P2_MAJOR | 54206 | 0.5677 | 0.5272 | +0.0405 | 0.4780 | 0.4380 | 0.3860 | [-0.0531, +0.1291] | no |
| P2_ALT | 216664 | 0.5272 | 0.5677 | -0.0405 | 0.4922 | 0.9944 | 0.9828 | [-0.1291, +0.0531] | no |

Thresholds (frozen, m=16, α_test=0.003125): a pocket needs `A_b>0.56` **and** `D_b>0.03` (both at p≤0.003125), `LCB_b>0.6`, ≤25% of rows, both-halves stability, 2-of-3 thirds, and 2-day-block agreement.

## Heterogeneity omnibus (centred on the within-bucket weighted mean)

| Partition | Ā_within | T_obs | p | fires at α=0.003125 |
|---|---:|---:|---:|---|
| P1 | 0.5348 | 1.874e-04 | 0.4544 | False |
| P2 | 0.5353 | 2.620e-04 | 0.2454 | False |

*(§8: P2 has exactly two cells, so `D_ALT = −D_MAJOR` and the P2 omnibus is algebraically a restatement of `D_MAJOR²` — one effective degree of freedom, one line of evidence, never mutual corroboration.)*

## Measured power (§10)

| Target | AUC 0.55 | AUC 0.60 | AUC 0.65 | AUC 0.70 | AUC 0.75 | MDE |
|---|---:|---:|---:|---:|---:|---|
| P1_ADXhi_ATRlo | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.65 |
| P2_MAJOR | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.65 |

## EXPLORATORY — score-decile table (NON-CONFIRMATORY, §D3)

Reported because raising `MCP_ENTRY_MIN_SCORE` is the program's most-proposed filter. It is **not** in the confirmatory family and **cannot move the verdict**.

| Decile | n | base rate | within-decile AUC |
|---:|---:|---:|---:|
| 1 | 28392 | 0.3063 | 0.5553 |
| 2 | 60608 | 0.3492 | 0.5002 |
| 3 | 0 | nan | nan |
| 4 | 19428 | 0.3435 | 0.4957 |
| 5 | 46451 | 0.3525 | 0.5046 |
| 6 | 8619 | 0.3739 | 0.4889 |
| 7 | 32023 | 0.3621 | 0.4978 |
| 8 | 26574 | 0.3861 | 0.4901 |
| 9 | 25312 | 0.3829 | 0.4922 |
| 10 | 23463 | 0.4328 | 0.5316 |

## Reading notes on the tables above

- **`P2_MAJOR` is the closest thing to a pocket, and it is not close.** `A_b` = 0.5677 with both
  chronological halves stable (0.570 / 0.570, `D` = +0.032 / +0.051) — but its own raw p-values
  are 0.438 / 0.386 against thresholds of 0.003125, its `LCB` is 0.478 against a 0.60 gate, and
  only 1 of 3 calendar thirds reaches 0.60. A 5-name bucket with the largest single-symbol share
  in the study (0.228) drifting 0.04 above the rest is what noise at 30 clusters looks like.
- **Two buckets can generate heterogeneity but can never be nominated**, exactly as
  pre-registered at D12: `P1_ADXlo_ATRlo` (34.6% of rows) and `P2_ALT` (80.0%) exceed the ≤25%
  narrowness cap. Both fail every other condition anyway, so the disclosed consequence never
  binds.
- **No Simpson inflation along P1.** `Ā_within,P1` = 0.5348 versus pooled 0.5346 — the two agree
  to 2e-4, so the pooled excess is *not* between-bucket base-rate information along the regime
  axes. The `UNIFORM_BASE_RATE_ARTIFACT` sub-flag (which needs `Ā_within ≤ 0.510`) does not
  apply. Bucket base rates span 0.339–0.395, and the within-bucket AUCs still average to the
  pooled value.
- **The pooled excess is itself small but not attributable to sampling noise at 30 clusters**:
  clustered 95% CI [0.5096, 0.5654] excludes 0.500. (Stated no more strongly than that: §5
  discloses one in-sample blend parameter inside `p_ens`, and SG7's `random_state=0` moved the
  pooled point estimate by 0.0041.) The excess is simply spread too thin, and sits too far below
  the frozen 0.60 AUC gate, for any selection rule in this family to reach it.
- **Decile 3 is empty (n=0)** in the exploratory table. That is a tie artifact, not a bug:
  `p_ens` is a weighted sum of two isotonic step functions, so its 20th and 30th percentiles
  coincide. It is reported rather than smoothed away.

## Deviations and fidelity notes

**Protocol deviations: NONE.** Every frozen element was executed as specified — partition axes,
the cuts 23.20 / 0.94, bucket membership and count, the floors, the statistics, the omnibus
centring, the resampling unit and scheme, both block lengths, B = 20,000, seed 20260726,
m = 16, all thresholds, the stability conditions, and the four terminal states. No orientation
flip exists anywhere in the code path. No re-partitioning, no added axis, no cut adjustment, no
merging or splitting, and no post-hoc subset was examined or is discussed as evidence.

Fidelity notes, disclosed:

1. **Measured geometry matched §14/§7 exactly** — harvest n = 325,047, `test_size` = 54,174,
   `oos_start` = 54,177, `n_oos` = 270,870, 30 populated days, 28 symbols, and all six bucket
   row counts identical to the frozen table. Nothing was trimmed to achieve this.
2. **Reproduction landed at 0.534618 vs the recorded 0.530517**, inside the frozen
   [0.520, 0.541] band. The gap is attributable to SG7 (`random_state=0` per §5 vs the
   trainer's default 42, which moves sklearn's `early_stopping='auto'` validation split), not
   to data drift — the harvest is byte-for-byte the same size as at freeze time.
3. **The unit check's synthetic score reached pooled AUC 0.514, not the ≈0.53 §8 uses
   illustratively.** The construction is exactly as specified (per-bucket constant offset +
   noise ⇒ within-bucket AUC 0.500 by construction, pooled lifted above 0.500 purely by
   base-rate spread) and the check passed decisively (P(p≤0.05) = 0.020 over 200 draws). The
   offset scale that would have hit 0.53 precisely is not specified in the prereg and was not
   tuned toward any target.
4. **The AUC machinery was validated to exactness, not to tolerance.** The day-block
   Mann-Whitney decomposition reproduces `sklearn.roc_auc_score` to 5.55e-17 on all thirteen
   row-sets under a heavily-tied synthetic score, and to **0.0** on the real ensemble score.

## Specification gaps — execution decisions declared before the run

These are clauses the frozen prereg leaves under-specified. Each was resolved by a named constant in `research/screen_edge_concentration.py` **before** execution. They are execution decisions, not deviations from a specified value.

- **SG1** — §10 MDE — 'inject ... into one bucket' never names which bucket.  
  → Primary target = the smallest P1 cell that clears the §11 cond-4 <=25% narrowness cap (smallest = hardest to detect = conservative MDE). Secondary target = P2 MAJOR (fewest symbols, largest single-symbol share). The REPORTED MDE is the larger (more conservative) of the two.
- **SG2** — §10 — 'detected' is not defined.  
  → Detected = terminal state CONCENTRATED **and** the single qualifying bucket is the injected one (faithful: the full decision rule must fire on it). The softer diagnostic 'injected bucket cleared conditions 1-7 regardless of terminal state' is reported alongside, so an artifactual power loss (a second bucket qualifying because injection into a P1 cell perturbs P2) is visible.
- **SG3** — §8 — 'near-uniform omnibus p' has no numeric pass criterion.  
  → 200 independent synthetic draws; PASS iff the empirical P(p <= 0.05) <= 0.10. A single draw cannot evidence uniformity of a p-value.
- **SG4** — §7.3 / §11 cond 5 — 'chronological half' is not defined.  
  → Halves = the ORDERED LIST of populated OOS UTC days split 15/15 (consistent with §11 cond 7, which says 'ordered list' explicitly). Thirds keep the explicit CALENDAR boundaries §11 cond 6 gives.
- **SG5** — §8 — omnibus replicate weights are written as n_k/N_P without saying whether n_k is the observed or the replicate count.  
  → Fixed OBSERVED n_k/N_P for both observed and replicate T (literal reading, lower variance).
- **SG6** — §11 cond 7 — '2-day blocks pair consecutive entries ... -> 15 blocks'.  
  → Days are paired (1-2, 3-4, ...) into 15 fixed super-clusters, then the same circular moving-block bootstrap runs over those 15 units.
- **SG7** — §5 specifies GBMModel(random_state=0); scripts/train_models.py uses the class default random_state=42, and sklearn's early_stopping='auto' fires above 10k rows so the choice moves p_ens.  
  → The prereg is the binding document: random_state=0 is used. If the reproduction gate FAILS, the mechanical outcome is NO_ANSWER: reproduction — the seed is NOT switched to obtain a passing gate (that would be the sample/threshold tuning §12 lists as invalidating). A random_state=42 pooled AUC is then additionally reported, labelled EXPLORATORY, purely so the owner can see whether the halt came from the seed inconsistency rather than data drift.

### SG8 — identified in POST-RUN review, NOT declared before the run

Recorded separately and honestly: unlike SG1–SG7 above, this gap was **not** foreseen and
**not** pinned by a pre-run constant. It was found while reviewing the finished power curve.

**Clause.** §10 specifies the injected effect sizes (`{0.55 … 0.75}`), the count (200 sims), and
the power bar (80%), but says nothing about the injected score's **dependence structure**.

**What was executed.** `synth_auc_score` draws `mu·y + N(0,1)` i.i.d. per row and rank-maps it
onto the bucket's real score marginal. That preserves the marginal but gives the injected bucket
almost no day-to-day dispersion in discrimination.

**Consequence, disclosed.** The day-block bootstrap's whole job is to price day-to-day
dispersion, so an i.i.d. injection is systematically easier to detect than a real pocket would
be. The step from power 0.00 at 0.60 to 1.00 at 0.65 with zero variance at both ends is the
signature of this. Cross-check against the real data: `P2_MAJOR` shows `A_b` − `LCB` = 0.0897,
implying a cluster SE ≈0.033; for a synthetic 0.65 bucket to clear `LCB > 0.60` in 200/200 sims
its SE must be under ≈0.018 — roughly half. **MDE = 0.65 is therefore a lower bound.** The same
mechanism inflates the condition-6 (thirds) pass rate.

**Why it was NOT re-run with a clustered injection.** Changing the injection's dependence
structure after seeing the outcome is closer to §12's "outcome information influencing … the
decision threshold" than to a fix. The frozen design was executed as written and the shortfall
is disclosed instead. A future pre-registration should pin the injected alternative's cluster
structure explicitly.

## Scope binding (§13)

Any verdict here is bound to **this** signal, **this** window (30 populated days, 2026-06-10 → 07-18, a single regime), **this** six-bucket conditioner family, at the **measured MDE**. It does not close conditioning across regimes and does not test the BTC-regime or idiosyncratic-vol axes (D2). No finite bucket experiment can prove uniformity over every measurable filter; the universal closure comes only from the separate analytic result that when the primary side carries zero directional information, `E[filter × side × return] = 0`.

