# INDEPENDENT ADJUDICATION REQUEST — CODEX-SOL-5.6

You are being asked for an **independent adjudication** of a completed, pre-registered statistical
run in a crypto trading bot repo. Do not run code. Do not modify anything. Read what is below,
optionally read the referenced files (read-only), and return a verdict.

**Disclosure of your own involvement (stated as fact, not as a request to defend anything):**
You (Codex) co-authored the pre-registration being adjudicated here. The design was reconciled
between Claude and Codex in `_workspace/strategy_pipeline/31_design_codex.md`, and the recorded
disagreements D5, D6, D7, D8, D9, D10 and D12 were all resolved **in your favour** (interaction
statistic `D_b`; the `A_b>0.560` / `D_b>0.030` thresholds; the panel-synchronous circular
moving-block bootstrap; multiplicity denominator m=16; UNIFORM-as-equivalence; the four terminal
states replacing the binary framing; the ≤25% narrowness cap). You are therefore adjudicating the
execution of a design that is substantially yours. Adjudicate the run, including the design, as
harshly as the evidence warrants.

---

## 1. THE QUESTION THE RUN ANSWERS (the only one)

The bot's entry signal measures **AUC 0.531 on 270,830 out-of-sample rows** (ensemble refit
2026-07-25; DSR 0.00, gate-refused). Excess over chance = **0.031**.

Is that 0.031 excess **UNIFORM** across feature space (a thin film of nothing, spread everywhere),
or **CONCENTRATED** in an identifiable subset (a real but narrow pocket)?

### Why it matters — both outcomes are valuable
- **UNIFORM** ⇒ no filter, conditioner, meta-label or regime gate can ever extract profit from
  this signal, because there is no subset to select. Combined with the already-established
  analytic proof (if the primary side carries no directional information then
  `E[filter × side × return] = 0` for EVERY measurable filter, so net expectancy =
  `−cost × P(fire) < 0`), this permanently closes the entire "condition the existing signal"
  family on our OWN data. That ends a large class of future spend.
- **CONCENTRATED** ⇒ the identified subset is the only region worth a future pre-registered screen.

### Binding context (measured facts — do not re-derive, do not contradict without evidence)
- ~2,400 refuted pattern tests. Every pre-registered screen this month returned NO_GO
  (C1 CFTC, VPIN, C3 quarter-hour, wrapper-discount, F1-selectivity, stablecoin-depeg).
- 30-day directional expectancy **−0.24R**. Three shadow probes hit their 30-outcome floors on
  2026-07-26 and ALL were gate-blocked (tsmom WR 0.333; zfade WR 0.567; rsi2 WR 0.60 — a 60% win
  rate still lost money).
- Statistical honesty rule adopted 2026-07-26: at n=30 a probe's AUC 95% CI is ~[0.29, 0.72];
  three such intervals are NOT three confirmations. The binding statistical fact is the
  large-sample 0.531.
- ALSO BINDING: naive Hanley–McNeil standard errors are WRONG on this dataset — 43 symbols with
  overlapping 4h bars and high cross-sectional crypto correlation mean the effective sample is far
  smaller than n. Any SE/CI must account for clustering (symbol-clustered or block bootstrap).
- Capital ~$420 retail. Frozen promotion gate is arithmetic: MIN_DSR ≥ 0.10, MAX_PBO ≤ 0.5,
  OOS-WR ≥ 0.55, AUC ≥ 0.60.

### The central trap this design had to defeat
Partitioning feature space and hunting for a "hot" bucket IS mass-variant mining — the exact
methodology this repo has refuted (443+ formulaic alphas, 1,989 candlestick tests, 0/16 band
buckets under Bonferroni). A design that finds a hot bucket without rigorous multiplicity control
has found nothing. The pre-registration therefore had to fix, BEFORE seeing outcomes: the
partition scheme, the number of buckets, the statistics, the inference scheme, the multiplicity
correction and the decision rule.

---

## 2. THE FROZEN PRE-REGISTRATION

**File:** `D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_prereg_edge_concentration.md`
**Sidecar:** `...\31_prereg_edge_concentration.sha256`
**SHA-256 (body, CRLF-normalised, through the `<!-- SHA256-BODY-END -->` sentinel):**
`48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1`
**Commit:** `9e52bd92eacef7a52ef04dd03a1449ff0867461d` (branch `probe/bundle-mr-shadow-2026-07-19`;
exactly 2 files; nothing under `data/`). Supersedes unpublished draft `7c6608f` / digest
`c45cbaee…`, amended pre-outcome for clarification only — lineage disclosed in §16. Blob and
working-tree digests both verify. **No outcome statistic had been computed at freeze time.**

### Frozen design summary

**Expectation stated up front in the prereg: UNIFORM / NO-GO for any filter.**

- **Partition — 6 buckets.** P1 (exhaustive 2×2): `adx_4h × atr_pct_1h` at frozen harvest medians
  **23.20 / 0.94**, read off `X[:,7]` / `X[:,8]` so `_coerce` handling is bit-identical to the
  model's. P2: `MAJOR{BTC,ETH,BNB,SOL,XRP}` vs `ALT`. Excluded: `side` (98.8% buy), hour-of-day
  (refuted 2026-06-02), `p_ens` itself (that is the ROC curve, not feature space).
- **Data freeze.** `load_dataset('futures')` + `l.ts <= 1784370569.0`; OOS = union of 5 anchored
  WalkForward folds (`test_size = n//6`, `oos_start = n − 5·test_size`). Reproduction gate:
  pooled OOS AUC ∈ [0.520, 0.541] or HALT.
- **Floors.** n ≥ 12,000; minority ≥ 2,500; ≥ 20 populated UTC days (≥ 8 per half); ≥ 20 symbols
  (P2 MAJOR ≥ 5). Failing bucket → `INSUFFICIENT_DATA`, never collapsed or redefined.
  < 4 of 6 qualifying → `NO_ANSWER`.
- **Statistics.** Within-bucket tie-aware AUC `A_b`; interaction `D_b = A_b − A_(not b)`;
  per-partition omnibus centred on the **within-bucket** weighted mean (not pooled AUC). Mandatory
  synthetic offset-only unit check must return near-uniform p.
- **Inference.** Panel-synchronous **circular moving-block bootstrap** on 30 UTC day-blocks
  (= the 96×15m label horizon), B = 20,000, seed 20260726. Hanley–McNeil / DeLong / row bootstrap
  **prohibited**.
- **Multiplicity.** Bonferroni **m = 16** (family is 14 nominal / 13 effective — denominator
  deliberately larger so it cannot be accused of tuning). α_test = 0.003125 → LCB at the 0.3125th
  percentile. Equivalence leg at 99.375% simultaneous intervals.

### §8 — THE EXACT DECISION RULE (four terminal states) — VERBATIM

> Codex's four-state structure is binding. The binary CONCENTRATED/UNIFORM framing is explicitly
> rejected as collapsing measurement into judgement.
>
> **STATE 1 — CONCENTRATED**
> A bucket `b` is a **qualifying narrow pocket** iff **ALL SEVEN** hold:
> 1. `b` meets every minimum-cell floor (§7).
> 2. `A_b > 0.560` at `p_raw ≤ 0.003125` **AND** `D_b > 0.030` at `p_raw ≤ 0.003125`
>    (block-bootstrap null-recentred).
> 3. `LCB_b > 0.60` — the frozen gate's own AUC floor, applied to the Bonferroni lower bound, not
>    to the point estimate.
> 4. `b` contains **≤ 25%** of OOS rows.
> 5. **Both-halves stability:** in EACH chronological half, `A_b,h ≥ 0.540` **and** `D_b,h ≥ 0.015`.
> 6. **Thirds stability:** point `A_b ≥ 0.60` in **≥ 2 of 3** equal-duration calendar thirds of the
>    OOS window (2026-06-10 → 06-23, 06-23 → 07-05, 07-05 → 07-18).
> 7. **Block robustness:** `LCB_b > 0.60` still holds under the **2-day** block bootstrap, where
>    2-day blocks pair consecutive entries of the ORDERED LIST of the 30 populated days
>    (pairs 1-2, 3-4, … → 15 blocks) — **not** calendar dates (the window spans 38 calendar days
>    with only 30 populated).
>
> **AND** the partition-level condition: `min(p_P1, p_P2) < 0.003125` (a corrected omnibus fires)
> **AND exactly one** of the six buckets qualifies.
>
> **Verdict language, binding:** *only that already-named bucket may earn one future, separately
> pre-registered, untouched-OOS screen. It does not earn trading permission and does not relax the
> frozen AUC ≥ 0.60, DSR ≥ 0.10, PBO ≤ 0.5, OOS-WR ≥ 0.55, or expectancy gates.*
>
> **STATE 2 — NON_UNIFORM_NOT_NARROW**
> **Two or more** buckets qualify. **Nominate no new screen** — combining them afterward would be a
> new, unregistered search. Further conditioning work is prohibited.
>
> **STATE 3 — UNIFORM (an equivalence result, not a failure to reject)**
> **All six** simultaneous 99.375% intervals on `D_b` lie **wholly inside [−0.030, +0.030]**, **and**
> no bucket qualifies under State 1. Interpretation: no pre-specified selectable bucket differs
> from the rest by a practically material 3 AUC points.
>
> Descriptive sub-flags, reported alongside but not altering the state:
> - `UNIFORM_HOMOGENEOUS` — neither omnibus fires.
> - `HETEROGENEOUS_BUT_UNACTIONABLE` — an omnibus fires but no bucket clears conditions 2–7.
>   Decision-equivalent to UNIFORM: a sub-0.60 pocket cannot pass the frozen gate, and with the
>   primary side carrying no directional information `E[filter × side × return] = 0` for every
>   measurable filter, so net expectancy = `−cost × P(fire) < 0`.
> - `UNIFORM_BASE_RATE_ARTIFACT` — additionally flagged when `Ā_within ≤ 0.510` while pooled AUC
>   ≈ 0.531. The pooled excess is then between-bucket base-rate information, not within-regime
>   timing information. This is the strongest available closure of the conditioning family.
>
> **STATE 4 — INDETERMINATE (operationally NO-GO)**
> Neither the concentration rule nor the equivalence rule is met.
>
> **NO_ANSWER (the design failed, not the hypothesis)**
> Reported instead of any verdict if: the reproduction gate fails; the synthetic offset-only unit
> check fails; fewer than 4 of 6 buckets meet the floors; or MDE > 0.80.
>
> **BINDING INTERPRETATION CLAUSE (Codex, verbatim)**
> > "Do not relabel failure to find a pocket as scientific uniformity. Both UNIFORM and
> > INDETERMINATE prohibit further conditioning work; only CONCENTRATED permits one future screen."
>
> **Reported unconditionally, in every state**
> The pooled AUC's two-sided 95% **day-block-clustered** CI. If it contains 0.500, the report must
> state plainly that the 0.031 excess is not distinguishable from zero at the honest effective
> sample size (30 day-blocks). This reinforces UNIFORM but does **not** by itself decide the verdict.

### §9/§12 — WHAT MAKES THE PROTOCOL **INVALID** RATHER THAN NEGATIVE — VERBATIM

> Outcome information influencing features, cutpoints, cell definitions, block length, sample
> inclusion, or the decision threshold; any minimum-cell or minimum-block requirement failing; the
> full-data AUC failing to reproduce; scores refit or reoriented after seeing results; iid rows,
> naive AUC SEs, or symbol-independent bootstrap being substituted; a cell being merged, split, or
> combined after results are seen; **any 'hot' subset outside these six buckets being discussed as
> evidence.**

### §9 — Acknowledged boundary (both models agreed; binding on the ledger row) — VERBATIM

> This can establish uniformity **only over this explicitly frozen six-bucket conditioner family,
> this signal, and this window**. No finite bucket experiment can prove uniformity over *every
> measurable filter*. The universal closure comes only from the separate analytic result that when
> the primary side truly has zero directional information, `E[filter × side × return] = 0`.

**Note on untested axes (your D2 position, recorded in the prereg):** your proposed axes
`|logit(p)| × idiosyncratic-vol × BTC-7d-return` were NOT tested. `|logit(p)|` was excluded at D3
as circular; the other two were dropped for cell-size feasibility. The run therefore does not
speak to them.

---

## 3. THE RUN RESULT

**Files (read-only, absolute paths):**
- `D:\Downloads\Trading_Bot\research\screen_edge_concentration.py`
- `D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_screen_edge_concentration.md`
- `D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_screen_edge_concentration.json`
- `D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_run.log`
- Prereg + sidecar as above.

Prereg hash re-verified in-run: `48a2bab1…` matches the sidecar and commit `9e52bd9`.
Executed 2026-07-26T14:45:48Z → 15:45:30Z. Warehouse opened `mode=ro`; no `core/`, `config.py`,
`.env` or running process touched.

### MECHANICAL VERDICT AS REPORTED BY THE RUN: `INDETERMINATE` (STATE 4)

### Gates (frozen order)
| Stage | Result |
|---|---|
| S1 reproduction — pooled OOS AUC in [0.520, 0.541] | **0.534618** → PASS |
| S2 machinery (day-block Mann-Whitney == `roc_auc_score`) | max abs err 5.55e-17 on tied synthetic, **0.0** on the real score → PASS |
| S3 unit check (offset-only synthetic, 200 draws) | P(p≤0.05)=0.020 → PASS |
| S4 measured MDE (§10) | MDE = 0.65 → ADEQUATE (but see SG8) |

### Measured geometry (matched the frozen §7/§14 table EXACTLY; nothing trimmed)
harvest n = 325,047; `test_size` = 54,174; `oos_start` = 54,177; **n_oos = 270,870**;
**30 populated UTC days**; 28 symbols; window 2026-06-10T21:15Z → 2026-07-18T10:28Z;
base rate 0.3612. All six bucket row counts identical to the frozen table.

| Bucket | n | share | days | symbols | max sym share | floors |
|---|---:|---:|---:|---:|---:|---|
| P1_ADXlo_ATRlo | 93631 | 0.346 | 27 | 25 | 0.090 | QUALIFIES |
| P1_ADXlo_ATRhi | 56354 | 0.208 | 30 | 26 | 0.117 | QUALIFIES |
| P1_ADXhi_ATRlo | 55634 | 0.205 | 30 | 25 | 0.139 | QUALIFIES |
| P1_ADXhi_ATRhi | 65251 | 0.241 | 30 | 27 | 0.102 | QUALIFIES |
| P2_MAJOR | 54206 | 0.200 | 30 | 5 | 0.228 | QUALIFIES |
| P2_ALT | 216664 | 0.800 | 30 | 23 | 0.069 | QUALIFIES |

**Pooled OOS AUC 0.534618**; day-block-clustered 95% CI **[0.5096, 0.5654]** on 30 clusters.
Contains 0.500: **False**.

### Per-bucket result (panel-synchronous circular moving-block bootstrap, 30 day-blocks, B=20,000, seed 20260726)

| Bucket | n | A_b | A_not_b | D_b | LCB(1d) | p(A) | p(D) | D 99.375% CI | pocket |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| P1_ADXlo_ATRlo | 93631 | 0.5213 | 0.5410 | −0.0197 | 0.4675 | 0.9702 | 0.9817 | [−0.0855, +0.0428] | no |
| P1_ADXlo_ATRhi | 56354 | 0.5269 | 0.5383 | −0.0115 | 0.4862 | 0.9851 | 0.9899 | [−0.0612, +0.0312] | no |
| P1_ADXhi_ATRlo | 55634 | 0.5565 | 0.5293 | +0.0271 | 0.4898 | 0.5839 | 0.5652 | [−0.0374, +0.1019] | no |
| P1_ADXhi_ATRhi | 65251 | 0.5427 | 0.5307 | +0.0120 | 0.5070 | 0.9074 | 0.8356 | [−0.0354, +0.0528] | no |
| P2_MAJOR | 54206 | 0.5677 | 0.5272 | **+0.0405** | 0.4780 | 0.4380 | 0.3860 | [−0.0531, +0.1291] | no |
| P2_ALT | 216664 | 0.5272 | 0.5677 | −0.0405 | 0.4922 | 0.9944 | 0.9828 | [−0.1291, +0.0531] | no |

### Heterogeneity omnibus (centred on the within-bucket weighted mean)
| Partition | Ā_within | T_obs | p | fires at α=0.003125 |
|---|---:|---:|---:|---|
| P1 | 0.5348 | 1.874e-04 | 0.4544 | False |
| P2 | 0.5353 | 2.620e-04 | 0.2454 | False |

(§8 note: P2 has exactly two cells, so `D_ALT = −D_MAJOR` and the P2 omnibus is algebraically a
restatement of `D_MAJOR²` — one effective degree of freedom, never mutual corroboration.)

### Measured power (§10)
| Target | AUC 0.55 | 0.60 | 0.65 | 0.70 | 0.75 | MDE |
|---|---:|---:|---:|---:|---:|---|
| P1_ADXhi_ATRlo | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.65 |
| P2_MAJOR | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.65 |

### The strongest bucket, in full (the run's own reading)
`P2_MAJOR`: `A_b` = 0.5677. Chronological halves **stable**: 0.570 / 0.570, with `D` = +0.032 /
+0.051 — i.e. its **point** `D_b` = +0.0405 **exceeds** the +0.030 materiality threshold in the
pooled window and in both halves. It fails on: raw p 0.438 (A) / 0.386 (D) against a threshold of
0.003125; `LCB` = 0.478 against a 0.60 gate; only **1 of 3** calendar thirds reaches 0.60; and it
is a 5-name bucket with the largest single-symbol share in the study (0.228). The run's stated
reading: *"A 5-name bucket with the largest single-symbol share in the study drifting 0.04 above
the rest is what noise at 30 clusters looks like."*

`P1_ADXhi_ATRlo` swings **0.463–0.598** across calendar thirds while averaging 0.5565.

### Why INDETERMINATE, mechanically (the run's decision trace)
1. **Not CONCENTRATED** — 0/6 buckets clear conditions 2–7. No `A_b` reaches 0.560 at p≤0.003125
   (best raw p 0.438); no `D_b` reaches +0.030 at p≤0.003125 (best 0.386); no `LCB_b` reaches 0.60
   (best 0.507, i.e. 0.09 short); neither omnibus fires.
2. **Not NON_UNIFORM_NOT_NARROW** — needs ≥2 qualifiers; there are 0.
3. **Not UNIFORM** — UNIFORM is an *equivalence* result (STATE 3) and must be earned. Four of six
   99.375% `D_b` intervals exceed ±0.030; widest `P2_ALT` [−0.1291, +0.0531].
4. ⇒ **INDETERMINATE.**

All six buckets cleared the floors, so `NO_ANSWER: insufficient cells` did not trigger.

### Exploratory (NON-CONFIRMATORY, §D3 — cannot move the verdict)
Score-decile within-decile AUCs, reported because raising `MCP_ENTRY_MIN_SCORE` is the program's
most-proposed filter: D1 0.5553 (n=28392), D2 0.5002, D3 n=0 (tie artifact: `p_ens` is a weighted
sum of two isotonic step functions so its 20th and 30th percentiles coincide), D4 0.4957,
D5 0.5046, D6 0.4889, D7 0.4978, D8 0.4901, D9 0.4922, D10 0.5316.

### Simpson check
`Ā_within,P1` = 0.5348 versus pooled 0.5346 — agree to 2e-4. The pooled excess is **not**
between-bucket base-rate information along the regime axes. `UNIFORM_BASE_RATE_ARTIFACT` (needs
`Ā_within ≤ 0.510`) does **not** apply. Bucket base rates span 0.339–0.395.

### PROTOCOL DEVIATIONS: **NONE**
Every frozen element executed as specified — partition axes, cuts 23.20/0.94, bucket membership
and count, floors, statistics, omnibus centring, resampling unit and scheme, both block lengths,
B=20,000, seed 20260726, m=16, all thresholds, stability conditions, four terminal states. No
orientation flip anywhere in the code path. No re-partitioning, no added axis, no cut adjustment,
no merging or splitting, no post-hoc subset examined or discussed as evidence.

### Fidelity notes disclosed by the run
1. Measured geometry matched §7/§14 exactly; nothing trimmed.
2. Reproduction landed at **0.534618** vs the recorded **0.530517**, inside the frozen band. The
   gap is attributed to SG7 (`random_state=0` per §5 vs the trainer's default 42, which moves
   sklearn's `early_stopping='auto'` validation split), not to data drift — the harvest is
   byte-for-byte the same size as at freeze time.
3. The unit check's synthetic score reached pooled AUC 0.514, not the ≈0.53 §8 uses
   illustratively; construction exactly as specified; check passed decisively.
4. AUC machinery validated to exactness, not tolerance (5.55e-17 tied synthetic; 0.0 real score).

### Specification gaps SG1–SG7 — pre-declared as named constants BEFORE the run
- **SG1** — §10 never names which bucket to inject into. → Primary target = smallest P1 cell
  clearing the ≤25% cap (smallest = hardest = conservative). Secondary = P2_MAJOR (fewest symbols,
  largest single-symbol share). Reported MDE = the larger (more conservative) of the two.
- **SG2** — "detected" undefined. → Detected = terminal state CONCENTRATED **and** the single
  qualifying bucket is the injected one. Softer diagnostic reported alongside.
- **SG3** — "near-uniform omnibus p" had no numeric criterion. → 200 independent synthetic draws;
  PASS iff empirical P(p≤0.05) ≤ 0.10.
- **SG4** — "chronological half" undefined. → Ordered list of populated OOS UTC days split 15/15.
  Thirds keep the explicit calendar boundaries §11 cond 6 gives.
- **SG5** — omnibus replicate weights ambiguous. → Fixed OBSERVED `n_k/N_P` for both observed and
  replicate T.
- **SG6** — 2-day blocks. → Days paired (1-2, 3-4, …) into 15 fixed super-clusters, same circular
  moving-block bootstrap over those units.
- **SG7** — §5 specifies `GBMModel(random_state=0)`; `scripts/train_models.py` uses class default
  42, and sklearn's `early_stopping='auto'` fires above 10k rows so the choice moves `p_ens`.
  → The prereg is binding: `random_state=0` used. If the reproduction gate had FAILED the
  mechanical outcome would be `NO_ANSWER: reproduction` — the seed would NOT be switched to obtain
  a passing gate.

### SG8 — IDENTIFIED IN POST-RUN REVIEW, **NOT** DECLARED BEFORE THE RUN — VERBATIM

> Recorded separately and honestly: unlike SG1–SG7 above, this gap was **not** foreseen and
> **not** pinned by a pre-run constant. It was found while reviewing the finished power curve.
>
> **Clause.** §10 specifies the injected effect sizes (`{0.55 … 0.75}`), the count (200 sims), and
> the power bar (80%), but says nothing about the injected score's **dependence structure**.
>
> **What was executed.** `synth_auc_score` draws `mu·y + N(0,1)` i.i.d. per row and rank-maps it
> onto the bucket's real score marginal. That preserves the marginal but gives the injected bucket
> almost no day-to-day dispersion in discrimination.
>
> **Consequence, disclosed.** The day-block bootstrap's whole job is to price day-to-day
> dispersion, so an i.i.d. injection is systematically easier to detect than a real pocket would
> be. The step from power 0.00 at 0.60 to 1.00 at 0.65 with zero variance at both ends is the
> signature of this. Cross-check against the real data: `P2_MAJOR` shows `A_b` − `LCB` = 0.0897,
> implying a cluster SE ≈0.033; for a synthetic 0.65 bucket to clear `LCB > 0.60` in 200/200 sims
> its SE must be under ≈0.018 — roughly half. **MDE = 0.65 is therefore a lower bound.** The same
> mechanism inflates the condition-6 (thirds) pass rate.
>
> **Why it was NOT re-run with a clustered injection.** Changing the injection's dependence
> structure after seeing the outcome is closer to §12's "outcome information influencing … the
> decision threshold" than to a fix. The frozen design was executed as written and the shortfall
> is disclosed instead. A future pre-registration should pin the injected alternative's cluster
> structure explicitly.

The run further argues: *"This does not change the answer, and must not be used to re-open it.
The §10 gate fires `NO_ANSWER: underpowered` only above MDE 0.80. If the honest MDE were >0.80 the
state would flip INDETERMINATE → `NO_ANSWER`, and §11's binding clause treats the two identically:
conditioning work prohibited. No correction to the power curve can reach CONCENTRATED or UNIFORM,
because those states are decided by the observed statistics, not by the MDE."*

### THE RUN'S OPERATIONAL CONCLUSION (the second half of what you are adjudicating)

> No actionable pocket exists anywhere in the frozen family and the misses are not near-misses.
> But homogeneity to ±0.030 is **uncertified** — at 30 day-clusters the intervals are wider than
> the equivalence margin. **The ledger row may say the conditioning family is closed
> operationally; it may NOT say uniformity was proven.**
> Operationally: **INDETERMINATE and UNIFORM both prohibit further conditioning work.** Only
> CONCENTRATED would have permitted one future screen, and it did not occur.

---

## 4. WHAT WE NEED FROM YOU

Answer these, numbered, in this order. Be concise but complete. Do not hedge into non-answers.

**Q1 — YOUR VERDICT IN THE PREREG'S OWN FOUR-STATE VOCABULARY.**
Exactly one of: `CONCENTRATED` / `NON_UNIFORM_NOT_NARROW` / `UNIFORM` / `INDETERMINATE` /
`NO_ANSWER` (and if NO_ANSWER, which trigger). State it as a single token on its own line, then
justify in ≤10 lines.

**Q2 — YOUR VERDICT MAPPED INTO THE CALLER'S SCHEMA.**
The calling workflow accepts exactly one of these four tokens and NOTHING else:
`CONCENTRATED` | `UNIFORM` | `INSUFFICIENT_DATA` | `INVALID_RUN`.
The prereg's `INDETERMINATE` and `NO_ANSWER` do not exist in that vocabulary. **You** must choose
the mapping — we will transcribe your choice, not substitute our own. Give the token on its own
line and one paragraph of reasoning. Note explicitly whether you consider `UNIFORM` available
given STATE 3's equivalence requirement (four of six `D_b` intervals exceed ±0.030), and whether
you consider `INVALID_RUN` triggered by anything (SG7's seed, SG8's post-hoc power disclosure, or
any §12 INVALID item).

**Q3 — DO YOU AGREE WITH THE RUN'S CONCLUSION?**
The run's conclusion has TWO parts: (a) the mechanical state `INDETERMINATE`, and (b) the
operational reading *"the conditioning family is closed operationally; uniformity was NOT proven."*
Answer AGREE or DISAGREE against the conclusion **as a whole**, then name which part (a or b, or
both) you dissent on if any. A single boolean is needed downstream, so make the overall stance
unambiguous.

**Q4 — THE STRONGEST ARGUMENT AGAINST THAT CONCLUSION.**
Steelman the opposition, do not strawman it. Consider at minimum:
- `P2_MAJOR`'s point `D_b` = +0.0405 **exceeds** the +0.030 materiality threshold, is stable across
  both halves (+0.032 / +0.051), and fails only on significance and LCB at 30 clusters. A hostile
  reader says: *"your largest effect clears the material threshold and is stable; you are calling
  it noise only because 30 clusters cannot resolve it — that is INSUFFICIENT_DATA, not closure."*
- SG8: MDE 0.65 is a disclosed **lower bound**; the honest MDE against a realistically clustered
  pocket was never measured and could exceed the 0.80 `NO_ANSWER` trigger.
- The m=16 Bonferroni correction on 30 effective clusters may make the test nearly unable to reject
  anything, i.e. the design could be pre-committed to a negative.
- The untested D2 axes (`|logit(p)|`, idiosyncratic vol, BTC-7d return).
- Single 30-day regime, 28 symbols, one blend parameter fit in-sample inside `p_ens`, and SG7's
  seed moving the pooled point estimate by 0.0041.
Then state whether that strongest argument **changes your Q1/Q2 answer** — and if not, why not.

**Q5 — WHAT DOES THIS RUN ACTUALLY CLOSE?**
Be precise about scope. Specifically: does it close the "condition the existing signal" family
(a) universally, (b) over this frozen six-bucket family / this signal / this 30-day window only,
or (c) not at all? What future work does it forbid, and what — if anything — does it still leave
open? Is the run's proposed ledger language (*"closed operationally; uniformity NOT proven"*)
correct, an overclaim, or an underclaim?

**Q6 — ISSUES.**
List discrete defects, overclaims, underclaims or methodological problems you found, one per line,
prefixed `ISSUE:`. If none, write `ISSUE: none`. These go into a structured field verbatim.

Ground every claim in the numbers above or in the referenced files. Do not invent statistics.
