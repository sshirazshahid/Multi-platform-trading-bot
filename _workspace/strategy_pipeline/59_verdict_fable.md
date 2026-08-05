# 59 — Fable independent verdict: `tsmom_20d_1h` (2026-08-03)

**Status: INDEPENDENT LEG, SEALED.** Written 2026-08-03 ~04:20Z from `59_s1_dossier_tsmom_20d_1h.md` only. Codex leg NOT run — usage limit exhausted at probe time (resets 2026-08-08 09:31 local); per protocol §Codex-mechanics the adjudication is PARKED and this verdict is not actionable single-model. Do not include this file in the Codex prompt when the leg runs.

## Q1 — PROMOTE-WORTHY: **NO**

Frozen gate fails 6/7 (oos_wr 0.2857, AUC 0.4984, net −51.19, expectancy −0.9142, PF 0.5742, DSR 0.0246 — every substantive gate). AUC is C5-computed from `shadow_tsmom_probe.score` with 56/56 coverage and matches the funnel snapshot. The gate is arithmetic and fail-closed; nothing to argue.

## Q2 — Disposition: **RETIRE via adjudication-close (option a)**

Reasons, in order of weight:

1. **The measurement question is answered, with better statistics than any prior retirement.** Expectancy 95% CI [−1.8251, −0.0033] excludes zero at n=56 — rsi2 was retired (48) on a bleed whose direction was clear but whose CI was wider. Keeping a lane open to "see if it resolves" is only justified when the CI straddles zero (the zfade case, 58); here it has resolved, negative.
2. **The loss is intrinsic, not frictional.** Frictionless P&L is −21.34 before fees/slippage; no cost-reduction path can rescue a signal that loses gross. This also forecloses the "evidence surprises → new prereg design" escape that kept zfade open: there is no positive after-cost residual to re-register.
3. **The bleed is unconditional within the sample:** both sides negative (buys −36.79, sells −14.40), all 3 symbols negative, both calendar months negative, and all 40 stop-exits are losses. There is no conditioning slice whose persistence is worth paying accrual attention for.
4. **Family confirmation, third strike:** textbook trend 0/40 OOS (2026-06-13), TSMOM redesign NO_GO on profit (2026-06-15), and now a 56-outcome forward shadow bleeding through the same mechanism (1h stops chopped by noise — 40 stops at avg R −1.19 vs 16 TPs at +1.70, breakeven WR 41% vs realized 29%). The forward probe has done its honest job: it confirmed the refutation with live-market evidence.

**Scope limits (binding):**
- Retirement is **lane-scoped bookkeeping**: the `tsmom_20d_1h` lane closes in the tracker/ledger; `TsmomProbeAgent` keeps running untouched because the `tsmom_20d_4h` lane (21/30) is still accruing toward its own adjudication (ETA ~9d). No probe-config change, no `.env` change, no restart now.
- Physical de-registration of the shared TSMOM probe is deferred to the **4h lane's** adjudication-close (mirrors the 48→58 shared-flag pattern for bundle-MR), as one attended-restart event.
- The 2 PENDING decisions at cutoff resolve normally and append to the closed record (C4 precedent); they do not reopen the lane.

## Q3 — Ledger

Proposed row text:

> **tsmom_20d_1h (TSMOM shadow, log-only)** — S1 adjudication #1 (2026-08-03): NO-PROMOTE, frozen gate failed 6/7 (WR 0.2857 vs 0.55; AUC 0.4984 C5-computed 56/56; net −51.19 after cost; expectancy −0.9142, 95% CI [−1.8251, −0.0033] excludes zero; PF 0.574; DSR proxy 0.025). Loss is intrinsic: gross −36.51 before fees; both sides, all 3 majors, both months negative; 40/40 stop-exits are losses (breakeven WR 41.1% vs realized 28.6%). Disposition: RETIRED via adjudication-close at n=56 (window 2026-07-12→08-02); TsmomProbeAgent continues solely for the separate tsmom_20d_4h lane; physical de-registration deferred to the 4h close. Trend/TSMOM family status UNCHANGED (refuted for live edge — this is the third independent confirmation; reopen bar unmet and now higher in practice).

## Honesty framing

Forward measurement of a refuted family; expectation was NO-PROMOTE from deployment, and the outcome is the expected one, now with resolvable statistics. The probe cost zero capital and closes the 1h-execution variant of TSMOM cleanly. Nothing in this verdict endorses or forecloses the 4h lane, which gets its own arithmetic when it crosses the floor.
