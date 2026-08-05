# 46 — ai-reviewer review block: lane `pullback_ma20_4h` (probe #7, arm `pullback_ma20_rsi14_4h_v1`)

*2026-07-30. Reviewer: ai-reviewer (Opus 5). Inputs: `46_s1_dossier_pullback_ma20_4h.md`,
`46_verdict_fable.md`, `46_verdict_codex.md`, `46_verdict_reconciled.md`, plus independent
re-derivation from `data/promotion_funnel.json` and `data/warehouse.sqlite`.*

## VERDICT: **APPROVE** — with two binding conditions on the record (C1, C2)

## SCOPE

**Authorizes:**

- (a) **NO-PROMOTE confirmed** for `pullback_ma20_rsi14_4h_v1`, and the **decision to RETIRE**
  the lane (stop accrual) via `SHADOW_PULLBACK_PROBE_ENABLED=false`, effective at the next
  **owner-attended** restart.
- (b) **Ledger probe #7 row closure** with the agreed record wording **as amended by C1 + C2**.
- (c) **S4 journal record** of this adjudication.

**Does NOT authorize:** any live or paper ORDER flow; any change to `core/promotion_gate.py`,
`config.py`, or any frozen threshold; any new family-level refutation claim; any re-tune,
variant, or continuation of probe #7; any unattended bot restart; any reopening of the
pullback-momentum family (it stays REFUTED on the 2026-07-25 three-way falsification —
forward n=37 is corroborating, not primary).

**Reviewer execution limit:** `.env*` is on the ai-reviewer immutable-kernel hard-stop list.
The retirement **decision** is mine and is APPROVED; the `.env` **write** is the owner's/operator's
action. I authorize it; I do not perform it.

## EVIDENCE REVIEWED (independently re-derived, not accepted from the dossier)

`data/promotion_funnel.json` (`generated_utc` 2026-07-30T03:40:07.152528+00:00, `resolved_floor` 30),
lane `pullback_ma20_4h`, `state: GATE_BLOCKED`, `gate.passed false`, `gate.fail_closed true`:

| Gate | Funnel value | Threshold | ok | Dossier | Match |
|---|---|---|---|---|---|
| n_resolved | 37 | 30 | true | 37 | ✓ |
| oos_wr | 0.1081 | 0.55 | false | 0.1081 | ✓ |
| auc | 0.5 | 0.6 | false | 0.50 | ✓ |
| net_after_cost_pnl | −116.546931 | 0.0 | false | −116.55 | ✓ |
| expectancy | −3.149917 | 0.0 | false | −3.15 | ✓ |
| profit_factor | 0.149422 | 1.0 | false | 0.149 | ✓ |
| dsr | 0.0 | 0.1 | false | 0.0 | ✓ |
| pbo | null | 0.5 | true (**not computable — informational only**) | not shown | n/a |

6 of 7 substantive gates FAILED; only `n_resolved` passes. PBO is `computable: false` on a single
stream and is flagged informational — it is **not** a pass in any meaningful sense, and the
dossier's "6 of 7 substantive gates" phrasing is the honest reading.

`data/warehouse.sqlite`, `shadow_outcomes` ⋈ `shadow_pullback_probe` (my own query, n=37, all
`label_status='RESOLVED'`, single arm, 22 distinct symbols, `resolved_ts` 2026-07-23 → 2026-07-30):

| Exit path | n | wins | net USDT | gross | fees | avg R |
|---|---|---|---|---|---|---|
| time (42-bar) | 21 | 4 | −39.3276 | −34.3788 | 5.0194 | −0.2088 |
| stop_loss | 16 | 0 | −77.2193 | −73.3497 | 3.7960 | −1.1240 |
| RSI>70 profit exit | **0** | — | — | — | — | — |
| **total** | **37** | **4** | **−116.546931** | **−107.728536** | **8.815363** | **−0.604574** |

Arithmetic re-checked by me, not quoted:

- Profit factor: 20.4739 / 137.0209 = **0.1494** ✓
- Expectancy: −116.546931 / 37 = **−3.14992** ✓
- Clopper-Pearson one-sided 95% UB on 4/37 = **0.23054** ✓ (dossier 0.2305)
- Binomial p vs 0.55 (less) = **2.3989e-08** ✓ (dossier 2.4e-08)
- Codex's "next 37 must all win": (4+37)/74 = **0.5541** ≥ 0.55 ✓ — claim is correct
- Net identity: gross − fees + funding = −107.7285 − 8.8154 + (−0.0030) = **−116.5469** ✓

Code paths verified by reading, not assumption:

- `core/agents/pullback_momentum_probe_agent.py` — **zero** order-placement calls
  (`place_order`/`create_order`/`execute_trade`/`order_manager`: no matches). Genuinely log-only.
- `core/bot_engine.py:767-781` — probe registered from the `PULLBACK_MOMENTUM_PROBE` config dict
  at engine startup, so the flag flip requires a process restart. Retirement mechanic is sound.
- `core/shadow_resolver.py:191-211` — `gross` is computed on **slippage-adjusted fills**
  (`entry_filled`/`exit_filled`); `net = gross − fees + funding`. The `slippage` column is a
  diagnostic of friction **already priced into gross**. This is the basis of C1.

## CHECKS REQUESTED

1. **Both-agree satisfied — YES.** Fable and Codex independently return NO / RETIRE / close-row on
   Q1-Q3. Agreement matrix in `46_verdict_reconciled.md` is accurate against the two source
   verdicts as written. Codex's five wording flags were accepted rather than disputed; no evidence
   conflict existed to reconcile. (But see C1 — flag #3 was accepted too shallowly.)
2. **Frozen gate treated as arithmetic — YES.** No reviewer argued past a failed threshold, proposed
   a threshold change, re-tuned the frozen score, or invoked a "punitive cost model" defence. Both
   verdicts state the gate is arithmetic and unarguable. The frozen score (tanh((RSI14−55)/15))
   was not re-tuned post-outcome; AUC 0.50 is reported as the honest zero-information result.
3. **Retirement mechanics — CORRECT IN PRINCIPLE, DEFECTIVE AS WRITTEN.** Env flip now, effect at
   next owner-attended restart, explicit "no unattended bounce" — all three artifacts state this
   correctly and it matches the standing rule. **However:** `.env` contains **no**
   `SHADOW_PULLBACK_PROBE_ENABLED` line at all, and `config.py:536` defaults to `"true"`. See C2.
4. **Ledger wording — PASSES, subject to C1.** Codex's record wording preserves owner-directed
   log-only provenance, explicitly forbids describing this as live trading / cross-regime
   validation / a new family-level refutation, and states the family remains refuted with the
   reopen bar unmet. Correct: this retires a PROBE, it does not add a refutation.
5. **PAPER scope — CONFIRMED. No ESCALATE_TO_HUMAN element.** Log-only probe with no order path;
   retirement moves strictly toward *less* activity; nothing touches
   `docs/CONTROLLED_LIVE_CHECKLIST.md`, `CONTROLLED_LIVE`, `live_trading`, withdrawals, or the
   frozen gate. No capital is at risk in either direction.

## BINDING CONDITIONS

### C1 — Correct the friction denominator in the ledger row (blocks (b))

The dossier states *"Gross −107.73, fees 8.82 → cost share of loss ≈ 8%. The loss is signal, not
friction."* Codex flagged this and the reconciliation narrowed it to *"fees ≈8% of the net loss."*
**The narrowed wording is arithmetically true but still misleading**, because the record line
"Gross −107.73 USDT; fees 8.82 USDT" invites `gross − fees = net` and the inference that fees are
the entire friction. They are not: slippage of **8.911784 USDT** is embedded in `gross` via
slippage-adjusted fills (`shadow_resolver.py:195-198`).

Total modeled friction = fees 8.8154 + embedded slippage 8.9118 = **17.7271 USDT = 15.2% of the
116.55 net loss** — roughly double the stated figure. The repo carries a standing
"⚠ Cost-split CONTESTED — don't quote either split as fact" caution; a ~2× under-report of friction
must not enter the permanent record.

**The conclusion survives the correction and must be stated as surviving it:** zero-friction P&L
would be −98.82 USDT over 37 trades, and WR 0.1081 / AUC 0.50 / 0-of-37 profit-exit firings are
cost-independent. The loss is signal. The denominator was simply wrong.

Required row text: *modeled friction = fees 8.82 + embedded slippage 8.91 = 17.73 USDT (≈15% of the
net loss; slippage is priced into `gross_pnl` via slippage-adjusted fills). Frictionless P&L −98.82
USDT — still deeply negative, so the failure is signal, not cost.*

### C2 — State the retirement action as an ADD, and confirm it post-restart (blocks calling (a) complete)

`.env` currently has **no** `SHADOW_PULLBACK_PROBE_ENABLED` line, and `config.py:536` reads
`os.getenv("SHADOW_PULLBACK_PROBE_ENABLED", "true")`. All three artifacts phrase the action as
"set ... `=false` in `.env`", which reads as editing an existing line. An owner who greps, finds
nothing, and assumes it is already handled leaves the probe **enabled by default** — accrual
continues and the "RETIRED" ledger row becomes false-in-fact.

Required: the action is **ADD the line `SHADOW_PULLBACK_PROBE_ENABLED=false` to `.env`
(currently absent; config default is `true`)**. The row may be treated as closed only after a
post-restart confirmation that `PULLBACK_MOMENTUM_PROBE["enabled"]` evaluates **False**.

Neither Fable, Codex, nor the reconciliation caught this.

## SURVIVED REFUTATION

- **Every load-bearing number.** I attacked the dossier by recomputing all seven gates, the exit
  split, PF, expectancy, the Clopper-Pearson bound and the binomial p from raw sources. All match
  to the stated precision. No misquote found — unusual, and worth recording.
- **"The loss is signal, not friction."** I tried to kill this by finding the omitted slippage.
  The claim survived: even at exactly zero fees and zero slippage the lane is −98.82 USDT, and the
  WR/AUC/profit-exit failures do not involve cost at all. Only the *number* was wrong (C1).
- **"No regime-dependence argument survives."** Attacked via the exit split: losses arrive through
  **both** paths (21 time exits negative on average, 16/16 stop-outs), and the designed profit exit
  fired 0/37. A strategy whose profit path never fires is mis-specified, not regime-starved.
- **"More data cannot change the decision."** Verified numerically: WR would require 37 consecutive
  wins to reach 0.5541, and five other gates would still fail. The retirement does not depend on a
  judgement call about sufficiency.
- **Codex flag #2 (independence).** Correctly raised and correctly scoped: 37 outcomes across 22
  symbols in one week are plausibly correlated, so effective n < 37 and the CP bound is optimistic.
  This **widens** the interval — it cannot rescue a 10.8% WR against a 55% floor, and it does not
  touch the arithmetic gate. Retained as a caveat, not a defect.

## KILLED / DEMOTED

- **"Cost share of loss ≈ 8%"** — demoted to wrong-as-stated; corrected to ~15% modeled friction
  (C1). Conclusion unaffected.
- **"Sample already decisive" / "final"** — correctly demoted by Codex flag #5, and I confirm it
  **empirically**, not just theoretically: the warehouse holds **39** proposals vs the funnel's 38,
  with **2 unresolved**, one created 2026-07-30T04:03:24Z — *after* the 03:40:07Z snapshot. n=37 is
  an adjudication snapshot; the cutoff must be stated in the row.
- **"No convergence toward positive expectancy"** — correctly demoted to "expectancy remained
  materially negative" (−0.728R → −0.605R did move toward zero). Accepted as reconciled.

## UNVERIFIED

- **Arm-label mismatch (cosmetic, logged so it is not later misread as missing data).** The
  warehouse `arm` column stores `pullback_ma20_rsi14_4h`, while `model_version`, the dossier, the
  verdicts and the ledger all say `pullback_ma20_rsi14_4h_v1`. A future query filtering
  `arm='pullback_ma20_rsi14_4h_v1'` returns **zero rows**. No gate number is affected — my join
  returns exactly 37 rows.
- **AUC 0.50 provenance.** I confirmed the funnel emits 0.5 but did not re-derive it from the score
  distribution. With 4 wins in 37 the statistic is near-degenerate regardless; it is not
  load-bearing for a decision already failed on six other gates.
- **DSR 0.0** is a single-stream zero-skill proxy, self-labelled as such in the funnel. Treated as
  a failed gate, not as a calibrated selection-aware DSR. Correct handling by both models.
- **Effective sample size** after cross-symbol clustering is not computed anywhere. Per C1's sibling
  caveat this only widens intervals; it cannot flip the verdict.

## CONFIDENCE: 93 / 100

Basis: every gate value, the exit-path split, and all four derived statistics were reproduced
independently from `promotion_funnel.json` and `warehouse.sqlite` with exact agreement; the
log-only property was confirmed by reading the agent (no order path) and the registration site;
the retirement mechanism was confirmed at `config.py:536` and `bot_engine.py:767`. Both-agree is
genuine, not manufactured. Deductions: the friction denominator was wrong in the shared dossier and
survived both independent reviews and the reconciliation (−4); the `.env` line is absent and all
three artifacts mis-describe the action (−2); AUC and effective-n were not re-derived (−1).

## IF ESCALATE — HUMAN ACTION REQUIRED

Not applicable. No money-scope element. The only owner action is operational: add
`SHADOW_PULLBACK_PROBE_ENABLED=false` to `.env` and let it take effect at the next attended
restart.
