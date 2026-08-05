# 46 — Reconciled final verdict: lane `pullback_ma20_4h` (probe #7)

*2026-07-30. Inputs: `46_s1_dossier_pullback_ma20_4h.md`, `46_verdict_fable.md`, `46_verdict_codex.md`. Verdicts were independent; neither model saw the other's before submitting.*

## Agreement matrix

| Question | Fable | Codex | Match |
|---|---|---|---|
| Q1 PROMOTE-WORTHY | NO | NO | ✓ |
| Q2 RETIRE vs CONTINUE | RETIRE | RETIRE | ✓ |
| Q3 close ledger row | YES | YES | ✓ |

**BOTH-AGREE: NO-PROMOTE, RETIRE, close ledger row.** Rebuttal round consumed as flag-acceptance — no evidence dispute existed; Codex's five wording flags are all accepted:

1. "No convergence toward positive expectancy" → replaced with **"expectancy remained materially negative"** (−0.728R → −0.605R did move toward zero).
2. Clopper-Pearson/binomial figures carry an **independence caveat**: cross-symbol outcomes inside one week may be correlated; effective n < 37. Does not alter the arithmetic gate failure.
3. "The loss is signal, not friction" → narrowed to **"gross P&L was already negative; fees ≈8% of the net loss."**
4. "Sample already decisive" applies to the frozen promotion decision only — no claim of regime-universal performance.
5. n=37 is the **adjudication snapshot**, not "final", until accrual actually stops; closeout numbers to be refreshed at the owner-attended disable if more outcomes resolve.

## Decision (PAPER scope — subject to ai-reviewer APPROVE)

1. **NO-PROMOTE confirmed** for `pullback_ma20_rsi14_4h_v1`: frozen gate failed 6/7 (WR 0.1081 vs ≥0.55, AUC 0.50, net −116.55, expectancy −3.15, PF 0.149, DSR proxy 0.0); designed RSI>70 profit exit fired 0/37.
2. **Probe retirement:** set `SHADOW_PULLBACK_PROBE_ENABLED=false` in `.env` now; takes effect at the next owner-attended restart (no unattended bounce — standing rule). Until then the probe logs harmlessly; new resolutions do not reopen this verdict.
3. **Ledger:** close the probe #7 row with Codex's agreed record wording (adjudication snapshot through 2026-07-30T03:40Z). Owner-directed log-only provenance preserved; NOT described as live trading, cross-regime validation, or a new family-level refutation. Family remains REFUTED; reopen bar unchanged and unmet.
4. Any regime-specific or variant re-test requires a NEW hashed preregistration — never a continuation or re-tune of probe #7.

## Addendum — ai-reviewer APPROVE with binding conditions (2026-07-30, `46_review_pullback_ma20_4h.md`)

- **C1 (accepted):** friction denominator corrected — slippage 8.91 USDT is embedded in gross via slippage-adjusted fills, so modeled friction = fees 8.82 + slippage 8.91 = **17.73 USDT ≈ 15.2%** of the net loss (not ≈8%). Frictionless P&L is **−98.82 USDT**; WR 0.1081 / AUC 0.50 / 0-of-37 profit exits are cost-independent — the loss-is-signal conclusion survives with the corrected denominator.
- **C2 (accepted):** `.env` contains NO `SHADOW_PULLBACK_PROBE_ENABLED` line; `config.py:536` defaults to enabled. The action is to **ADD** `SHADOW_PULLBACK_PROBE_ENABLED=false`. The reviewer authorized the decision but reserved the `.env` write for the owner (immutable-kernel hard-stop). **OWNER ACTION:** add the line, then restart attended; the ledger row states retirement as APPROVED-PENDING until post-restart confirmation that `PULLBACK_MOMENTUM_PROBE["enabled"]` is False.
- Snapshot cutoff frozen at **2026-07-30T03:40:07Z** (37 resolved); warehouse already shows 39 proposals / 2 unresolved — later resolutions cannot reopen the verdict (WR repair alone would need the next 37 outcomes all wins; five other gates would still fail).
- Cosmetic: warehouse `arm` column stores `pullback_ma20_rsi14_4h` (no `_v1` suffix) — queries filtering on the `_v1` form return zero rows; no gate number affected.
