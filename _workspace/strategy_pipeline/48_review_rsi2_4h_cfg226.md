# 48 — ai-reviewer review block: lane `rsi2_4h_cfg226` (bundle-MR TRACKER arm, `Rsi2TrackerProbeAgent`)

*2026-07-31. Reviewer: ai-reviewer (Opus 5). Inputs: `48_s1_dossier_rsi2_4h_cfg226.md`,
`48_verdict_fable.md`, `48_verdict_codex.md`, `48_verdict_reconciled.md`, `48_rsi2_breakdown.py`,
`46_review_pullback_ma20_4h.md` (binding conditions C1/C2), plus independent re-derivation from
`data/promotion_funnel.json`, `data/warehouse.sqlite`, `scripts/promotion_funnel.py`,
`core/shadow_resolver.py`, `core/agents/bundle_mr_probe_agent.py`, `core/bot_engine.py`, `config.py`.*

## VERDICT: **APPROVE** — with five binding conditions (C1–C5)

The decision under review is correct and **over-determined**: four of the failing gates
(net, expectancy, PF, DSR) are measured on realized after-cost money and are not arguable.
My conditions correct the **record**, not the direction. Two of them are defects that survived
the shared dossier, both independent verdicts, and the reconciliation.

## SCOPE

**Authorizes:**

- (a) **NO-PROMOTE confirmed** for `rsi2_4h_cfg226` on the frozen gate.
- (b) **RETIRE via ADJUDICATION-CLOSE (option a)** — the lane verdict is final as of the
  2026-07-31T03:40:07Z funnel snapshot; later resolutions do **not** reopen it. No per-arm flag
  is added. Physical de-registration remains deferred to the `zfade_4h_cfg365` adjudication.
- (c) **Ledger tracker-row closure** using the Codex-endorsed row text **as amended by C1–C4**.
- (d) S4 journal record of this adjudication.

**Does NOT authorize:** any live or paper ORDER flow; any change to `core/promotion_gate.py`,
`config.py`, `.env*`, or any frozen threshold; any re-tune of the frozen score; any variant or
continuation of cfg226; any unattended bot restart; any pre-judgement of `zfade_4h_cfg365`; any
reopening of the RSI mean-reversion family (it stays REFUTED — 5 coins × 3yr NO_EDGE, 2026-06;
forward n=102 is corroborating, not a new family-level refutation).

**Reviewer execution limit:** `.env*` is on my immutable-kernel hard-stop list. The deferred
de-registration **decision** is approved; the `.env` **write** is the owner's/operator's action.

## EVIDENCE REVIEWED (independently re-derived, not accepted from the dossier)

`data/promotion_funnel.json` (`generated_utc` 2026-07-31T03:40:07.572328+00:00, `resolved_floor` 30),
lane `rsi2_4h_cfg226`, `state: GATE_BLOCKED`, `gate.passed false`, `gate.fail_closed true`:

| Gate | Funnel | Threshold | ok | Dossier | Match |
|---|---|---|---|---|---|
| n_resolved | 102 | 30 | true | 102 | ✓ |
| oos_wr | 0.6471 | 0.55 | true | 0.6471 | ✓ |
| auc | 0.5 | 0.6 | false | 0.50 | ✓ (**but see C2 — placeholder, not measured**) |
| net_after_cost_pnl | −79.462309 | 0.0 | false | −79.46 | ✓ |
| expectancy | −0.779042 | 0.0 | false | −0.779 | ✓ |
| profit_factor | 0.652216 | 1.0 | false | 0.652 | ✓ |
| dsr | 0.0403 | 0.1 | false | 0.0403 | ✓ (single-stream zero-skill proxy, self-labelled) |
| pbo | null | 0.5 | true (`computable:false`, informational) | not shown | n/a |

**5 of 7 substantive gates FAILED.** "5 of 7" is the honest phrasing (PBO is non-computable on a
single stream and is not a pass in any meaningful sense).

`data/warehouse.sqlite`, `shadow_outcomes ⋈ shadow_decisions` on `agent_id='Rsi2TrackerProbeAgent'`,
`label_status='RESOLVED'` — **my own query, not the dossier's script** (n=102, single
`model_version='rsi2_4h_cfg226_v1'`, resolved window 2026-07-19T20:35:03Z → 2026-07-30T16:35:03Z):

| Exit path | n | wins | net USDT | gross | slippage | avg R |
|---|---|---|---|---|---|---|
| take_profit (0.8×ATR) | 66 | 66 | +149.02 | +165.18 | 13.19 | +0.320 |
| stop_loss (2.0×ATR) | 31 | 0 | −214.76 | −207.86 | 9.31 | −1.077 |
| time (12-bar) | 5 | 0 | −13.72 | −12.39 | 1.00 | −0.390 |
| **total** | **102** | **66** | **−79.4623** | **−55.0735** | **23.4968** | **−0.1396** |

Arithmetic re-checked by me, not quoted — **every load-bearing number reproduces exactly**:

- WR 66/102 = **0.647059** ✓ · expectancy −79.4623/102 = **−0.779042** ✓
- PF 149.02 / 228.48 = **0.652216** ✓
- avg win +2.2579 / avg loss −6.3467 → payoff **0.3558**, breakeven WR **0.7376** (73.8%) ✓;
  realized 64.7% → **9.1pp short** ✓
- Net identity: gross − fees + funding = −55.0735 − 24.4729 + 0.0840 = **−79.4623** ✓
- Friction components: fees **24.4729**, slippage **23.4968**, funding **+0.0840** ✓ (dossier 48.05)
- expectancy sd 4.5043, se 0.4460, t **−1.747**, 95% CI **[−1.6532, +0.0951]** ✓
- Sides both negative: buy 46 → −43.82, sell 56 → −35.64 ✓ · worst symbol ZEC 4 trades −37.35 ✓ ·
  **ex-ZEC −42.12 over 98** ✓
- Universe widening: pre-widen n=1 (+0.79), post-widen n=101 (WR 0.644, −80.25) ✓ — dossier's
  "101 of 102 post-widen" is accurate.

Data-pull script `48_rsi2_breakdown.py` — **query is sane**: read-only URI (`mode=ro`), resolved-only
(`o.label_status='RESOLVED'`), joined on `proposal_id`, filtered on `agent_id='Rsi2TrackerProbeAgent'`.
No look-ahead, no selection, no re-tune. Single arm confirmed (only one `model_version` in the
result set; the sibling `zfade_cfg365` lives under a different `agent_id` and is not contaminating
the pull). My independent query returns the identical 102 rows.

Code paths verified by reading, not assumption:

- `core/agents/bundle_mr_probe_agent.py` — **zero** order-placement calls
  (`place_order`/`create_order`/`execute_trade`/`order_manager`/`submit`: no matches). Genuinely log-only.
- `core/bot_engine.py:742-758` — both arms registered from the shared `BUNDLE_MR_PROBE` config dict
  at engine startup; a flag flip therefore requires a process restart. Mechanic is sound.
- `core/shadow_resolver.py:191-211` — `gross = move * size` computed on **slippage-adjusted fills**
  (`entry_filled`/`exit_filled`); `slippage_cost` is a diagnostic of friction **already priced into
  gross**; `net = gross − fees + funding_total`. This is the basis of C1.
- `config.py:519-520` — `BUNDLE_MR_PROBE["enabled"] = os.getenv("SHADOW_BUNDLE_MR_PROBE_ENABLED", "true")`.
  The line is **absent from `.env`**; default is enabled. Basis of C3.

## CHECKS REQUESTED

1. **Both-agree — GENUINELY SATISFIED.** I read both source verdicts in full. Fable and Codex
   independently return NO / RETIRE-option-(a) / close-row on Q1–Q3. The agreement matrix in
   `48_verdict_reconciled.md` is accurate against the two verdicts as written. Codex's three flags
   are wording-and-inference discipline (band phrasing, causal overclaim, cross-symbol correlation),
   all accepted rather than disputed — no substantive evidence conflict existed. Agreement is real,
   not manufactured. **However, neither model nor the reconciliation caught C1 or C2.**
2. **Frozen gate treated as arithmetic — YES.** No reviewer argued past a failed threshold, proposed
   a threshold change, re-tuned the frozen score, or ran a "punitive cost model" defence. Both
   explicitly refuse to let the positive CI upper bound function as a gate exception. Correct: the
   boundary scenario is *zero* expectancy, which still fails net>0, PF>1 and DSR.
3. **Friction-split condition C1 (from `46_review_pullback_ma20_4h.md`) — PARTIALLY HONORED.** See C1.
4. **Adjudication-close mechanics vs live decision paths — CLEAN.** Adjudication-close is a
   documentation action. It disables nothing today, changes no config, and touches no order path.
   The probe remains log-only and registered. Nothing in this decision can move capital in either
   direction. See C3/C4 for the honesty consequence of that.
5. **PAPER scope — CONFIRMED. No ESCALATE_TO_HUMAN element.** Nothing here touches
   `docs/CONTROLLED_LIVE_CHECKLIST.md`, `CONTROLLED_LIVE`, `live_trading`, withdrawals, order flow,
   or the frozen gate. Retirement moves strictly toward *less* activity.

## BINDING CONDITIONS

### C1 — Correct the "frictionless" figure; C1-of-46 is PARTIALLY honored (blocks (c))

**What C1-of-46 got right here:** the dossier's friction split correctly counts the slippage that
lives *inside* `gross` — fees 24.47 + embedded slippage 23.50 (+ funding 0.08) = 48.05 USDT ≈ 60%
of the net loss. That is exactly the correction review 46 demanded, and it is arithmetically
consistent (net −79.46 minus friction ≈ −31.5).

**What fails is the very next clause.** The dossier states *"Frictionless (gross) P&L = **−55.07** —
still negative at zero cost"*, and both verdicts and the endorsed ledger row repeat it (Fable:
"Frictionless P&L is −55.07"; Codex: "−55.07 USDT even without all costs"). **This labels `gross`
as frictionless when `gross` still carries the full 23.50 USDT of slippage.** The dossier is
internally inconsistent with itself: friction cannot be 60% of the loss *and* removing it leave −55.07.

Correct zero-cost P&L = `gross + slippage` = −55.0735 + 23.4968 = **−31.58 USDT** — precisely the
arithmetic review 46 performed for the pullback lane (−107.73 + 8.91 = −98.82).

**The conclusion survives and must be stated as surviving it:** −31.58 over 102 trades
(−0.31/trade) is still negative, and WR 64.7% vs breakeven 73.8% is cost-independent. The loss is
signal *and* friction, as the dossier says — only the frictionless number was wrong, by 74% in
relative terms.

Required row text: *modeled friction = fees 24.47 + embedded slippage 23.50 = 47.97 USDT gross,
less a +0.08 funding credit = **47.89 USDT net friction** (≈60% of the net loss; slippage is priced
into `gross_pnl` via slippage-adjusted fills, `shadow_resolver.py:191-211`). Frictionless P&L
−31.58 USDT — still negative, so the failure is signal as well as cost.*

Immaterial sub-point, corrected for the record: funding **+0.0840** is a *credit* (`net = gross −
fees + funding`) and was summed into the dossier's 48.05 friction total as if it were a cost.
Magnitude 0.17 USDT — does not move any conclusion.

### C2 — AUC 0.50 is a NOT-COMPUTABLE default, not a measurement; the true measured AUC is 0.4815 (blocks (c))

`scripts/promotion_funnel.py:365-370`:

```python
def _auc(scores_pos: list[float], scores_neg: list[float]) -> float:
    if not scores_pos or not scores_neg:
        return 0.5
```

and `:397-400` builds those lists from `shadow_decisions.p_win`. **`p_win` is NULL for all 119
`Rsi2TrackerProbeAgent` rows** — so both lists are empty and the funnel returns the literal constant
`0.5`. Unlike `pbo`, which is honestly flagged `computable: false, informational: true`, the `auc`
field carries **no such flag** and reads as a measured statistic. The dossier, both verdicts and the
endorsed ledger row all quote "AUC 0.50" as if the frozen score had been evaluated at exactly zero
discrimination. It was not evaluated at all.

**This is systemic, not rsi2-specific.** `p_win` is populated 0-of-N for *every* probe agent —
`Rsi2TrackerProbeAgent` 0/119, `ZfadeProbeAgent` 0/60, `TsmomProbeAgent` 0/70,
`PullbackMomentumProbeAgent` 0/46, `BreakoutProbeAgent` 0/1 (contrast `TrendAgent` 31290/31290).
Every probe lane's AUC gate has been a fail-closed placeholder.

**The score does exist and IS measurable.** `shadow_bundle_mr_probe.score` is populated 102/102 with
102 distinct values. Computing the funnel's own AUC formula on it:

> **true AUC (frozen score `tanh((10−RSI2)/10)` longs / `tanh((RSI2−90)/10)` shorts) = 0.4815**
> (n_pos 66, n_neg 36; mean score on wins 0.2945 vs losses 0.3115 — very slightly *anti*-predictive)

The measured value **fails the 0.60 floor harder than the placeholder**, so the verdict direction is
unaffected and in fact strengthened. But the permanent record must not assert an unmeasured number.

Required: the row states **AUC 0.4815 (measured from `shadow_bundle_mr_probe.score`, n=102; the
funnel's `auc: 0.5` is a not-computable default because `shadow_decisions.p_win` is NULL for all
probe rows)**. The Codex-endorsed row text embeds the bare figure "AUC 0.50" — when C1–C4 are
applied, that figure must be **replaced**, not supplemented: the row carries `AUC 0.4815 (measured)`
and retains 0.50 **only** inside the explicit placeholder explanation, nowhere else.

### C3 — Bind the DEFERRED de-registration as an ADD, not a SET (blocks calling (b) complete)

`.env` contains **no** `SHADOW_BUNDLE_MR_PROBE_ENABLED` line, and `config.py:520` defaults to
`"true"`. All three artifacts phrase the deferred action as "`SHADOW_BUNDLE_MR_PROBE_ENABLED=false`",
which reads as editing an existing line. This is the **exact C2 failure mode from review 46,
arriving pre-emptively** — an owner who greps, finds nothing, and assumes it is handled leaves both
bundle-MR arms **enabled by default**.

Evidence this distinction is what makes owners act correctly: review 46's C2 was honored —
`.env:233` now reads `SHADOW_PULLBACK_PROBE_ENABLED=false`.

Required: the deferred action is **ADD the line `SHADOW_BUNDLE_MR_PROBE_ENABLED=false` to `.env`
(currently absent; config default is `true`)**, effective at the next **owner-attended** restart
(no unattended bounce — standing rule), and only after `zfade_4h_cfg365` has its own adjudication.
De-registration may be called complete only after a post-restart confirmation that
`BUNDLE_MR_PROBE["enabled"]` evaluates **False**.

### C4 — The row must not imply the probe has stopped; carry the snapshot cutoff (blocks (c))

Adjudication-close ends the *verdict*, not the *logging*. On the day this row is written "CLOSED /
RETIRED" the arm is still registered, still generating decisions, and still resolving them: at read
time the warehouse holds **119 proposals, 102 RESOLVED, 17 PENDING**. (I verified no proposal was
created at or after the 03:40:07Z snapshot — latest proposal 2026-07-30T16:00:00Z — so the snapshot
is not stale, but the 17 pending rows will resolve after it.)

Required: the row states that (i) numbers are an **adjudication snapshot at 2026-07-31T03:40:07Z**,
(ii) 17 proposals remain unresolved and the arm **continues logging** until the deferred shared-flag
retirement, and (iii) those later resolutions **do not reopen** this verdict.

### C5 — The `zfade_4h_cfg365` dossier must compute AUC from the probe table (forward-binding)

`48_verdict_reconciled.md` characterizes the next lane as *"`zfade_4h_cfg365` (56 resolved, passes
6/7 — AUC only fail; the genuinely interesting lane)."* Per C2, that lane's `p_win` is also 0/60 —
so the **sole stated blocker on the lane the reconciliation itself calls the interesting one is a
not-computed placeholder**. That is a live risk of a wrong promotion-track decision at the very next
iteration.

Required: the `zfade_4h_cfg365` S1 dossier must compute AUC from `shadow_bundle_mr_probe.score`
(arm `zfade_cfg365`, frozen `tanh(|z_entry|/3.0)`, resolved-only) and state the method; it **may not
quote the funnel's `auc` field**. I deliberately did **not** compute or publish zfade's AUC here —
that number belongs in its dossier, so both models see it simultaneously and the independent-verdict
protocol is preserved.

**Recommendation (not an authorization, and not performed by me):** `scripts/promotion_funnel.py` is
a reporting/shadow path, not the immutable kernel (`core/promotion_gate.py` is). Its `_auc` should
either read the probe-table score or emit `computable: false, informational: true` the way `pbo`
does, so a non-measurement can never again be recorded as a measurement. Any such change is
shadow-integrator scope with tests, and does not touch a frozen threshold.

## SURVIVED REFUTATION

- **Every headline number.** I attacked the dossier by re-deriving all seven gates, the exit split,
  PF, expectancy, payoff, breakeven WR, the t-statistic and CI, the side/symbol splits and the
  ex-ZEC figure from raw sources with my own query. All reproduce **exactly**. The data-pull script
  is honest — resolved-only, single-arm, read-only, no selection.
- **"NO-PROMOTE" is over-determined.** I tried to find a reading in which the lane survives. There
  is none: net, expectancy, PF and DSR are all computed on realized after-cost money, and WR 64.7%
  sits 9.1pp below the bracket's 73.8% breakeven. Even granting the CI's optimistic edge (zero
  expectancy), net>0 / PF>1 / DSR≥0.10 still fail. No accessible sample size rescues it.
- **"The loss is not merely cost."** I attacked this by hunting the omitted slippage (the C1-of-46
  failure mode) and found the mislabel — but the claim survived: at exactly zero fees and zero
  slippage the arm is **−31.58 USDT**, and the WR-vs-breakeven gap is cost-free arithmetic.
- **"Not symbol- or side-driven."** Verified: both sides negative, ex-ZEC still −42.12 over 98.
  Not a repairable artifact.
- **"The registered question is answered."** The tracker was kept *specifically* to measure the
  band-vs-profit tension forward. Bundle test predicted WR 67–68% with negative net; forward
  delivered WR 64.7% with −79.46 over 102 (3.4× the floor). Prediction confirmed; no registered
  decision question remains, so continuation would be accrual without a hypothesis. RETIRE is right.
- **Option (b) rejection holds.** A per-arm flag buys nothing: marginal compute is ~0 (shared ticks
  with zfade), and adding shadow-lane code + tests to save nothing is negative-value churn.
- **Codex flag #3 (cross-symbol correlation).** Correctly raised and correctly scoped: 101/102
  events post-date the 5→43 widening and are plausibly correlated, so effective n < 102 and the CIs
  are optimistic. This **widens** intervals — it weakens any continuation case and cannot soften the
  arithmetic gate. Retained as caveat, not defect.

## KILLED / DEMOTED

- **"Frictionless (gross) P&L = −55.07"** — killed as stated. Correct frictionless is **−31.58**
  (C1). Survived all three prior reviews. Conclusion unaffected.
- **"AUC 0.50"** — demoted from measurement to **fail-closed placeholder**; the measured value is
  **0.4815** (C2). Survived all three prior reviews.
- **Funding 0.084 counted as a cost** — corrected to a credit; immaterial (0.17 USDT).
- **"RETIRED" read as "stopped"** — demoted: the verdict closes, the logging does not (C4).
- **"The band is real"** — already correctly demoted by Codex flag #1 and accepted into the
  reconciliation as "observed WR in band, 95% CI 0.546–0.739." I confirm the CI independently and
  endorse the demotion: this is an observed in-band result, not an established true band.
- **"High WR is manufactured by the bracket"** — already correctly softened per Codex flag #2. The
  0.8/2.0 geometry demonstrably sets breakeven at 73.8%; it does not prove the causal source of
  every win. Endorsed as reconciled.

## UNVERIFIED

- **`zfade_4h_cfg365`'s true AUC** — deliberately not computed or published here (C5), to preserve
  the independent-verdict protocol for its own adjudication. This is a chosen deferral, not a gap.
- **DSR 0.0403** is the funnel's single-stream zero-skill proxy (`_dsr`, sr_var=1/n convention),
  self-labelled as such. Treated as a failed gate, not a calibrated selection-aware DSR. Correct
  handling by both models. I re-derived the inputs (mean/sd/n) but did not re-litigate the proxy.
- **Effective sample size** after cross-symbol clustering is not computed anywhere. Per Codex flag #3
  this only widens intervals; it cannot flip the verdict.
- **The bundle test's original ~432-config sweep** (owner's cloud test, 2026-07-19) is external
  provenance I did not re-audit. It is not load-bearing: cfg226 was registered as a *tracker* with a
  NO-PROMOTE expectation, no selection was applied forward, and the forward result is a failure —
  a burned-holdout concern could only make the case worse, never better.
- **Whether the 17 PENDING proposals resolve within the bounded 12-bar horizon** — not checked; C4
  makes it irrelevant to the closed verdict.

## CONFIDENCE: 91 / 100

Basis: every gate value, the full exit-path split, and all eight derived statistics were reproduced
independently from `promotion_funnel.json` and `warehouse.sqlite` with exact agreement using my own
query rather than the dossier's script; the log-only property was confirmed by reading the probe
module (no order path) and its registration site; the retirement mechanism was confirmed at
`config.py:520` and `bot_engine.py:742-758`; the gross/net/slippage semantics were confirmed at
`core/shadow_resolver.py:191-211`; the AUC placeholder was confirmed by reading
`scripts/promotion_funnel.py:365-400` and counting NULL `p_win` across all five probe agents; both
source verdicts were read in full and genuinely match. Deductions: the frictionless mislabel (−4)
and the AUC placeholder (−3) each survived the dossier, two independent verdicts and the
reconciliation — the same "three reviews miss the friction arithmetic" pattern as review 46;
zfade's AUC deliberately not re-derived (−1); effective-n uncomputed (−1).

## IF ESCALATE — HUMAN ACTION REQUIRED

**Not applicable — no money-scope element.** This is a log-only shadow lane with no order path;
the decision closes a measurement and moves strictly toward less activity; nothing touches
`docs/CONTROLLED_LIVE_CHECKLIST.md`, `CONTROLLED_LIVE`, `live_trading`, withdrawals, or any frozen
threshold. The only owner action is operational and **deferred**: after `zfade_4h_cfg365` is
adjudicated, ADD `SHADOW_BUNDLE_MR_PROBE_ENABLED=false` to `.env` (currently absent; config default
`true`) and let it take effect at the next attended restart.
