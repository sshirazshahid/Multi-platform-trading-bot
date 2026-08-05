# 48 — Fable verdict: `rsi2_4h_cfg226` (independent; written before reading any Codex output)

## Q1 — PROMOTE-WORTHY?

**NO.** The frozen gate fails 5 of 7 substantive gates; only n and WR pass. There is no favorable surprise: forward measurement reproduced the bundle test's OOS prediction almost exactly (predicted "WR 67–68%, net negative"; delivered WR 64.7%, net −79.46 over 102). The adverse findings are structural, not incidental:

- Frictionless P&L is −55.07 — the arm loses money at zero cost. Friction (48.05, ~60% of the net loss) makes it worse but is not the cause.
- Breakeven WR at the frozen 0.8/2.0 ATR bracket is 73.8%; the arm's true-WR 95% CI tops out at 73.9%. Even the most optimistic read of the win rate barely touches breakeven — and that read is excluded by the realized net.
- Both sides negative, not symbol-driven (ex-ZEC still −42.12). Nothing here is a repairable artifact.

This is the geometry≠edge caveat confirmed forward at n=102: the 63–67% band is manufactured by bracket asymmetry and carries no after-cost edge.

## Q2 — RETIRE vs CONTINUE

**RETIRE, via option (a) ADJUDICATION-CLOSE.**

- The tracker's registered question — does the band-vs-profit tension persist forward, live? — is answered YES at 102 resolved, 3.4× the floor. No registered decision question remains; continuation (option c) would be accrual without a hypothesis.
- Option (b) (per-arm flag code change) buys nothing over (a): marginal compute is ~0 because the arm shares ticks with the still-pending zfade arm, and adding shadow-lane code + tests to save nothing is negative-value churn. When `zfade_4h_cfg365` is adjudicated, the single shared `SHADOW_BUNDLE_MR_PROBE_ENABLED=false` retires the module at the next owner-attended restart (no unattended bounce; standing rule).
- Honesty caveat, stated not hidden: the expectancy CI [−1.653, +0.095] does not exclude zero at 95%. This does not argue for continuation: the boundary scenario is *zero* expectancy, which still fails net>0, PF>1, and DSR gates — there is no accessible sample size at which this arm passes the frozen gate while the point estimates stand where they are. Adjudication-close means later resolutions (which will keep logging until the zfade retirement restart) do NOT reopen the verdict.

## Q3 — Ledger action

Close the cfg226 tracker row with:

> ADJUDICATED NO-PROMOTE / RETIRED (adjudication-close 2026-07-31) after failed frozen S1 gate; 102 resolved forward paper outcomes 2026-07-19 → 2026-07-30: WR 0.6471 (in/near the 63–67 band — the band is real), net −79.46 USDT, expectancy −0.779/trade (95% CI [−1.65, +0.10]), PF 0.652, AUC 0.50, DSR proxy 0.04. Payoff ratio 0.356 → breakeven WR 73.8% vs actual 64.7%. Frictionless P&L −55.07 (loss is signal, not just cost). Bundle-test band-vs-profit prediction CONFIRMED forward. Owner-directed log-only tracker; RSI mean-reversion family remains REFUTED; reopen bar unmet. Physical de-registration deferred to the zfade_4h_cfg365 adjudication (shared enable flag); later resolutions do not reopen this verdict.

## Flags for the rebuttal round

- If Codex argues CONTINUE on the CI-not-excluding-zero point, the response is above: zero expectancy still fails the gate; no continuation target exists.
- If Codex proposes the per-arm flag (b), I would want a concrete benefit stated — I see none while zfade shares the ticks.
