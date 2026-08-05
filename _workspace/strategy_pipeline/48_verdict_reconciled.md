# 48 — Reconciled verdict: `rsi2_4h_cfg226` (2026-07-31)

*Inputs: `48_s1_dossier_rsi2_4h_cfg226.md`, `48_verdict_fable.md`, `48_verdict_codex.md` (CODEX-OK; independent, neither saw the other's before submitting).*

## Agreement check — MATCHING on all three questions

| Question | Fable | Codex | Match |
|---|---|---|---|
| Q1 PROMOTE-WORTHY | NO | NO | ✓ |
| Q2 disposition | RETIRE, option (a) adjudication-close | RETIRE, option (a) adjudication-close | ✓ |
| Q3 ledger closure | close row, final, later resolutions don't reopen | same, endorsed row text supplied | ✓ |

No rebuttal-round substance dispute. Codex's three Flags are vocabulary/inference policing, all ACCEPTED into the final wording:

1. "The band is real" → replaced with "observed WR in band (95% CI 0.546–0.739)"; the CI supports an observed in-band result, not a precisely established true band.
2. "High WR is manufactured by the bracket" → replaced with "the 0.8/2.0 ATR bracket geometry sets breakeven WR at 73.8%, 9.1pp above realized"; causal attribution of every win is not claimed.
3. Cross-symbol correlation caveat added: 101/102 events post-date the 5→43 universe widening and events may be correlated across symbols, so the CI and side/symbol splits are not independence-grade — this weakens any continuation case and does not soften the arithmetic gate failure.

## Final joint verdict (both-agree, binding for PAPER scope pending ai-reviewer)

1. **NO-PROMOTE** — frozen gate failed 5/7 (AUC 0.50, net −79.46, expectancy −0.779, PF 0.652, DSR 0.0403); WR 0.6471 and n=102 pass but cannot override.
2. **RETIRE via ADJUDICATION-CLOSE (option a):** lane verdict is final now; later resolutions do not reopen it. NO per-arm flag is added. Physical de-registration happens with the shared `SHADOW_BUNDLE_MR_PROBE_ENABLED=false` flag once `zfade_4h_cfg365` receives its own adjudication, at the next owner-attended restart.
3. **Ledger:** close the cfg226 tracker row using Codex's endorsed row text (adopted verbatim, numbers verified against `48_rsi2_breakdown.py` output).

## Honesty framing (preserved)

Owner-directed log-only tracker; forward measurement of the registered band-vs-profit question, not an endorsement. The bundle test's OOS prediction (WR in/near band, net negative) is CONFIRMED forward. RSI mean-reversion family remains REFUTED; the reopen bar remains unmet. This is not live trading, not a regime validation, and not a new family-level refutation.

## Remaining S1 queue after this adjudication

`zfade_4h_cfg365` (56 resolved, passes 6/7 — AUC only fail; the genuinely interesting lane), `tsmom_20d_1h` (48 resolved, heading to clean NO-PROMOTE). One lane per iteration.

---

## ADDENDUM — ai-reviewer outcome (2026-07-31, post-reconciliation)

**APPROVE with 5 binding conditions** (`48_review_rsi2_4h_cfg226.md`; reviewer independently reproduced every headline number; money-scope NO, no ESCALATE_TO_HUMAN). Two record errors survived the dossier, both independent verdicts, and this reconciliation — corrections are binding on the ledger row and all future citations:

1. **C1 — "frictionless −55.07" was mislabeled.** `gross_pnl` in the resolver already embeds slippage (`core/shadow_resolver.py:191-211`); true zero-cost P&L = **−31.58 USDT** over 102. The "loss is signal, not just cost" conclusion SURVIVES (still negative at literal zero cost), but the −55.07 figure was wrong by 74% and must not be quoted as frictionless.
2. **C2 — "AUC 0.50" is a fail-closed placeholder, not a measurement.** `shadow_decisions.p_win` is NULL for all rsi2 rows (and for every probe agent); `promotion_funnel._auc` returns literal 0.5 on empty score lists. The frozen score IS persisted in `shadow_bundle_mr_probe.score` (102/102); computing the funnel's own AUC formula on it gives **true AUC = 0.4815** — fails harder; direction unaffected and strengthened.
3. **C3 — deferred de-registration is an ADD, not a SET:** `.env` has no `SHADOW_BUNDLE_MR_PROBE_ENABLED` line; `config.py:520` defaults enabled. The future owner action is ADD the line =false + attended restart.
4. **C4 — closure ends the verdict, not the logging:** 119 proposals / 102 resolved / 17 PENDING at review time; the arm keeps logging until the deferred shared-flag retirement. Snapshot cutoff **2026-07-31T03:40:07Z** is part of the row.
5. **C5 (forward-binding on the NEXT S1) — the `zfade_4h_cfg365` dossier MUST compute AUC from `shadow_bundle_mr_probe.score` and MUST NOT quote the funnel's `auc` field** (its sole stated gate blocker is currently the same not-computed placeholder). Reviewer also recommends (not authorized here): make `scripts/promotion_funnel.py::_auc` read the probe-table score or emit `computable: false` like `pbo`.
