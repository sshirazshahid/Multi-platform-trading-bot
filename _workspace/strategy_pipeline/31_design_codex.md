# 31 — Uniform-vs-Concentrated pre-registration design (CODEX-SOL-5.6, verbatim)

Source: codex-cli 0.144.5, `codex exec` (read-only sandbox, cwd=scratchpad), 2026-07-26.
Brief: scratchpad/codex_brief_uniform_vs_concentrated.txt
This file is Codex output, unedited. Claude added only this header.

---

Pre-register the expected outcome as: **UNIFORM / NO-GO.** The burden of proof is solely on a narrowly defined, multiplicity-corrected result to overturn that.

This tests whether a selectable region has materially stronger *within-region* discrimination—not whether an arbitrary post-hoc bucket looks good.

### 1. Freeze the data and score

Use the existing 270,830 OOS predictions from the 2026-07-25 ensemble refit.

- Lock the score orientation that produces full-sample AUC = 0.531; never flip it by bucket.
- Lock the labels, timestamps, symbols, score vector, and feature code by manifest/hash before examining any bucket outcome.
- No refit, calibration, symbol removal, time-period selection, or threshold adjustment.
- Reproduce full-sample AUC as 0.531 ± 0.001 before proceeding. Otherwise abort: this protocol is not being applied to the stated dataset.

### 2. Fixed partition: 8 cells

Use exactly these three pre-entry axes, each split at its feature-only pooled median, with deterministic tie assignment based on `SHA256(symbol|timestamp)`:

| Axis | Low / high definition |
|---|---|
| Score confidence | \(C=|\mathrm{logit}(p)|\), where \(p\) is the frozen ensemble probability |
| Idiosyncratic volatility | \(V=\log\sqrt{\frac{1}{6}\sum_{k=1}^{6}r_{i,t-k}^2}\), ranked within symbol before pooling |
| Market regime | \(M=\log(P_{\mathrm{BTC},t-1}/P_{\mathrm{BTC},t-43})\): trailing 7-day BTC return |

The resulting eight fixed cells are:

\[
(C_{\rm low/high})\times(V_{\rm low/high})\times(M_{\rm low/high}).
\]

No fourth feature, no alternate quantiles, no interactions beyond these eight cells, no merging cells, and no “one more look” after outcomes are revealed.

This is intentionally a small, semantic set: confidence, local volatility, and broad-market regime—the three plausible ways one would try to condition this entry signal.

### 3. Minimum viable cell requirements

Every one of the eight cells must satisfy all of these:

- ≥12,000 rows total;
- ≥2,500 positive and ≥2,500 negative labels;
- ≥35 of 43 symbols represented, with ≥200 rows per represented symbol;
- in each of two pre-fixed chronological halves: ≥5,000 rows, ≥1,000 of each label, and ≥30 symbols;
- ≥30 non-overlapping 28-day panel blocks overall and ≥15 per chronological half.

If any cell fails, do not collapse, drop, or redefine it. The localization study is **invalid / not executed**, not “uniform.”

### 4. Statistics

For each cell \(b\), calculate the ordinary tie-aware, within-cell AUC:

\[
A_b=P(s^+_b>s^-_b)+0.5P(s^+_b=s^-_b).
\]

Also calculate the pre-specified interaction statistic:

\[
D_b=A_b-A_{\neg b}.
\]

`D_b` is the key statistic. It asks whether filtering to that cell genuinely improves discrimination versus trading everywhere else. It avoids calling a bucket special merely because it has a favorable base rate.

AUC does not decompose as a simple weighted average of within-cell AUCs; that is deliberate here. The estimand is actionable: “What happens if this pre-specified filter is applied?”

### 5. Dependence-aware inference

Do not use Hanley–McNeil, DeLong iid inference, row bootstrap, or symbol-independent resampling.

Use a **synchronous circular moving-block bootstrap**:

- Resampling unit: the entire 43-symbol panel at a timestamp, never individual rows.
- Block length: 168 four-hour bars = 28 calendar days.
- Sample enough circular blocks to reconstruct the original timestamp length.
- Retain all symbols jointly within each sampled block.
- Bootstrap replicates: 20,000.

This preserves overlapping-label dependence, serial dependence, and the crypto cross-sectional correlation structure. It treats the actual 43-symbol universe as the fixed trading universe rather than pretending its symbols are independent draws.

### 6. Multiplicity control and concentration rule

There are 16 elementary one-sided tests: eight \(A_b\) tests and eight \(D_b\) tests.

For each cell, test:

\[
H^A_{0b}: A_b \leq 0.560
\]
\[
H^D_{0b}: D_b \leq 0.030.
\]

Use the block-bootstrap null-recentering p-value for each test and Bonferroni correction across all 16 tests:

\[
p_{\rm raw} \leq 0.05/16 = 0.003125.
\]

A cell is a qualifying narrow pocket only if all conditions hold:

1. Both corrected tests pass: \(A_b>0.560\) and \(D_b>0.030\).
2. It contains no more than 25% of rows.
3. In **each** fixed chronological half, its point estimates satisfy:
   \[
   A_{b,h}\geq0.540,\qquad D_{b,h}\geq0.015.
   \]
4. Exactly one of the eight cells qualifies.

The 0.560 requirement means the local excess must be at least 0.060—roughly twice the observed full-sample 0.031. The 0.030 interaction requirement means it must outperform the rest of the universe by at least the entire observed global excess. These are fixed practical-effect floors, not merely significance thresholds.

If exactly one cell qualifies, conclude:

> **CONCENTRATED:** only that already-named cell may earn one future, separately pre-registered, untouched-OOS screen. It does not earn trading permission and does not relax the frozen AUC ≥0.60, DSR, PBO, or expectancy gates.

If two or more cells qualify, call it **non-uniform but not a narrow pocket**; nominate no new screen. Combining them afterward would be a new, unregistered search.

### 7. Uniform rule

For every \(D_b\), construct a two-sided, simultaneous 95% familywise block-bootstrap interval using Bonferroni across eight cells: 99.375% per-cell intervals.

Conclude:

> **UNIFORM:** every one of the eight simultaneous intervals lies wholly inside \([-0.030,+0.030]\), and no cell qualifies above.

This is an equivalence result: no pre-specified selectable cell differs from the rest by a practically material 3 AUC points.

If neither the concentration nor equivalence rule is met, report:

> **INDETERMINATE, operationally NO-GO.**

Do not relabel failure to find a pocket as scientific uniformity. Both UNIFORM and INDETERMINATE prohibit further conditioning work; only CONCENTRATED permits one future screen.

### 8. What falsifies the design

The protocol is invalid, rather than negative, if any of these occur:

- outcome information influences features, cutpoints, cell definitions, block length, sample inclusion, or the decision threshold;
- any minimum-cell or minimum-block requirement fails;
- the full-data AUC cannot be reproduced;
- scores were refit or reoriented after seeing results;
- iid rows, naive AUC SEs, or symbol-independent bootstrap are substituted;
- a cell is merged, split, or combined after results are seen;
- any “hot” subset outside these eight cells is discussed as evidence.

### 9. Mining traps defeated

This defeats the specific failure modes behind the prior pattern work:

- no adaptive bucket count or boundary hunting;
- no trying many features, quantiles, symbols, or time windows;
- no choosing the strongest cell after inspecting outcomes without familywise correction;
- no treating correlated 4-hour rows as 270,830 independent observations;
- no treating 43 correlated coins as 43 independent confirmations;
- no 30-trade probe logic;
- no conversion of a local AUC result into a profitability claim.

One boundary remains important: this can establish uniformity only over this explicitly frozen eight-cell conditioner family. No finite bucket experiment can prove uniformity over *every measurable filter*. The universal closure comes only from the separate analytic result when the primary side truly has zero directional information.

---

## Raw session transcript (stdout)

```
Reading prompt from stdin...
2026-07-26T13:57:39.333938Z ERROR codex_models_manager::cache: failed to load models cache: missing field `supports_reasoning_summaries` at line 88 column 5
OpenAI Codex v0.144.5
--------
workdir: C:\Users\SyedShirazShahid\AppData\Local\Temp\claude\D--Downloads-Trading-Bot\ef3c3ceb-badd-49e5-96e8-746426da1017\scratchpad
model: gpt-5.6-terra
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019f9eb7-9a60-7d93-9806-d0153893ac1e
--------
user
THE QUESTION (the only one this run answers):
The bot's entry signal measures AUC 0.531 on 270,830 out-of-sample rows (ensemble refit 2026-07-25; DSR 0.00, gate-refused). Excess over chance = 0.031.
Is that 0.031 excess UNIFORM across feature space (a thin film of nothing, spread everywhere), or CONCENTRATED in an identifiable subset (a real but narrow pocket)?

WHY IT MATTERS — both outcomes are valuable:
- UNIFORM => no filter, conditioner, meta-label or regime gate can ever extract profit from this signal, because there is no subset to select. Combined with the already-established analytic proof (if the primary side carries no directional information then E[filter x side x return] = 0 for EVERY measurable filter, so net expectancy = -cost x P(fire) < 0), this permanently closes the entire "condition the existing signal" family on our OWN data. That ends a large class of future spend.
- CONCENTRATED => the identified subset is the only region worth a future pre-registered screen.

BINDING CONTEXT (measured facts — do not re-derive, do not contradict without evidence):
- ~2,400 refuted pattern tests. Every pre-registered screen this month returned NO_GO (C1 CFTC, VPIN, C3 quarter-hour, wrapper-discount, F1-selectivity, stablecoin-depeg).
- 30-day directional expectancy -0.24R. Three shadow probes hit their 30-outcome floors on 2026-07-26 and ALL were gate-blocked (tsmom WR 0.333; zfade WR 0.567; rsi2 WR 0.60 — a 60% win rate still lost money).
- Statistical honesty rule adopted 2026-07-26: at n=30 a probe's AUC 95% CI is ~[0.29,0.72] — three such intervals are NOT three confirmations. The binding statistical fact is the large-sample 0.531.
- ALSO BINDING: naive Hanley-McNeil standard errors are WRONG on this dataset — 43 symbols with overlapping 4h bars and high cross-sectional crypto correlation mean the effective sample is far smaller than n. Any SE/CI must account for clustering (symbol-clustered or block bootstrap).
- Capital ~$420 retail. Frozen gate is arithmetic: MIN_DSR>=0.10, MAX_PBO<=0.5, OOS-WR>=0.55, AUC>=0.60.

THE CENTRAL TRAP THIS DESIGN MUST DEFEAT: partitioning feature space and hunting for a "hot" bucket IS mass-variant mining — the exact methodology this repo has refuted (443+ formulaic alphas, 1,989 candlestick tests, 0/16 band buckets under Bonferroni). A design that finds a hot bucket without rigorous multiplicity control has found nothing. The pre-registration must therefore fix, BEFORE seeing outcomes: the partition scheme, the number of buckets, the test statistic, the multiplicity correction, the minimum cell size, and the exact decision rule that distinguishes CONCENTRATED from UNIFORM. Expectation to state up front: UNIFORM (i.e. NO_GO for any filter).

YOUR TASK:
Design a pre-registration answering this. Specify EXACTLY: partition scheme, number of buckets, minimum cell size, test statistic, multiplicity control, the numeric decision rule separating CONCENTRATED from UNIFORM, what would falsify your design, and which mining traps you defeat. Be concrete and numeric. Note the dataset is 43 crypto symbols with overlapping 4h bars, so naive independence assumptions are invalid.

codex
I’ll frame this as a single, outcome-blind confirmatory test: the default conclusion is UNIFORM, and “CONCENTRATED” will require a pre-specified interaction that survives dependence-aware resampling and familywise correction.
codex
Pre-register the expected outcome as: **UNIFORM / NO-GO.** The burden of proof is solely on a narrowly defined, multiplicity-corrected result to overturn that.

This tests whether a selectable region has materially stronger *within-region* discrimination—not whether an arbitrary post-hoc bucket looks good.

### 1. Freeze the data and score

Use the existing 270,830 OOS predictions from the 2026-07-25 ensemble refit.

- Lock the score orientation that produces full-sample AUC = 0.531; never flip it by bucket.
- Lock the labels, timestamps, symbols, score vector, and feature code by manifest/hash before examining any bucket outcome.
- No refit, calibration, symbol removal, time-period selection, or threshold adjustment.
- Reproduce full-sample AUC as 0.531 ± 0.001 before proceeding. Otherwise abort: this protocol is not being applied to the stated dataset.

### 2. Fixed partition: 8 cells

Use exactly these three pre-entry axes, each split at its feature-only pooled median, with deterministic tie assignment based on `SHA256(symbol|timestamp)`:

| Axis | Low / high definition |
|---|---|
| Score confidence | \(C=|\mathrm{logit}(p)|\), where \(p\) is the frozen ensemble probability |
| Idiosyncratic volatility | \(V=\log\sqrt{\frac{1}{6}\sum_{k=1}^{6}r_{i,t-k}^2}\), ranked within symbol before pooling |
| Market regime | \(M=\log(P_{\mathrm{BTC},t-1}/P_{\mathrm{BTC},t-43})\): trailing 7-day BTC return |

The resulting eight fixed cells are:

\[
(C_{\rm low/high})\times(V_{\rm low/high})\times(M_{\rm low/high}).
\]

No fourth feature, no alternate quantiles, no interactions beyond these eight cells, no merging cells, and no “one more look” after outcomes are revealed.

This is intentionally a small, semantic set: confidence, local volatility, and broad-market regime—the three plausible ways one would try to condition this entry signal.

### 3. Minimum viable cell requirements

Every one of the eight cells must satisfy all of these:

- ≥12,000 rows total;
- ≥2,500 positive and ≥2,500 negative labels;
- ≥35 of 43 symbols represented, with ≥200 rows per represented symbol;
- in each of two pre-fixed chronological halves: ≥5,000 rows, ≥1,000 of each label, and ≥30 symbols;
- ≥30 non-overlapping 28-day panel blocks overall and ≥15 per chronological half.

If any cell fails, do not collapse, drop, or redefine it. The localization study is **invalid / not executed**, not “uniform.”

### 4. Statistics

For each cell \(b\), calculate the ordinary tie-aware, within-cell AUC:

\[
A_b=P(s^+_b>s^-_b)+0.5P(s^+_b=s^-_b).
\]

Also calculate the pre-specified interaction statistic:

\[
D_b=A_b-A_{\neg b}.
\]

`D_b` is the key statistic. It asks whether filtering to that cell genuinely improves discrimination versus trading everywhere else. It avoids calling a bucket special merely because it has a favorable base rate.

AUC does not decompose as a simple weighted average of within-cell AUCs; that is deliberate here. The estimand is actionable: “What happens if this pre-specified filter is applied?”

### 5. Dependence-aware inference

Do not use Hanley–McNeil, DeLong iid inference, row bootstrap, or symbol-independent resampling.

Use a **synchronous circular moving-block bootstrap**:

- Resampling unit: the entire 43-symbol panel at a timestamp, never individual rows.
- Block length: 168 four-hour bars = 28 calendar days.
- Sample enough circular blocks to reconstruct the original timestamp length.
- Retain all symbols jointly within each sampled block.
- Bootstrap replicates: 20,000.

This preserves overlapping-label dependence, serial dependence, and the crypto cross-sectional correlation structure. It treats the actual 43-symbol universe as the fixed trading universe rather than pretending its symbols are independent draws.

### 6. Multiplicity control and concentration rule

There are 16 elementary one-sided tests: eight \(A_b\) tests and eight \(D_b\) tests.

For each cell, test:

\[
H^A_{0b}: A_b \leq 0.560
\]
\[
H^D_{0b}: D_b \leq 0.030.
\]

Use the block-bootstrap null-recentering p-value for each test and Bonferroni correction across all 16 tests:

\[
p_{\rm raw} \leq 0.05/16 = 0.003125.
\]

A cell is a qualifying narrow pocket only if all conditions hold:

1. Both corrected tests pass: \(A_b>0.560\) and \(D_b>0.030\).
2. It contains no more than 25% of rows.
3. In **each** fixed chronological half, its point estimates satisfy:
   \[
   A_{b,h}\geq0.540,\qquad D_{b,h}\geq0.015.
   \]
4. Exactly one of the eight cells qualifies.

The 0.560 requirement means the local excess must be at least 0.060—roughly twice the observed full-sample 0.031. The 0.030 interaction requirement means it must outperform the rest of the universe by at least the entire observed global excess. These are fixed practical-effect floors, not merely significance thresholds.

If exactly one cell qualifies, conclude:

> **CONCENTRATED:** only that already-named cell may earn one future, separately pre-registered, untouched-OOS screen. It does not earn trading permission and does not relax the frozen AUC ≥0.60, DSR, PBO, or expectancy gates.

If two or more cells qualify, call it **non-uniform but not a narrow pocket**; nominate no new screen. Combining them afterward would be a new, unregistered search.

### 7. Uniform rule

For every \(D_b\), construct a two-sided, simultaneous 95% familywise block-bootstrap interval using Bonferroni across eight cells: 99.375% per-cell intervals.

Conclude:

> **UNIFORM:** every one of the eight simultaneous intervals lies wholly inside \([-0.030,+0.030]\), and no cell qualifies above.

This is an equivalence result: no pre-specified selectable cell differs from the rest by a practically material 3 AUC points.

If neither the concentration nor equivalence rule is met, report:

> **INDETERMINATE, operationally NO-GO.**

Do not relabel failure to find a pocket as scientific uniformity. Both UNIFORM and INDETERMINATE prohibit further conditioning work; only CONCENTRATED permits one future screen.

### 8. What falsifies the design

The protocol is invalid, rather than negative, if any of these occur:

- outcome information influences features, cutpoints, cell definitions, block length, sample inclusion, or the decision threshold;
- any minimum-cell or minimum-block requirement fails;
- the full-data AUC cannot be reproduced;
- scores were refit or reoriented after seeing results;
- iid rows, naive AUC SEs, or symbol-independent bootstrap are substituted;
- a cell is merged, split, or combined after results are seen;
- any “hot” subset outside these eight cells is discussed as evidence.

### 9. Mining traps defeated

This defeats the specific failure modes behind the prior pattern work:

- no adaptive bucket count or boundary hunting;
- no trying many features, quantiles, symbols, or time windows;
- no choosing the strongest cell after inspecting outcomes without familywise correction;
- no treating correlated 4-hour rows as 270,830 independent observations;
- no treating 43 correlated coins as 43 independent confirmations;
- no 30-trade probe logic;
- no conversion of a local AUC result into a profitability claim.

One boundary remains important: this can establish uniformity only over this explicitly frozen eight-cell conditioner family. No finite bucket experiment can prove uniformity over *every measurable filter*. The universal closure comes only from the separate analytic result when the primary side truly has zero directional information.
tokens used
19,876
Pre-register the expected outcome as: **UNIFORM / NO-GO.** The burden of proof is solely on a narrowly defined, multiplicity-corrected result to overturn that.

This tests whether a selectable region has materially stronger *within-region* discrimination—not whether an arbitrary post-hoc bucket looks good.

### 1. Freeze the data and score

Use the existing 270,830 OOS predictions from the 2026-07-25 ensemble refit.

- Lock the score orientation that produces full-sample AUC = 0.531; never flip it by bucket.
- Lock the labels, timestamps, symbols, score vector, and feature code by manifest/hash before examining any bucket outcome.
- No refit, calibration, symbol removal, time-period selection, or threshold adjustment.
- Reproduce full-sample AUC as 0.531 ± 0.001 before proceeding. Otherwise abort: this protocol is not being applied to the stated dataset.

### 2. Fixed partition: 8 cells

Use exactly these three pre-entry axes, each split at its feature-only pooled median, with deterministic tie assignment based on `SHA256(symbol|timestamp)`:

| Axis | Low / high definition |
|---|---|
| Score confidence | \(C=|\mathrm{logit}(p)|\), where \(p\) is the frozen ensemble probability |
| Idiosyncratic volatility | \(V=\log\sqrt{\frac{1}{6}\sum_{k=1}^{6}r_{i,t-k}^2}\), ranked within symbol before pooling |
| Market regime | \(M=\log(P_{\mathrm{BTC},t-1}/P_{\mathrm{BTC},t-43})\): trailing 7-day BTC return |

The resulting eight fixed cells are:

\[
(C_{\rm low/high})\times(V_{\rm low/high})\times(M_{\rm low/high}).
\]

No fourth feature, no alternate quantiles, no interactions beyond these eight cells, no merging cells, and no “one more look” after outcomes are revealed.

This is intentionally a small, semantic set: confidence, local volatility, and broad-market regime—the three plausible ways one would try to condition this entry signal.

### 3. Minimum viable cell requirements

Every one of the eight cells must satisfy all of these:

- ≥12,000 rows total;
- ≥2,500 positive and ≥2,500 negative labels;
- ≥35 of 43 symbols represented, with ≥200 rows per represented symbol;
- in each of two pre-fixed chronological halves: ≥5,000 rows, ≥1,000 of each label, and ≥30 symbols;
- ≥30 non-overlapping 28-day panel blocks overall and ≥15 per chronological half.

If any cell fails, do not collapse, drop, or redefine it. The localization study is **invalid / not executed**, not “uniform.”

### 4. Statistics

For each cell \(b\), calculate the ordinary tie-aware, within-cell AUC:

\[
A_b=P(s^+_b>s^-_b)+0.5P(s^+_b=s^-_b).
\]

Also calculate the pre-specified interaction statistic:

\[
D_b=A_b-A_{\neg b}.
\]

`D_b` is the key statistic. It asks whether filtering to that cell genuinely improves discrimination versus trading everywhere else. It avoids calling a bucket special merely because it has a favorable base rate.

AUC does not decompose as a simple weighted average of within-cell AUCs; that is deliberate here. The estimand is actionable: “What happens if this pre-specified filter is applied?”

### 5. Dependence-aware inference

Do not use Hanley–McNeil, DeLong iid inference, row bootstrap, or symbol-independent resampling.

Use a **synchronous circular moving-block bootstrap**:

- Resampling unit: the entire 43-symbol panel at a timestamp, never individual rows.
- Block length: 168 four-hour bars = 28 calendar days.
- Sample enough circular blocks to reconstruct the original timestamp length.
- Retain all symbols jointly within each sampled block.
- Bootstrap replicates: 20,000.

This preserves overlapping-label dependence, serial dependence, and the crypto cross-sectional correlation structure. It treats the actual 43-symbol universe as the fixed trading universe rather than pretending its symbols are independent draws.

### 6. Multiplicity control and concentration rule

There are 16 elementary one-sided tests: eight \(A_b\) tests and eight \(D_b\) tests.

For each cell, test:

\[
H^A_{0b}: A_b \leq 0.560
\]
\[
H^D_{0b}: D_b \leq 0.030.
\]

Use the block-bootstrap null-recentering p-value for each test and Bonferroni correction across all 16 tests:

\[
p_{\rm raw} \leq 0.05/16 = 0.003125.
\]

A cell is a qualifying narrow pocket only if all conditions hold:

1. Both corrected tests pass: \(A_b>0.560\) and \(D_b>0.030\).
2. It contains no more than 25% of rows.
3. In **each** fixed chronological half, its point estimates satisfy:
   \[
   A_{b,h}\geq0.540,\qquad D_{b,h}\geq0.015.
   \]
4. Exactly one of the eight cells qualifies.

The 0.560 requirement means the local excess must be at least 0.060—roughly twice the observed full-sample 0.031. The 0.030 interaction requirement means it must outperform the rest of the universe by at least the entire observed global excess. These are fixed practical-effect floors, not merely significance thresholds.

If exactly one cell qualifies, conclude:

> **CONCENTRATED:** only that already-named cell may earn one future, separately pre-registered, untouched-OOS screen. It does not earn trading permission and does not relax the frozen AUC ≥0.60, DSR, PBO, or expectancy gates.

If two or more cells qualify, call it **non-uniform but not a narrow pocket**; nominate no new screen. Combining them afterward would be a new, unregistered search.

### 7. Uniform rule

For every \(D_b\), construct a two-sided, simultaneous 95% familywise block-bootstrap interval using Bonferroni across eight cells: 99.375% per-cell intervals.

Conclude:

> **UNIFORM:** every one of the eight simultaneous intervals lies wholly inside \([-0.030,+0.030]\), and no cell qualifies above.

This is an equivalence result: no pre-specified selectable cell differs from the rest by a practically material 3 AUC points.

If neither the concentration nor equivalence rule is met, report:

> **INDETERMINATE, operationally NO-GO.**

Do not relabel failure to find a pocket as scientific uniformity. Both UNIFORM and INDETERMINATE prohibit further conditioning work; only CONCENTRATED permits one future screen.

### 8. What falsifies the design

The protocol is invalid, rather than negative, if any of these occur:

- outcome information influences features, cutpoints, cell definitions, block length, sample inclusion, or the decision threshold;
- any minimum-cell or minimum-block requirement fails;
- the full-data AUC cannot be reproduced;
- scores were refit or reoriented after seeing results;
- iid rows, naive AUC SEs, or symbol-independent bootstrap are substituted;
- a cell is merged, split, or combined after results are seen;
- any “hot” subset outside these eight cells is discussed as evidence.

### 9. Mining traps defeated

This defeats the specific failure modes behind the prior pattern work:

- no adaptive bucket count or boundary hunting;
- no trying many features, quantiles, symbols, or time windows;
- no choosing the strongest cell after inspecting outcomes without familywise correction;
- no treating correlated 4-hour rows as 270,830 independent observations;
- no treating 43 correlated coins as 43 independent confirmations;
- no 30-trade probe logic;
- no conversion of a local AUC result into a profitability claim.

One boundary remains important: this can establish uniformity only over this explicitly frozen eight-cell conditioner family. No finite bucket experiment can prove uniformity over *every measurable filter*. The universal closure comes only from the separate analytic result when the primary side truly has zero directional information.

```
