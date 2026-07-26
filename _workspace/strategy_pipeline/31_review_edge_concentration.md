# 31 — AI Reviewer verdict: edge-concentration screen (UNIFORM vs CONCENTRATED)

Reviewer: AI Reviewer (Opus 5), PAPER-scope final authority.
Date: 2026-07-26. Subject: `research/screen_edge_concentration.py` run, artifacts `31_*`.
Posture: adversarial. Default REJECT; APPROVE earned by evidence.

---

```
VERDICT: APPROVE — with one MANDATORY correction to the screen report's prose (below).
         The run's own terminal state INDETERMINATE stands, unmodified.
```

## SCOPE

**Authorized:** recording the run's verdict `INDETERMINATE` as the program's answer; adding a
ledger row that closes the "condition / filter / meta-label / regime-gate the existing entry
signal" family **operationally**; retiring further conditioning spend on this signal.

**NOT authorized, explicitly:**
- Not a claim that the 0.031 excess was **proven uniform**. The equivalence leg FAILED.
- No probe, no order path, no MCP change, no gate change, no live authority. Nothing was promoted.
- Scope of the empirical leg is the frozen 6-bucket family, this signal, this OOS window only.

---

## EVIDENCE REVIEWED — and what I verified independently

| Check | Method | Result |
|---|---|---|
| Prereg integrity | Recomputed SHA-256 of CRLF-normalised body through the sentinel, on BOTH the working tree and the committed blob `9e52bd9` | `48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1` — both match the sidecar. Commit contains exactly 2 files, nothing under `data/` |
| Prereg is pre-outcome | `git show --stat 9e52bd9`; commit message states no outcome statistic computed; run started after | Held |
| **Pooled OOS AUC** | Rebuilt `p_ens` from scratch in my own script (own warehouse handle, own driver), scored with `sklearn.roc_auc_score` | **0.5346184612** — matches the run to 10 dp |
| **All six bucket `A_b`** | `roc_auc_score` on each bucket subset, independent of the screen's M-matrix | 0.52130 / 0.52685 / 0.55645 / 0.54268 / **0.56766** / 0.52720 — all match to 5 dp |
| **All six `D_b`** | Complement taken within own partition, computed independently | −0.01965 / −0.01150 / +0.02715 / +0.01196 / **+0.04045** / −0.04045 — all match |
| Both omnibus statistics | Re-derived `Ā_within` and `T_obs` by hand from the bucket table | P1 Ā 0.5348274851, T 1.873563e−04; P2 Ā 0.5352988147, T 2.619559e−04 — exact match. Centring is on the **within-bucket** weighted mean as §8 requires, not pooled |
| **Clustering is real** | Wrote my OWN day-clustered bootstrap (resample 30 UTC days with replacement, concatenate rows, `roc_auc_score`), different seed, B=3,000 | P2_MAJOR SD **0.03267**, 0.3125th pctl **0.48112** vs the screen's reported LCB 0.47803 — agreement within MC noise at that tail |
| Clustering is NOT row-level in disguise | Same script, row-level bootstrap for contrast | Row-level SD **0.00238** — **13.7× narrower**; it would have produced LCB ≈ 0.561. The reported bound is unambiguously cluster-scale |
| Naive-SE cross-check | Hanley-McNeil at n=270,870 gives SE ≈ 0.001; the reported pooled day-block CI half-width is 0.028 | ~14× wider than naive. A prohibited method could not produce this |
| Prohibited methods absent | `grep` for hanley/delong/row-resample across the script | Only the comment declaring them prohibited. `counts_matrix` reuses `core.decision.monte_carlo.block_bootstrap` (circular, `block_len=1` → 30 iid day units) as §8 specifies |
| Multiplicity is real | Read the constants and their use sites | `M_MULTIPLICITY = 16`, `ALPHA_TEST = 0.05/16 = 0.003125`, `LCB_PCTL = 0.3125`. Used in condition 2 (`p_A`/`p_D` ≤ α), condition 3/7 (LCB percentile) and `fires_at_alpha`. Denominator 16 > family of 14 run — conservative, not tuned |
| Bootstrap p-values are null-recentred | Read `p_A = mean(A_rep ≥ 2A − 0.560)` | Algebraically correct recentring of the percentile bootstrap onto `H0: A_b ≤ 0.560`. Same for `D` |
| Machinery identity | The run's own S2 (day-block Mann-Whitney == `roc_auc_score`) and my independent match | max abs err 5.55e−17 synthetic, **0.0** on the real score |
| SG7 attribution (seed vs drift) | Re-ran the whole construction with `GBMModel(random_state=42)` | **0.529676** vs 0.534618 at `random_state=0`. Confirms the reproduction gap is the GBM seed, not data drift. Residual 0.0008 vs the recorded 0.530517 reference remains unexplained (see UNVERIFIED) |
| Decision rule arithmetic | Recomputed from the JSON | 6 buckets TESTED (≥ 4, so no `NO_ANSWER: insufficient cells`); **0** qualifying pockets; `min(p_P1, p_P2) = 0.245 > 0.003125` so no omnibus fires; **6 of 6** `D_b` equivalence intervals fall outside ±0.030 ⇒ `inside = False`. Mechanically: not CONCENTRATED, not NON_UNIFORM_NOT_NARROW, not UNIFORM ⇒ **INDETERMINATE**. The claimed verdict is the rule's output |

---

## MANDATORY CORRECTION (the one defect I found)

`31_screen_edge_concentration.md` line 21 states:

> "four of the six exceed the band"

**This is wrong. Six of six exceed it.** Every interval breaches ±0.030 at BOTH ends:

| Bucket | `D_b` 99.375% CI | inside ±0.030? |
|---|---|---|
| P1_ADXlo_ATRlo | [−0.08547, +0.04280] | no (both ends) |
| P1_ADXlo_ATRhi | [−0.06125, +0.03120] | no (both ends) |
| P1_ADXhi_ATRlo | [−0.03738, +0.10190] | no (both ends) |
| P1_ADXhi_ATRhi | [−0.03542, +0.05283] | no (both ends) |
| P2_MAJOR | [−0.05306, +0.12907] | no (both ends) |
| P2_ALT | [−0.12907, +0.05306] | no (both ends) |

The **code** computed `inside = False` correctly, so the verdict is unaffected. But the prose
**understates how far homogeneity is from being certified** — the error runs in the direction that
flatters a UNIFORM reading, which is precisely the direction this program must not drift. The
screen artifact is an outcome record and must NOT be silently edited; this review file is the
authoritative correction. Any ledger row or downstream memo must cite **6/6**, not 4/6.

---

## SURVIVED REFUTATION

1. **"The clustering was actually applied."** Attacked three ways: code read, an independently
   written bootstrap, and a row-level contrast. Survived. Cluster SE 0.033 vs row SE 0.0024. Note
   the verdict is robust even to the wrong method: a row-level LCB of ≈0.561 would still fail the
   0.60 condition, so no pocket appears under either inference scheme.
2. **"The multiplicity correction is real, not decorative."** α = 0.003125 is applied at every
   confirmatory decision point. Survived.
3. **"The verdict is the rule's mechanical output, not a narrative."** Re-derived from the JSON.
   Survived. `INDETERMINATE` follows necessarily.
4. **"The reported numbers are the data's numbers."** Full independent re-derivation reproduces
   pooled AUC, all six `A_b`, all six `D_b`, and both omnibus statistics. Survived.
5. **"The omnibus is not a base-rate artifact machine."** §8's within-bucket centring is
   implemented as written, and the S3 unit check returned P(p≤0.05) = 0.020 over 200 offset-only
   draws. Survived. Independently: `Ā_within(P1) = 0.53483` ≈ pooled `0.53462`, so between-bucket
   base-rate information contributes ≈ nothing — the excess is genuinely within-bucket.
6. **"No protocol deviation."** Axes, cuts 23.20/0.94, floors, B, seed, block definition, m=16,
   thresholds, stability conditions, four terminal states, omnibus centring: all executed as
   frozen. Measured geometry matched §7/§14 exactly (n=325,047; test_size 54,174; n_oos 270,870;
   30 days; 28 symbols; all six bucket counts identical). Nothing trimmed, no orientation flip, no
   post-hoc subset. Survived.
7. **The run's own honesty flags survived.** SG7 was prereg-compliant with a **pre-committed halt
   branch** (the seed could not have been switched to rescue a failing gate). SG8 was found in
   post-run review and correctly **not** re-run — changing the injection structure after seeing
   outcomes would be §12 invalidating conduct. SG8 cannot move the verdict: §10's `NO_ANSWER` gate
   fires only above MDE 0.80, and §11 treats `NO_ANSWER` and `INDETERMINATE` identically.

## KILLED / DEMOTED

- **"Four of six intervals exceed the band"** → corrected to **six of six**. Demoted from the
  record; cite this review instead.
- **Any UNIFORM framing.** The run did not reach STATE 3 and says so. I am not letting the task's
  primed expectation ("expectation: UNIFORM") override the measured state. Uniformity is
  **uncertified**, not established.
- **Advisory claim that cond-7 never executed on real data** → false; line 987 calls
  `evaluate(p_ens, short_circuit=False)`, so 2-day-block LCBs were computed for all six buckets:
  0.4720 / 0.4920 / 0.4740 / 0.5062 / 0.4910 / 0.4910 — all below 0.60. Cond 7 fails on its own
  merits, not by fail-closed short-circuit. This *strengthens* the no-pocket finding.
- **Reading the pooled CI excluding 0.500 as evidence against the thin-film picture.** It is not in
  tension: a small excess spread everywhere predicts exactly a detectable global excess with
  indistinguishable between-bucket differences.

## UNVERIFIED (named, not assumed favourably)

1. **The 0.530517 reference itself.** My `random_state=42` rebuild lands at **0.529676**, not
   0.530517 — an 0.0008 residual I could not attribute. Immaterial to the verdict (the gate band
   was frozen pre-outcome at [0.520, 0.541] and both values sit inside), but it is not a bit-exact
   reproduction of the recorded refit.
2. **MDE against a realistic (clustered) alternative.** SG8 is right: the i.i.d.-per-row injection
   makes 0.65 a lower bound. The true clustered MDE is unmeasured and is plausibly > 0.80. I am
   NOT authorizing a re-run to find out — that would be post-outcome design change. Consequence
   recorded: the design's power to *detect a pocket* is weaker than the 0.65 number suggests, which
   makes "no pocket found" weaker evidence than it looks, and is a further reason the answer is
   INDETERMINATE rather than UNIFORM.
3. **Unit-check strength.** §8 specified an offset-only synthetic with pooled AUC ≈ 0.53; the
   realised synthetic averaged **0.5143**, i.e. it stresses the omnibus with roughly half the
   base-rate artifact the prereg envisaged. The check still passed conservatively (0.020), and it
   is moot in practice because the measured artifact contribution is ≈ 0 (item 5 above) — but the
   check is weaker than written.
4. **Headline stability.** The pooled AUC moves 0.5297 → 0.5346 on a GBM seed alone: **≈ 0.005, or
   ~16% of the entire claimed 0.031 excess**, before counting that the construction selects
   best-of-3 `C` and best-of-2 `lr` on the same OOS and fits ensemble weights on OOS labels
   (mirrors `scripts/train_models.py`; frozen by §5). The 0.031 is therefore an **upward-biased,
   seed-sensitive** point estimate. This cuts toward the run's conclusion, not against it.
5. The `RuntimeWarning: Mean of empty slice` at line 991 sits in the exploratory decile block,
   outside the decision path — cosmetic; I confirmed by reading the call site, not by re-running.

## CONFIDENCE: 88 / 100

Basis: every load-bearing number was reproduced independently (pooled AUC to 10 dp, six `A_b`, six
`D_b`, two omnibus statistics), the clustering was proven empirically rather than accepted on a
code read, and the decision rule was re-derived from the artifact JSON. Held below 95 by: the
unexplained 0.0008 reference residual, the unmeasured clustered MDE (SG8), the weaker-than-written
unit check, and one factual error found in the report prose — which is direct evidence that the
producing agent's narrative layer is not perfectly reliable even where its computation is.

---

## WHAT THIS CLOSES — stated plainly

**Closed, operationally and permanently, for this signal:**
0 of 6 pre-registered floor-clearing buckets contains an actionable pocket. Best lower bound
`LCB = 0.507` against the frozen `AUC ≥ 0.60` gate; best p-values 0.438 (`A`) / 0.386 (`D`) against
α = 0.003125; neither omnibus fires (p = 0.454, 0.245). These are **not near-misses** — the
strongest bucket misses the LCB requirement by 0.12. Combined with the analytic result already on
the record — if the primary side carries no directional information then
`E[filter × side × return] = 0` for EVERY measurable filter, hence net expectancy
`= −cost × P(fire) < 0` — **no further conditioner, filter, meta-label, or regime-gate work on the
existing entry signal is authorized.** §11's binding interpretation clause makes INDETERMINATE
prohibit conditioning work exactly as UNIFORM would; the downstream action is identical. That
class of spend ends here.

**NOT closed — and the ledger row may not say otherwise:**
Uniformity was **not proven**. The equivalence leg failed: 6 of 6 `D_b` intervals exceed ±0.030,
widest [−0.1291, +0.0531]. At 30 day-clusters the honest intervals are simply wider than the
equivalence margin, so homogeneity to ±0.030 is **uncertified**.

- **Permitted ledger wording:** "no actionable pocket in the frozen 6-bucket family; conditioning
  the existing signal is closed operationally; homogeneity to ±0.030 uncertified at 30
  day-clusters."
- **Forbidden wording:** "the excess is uniform", "proven a thin film of nothing", "uniformity
  established".

**Scope of the closure.** The *general* claim rests on the analytic proof, which is signal-agnostic
and needs no partition. This run supplies the **empirical leg for the pre-specified partitions
only** — regime (`adx_4h × atr_pct_1h`) and name-selection (`MAJOR/ALT`). It does not, by itself,
rule out a pocket on an axis nobody registered; nothing here licenses going to look for one.
De-dup note for the ledger: this row is **distinct** from the 2026-07-12 band-bucket row (that
tested conditional entry *filters* on band geometry; this tests *concentration of model-score AUC
excess* across feature space).

---

## Files

- Prereg: `D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_prereg_edge_concentration.md`
  (+ `.sha256`), commit `9e52bd9`
- Screen: `D:\Downloads\Trading_Bot\research\screen_edge_concentration.py`
- Outputs: `31_screen_edge_concentration.md` / `.json`, `31_run.log`
- This review: `D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_review_edge_concentration.md`
