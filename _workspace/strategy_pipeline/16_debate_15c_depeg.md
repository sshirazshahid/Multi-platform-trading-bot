# 16 — Multi-Model Debate Record: 15c Stablecoin Depeg Micro-Reversion

Strategy-evidence-pipeline run 2026-07-16; debate reconciled 2026-07-17 (rev2).
Screen on trial: `research/screen_stablecoin_depeg.py` -> `15c_screen_depeg.{md,json}`.
Screener verdict on trial: **NO_GO**.
Reconciler: Fable 5 (honesty-auditor role). All adjudications re-grounded on the
primary artifacts; every load-bearing number re-run read-only with
`venv/Scripts/python.exe` reusing the screen's own audited helpers
(`extract_events`, `apply_costs`, `_dsr_prob`, `_oos_wr_walk_forward`,
`_monte_carlo` seed=7 deterministic, `rank_auc`).

**Rev2 note:** a prior reconciliation pass received only TWO of the three
announced auditor reports (Sonnet 4.6, Opus 4.8). This pass received all THREE
(Sonnet 4.6, Opus 4.8, Fable 5) and supersedes the prior record. The prior
record's decisive AUC-gate recomputation was NOT trusted on faith — it was
re-run from the parquets and reproduced.

---

## Reconciler's independent recomputation (basis for all rulings)

Binding model reproduces the verdict JSON exactly (theta=10: mean −8.29 bps,
WR 0.067, OOS 0.067, DSR ~0, AUC 0.986, MC P>0 0.000, maxDD p95 0.072, exit mix
30/0/45; theta=20: mean −28.96 bps, worst −827.3 bps, maxDD p95 0.257, 6/1/25).
Full frozen gate battery, theta=10 (n=75), alternative cost models (fee 0):

| cost model | mean | WR | OOS-WR | DSR | AUC | MC P>0 | MC maxDD p95 | gate fails |
|---|---|---|---|---|---|---|---|---|
| binding 5 bps/side | −8.29 bps | 0.067 | 0.067 | ~0 | 0.986 | 0.000 | 0.072 | mean, WR, OOS, DSR, MC-P |
| flat 1.0 bp/side | −0.29 bps | 0.573 | 0.633 | 0.116 | **0.554** | 0.381 | 0.015 | mean, **AUC**, MC-P |
| flat 0.1 bp/side | +1.51 bps | 0.613 | 0.667 | 0.803 | **0.548** | 0.958 | 0.008 | **AUC** |
| per-pair 0.1/1.0 bp (measured) | +0.16 bps | 0.573 | 0.633 | 0.255 | **0.554** | 0.574 | 0.013 | **AUC**, MC-P |
| ZERO cost (gross) | +1.71 bps | 0.613 | 0.667 | 0.857 | **0.548** | 0.974 | 0.007 | **AUC** |

theta=20 is mean-negative at EVERY model including zero cost (−18.84 bps gross;
the single Apr-2025 −827 bps FDUSD gap event dominates). theta=30 n=11 < 30, not
evaluable. Auditor-3 96h-timeout counterfactual reproduced: flat 1 bp/side →
mean +0.94 bps, WR 0.640, but MC P>0 0.885 and AUC 0.544 fail (binding costs:
−7.06 bps). FDUSD chronic-regime facts reproduced: theta=10 triggers in
**19/24 months**; last-30d 83.75% of closes < 0.9990, median 0.9982, last close
0.9984 — still below the 0.9998 re-arm at sample end.

**Decisive re-grounded fact:** under the FULL frozen battery — which includes
the pre-registered AUC >= 0.60 integrability gate (.md line 104, script
MIN_AUC) — **no cost model clears theta=10, not even zero cost.** The mean and
AUC gates move in opposite directions with cost: at costs low enough for
mean > 0, the frozen score stops discriminating winners (AUC ~0.55); at costs
high enough for the score to discriminate, the mean is deeply negative. The
gross edge is ~1.7 bps per ~48h full-notional hold — too small to pay any
realistic friction and too undifferentiated to score.

Process facts verified: all three artifacts are git-untracked (`??`); mtimes
script 23:47, parquets 23:49–23:50, JSON 23:55, .md 23:56 (2026-07-16 +0500).
Repo-wide grep for `tradeFee|fetch_trading_fee`: prose/comments only — no pull
code, no raw API response artifact.

---

## Per-model attack summaries (attributed)

**Sonnet 4.6** (verdict_stands=true, high): recomputed every headline event
statistic — exact match; verified data integrity and the Apr-2025 tail. MAJORs:
(1) "VERIFIED zero-fee" claim has no durable artifact; (2) theta=10 conflates
FDUSD's chronic sub-peg band with genuine depeg events (19/24-month trigger
calendar). Minors: 48h-timeout/re-arm adjudicated fair; theta=20 independent
episodes 29 < 30 floor noted; cumsum-vs-cumprod MC drawdown convention noted.

**Opus 4.8** (verdict_stands=true, high): re-ran gate helpers under alternative
slippage. Claimed the screener's "kill is mechanism-level not cost / no cost
model clears the gates" is FALSIFIED — theta=10 "flips to a 6/6 GO" at flat
0.1 bp, positive-mean 5/6 at per-pair measured spread failing only MC P>0;
demanded re-grounding as a fragile capital-preservation NO_GO and narrow ledger
scoping (theta>=30 not refuted, USDC-only insufficient, transient-event
definitions untested). Minors: fee claim prose-only; raw-n vs
independent-episode-n statistical power.

**Fable 5 (auditor)** (verdict_stands=true, high): full recompute bit-for-bit;
independently re-pulled `sapi/v1/asset/tradeFee` 2026-07-17 → 0.0/0.0 both
pairs (attested; corroborates the promo, does not cure the artifact gap).
MAJORs: (1) "mechanism-level not cost" overstated for theta=10 — 96h/1bp
counterfactual is sign-positive (+0.9 bps, WR 0.640) though sub-gate; ledger
row must exclude maker-first/low-slip and longer-timeout expressions and
theta=30; (2) pre-registration freeze is not tamper-evident (untracked
artifacts, self-attested timing). Minors: PBO weekly CSCV is degenerate
(zero-filled episodic rows — cosmetic, not robustness evidence); the two
self-flagged pressure points (48h timeout, 0.9998 re-arm) adjudicated FAIR via
counterfactuals (immediate re-arm bleeds far worse: WR 0.087–0.136).

---

## Adjudications (all FATAL/MAJOR findings; no FATALs were filed)

### A1-M1 (Sonnet, MAJOR, too_lenient) — "VERIFIED zero-fee" has no supporting artifact
**VALID.** Grep confirms prose-only: the script's hardcoded
`FEE_SIDE_BINDING = 0.0` comment (line 67), the JSON `fee_truth` block, the
.md, and the scout doc. No pull code, no logged raw response. Auditor 3's
independent re-pull (0.0/0.0, 2026-07-17) corroborates the promo's reality but
is itself attested-only. Non-overturning: zero fee is the most generous
assumption; any real fee strengthens NO_GO. The "VERIFIED / CONFIRMED REAL"
language overstates the evidence trail; the claim must not be reused by any
future marginal variant without a persisted artifact. (Opus A2-m3 and Fable
A3-m4 concur; folded in.)

### A1-M2 (Sonnet, MAJOR, unfair_to_strategy) — theta=10 conflates chronic regime with events
**VALID (construct validity), non-overturning — and it cuts TOWARD the NO_GO.**
Reproduced exactly: FDUSD theta=10 triggers in 19/24 months; FDUSD sits ~0.998
at sample end, still disarmed; last-30d median 0.9982. The pre-registered
absolute-threshold trigger samples a persistent discount regime rather than
discrete redemption shocks. See the persistent-discount ruling below for why
this does not create a rescreen obligation. Sonnet's own caveat is confirmed:
theta=20/30 (more plausibly genuine-shock thresholds) fail independently, so
excluding theta=10 would not change the outcome.

### A2-M1 (Opus, MAJOR, unfair_to_strategy) — "no cost model clears the gates" falsified; theta=10 flips to 6/6 GO at 0.1 bp
**PARTIALLY-VALID — every number reproduces, the headline is wrong.**
Reconciler reproduced Opus's metrics to the digit (see table). But the
"6/6 PASS = a GO" count **omits the pre-registered AUC >= 0.60 gate**, which
FAILS at 0.548–0.554 in every one of Opus's generous cost models — and at zero
cost. Under the full frozen battery there is NO cost model under which theta=10
clears; the screener's disputed sentence survives literally. The flip-to-GO
also leaned on flat 0.1 bp for BOTH pairs, mispricing FDUSD (measured 1.0 bp
book; 56/75 of events); the honest per-pair model still fails MC P>0 (0.574)
plus AUC. What survives of the finding and is accepted into the record:
(a) theta=10's mean/WR/DSR/MC gate failures ARE artifacts of the conservative
5 bps/side convention — the honest characterization is a near-zero gross edge
(~1.7 bps per 48h full-notional hold), and this fragility must be recorded;
(b) the .md's "pennies in front of the tail steamroller" narrative is wrong at
theta=10 specifically (zero stops there; maxDD p95 0.008–0.015 at generous
costs — the tail lives at theta=20); (c) "mechanism-level" is defensible but
imprecise — the precise kill is: reversion too slow (45/75 full-48h timeouts),
gross capture below any realistic friction, and unscoreable wherever mean > 0.

### A2-M2 (Opus, MAJOR, validity_other) — re-ground the verdict; scope the ledger row
**VALID on scoping; PARTIALLY-VALID on re-grounding.** Verified against the
JSON: theta=30 n=11 < 30 (WR 0.636, OOS 0.625, median_net +2.03 bps) — not
evaluable, therefore NOT refuted; USDC-only 19/5/1 events — INSUFFICIENT_DATA,
not refutation; FDUSD last close 0.9984 confirmed. The .md's proposed ledger
row ("thresholds 10-30 bps") overreaches and is corrected below. The
re-grounding demand is accepted in substance, but Opus's own re-grounded reason
("fails ONLY the MC capital-preservation floor" at honest costs) is incomplete:
theta=10 also fails the AUC integrability gate at every favorable cost model,
which independently blocks GO and probe integration.

### A3-M1 (Fable auditor, MAJOR, unfair_to_strategy) — "mechanism-level not cost" overstated; ledger must be scoped narrowly
**VALID.** The 96h-timeout counterfactual reproduces exactly (flat 1 bp/side:
mean +0.94 bps, WR 0.640 — sign-positive but sub-gate: MC P>0 0.885 < 0.95,
AUC 0.544 < 0.60; at binding costs −7.06 bps). The auditor's own framing is
correct and careful: the shallow-threshold kill under near-true costs is "no
economically meaningful edge", not mechanism death — and it claimed no
flip-to-GO. theta=30 formally not evaluable; maker-first/low-slippage and
longer-timeout expressions are untested by this screen and not refuted by it.
Note: the 96h variant is a post-hoc exploration by the auditor, outside the
frozen trial count — it informs scoping only and cannot ground any GO.

### A3-M2 (Fable auditor, MAJOR, too_lenient) — pre-registration freeze is not tamper-evident
**VALID.** Verified: `git status --porcelain` shows `??` for the script, .md,
and JSON; mtimes script 23:47, parquets 23:49–23:50, JSON 23:55, .md 23:56
(2026-07-16 +0500). The claimed freeze (~18:45 UTC = 23:45 local) precedes the
script mtime and is internally consistent — but freeze-before-run is
self-attested only, because results were appended into the same .md file.
Non-overturning here (gaming a prereg serves GO, not NO_GO), but binding
process fix: commit or hash the frozen prereg BEFORE any outcome computation so
future GO verdicts are verifiable.

---

## Ruling: persistent discount vs. event (the tasked question)

The screen conflated a chronic FDUSD sub-peg regime with acute depeg events —
all three auditors agree, and it reproduces (19/24 trigger months; disarmed at
sample end; last-30d median 0.9982). Ruling: **this is the honest finding, not
a false-NO_GO.** The pre-registration froze the absolute-threshold expression;
the screen tested exactly that expression and it loses. The conflation does not
bias the verdict against the tested strategy — it IS the tested strategy's
failure mode: at these thresholds "the discount" is mostly a regime that does
not re-peg within 48h, so the pre-registered mechanism (immediacy premium at
restoration) never gets paid. Excluding the regime post-hoc would be
respecification, not correction (per-auditor counterfactuals confirm: immediate
re-arm bleeds far worse; longer timeouts stay sub-gate). What the conflation
DOES limit is the ledger row's blast radius: a transient-event trigger
definition (regime-relative deviation, deviation velocity) was never tested and
is not foreclosed. No rescreen is REQUIRED — the gross-capture ceiling
(~1.7 bps per 48h hold at theta=10 even at ZERO cost) gives a weak prior for
any low-threshold re-spec; a deep-threshold (>=30 bps) acute-event re-spec
remains INSUFFICIENT_DATA and would need forward accrual or other-venue/older
history, as a NEW pre-registration with its own trial count (disclosing the
auditors' exploratory counterfactuals in that count's history).

---

## Material dissents (verbatim)

Opus 4.8, retained as the minority position on the screener's prose:
> "The screener's central justification for the theta=10 NO_GO — 'Not a fee
> artifact and not a slippage artifact... There is no cost model under which
> the pre-registered strategy clears the gates' and 'kill is mechanism-level
> not cost' — is falsified. The theta=10 kill is driven ENTIRELY by the
> pre-registered 5bps/side slippage, which the prereg itself concedes is
> '5-50x conservative' for these books. At the prereg's own measured
> top-of-book, the verdict flips."

Reconciler's disposition: the falsification claim does not survive the full
frozen gate battery (AUC 0.548 < 0.60 at that cost model and even at zero
cost; the "flip" is a 6-of-8-gate count). The fragility evidence inside it is
accepted and recorded in the re-grounded failure statement.

Fable 5 (auditor), concurring-in-part, retained:
> "the shallow-threshold kill under near-true costs is 'no economically
> meaningful edge', not mechanism death."

Accepted as the precise wording for theta=10; "mechanism death" remains
accurate for theta=20 (mean-negative at zero cost, tail-dead).

---

## FINAL STATUS: **CONFIRMED_NO_GO**

All three auditors independently concluded verdict_stands=true; the
reconciler's recomputation confirms the NO_GO holds under the frozen battery in
EVERY cost model examined, including the auditors' most generous ones and zero
cost. Zero FATAL findings were filed; zero VALID findings overturn. No deadlock
— no escalation needed (had there been one, the default is NO_GO).

Adjudication tally (6 MAJORs): VALID 4 (A1-M1, A1-M2, A3-M1, A3-M2),
PARTIALLY-VALID 2 (A2-M1, A2-M2), INVALID 0.

Re-grounded failure statement (supersedes .md §"Why it fails" pt 3 narrative):
theta=10 — gross reversion capture ~1.7 bps per ~48h full-notional hold; deeply
negative after the conservative binding costs; a ~zero-mean coin flip after
honest measured costs (per-pair mean +0.16 bps, MC P>0 0.574); and at every
cost level where the mean is positive the frozen score cannot discriminate
outcomes (AUC ~0.55 < 0.60), so it is unpromotable and un-probe-integrable;
catastrophic capital efficiency (45/75 events hold the full 48h); entirely
contingent on a revocable zero-fee promo whose in-screen verification is
prose-only. theta=20 — mean-negative even at zero cost (−18.8 bps), tail-dead
(Apr-2025 −8.27% gap-through stop; MC maxDD p95 0.257 > 0.25 at binding costs).
theta=30 — NOT evaluable (n=11 < 30).

**Ledger row (corrected scope, for refuted-families-ledger):**
Stablecoin-peg mean-reversion, long-discount ABSOLUTE-threshold expression,
USDC/USDT + FDUSD/USDT Binance spot, theta 10–20 bps (evaluable), 48h timeout,
taker-convention costs, zero-fee era -> NO_GO (theta=10: cost-floor-fragile
near-zero edge + AUC integrability fail; theta=20: negative mean at all costs
+ binding tail). Explicitly NOT covered: theta >= 30 bps (n=11,
INSUFFICIENT_DATA), USDC-only expression (INSUFFICIENT_DATA),
transient/regime-relative event definitions (untested), maker-first/low-slip
execution expressions (untested), longer-timeout expressions (96h explored
post-hoc: sign-positive, sub-gate — untested as a registered variant),
non-Binance venues.

**Owner follow-ups:**
1. Record the ledger row exactly as scoped above (NOT the .md's "10-30 bps"
   wording).
2. Process rule (binding, all future screens): persist the raw API response of
   any live account pull the binding cost model depends on; "VERIFIED" without
   an artifact is not verification (all three auditors concur).
3. Process rule (binding): commit or hash the frozen pre-registration before
   any outcome computation — freeze-before-run must be tamper-evident, not
   self-attested (Fable auditor A3-M2).
4. Optional, weak prior, NEW pre-registration only: deep-threshold (>=30 bps)
   acute-event depeg screen once forward accrual / other venues lift n >= 30;
   and/or a regime-relative transient-event definition. Neither is queued as
   REQUIRED.
