# 59 — S1 dossier: `tsmom_20d_1h` (2026-08-03)

> **STATUS: PENDING-CODEX (parked 2026-08-03).** Codex usage-limit probe FAILED at 04:11Z (limit resets 2026-08-08 09:31 local; re-probed today, same message as 58). Fable independent leg sealed in `59_verdict_fable.md`. Per protocol §Codex-mechanics, no agreement-gated action is valid until the Codex leg runs against THIS dossier (do not pass the Fable verdict into the Codex prompt). Accrual is ~2.9/day → resolved count will be ~+14–17 by the Codex reset; refresh numbers if it has moved >+15 by then.

**Lane:** `tsmom_20d_1h` (TsmomProbeAgent, `model_version = tsmom_20d_1h_v1`). The agent also serves the separate `tsmom_20d_4h` lane (21/30, still accruing, ETA ~9d) — dispositions here must not disturb that lane's accrual.
**Snapshot cutoff:** funnel `2026-08-03T03:40:08Z`; warehouse pull 2026-08-03 ~04:15Z (`59_tsmom_1h_breakdown.py`, read-only).
**Sample:** 56 RESOLVED (58 decisions, 2 PENDING at cutoff). Window 2026-07-12 00:35Z → 2026-08-02 22:35Z. Universe: majors-3 only (BTC 22, ETH 18, SOL 16) — this lane never widened; all outcomes are frozen-basket majors.

## Frozen-gate arithmetic (fail-closed; funnel snapshot cross-checked)

| Gate | Value | Threshold | Result |
|---|---|---|---|
| n_resolved | 56 | ≥30 | PASS |
| **oos_wr** | **0.2857** | **≥0.55** | **FAIL** |
| **auc** | **0.4984** | **≥0.6** | **FAIL** |
| **net_after_cost_pnl** | **−51.19** | **>0** | **FAIL** |
| **expectancy** | **−0.9142** | **>0** | **FAIL** |
| **profit_factor** | **0.5742** | **>1.0** | **FAIL** |
| **dsr** | **0.0246** | **≥0.1** | **FAIL** (single-stream zero-skill proxy) |
| pbo | n/a | ≤0.5 | informational, not computable (single stream) |

**Gate verdict: FAILED (6/7). Fail-closed; arithmetic is not negotiable per protocol rule 1.**

## C5 compliance (binding from `48_review_rsi2_4h_cfg226.md`)

AUC computed directly from `shadow_tsmom_probe.score` joined on `proposal_id`, funnel rank-sum formula: **AUC = 0.4984, score coverage 56/56 (0 missing)**. Equals the funnel snapshot value — a real measurement, not a placeholder. Direction: the frozen score is a coin flip — mean score of winners 0.4955 vs losers 0.4756; no ranking skill in either direction.

## Economics decomposition (C1 lesson applied: `gross_pnl` already embeds slippage)

- net = gross − fees + funding: **−51.19 = −36.51 − 13.43 + (−1.26)** (slippage 15.17 is inside gross).
- True frictionless P&L ≈ gross + slippage = **−21.34** over 56. Unlike zfade, the signal loses money BEFORE costs; costs roughly double the bleed but do not cause it.
- Expectancy −0.9142/outcome, sd 3.478, se 0.4648, **t = −1.967, 95% CI [−1.8251, −0.0033] — entirely below zero.** At n=56 the negative expectancy is statistically resolvable at ~95%, unlike zfade's undetermined CI.
- Payoff ratio 1.4355 (avg win +4.31 vs avg loss −3.01) → **breakeven WR 41.06%**. Realized 28.57%; WR CP95 CI [0.1730, 0.4221] — the upper bound only grazes breakeven (0.4221 vs 0.4106); the point estimate is 12.5pp below it.

## Structure

- **Exit mix fully separating:** all 16 wins are take_profit (+69.03, avg R +1.699); all 40 losses are stop_loss (−120.23, avg R −1.194, zero wins). Mean R −0.3674. No time exits in this lane's mix.
- **Both sides lose:** buy 38 outcomes, 11 wins, −36.79; sell 18 outcomes, 5 wins, −14.40. This is not a long-only-in-a-downtape artifact — shorts bleed too.
- **Every symbol loses:** BTC −27.32 (n=22), SOL −15.47 (n=16), ETH −8.40 (n=18). 0 of 3 net-positive.
- **Both months lose:** 2026-07 n=50, −43.17; 2026-08 n=6, −8.03. Persistent, not one bad week.
- **Family context:** TSMOM was fleet-snapshot-flagged "heading to legitimate NO-PROMOTE" at loop creation (protocol §fleet, WR 0.36 then; it has since worsened to 0.286). The family's own redesign (2026-06-15) was NO_GO on profit; independent OOS triangulation (2026-06-13) refuted textbook trend on majors 0/40.

## Adjudication questions (independent answers required from both models)

- **Q1 — PROMOTE-WORTHY?** Can only be NO under the frozen gate (6/7 FAIL). State it explicitly.
- **Q2 — Disposition:** (a) **RETIRE via adjudication-close** (rsi2 precedent `48_verdict_reconciled.md`: retired while bleeding net −73.91 with weaker statistical evidence than this), or (b) CONTINUE accruing log-only with defined triggers, or (c) other. Note: retirement is lane-scoped bookkeeping — TsmomProbeAgent must keep running for the `tsmom_20d_4h` lane until that lane's own adjudication; any physical de-registration of the shared agent is deferred to the 4h close.
- **Q3 — Ledger:** row update text (trend/TSMOM family status unchanged either way; this is forward measurement of a refuted family, not endorsement).

## Honesty framing (standing)

Log-only probe of a family already refuted for live edge; expectation was NO-PROMOTE from deployment. The forward measurement question — does 20d momentum with 1h execution carry after-cost edge on majors? — now has a statistically resolvable answer: no (negative before costs, CI excludes zero). Zero capital was at risk.
