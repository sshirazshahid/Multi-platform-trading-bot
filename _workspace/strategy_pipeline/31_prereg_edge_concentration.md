# 31 — PRE-REGISTRATION: Is the 0.031 AUC excess UNIFORM or CONCENTRATED?

**Status:** FROZEN. Committed BEFORE any outcome statistic is computed.
**Date frozen:** 2026-07-26
**Pipeline rule invoked:** prereg commit/hash BEFORE run (binding since 2026-07-17).
**Scope:** read-only research on frozen warehouse rows. No `core/` change, no `config.py`
change, no `.env` change, no restart. The bot continues running in PAPER, untouched.

---

## 1. Hypothesis and expectation

**The question (the only one this run answers).** The bot's entry signal measures pooled
out-of-sample AUC **0.5305171091569454** on **270,830** rows (ensemble refit
`ens_futures_v1_20260725_171507`, 2026-07-25; DSR 0.00, PBO 0.371, gate-refused). Excess over
chance = **0.031**.

Is that excess **UNIFORM** across feature space (a thin film of nothing, spread everywhere), or
**CONCENTRATED** in an identifiable subset (a real but narrow pocket)?

**H0 (the registered expectation): UNIFORM.** The 0.031 excess is homogeneous across every
pre-specified conditioner in the frozen family; no subset carries materially stronger
within-subset discrimination.

**Stated expectation up front, before any outcome is read: UNIFORM / NO-GO for any filter.**
The burden of proof rests entirely on a narrowly defined, multiplicity-corrected,
stability-checked result to overturn it. Nothing in this document may be read as anticipating
a pocket.

**Why both outcomes are valuable.**

- **UNIFORM** ⇒ no filter, conditioner, meta-label or regime gate can extract profit from this
  signal within the frozen family, because there is no subset to select. Combined with the
  already-established analytic proof — if the primary side carries no directional information
  then `E[filter × side × return] = 0` for every measurable filter, so net expectancy =
  `−cost × P(fire) < 0` — this closes the "condition the existing signal" family **on our own
  data** for this signal and this window. That ends a large class of future spend.
- **CONCENTRATED** ⇒ the identified cell is the *only* region worth one future, separately
  pre-registered, untouched-OOS screen. It earns no trading permission and relaxes no gate.

**Binding context (measured facts; not re-derived here, not contradicted).**
~2,400 refuted pattern tests. Every pre-registered screen this month returned NO_GO (C1 CFTC,
VPIN, C3 quarter-hour, wrapper-discount, F1-selectivity, stablecoin-depeg). 30-day directional
expectancy −0.24R. Three shadow probes hit their 30-outcome floors on 2026-07-26 and all were
gate-blocked (tsmom WR 0.333; zfade WR 0.567; rsi2 WR 0.60 — a 60% win rate still lost money).
At n=30 a probe's AUC 95% CI is ≈[0.29, 0.72]; three such intervals are **not** three
confirmations. The binding statistical fact is the large-sample 0.531. Naive Hanley–McNeil /
DeLong iid standard errors are **wrong** on this dataset and are prohibited below. Frozen gate
is arithmetic: MIN_DSR ≥ 0.10, MAX_PBO ≤ 0.5, OOS-WR ≥ 0.55, AUC ≥ 0.60. Capital ≈ $420 retail.

**The central trap this design must defeat.** Partitioning feature space and hunting for a "hot"
bucket **is** mass-variant mining — the exact methodology this repo has already refuted (443+
formulaic alphas, 1,989 candlestick tests, 0/16 band buckets under Bonferroni). A design that
finds a hot bucket without rigorous multiplicity control has found nothing. Everything tunable
is therefore fixed, numerically, below, and hashed before the harvest runs.

---

## 2. Provenance of this reconciliation

Three independent designs were commissioned. This document is the reconciled freeze.

| Source | Status | Contribution surviving into the freeze |
|---|---|---|
| **CLAUDE** | Delivered | Partition axes and frozen numeric cuts; day-block clustering; within-bucket AUC + heterogeneity omnibus centred on the within-bucket weighted mean; measured-MDE power gate; base-rate-artifact classification; scope-bound ledger language |
| **CODEX** (GPT-5.6-Sol, codex-cli 0.144.5) | Delivered (`31_design_codex.md`) | The interaction statistic `D_b`; equivalence-based UNIFORM (not failure-to-reject); the four-state terminal rule; larger row/minority floors; panel-synchronous circular block bootstrap; B=20,000; the ≤25%-of-rows narrowness cap |
| **CURSOR** | **UNAVAILABLE — no design received** | none |

**CURSOR unavailability is recorded, not silently dropped.** Every field of the Cursor design
is absent: partition scheme, bucket count, minimum cell size, test statistic, multiplicity
control, decision rule, falsification, traps addressed. Invocation log: attempt 1 failed the
workspace-trust gate (`EXIT=1`, stderr "Workspace Trust Required"); attempt 2 with `--trust`
passed the gate but had emitted no stdout and not exited when the structured answer was demanded
(`cursor-agent` buffers `-p --output-format text` output until completion, so 0 bytes is
consistent with "still thinking", not with failure). No placeholder design was fabricated.

**Meta-consequence, recorded as a limitation of this freeze:** the design was frozen on **2 of
3** intended independent designs. There is one fewer independent check on design fragility than
the protocol intended. This does not invalidate the freeze — the two received designs disagreed
substantively and productively — but a future reader must not treat the merge as a
three-way consensus.

---

## 3. Adjudication rule used to reconcile

Instruction: where the models disagree, choose the **more conservative** option (fewer buckets,
stricter multiplicity control, larger minimum cell size), and record every disagreement.

That instruction has one failure mode here, and the rule below is what prevents it:

> **Conservatism is applied within the feasible set.** A floor that no partition of this dataset
> can satisfy is not a stricter test — it is a different dataset's test. Where a model's floor is
> satisfiable on the measured geometry, that model's number is adopted. Where it is not, the
> strictest *satisfiable* analogue is adopted and the substitution is recorded together with the
> measured reason it was necessary.

Mechanically adopting Codex's unsatisfiable floors would render the run **NOT EXECUTED**, which
answers nothing and burns the pre-registration. That is a worse outcome than either verdict.

Where a hurdle from each model is independently satisfiable, **both are required** (union of
hurdles), which is strictly harder than either design alone.

---

## 4. Recorded disagreements and their resolutions

Disagreements are evidence about design fragility. All are recorded, including the ones where
one model was simply factually wrong about the data.

### D1 — Number of buckets. **CLAUDE 6, CODEX 8.**
Resolution: **6** (fewer buckets = more conservative, per instruction). Codex's third axis is
dropped; see D3.

### D2 — Partition axes. **CLAUDE** `adx_4h × atr_pct_1h` (regime family) + `MAJOR/ALT` (name-selection family). **CODEX** `|logit(p)| × idiosyncratic-vol × BTC-7d-return`.
Resolution: **Claude's axes**, for three reasons. (a) They are `FEATURE_KEYS[7]` and `[8]`, read
straight off the model's own input matrix, so null/non-finite handling is bit-identical to what
the model saw — no JSON re-parse, no reconstruction risk. (b) Codex's `V` and `M` axes require
per-symbol return histories and a BTC price series that are **not** in the frozen `X` matrix and
would have to be reconstructed from a different source, introducing an unfrozen join. (c) They
span the two filter families this repo actually keeps proposing: regime gates and name selection.
**Recorded cost:** Codex's BTC-regime axis is not tested. The verdict's scope is narrowed
accordingly in §13.

### D3 — Conditioning on the score itself. **CODEX** includes `C = |logit(p)|` as an axis. **CLAUDE** excludes it as circular ("that is the ROC curve, not a feature-space subset").
Resolution: **excluded from the confirmatory family** (Claude). Selecting on the score and then
measuring the score's discrimination is a property of the ROC curve, not a statement about
feature space. **But** raising `MCP_ENTRY_MIN_SCORE` is the single most frequently proposed
filter in this program, so refusing to look at it invites exactly the post-hoc "one more look"
this design forbids. Handling: a **score-decile table** (n, base rate, within-decile AUC) is
reported as **EXPLORATORY**, is marked non-confirmatory in the output, and **cannot move the
verdict** in any direction. It exists so the owner's most common filter proposal is visibly
addressed and permanently on the record.

### D4 — Minimum cell size. **CODEX far stricter, and partly unsatisfiable on this dataset.**
This is the most important recorded disagreement.

Codex required, per cell: ≥12,000 rows; ≥2,500 of each label; **≥35 of 43 symbols represented**;
**≥30 non-overlapping 28-day panel blocks overall and ≥15 per chronological half**.

Measured reality (verified today, §12): the `labels` table for `futures` spans
**2026-04-14 → 2026-07-25 = 68 populated UTC days**, holds **35 distinct symbols in total**, and
the OOS window holds **28 symbols over 30 populated UTC days (≈38 calendar days)**. Labels are
15-minute bars with a 96-bar (24h) horizon, not 4-hour bars.

Therefore:
- "≥35 of 43 symbols" is **impossible** — only 28 symbols exist in the OOS window at all, so no
  cell can represent 35.
- "≥30 non-overlapping 28-day blocks" requires ≈2.3 years of span; the OOS window is ≈38
  calendar days, which contains **at most one** such block.

Resolution under §3: **adopt every Codex floor that is satisfiable, substitute the strictest
satisfiable analogue for the rest.** Final floors in §7. Codex's ≥12,000 rows and ≥2,500 minority
are adopted verbatim (strictly stricter than Claude's 10,000 / 2,000, and all six buckets clear
them). The symbol floor becomes ≥20 for the four P1 cells (measured 25–27) and ≥5 structurally
for P2 MAJOR (a 5-name basket by construction). The block floor becomes ≥20 populated UTC days
overall and ≥8 per chronological half.

**Recorded as evidence about design fragility:** Codex produced these floors *without touching
the data*, and specified a universe size (43), a bar interval (4h), and a history length (~2.3yr)
that the dataset does not have. A design authored against assumed geometry can silently
self-invalidate before a single statistic is computed. Claude flagged this same risk in its own
submission without verifying it; this document verifies it (§12).

### D5 — Test statistic. **CLAUDE** within-bucket AUC + heterogeneity omnibus. **CODEX** within-cell AUC `A_b` + interaction `D_b = A_b − A_(not b)`.
Resolution: **both required** (union of hurdles = strictly harder). Codex's `D_b` is a genuine
additional hurdle and its rationale is adopted verbatim: it asks whether filtering to that cell
genuinely improves discrimination *versus trading everywhere else*, and it prevents calling a
bucket special merely because it has a favourable base rate.

### D6 — Qualification threshold. **CLAUDE** Bonferroni LCB > 0.60 (the frozen gate's own AUC floor). **CODEX** `A_b > 0.560` and `D_b > 0.030`.
Resolution: **both required.** Claude's 0.60-on-the-LCB is the stricter level; Codex's `D_b`
floor is an additional, orthogonal hurdle. Taking the maximum of the level requirements and the
union of the hurdle types.

### D7 — Resampling scheme. **CLAUDE** iid cluster bootstrap over 30 UTC day-blocks, B=10,000. **CODEX** panel-synchronous **circular moving-block** bootstrap, block = 168 4h bars = 28 days, B=20,000.
Resolution: **Codex's method, Claude's feasible block length, Codex's B.** Circular moving-block
preserves serial dependence across block boundaries better than an iid block resample, and
panel-synchronicity (whole timestamps travel with all symbols) is agreed by both. Codex's 28-day
block length is infeasible (≈1.4 blocks in the window) and is replaced by 1-day primary blocks
(30 units) with a mandatory 2-day robustness run (15 units). B = 20,000 (Codex's larger count).
**Considered and rejected:** a 3-day block requirement. At 10 clusters a percentile bootstrap is
not conservative, it is unstable — the LCB becomes an artifact of which 10 units were drawn, and
it can fail in either direction. Recorded so a future reader knows it was weighed, not missed.

### D8 — Multiplicity denominator. **CLAUDE** m=8. **CODEX** m=16.
Resolution: **m = 16** (Codex's, the larger). The reconciled design has **14** confirmatory
tests (2 omnibus + 6 × 2 per-bucket). Bonferroni is applied over **16** — deliberately larger
than the actual family — so the denominator can never be accused of having been tuned down to
fit the family that was run.

### D9 — Nature of the UNIFORM verdict. **CLAUDE** "UNIFORM otherwise" (failure to reject). **CODEX** UNIFORM only as an **equivalence** result (all simultaneous intervals inside ±0.030).
Resolution: **Codex's equivalence requirement is adopted, and is the single most valuable thing
Codex contributed.** A failure to reject is not evidence of uniformity; it is compatible with an
underpowered test. UNIFORM must be *earned* by positive evidence of homogeneity. Claude's
measured-MDE power gate is retained **in addition**, so the run is protected against
pseudo-closure by two independent devices.

### D10 — Terminal states. **CLAUDE** binary (CONCENTRATED / UNIFORM with sub-flags). **CODEX** explicitly refused the binary framing and specified four states.
Resolution: **Codex's four states**, with Claude's sub-flags folded in as descriptors. Codex's
binding interpretation clause is adopted verbatim: *"Do not relabel failure to find a pocket as
scientific uniformity. Both UNIFORM and INDETERMINATE prohibit further conditioning work; only
CONCENTRATED permits one future screen."* Collapsing four states into two would substitute
judgement for measurement.

### D11 — Reproduction gate. **CODEX** full-sample AUC must reproduce to **±0.001** or abort. **CLAUDE** pooled OOS AUC in **[0.520, 0.541]**.
Resolution: **Claude's band, by necessity, with the reason recorded.** Codex's ±0.001 is
achievable only by reusing a stored per-row OOS prediction vector. **Verified today: no such
vector exists.** `data/models/` holds only the 1,214-byte metrics JSON and the *final full-data*
model pickles; the per-fold OOS models that produced the 270,830 predictions were never
persisted. The predictions must therefore be reassembled, and reassembly cannot be bit-exact:
48 late-arriving rows have landed since the refit (324,999 → 325,047, §12), which shifts every
fold boundary. A two-branch gate whose strict branch can never be taken would be decoration, so
a single band is frozen. Recorded cost: the reproduction gate is weaker than Codex demanded.

### D12 — Narrowness cap. **CODEX** a qualifying cell must contain ≤25% of rows. **CLAUDE** no such cap.
Resolution: **adopted** (Codex's, the stricter). **Disclosed consequence, pre-registered:** the
P1 ADXlo/ATRlo cell holds 34.6% of OOS rows and therefore **can generate heterogeneity but can
never be nominated as a pocket**. This is deliberate — a filter that fires on a third of all rows
is not a "narrow pocket" — and it is harmless to the decision, because if that cell drives the
omnibus the verdict lands in a state (NON_UNIFORM_NOT_NARROW or INDETERMINATE) that forbids
further conditioning work anyway.

### D13 — Single-symbol dominance gate. Proposed during reconciliation as a feasible analogue of Codex's per-symbol requirement; **rejected as a gate.**
Adding an unverified threshold that could silently invalidate a partition buys nothing the
symbol floor does not already buy. It is instead a **reported diagnostic**. Measured today
(§12), max single-symbol share per bucket is **0.069–0.228** — no bucket is close to being one
symbol in disguise, so the concern Codex was guarding against is empirically absent here.

---

## 5. §0 — Data freeze and reproduction gate

**Frozen harvest.** `scripts.train_models.load_dataset('futures')` verbatim (labels path:
`labels JOIN candidates`, `length(features_json) > 100`, `features_json LIKE '%adx_1h%'`,
`ORDER BY l.ts`), plus exactly one additional restriction:

```
l.ts <= 1784370569.0
```

= `train_window_end` of `ens_futures_v1_20260725_171507` (verified in the warehouse
`model_versions` row) = 2026-07-18T10:29:29Z.

**Frozen OOS construction.** The union of the 5 anchored WalkForward test folds exactly as
`_walk_forward_oos` builds them: `WalkForward(n_splits=5, embargo_bars=_effective_embargo(24, 96)
= 96, anchored=True)`, whose fold geometry comes from `sklearn.TimeSeriesSplit(n_splits=5)`:
`test_size = n // 6`, `oos_start = n − 5 × test_size`. The embargo affects training rows only and
does not alter the OOS index set.

**`scripts/train_models.py` is NOT edited.** A scratch script imports `load_dataset`,
`_walk_forward_oos`, `_ensemble_weights`, `_effective_embargo`, `FEATURE_KEYS` and reassembles
the pipeline. Seed 20260726; `GBMModel(random_state=0)`.

**Score orientation is locked**: the orientation that yields pooled AUC ≈ 0.5305. It may never be
flipped, globally or per bucket.

**Prohibited absolutely:** refit on OOS rows, recalibration, symbol removal, time-period
selection, threshold adjustment, or any alternate score.

**REPRODUCTION GATE (runs first; no bucket statistic may be computed before it passes).**
Pooled OOS ensemble AUC must land in **[0.520, 0.541]**. Outside that band the run **HALTS with
no verdict** (`NO_ANSWER: reproduction`). Reference value: 0.5305171091569454.

**Residual optimism, disclosed:** isotonic calibration is monotone and cannot move any AUC,
pooled or per bucket. `_ensemble_weights` (`scripts/train_models.py:291`, verified — Claude's
submission cited line 416, which is wrong) fits the LR/GBM simplex weight
on the same pooled `y_oos`, so `p_ens` carries exactly one in-sample blend parameter — optimism
bounded by 1 dof on 270,870 rows. Negligible, but not literally zero.

---

## 6. §1 — The frozen partition (6 buckets)

Two pre-registered partitions of the **same** frozen OOS rows. Numeric cuts are **outcome-blind
medians of the frozen 325,047-row harvest**, computed and verified before this freeze (§12), and
are now immutable constants.

**P1 — regime-filter family (exhaustive 2×2 = 4 cells).**
`adx_4h × atr_pct_1h`, read directly off `X[:, 7]` and `X[:, 8]`
(`FEATURE_KEYS[7] = "adx_4h"`, `FEATURE_KEYS[8] = "atr_pct_1h"`, verified §12), so `_coerce`
null/non-finite handling is bit-identical to what the model saw. No JSON re-parse.

| Cut | Frozen value | Rule |
|---|---|---|
| `adx_4h` | **23.20** | `hi ⇔ value >= 23.20` |
| `atr_pct_1h` | **0.94** | `hi ⇔ value >= 0.94` |

**P2 — name-selection family (2 cells).**
`MAJOR = {BTC, ETH, BNB, SOL, XRP}/USDT` — the repo's pre-existing frozen 5-major basket, chosen
outcome-blind and long predating this run — versus `ALT = all others`.

**Axes deliberately excluded, with reasons.** `side` (98.8% buy; degenerate). Hour-of-day
(already in the refuted ledger: sweet-spots/seasonality, 2026-06-02). The ensemble score itself
(D3). Codex's BTC-regime and idiosyncratic-vol axes (D2 — not in the frozen `X`). A third P1 axis
(it produces cells such as MAJOR × ATRhi at n≈2,415 over only 8 populated days — below every
floor, buying nothing but multiplicity).

---

## 7. §2/§3 — Bucket geometry and minimum cell size

**Measured geometry (verified today, outcome-blind — §12).** `n_oos = 270,870`; window
2026-06-10T21:15Z → 2026-07-18T10:28Z; 28 symbols; 30 populated UTC days.

| Bucket | n | share | populated days | symbols | max single-symbol share |
|---|---:|---:|---:|---:|---:|
| P1 ADXlo/ATRlo | 93,631 | 0.346 | 27 | 25 | 0.090 |
| P1 ADXlo/ATRhi | 56,354 | 0.208 | 30 | 26 | 0.117 |
| P1 ADXhi/ATRlo | 55,634 | 0.205 | 30 | 25 | 0.139 |
| P1 ADXhi/ATRhi | 65,251 | 0.241 | 30 | 27 | 0.102 |
| P2 MAJOR | 54,206 | 0.200 | 30 | 5 | 0.228 |
| P2 ALT | 216,664 | 0.800 | 30 | 23 | 0.069 |

**Frozen minimum-cell floors.** A bucket QUALIFIES for testing iff **all** hold:

1. **n ≥ 12,000 rows** (Codex's number, adopted verbatim)
2. **minority-class count ≥ 2,500** (Codex's number, adopted verbatim) — evaluated at execution
   time, before any AUC
3. **≥ 20 distinct populated UTC days** overall, **and ≥ 8 in each chronological half**
   (feasible analogue of Codex's block requirement — D4)
4. **≥ 20 distinct symbols**, except **P2 MAJOR ≥ 5** (structural: it is a 5-name basket by
   construction)

Rows 1, 3 and 4 are already verified to hold for all six buckets. Row 2 is y-derived and is
deliberately left to execution time so that this pre-registration reads no outcome information.

**Binding failure clause (Codex's, adopted verbatim in force):** if a bucket fails any floor,
it must **not** be collapsed, dropped-and-replaced, or redefined. It is reported
`INSUFFICIENT_DATA`, is removed from its partition's omnibus, and **can never support a
CONCENTRATED verdict**. If **fewer than 4 of the 6 buckets** meet the floors, the run reports
`NO_ANSWER: insufficient cells` — **not** UNIFORM.

**The binding scarcity is not rows, it is day-blocks.** 270,870 rows are only **30 independent
daily units**. Every floor is therefore expressed in day-blocks as well as rows, and every
interval below is computed on 30 clusters, not 270,870 rows.

---

## 8. §4/§5 — Test statistics and dependence-aware inference

**Resampling unit.** The UTC day-block — exactly the 96 × 15m = 24h label horizon for futures
(`scripts/build_labels.py:40-41`: `DEFAULT_TIME_BARS["futures"] = 96`,
`DEFAULT_TF["futures"] = "15m"`; verified §14). **Panel-synchronous:** all symbols inside a
resampled day travel together, absorbing both overlapping-label dependence and cross-sectional
crypto correlation.

**Scheme.** Circular moving-block bootstrap over the **ordered list of the 30 populated days**,
primary block length **1 day** (30 units), **B = 20,000**, seed 20260726. Enough circular blocks
are drawn to reconstruct the original day-count.

**PROHIBITED, explicitly:** Hanley–McNeil, DeLong iid inference, row-level bootstrap, and any
symbol-independent resampling. The 43-symbol (here: 28-symbol) universe is treated as the fixed
trading universe, not as independent draws.

**Statistics, per qualifying bucket `b`:**

1. **`A_b`** — tie-aware within-bucket AUC: `P(s⁺ > s⁻) + 0.5 · P(s⁺ = s⁻)`, computed **within**
   the bucket.
2. **`D_b = A_b − A_(not b)`** — the interaction statistic (Codex). Within-bucket AUC minus AUC
   on all OOS rows outside the bucket. **Complements are taken within the bucket's own
   partition**, which is exhaustive in both cases: for a P1 cell, `not b` = the other three P1
   cells; for a P2 cell, `not b` = the other P2 cell.
   **Disclosed degeneracy of the 2-cell partition, pre-registered so execution cannot mistake it
   for an under-specification:** because P2 has exactly two cells,
   `D_ALT = A_ALT − A_MAJOR = −D_MAJOR`. P2's two `D_b` tests are therefore one test with a sign
   flip, and the P2 omnibus `T_P2 ∝ (A_MAJOR − A_ALT)²` is algebraically a restatement of
   `D_MAJOR²`. Consequences, all conservative and none altering any threshold: P2 contributes 2 of
   the 14 nominal tests but only **1 effective degree of freedom** (so the effective family is 13,
   not 14 — the m=16 denominator absorbs this with room to spare, per D8), and **P2's omnibus and
   its `D_b` tests count as a single line of evidence**, never as mutual corroboration. Both P2
   tests are still run and reported, and both must still pass their own corrected thresholds; this
   note forbids only the misreading that they are independent confirmations.
3. **`LCB_b`** — the one-sided Bonferroni lower confidence bound on `A_b`: the
   **0.3125th percentile** of that bucket's day-block bootstrap distribution (α = 0.05/16 =
   0.003125). **Note for anyone cross-referencing the source designs:** Claude's submission used
   α = 0.05/6 = 0.008333 → the 0.833rd percentile. The tightening to the **0.3125th** percentile
   is a direct consequence of adopting Codex's larger multiplicity denominator (D8), and the
   0.3125th percentile — not the inherited 0.833rd — is what §11 condition 3 (`LCB_b > 0.60`) is
   read against.

**Per-partition heterogeneity omnibus** `T_P = Σ_k (n_k / N_P) · (A_k − Ā_within,P)²`, where
`Ā_within,P = Σ_k (n_k / N_P) · A_k` is the size-weighted mean of **within-bucket** AUCs — **not**
the pooled-ranking AUC. This centring is load-bearing: pooled AUC can exceed every within-bucket
AUC when buckets differ in base rate and the score shifts by bucket, so centring on 0.5305 would
trip the omnibus on data where all four P1 cells sit at exactly 0.500.

**Null for `T_P`:** recentred cluster bootstrap. Replicate `b` gives
`ã_k^b = A_k^b − A_k^obs + Ā_within,P^obs`, imposing H0 (all buckets equal) while preserving the
observed cluster covariance. `p_P` = share of `T_P^b ≥ T_P^obs`.

**MANDATORY UNIT CHECK, before harvest.** A synthetic score built as *per-bucket constant offset
+ noise* — within-bucket AUC 0.500 by construction, pooled ≈ 0.53 — must return a near-uniform
omnibus p-value. This is the executable proof that the Simpson / base-rate artifact is
neutralised. If it fails, the run halts (`NO_ANSWER: unit check`).

**Descriptive-only, never a decision input:** bucket base rates and bucket win rates. A bucket
that merely has a higher unconditional WR is the already-refuted band-bucket question (0/16 under
Bonferroni, 2026-07-12), not this one.

**Tooling reused, not reimplemented:** `core/walk_forward.py` (purge + embargo, OOS folds),
`core/stat_tests.py` (bootstrap CI conventions, DSR, PBO), `core/decision/monte_carlo.py`
(block-bootstrap / streak-preserving resample semantics), `core/features.py`.

---

## 9. §6 — Multiplicity correction

**Bonferroni over m = 16**, familywise α = 0.05 ⇒ **α_test = 0.05/16 = 0.003125**.

The confirmatory family is: 2 omnibus heterogeneity tests + 6 buckets × 2 statistics (`A_b` LCB,
`D_b`) = **14 tests**. The denominator is set at **16** — larger than the family actually run —
so it cannot be accused of having been tuned to fit (D8).

**Per-test one-sided nulls (Codex's, adopted):** `H0^A_b: A_b ≤ 0.560`; `H0^D_b: D_b ≤ 0.030`.
p-values are block-bootstrap null-recentred, computed under §8's scheme — never from an analytic
or iid SE.

These are **fixed practical-effect floors, not merely significance thresholds**: the 0.560
requirement means the local excess must be at least 0.060, roughly twice the observed full-sample
0.031; the 0.030 interaction requirement means the cell must outperform the rest of the universe
by at least the entire observed global excess.

**Equivalence leg (for UNIFORM), separate family:** two-sided simultaneous **99.375%** per-bucket
block-bootstrap intervals on `D_b`. Codex specified 99.375% for 8 cells; with 6 buckets
Bonferroni would give 99.1667%, so the **stricter 99.375%** is retained — wider intervals make
UNIFORM *harder* to declare, which is the conservative direction against pseudo-closure.

**Everything tunable is fixed above, numerically, before outcomes:** partition axes, cut values
(23.20 / 0.94), bucket membership, bucket count, statistics, omnibus centre, B, seed, block
definition, floors, thresholds, and the decision rule. No re-partitioning, no added axis, no cut
adjustment, no alternate score, no merging, no splitting after any outcome is seen.

---

## 10. §7 — Measured power (MDE). Power is measured, not asserted

After the reproduction gate and the unit check, and **before any real bucket AUC is read**:
inject a synthetic score with known within-bucket AUC delta ∈ {0.55, 0.60, 0.65, 0.70, 0.75} into
one bucket while all other buckets keep the real `p_ens`; **200 simulations per delta**; run the
full decision rule on each.

**MDE** = the smallest delta detected at ≥ 80% power. The full detection curve is a **required
output field**.

**If MDE > 0.80** — i.e. the run cannot distinguish CONCENTRATED from UNIFORM at any plausible
effect size — the verdict is **`NO_ANSWER: underpowered`**, reported honestly. A low-power
UNIFORM is not a closure and may never be reported as one.

Non-binding prior only (not a claim): with 30 day-clusters and ~1.8k rows/bucket/day the
cluster-bootstrap SE on a bucket AUC is plausibly 0.01–0.02, so the α = 0.003125 one-sided LCB
may bind near AUC 0.62–0.66. If so, the UNIFORM verdict would be materially stronger than a
0.70-MDE design would license. This is a prior, to be replaced by the measured curve.

---

## 11. §8 — The exact decision rule (four terminal states)

Codex's four-state structure is binding. The binary CONCENTRATED/UNIFORM framing is explicitly
rejected as collapsing measurement into judgement.

### STATE 1 — CONCENTRATED
A bucket `b` is a **qualifying narrow pocket** iff **ALL SEVEN** hold:

1. `b` meets every minimum-cell floor (§7).
2. `A_b > 0.560` at `p_raw ≤ 0.003125` **AND** `D_b > 0.030` at `p_raw ≤ 0.003125`
   (block-bootstrap null-recentred).
3. `LCB_b > 0.60` — the frozen gate's own AUC floor, applied to the Bonferroni lower bound, not
   to the point estimate.
4. `b` contains **≤ 25%** of OOS rows.
5. **Both-halves stability:** in EACH chronological half, `A_b,h ≥ 0.540` **and** `D_b,h ≥ 0.015`.
6. **Thirds stability:** point `A_b ≥ 0.60` in **≥ 2 of 3** equal-duration calendar thirds of the
   OOS window (2026-06-10 → 06-23, 06-23 → 07-05, 07-05 → 07-18).
7. **Block robustness:** `LCB_b > 0.60` still holds under the **2-day** block bootstrap, where
   2-day blocks pair consecutive entries of the ORDERED LIST of the 30 populated days
   (pairs 1-2, 3-4, … → 15 blocks) — **not** calendar dates (the window spans 38 calendar days
   with only 30 populated).

**AND** the partition-level condition: `min(p_P1, p_P2) < 0.003125` (a corrected omnibus fires)
**AND exactly one** of the six buckets qualifies.

**Verdict language, binding:** *only that already-named bucket may earn one future, separately
pre-registered, untouched-OOS screen. It does not earn trading permission and does not relax the
frozen AUC ≥ 0.60, DSR ≥ 0.10, PBO ≤ 0.5, OOS-WR ≥ 0.55, or expectancy gates.*

### STATE 2 — NON_UNIFORM_NOT_NARROW
**Two or more** buckets qualify. **Nominate no new screen** — combining them afterward would be a
new, unregistered search. Further conditioning work is prohibited.

### STATE 3 — UNIFORM (an equivalence result, not a failure to reject)
**All six** simultaneous 99.375% intervals on `D_b` lie **wholly inside [−0.030, +0.030]**, **and**
no bucket qualifies under State 1. Interpretation: no pre-specified selectable bucket differs
from the rest by a practically material 3 AUC points.

Descriptive sub-flags, reported alongside but not altering the state:
- `UNIFORM_HOMOGENEOUS` — neither omnibus fires.
- `HETEROGENEOUS_BUT_UNACTIONABLE` — an omnibus fires but no bucket clears conditions 2–7.
  Decision-equivalent to UNIFORM: a sub-0.60 pocket cannot pass the frozen gate, and with the
  primary side carrying no directional information `E[filter × side × return] = 0` for every
  measurable filter, so net expectancy = `−cost × P(fire) < 0`.
- `UNIFORM_BASE_RATE_ARTIFACT` — additionally flagged when `Ā_within ≤ 0.510` while pooled AUC
  ≈ 0.531. The pooled excess is then between-bucket base-rate information, not within-regime
  timing information. This is the strongest available closure of the conditioning family.

### STATE 4 — INDETERMINATE (operationally NO-GO)
Neither the concentration rule nor the equivalence rule is met.

### NO_ANSWER (the design failed, not the hypothesis)
Reported instead of any verdict if: the reproduction gate fails; the synthetic offset-only unit
check fails; fewer than 4 of 6 buckets meet the floors; or MDE > 0.80.

### BINDING INTERPRETATION CLAUSE (Codex, verbatim)
> "Do not relabel failure to find a pocket as scientific uniformity. Both UNIFORM and
> INDETERMINATE prohibit further conditioning work; only CONCENTRATED permits one future screen."

### Reported unconditionally, in every state
The pooled AUC's two-sided 95% **day-block-clustered** CI. If it contains 0.500, the report must
state plainly that the 0.031 excess is not distinguishable from zero at the honest effective
sample size (30 day-blocks). This reinforces UNIFORM but does **not** by itself decide the
verdict.

---

## 12. §9 — Falsification, and what would make the answer wrong

### What could produce a FALSE 'CONCENTRATED', and the mitigation
1. **Only 30 clusters** — a percentile bootstrap can under-cover, making `LCB_b`
   anti-conservative. Mitigated by condition 7 (2-day block agreement); disagreement fails
   **CLOSED** to not-qualifying.
2. **A bucket proxying one calendar episode** (e.g. a single 3-day vol burst) would look real.
   Mitigated by the ≥20 populated-day floor, ≥8-per-half floor, and conditions 5 and 6.
3. **Simpson / base-rate artifact** — pooled AUC inflated by between-bucket base-rate spread.
   Neutralised structurally by using **within-bucket** AUC, by centring the omnibus on
   `Ā_within` rather than pooled AUC, by `D_b`, by the `UNIFORM_BASE_RATE_ARTIFACT` state, and by
   the mandatory synthetic offset-only unit check.
4. **Bucket-hunting / mass-variant mining** — neutralised by hash-first pre-registration, 6 fixed
   buckets, 14 fixed tests corrected at m=16, and the ban on post-hoc partitions.
5. **A bucket that is one symbol in disguise** — symbol floors, plus the reported max
   single-symbol share (measured 0.069–0.228; empirically absent here).
6. **Non-determinism / dataset drift** — the reproduction gate.

### What would prove the DESIGN wrong (⇒ NO_ANSWER, never UNIFORM)
Pooled AUC outside [0.520, 0.541]; the unit check failing; fewer than 4 of 6 buckets meeting the
floors; MDE > 0.80.

### What makes the protocol INVALID rather than negative (Codex §8, adopted)
Outcome information influencing features, cutpoints, cell definitions, block length, sample
inclusion, or the decision threshold; any minimum-cell or minimum-block requirement failing; the
full-data AUC failing to reproduce; scores refit or reoriented after seeing results; iid rows,
naive AUC SEs, or symbol-independent bootstrap being substituted; a cell being merged, split, or
combined after results are seen; **any 'hot' subset outside these six buckets being discussed as
evidence.**

### Acknowledged boundary (both models agree; binding on the ledger row)
This can establish uniformity **only over this explicitly frozen six-bucket conditioner family,
this signal, and this window**. No finite bucket experiment can prove uniformity over *every
measurable filter*. The universal closure comes only from the separate analytic result that when
the primary side truly has zero directional information, `E[filter × side × return] = 0`.

---

## 13. Traps addressed, and scope binding on the ledger row

1. **Mass-variant mining (the central trap)** — 6 buckets, 14 tests, Bonferroni at m=16,
   hash-committed prereg, no post-hoc re-partitioning. Cuts are frozen numeric constants
   (23.20 / 0.94) derived outcome-blind and independently verified (§14), not chosen to make a
   cell look good.
2. **Naive standard errors** — explicitly prohibited. Panel-synchronous circular moving-block
   bootstrap on the UTC day-block = exactly the 96×15m label horizon. The report leads with the
   real headline: 270,870 rows are **30 independent units**.
3. **Simpson / base-rate artifact** — §8, plus a mandatory executable unit check.
4. **Finding a pocket that cannot be traded** — the bucket threshold is the frozen gate's own AUC
   floor (0.60) applied to the Bonferroni LCB, plus Codex's `D_b` and ≤25% narrowness cap.
5. **Single-episode pockets** — day floors + halves + thirds + 2-day blocks, all fail-closed.
6. **Degenerate axes** — `side` (98.8% buy) and hour-of-day (refuted 2026-06-02) excluded.
7. **Circularity** — conditioning on `p_ens` excluded from the confirmatory family (D3).
8. **The 3-probe false-confirmation fallacy** — nothing in this design reads the n=30 probe
   results; the binding statistic is the large-sample clustered one.
9. **Low-power pseudo-closure** — measured MDE with a NO_ANSWER trigger, **and** UNIFORM as an
   equivalence result. Two independent devices.
10. **Touching the live bot** — read-only: warehouse opened `mode=ro`, `train_models.py` imported
    not edited, no `core/` or `config.py` change, no restart.

**SCOPE BINDING ON THE LEDGER ROW.** A UNIFORM verdict closes conditioning on **this** signal,
over **this** window (30 populated days, 2026-06-10 → 07-18, a single regime), within **this**
six-bucket family, at the **measured MDE**. It does **not** close conditioning across regimes,
and it does not test Codex's BTC-regime or idiosyncratic-vol axes (D2). A ledger row omitting
that scope overclaims.

**Prior-strengthening note (supports the stated expectation, not the design):** `adx_4h` and
`atr_pct_1h` are `FEATURE_KEYS[7]`/`[8]` — already inputs to both the LR and the GBM. The GBM
would have partly absorbed any genuine regime-concentrated discrimination during fitting, so an
undiscovered high-AUC pocket along the P1 axes is a priori *less* likely. That is precisely why
P1 is the honest place to look: it is where the model had its best chance and still returned
0.531.

---

## 14. Constants verified before freezing (outcome-blind)

Every constant below was verified against the warehouse **today, before this file was hashed**,
reading feature-side geometry only. No label-derived or score-derived quantity was computed.

| Constant | Value | Status |
|---|---|---|
| `model_versions.train_window_end` | 1784370569 (2026-07-18T10:29:29Z) | verified |
| Frozen harvest n (today) | 325,047 | verified |
| n at refit time (`metrics.n_train`) | 324,999 | verified (48 late rows) |
| `test_size = n // 6` | 54,174 | verified |
| `oos_start = n − 5·test_size` | 54,177 | verified |
| `n_oos` | 270,870 (refit recorded 270,830) | verified |
| OOS window | 2026-06-10T21:15:07Z → 2026-07-18T10:28:24Z | verified |
| Distinct symbols in OOS | 28 | verified |
| Distinct populated UTC days in OOS | 30 | verified |
| `FEATURE_KEYS[7]`, `[8]` | `adx_4h`, `atr_pct_1h` | verified |
| Harvest median `adx_4h` | 23.200000 | verified |
| Harvest median `atr_pct_1h` | 0.940000 | verified |
| All six bucket n / days / symbols | as tabulated in §7 | verified |
| Max single-symbol share per bucket | 0.069 – 0.228 | verified |
| Recorded pooled OOS AUC | 0.5305171091569454 | verified |
| Recorded PBO / DSR | 0.3714 / 0.00 | verified |
| Stored per-row OOS prediction vector | **does not exist** | verified (D11) |
| `labels` futures full span | 2026-04-14 → 2026-07-25, 68 populated days, 35 symbols | verified (D4) |
| `DEFAULT_TIME_BARS["futures"]`, `DEFAULT_TF["futures"]` | 96, `"15m"` (⇒ 24h horizon = the day-block) | verified |
| `_ensemble_weights` location | `scripts/train_models.py:291` | verified |

---

## 15. DEVIATION CLAUSE (binding)

**Any deviation from this document during execution invalidates the run.**

If any element of the frozen design is changed after this file is committed — the partition axes,
the cut values 23.20 / 0.94, the bucket membership or count, the minimum-cell floors, the
statistics, the omnibus centring, the resampling unit or scheme, the block lengths, B, the seed,
the multiplicity denominator m=16, the thresholds (0.560 / 0.030 / 0.60 / ±0.030 / 25%), the
stability conditions, the decision rule, or the terminal-state definitions — then **no verdict of
any kind may be reported**. The run is `INVALIDATED`, the reason is recorded, and any re-run
requires a **new** pre-registration with a **new** hash, committed before the new harvest.

Post-hoc slices may be printed for curiosity. They must be labelled **EXPLORATORY**, and they
**cannot move the verdict** in any direction. Discussing any 'hot' subset outside these six
buckets as evidence is itself an invalidating deviation (§12).

**No outcome statistic had been computed at the time this file was hashed and committed.**

---

## 16. Hash of this pre-registration

**Convention (stated so the hash is independently checkable).** The SHA-256 is computed over the
bytes of this file **from the first byte up to and including the sentinel line below**, after
**normalising CRLF → LF**. The hash line and everything after the sentinel are excluded from the
digest — a self-referencing digest that included itself would be uncomputable.

The CRLF→LF normalisation is load-bearing, not decoration: this repo has `core.autocrlf=true` and
no `.gitattributes`, so a fresh checkout rewrites this file with CRLF line endings. Without
normalisation the digest would verify on the authoring machine and fail everywhere else.

Verification command (from the repo root; works on the working tree in any line-ending state):

```
venv/Scripts/python.exe -c "import hashlib; b=open('_workspace/strategy_pipeline/31_prereg_edge_concentration.md','rb').read().replace(b'\r\n', b'\n'); s=b'<!-- SHA256-BODY-END -->'; i=b.index(s)+len(s); print(hashlib.sha256(b[:i]).hexdigest())"
```

Equivalently, against the committed blob rather than the working tree:

```
git show HEAD:_workspace/strategy_pipeline/31_prereg_edge_concentration.md | venv/Scripts/python.exe -c "import sys,hashlib; b=sys.stdin.buffer.read().replace(b'\r\n', b'\n'); s=b'<!-- SHA256-BODY-END -->'; i=b.index(s)+len(s); print(hashlib.sha256(b[:i]).hexdigest())"
```

The resulting digest is recorded in `_workspace/strategy_pipeline/31_prereg_edge_concentration.sha256`
and in the commit message of the commit that introduced these two files.

**Amendment record (full disclosure of hash lineage).** An earlier draft of this file was
committed locally as `7c6608f` with body digest
`c45cbaee9c054419b4034a56eba27eee73124b972a2e045d0463cfcf588f2d13`, then **amended before any
outcome statistic was computed and before the freeze was reported anywhere**. The amendment was
purely clarifying and made nothing easier to pass: it disclosed the algebraic degeneracy of the
2-cell P2 partition (§8), pinned the `LCB_b` percentile against the m=16 denominator rather than
Claude's inherited 0.833rd (§8), and corrected two source citations (`scripts/build_labels.py:40-41`
and `scripts/train_models.py:291`, the latter cited as line 416 in Claude's submission). No axis,
cut, bucket, floor, threshold, statistic, or decision rule was altered. The digest below is the
**only** binding one; `c45cbaee…` is superseded and must not be used for verification.

<!-- SHA256-BODY-END -->

**SHA-256 (body as defined above):**

```
48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1
```

This digest was computed and committed **before any outcome statistic was calculated**. If the
verification command above returns a different digest, this pre-registration has been altered
after the freeze and **no verdict derived from it may be reported** (§15).
