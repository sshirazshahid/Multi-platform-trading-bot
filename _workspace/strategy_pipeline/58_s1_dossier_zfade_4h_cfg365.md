# 58 — S1 dossier: `zfade_4h_cfg365` (2026-08-02)

> **STATUS: PENDING-CODEX (parked 2026-08-02).** Codex usage-limit probe FAILED at 04:15Z (limit resets 2026-08-08 09:31 local). Fable independent leg sealed in `58_verdict_fable.md`. Per protocol §Codex-mechanics, no agreement-gated action is valid until the Codex leg runs against THIS dossier (do not pass the Fable verdict into the Codex prompt). Numbers must be refreshed if resolved count has moved materially (>+15) by then.

**Lane:** `zfade_4h_cfg365` (ZfadeProbeAgent, shared bundle-MR probe with retired `rsi2_4h_cfg226`).
**Snapshot cutoff:** funnel `2026-08-02T03:40:39Z`; warehouse pull 2026-08-02 ~04:12Z (`55_zfade_breakdown.py`, read-only).
**Sample:** 70 RESOLVED (79 decisions, 9 PENDING at cutoff). Window 2026-07-20 08:35Z → 2026-08-01 21:35Z. All 70 outcomes post-date the 2026-07-20 universe widening (5 → 43 symbols).

## Frozen-gate arithmetic (fail-closed; funnel snapshot cross-checked)

| Gate | Value | Threshold | Result |
|---|---|---|---|
| n_resolved | 70 | ≥30 | PASS |
| oos_wr | 0.7571 | ≥0.55 | PASS |
| **auc** | **0.4018** | **≥0.6** | **FAIL** |
| net_after_cost_pnl | +52.00 | >0 | PASS |
| expectancy | +0.7429 | >0 | PASS |
| profit_factor | 1.519 | >1.0 | PASS |
| dsr | 0.9297 | ≥0.1 | PASS (single-stream zero-skill proxy) |
| pbo | n/a | ≤0.5 | informational, not computable (single stream) |

**Gate verdict: FAILED (1/7). Fail-closed; arithmetic is not negotiable per protocol rule 1.**

## C5 compliance (binding from `48_review_rsi2_4h_cfg226.md`)

AUC was computed directly from `shadow_bundle_mr_probe.score` joined on `proposal_id`, using the funnel's own rank-sum formula: **AUC = 0.4018, score coverage 70/70 (0 missing)**. It equals the funnel snapshot value — for this lane the funnel field is a real measurement, NOT the rsi2-era NULL-placeholder 0.5 (p_win repair PROBE_SCORE_TABLES, 2026-07-31). Direction: the frozen score is *anti-predictive* — mean score of winners 0.55 vs losers 0.5882.

## Economics decomposition (C1 lesson applied: `gross_pnl` already embeds slippage)

- net = gross − fees + funding: **+52.00 = 68.42 − 16.74 + 0.32** (slippage 14.84 is inside gross).
- True frictionless P&L ≈ gross + slippage = **+83.26** over 70. The signal makes money before costs and survives them.
- Expectancy +0.7429/outcome, sd 4.218, se 0.5041, **t = 1.474, 95% CI [−0.245, +1.731] — includes zero.** The +52 is not yet statistically distinguishable from luck at n=70.
- Payoff ratio 0.4872 (avg win +2.87 vs avg loss −5.89) → **breakeven WR 67.24%**. Realized 75.71% clears it by 8.5pp, but the WR 95% CI [0.6399, 0.8517] dips below breakeven at the low end.

## Structure

- **Exit mix is fully separating:** all 53 wins are take_profit; all 17 losses are stop_loss (9, −69.52, avg R −1.07) or time exits (8, −30.68, avg R −0.42). Mean R only +0.0797.
- **Side asymmetry:** sell 43 outcomes, 38 wins, +76.26; buy 27 outcomes, 15 wins, **−24.26**. The whole net edge is short-side fade in this window's tape.
- **Universe:** 38 symbols traded, 23 net-positive. Majors-5 basket: n=8, −6.53. Widened-38: n=62, +58.54. The frozen original basket LOSES; the profit lives in the widened alts.
- **Correlation caveat (carried from 48):** 70/70 outcomes post-widen; simultaneous cross-symbol fades are correlated, so the CIs and side/symbol splits are not independence-grade.

## Adjudication questions (independent answers required from both models)

- **Q1 — PROMOTE-WORTHY?** Can only be NO under the frozen gate (AUC fail). State it explicitly.
- **Q2 — Disposition:** (a) RETIRE via adjudication-close (as rsi2), or (b) **CONTINUE accruing** log-only with a defined re-adjudication trigger, or (c) other. Note: `48_verdict_reconciled.md` §2 ties physical de-registration of the retired rsi2 arm (shared `SHADOW_BUNDLE_MR_PROBE_ENABLED` flag) to THIS adjudication's outcome — a CONTINUE keeps both arms logging; a RETIRE frees the owner to add `SHADOW_BUNDLE_MR_PROBE_ENABLED=false` (ADD, not SET — line absent from `.env`, `config.py:520` defaults enabled) at next attended restart.
- **Q3 — Ledger:** row update text (mean-reversion family status unchanged either way; this is forward measurement, not endorsement).

## Honesty framing (standing)

Log-only probe of a family whose directional edge is not confirmed; expectation remains NO-PROMOTE unless evidence surprises. A positive 70-sample window with an anti-predictive score and buy-side bleed is *interesting*, not *confirmed*. Zero capital risk either way.
