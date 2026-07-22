# 19 — Dual-Model Strategy Loop Protocol (standing)

**Created:** 2026-07-23 (session ef3c3ceb) — owner-directed.
**Owner directives (verbatim):**
- "Start doing PAPER trading with the strategies you and Codex were able to develop or use what I have said" (2026-07-22)
- "I have Codex-Sol-5.6 and ClaudeCode ultrathink and setup in a way that both of you /deep-research ultrathink test simulate strategies. Challenge each others decision and when you both agree on the strategies then implement them setup a /loop for this" (2026-07-22)

This file is the binding procedure for every loop iteration. Re-read it at each wakeup before acting.

## Standing rules (inherited from CLAUDE.md harness — the loop NEVER overrides these)

1. **"Implement" means log-only.** Agreed strategies are implemented ONLY as log-only shadow probes (`core/agents/`) or pre-registered screens through `strategy-evidence-pipeline`. The loop makes ZERO live/paper-order decision-path changes. Promotion beyond log-only = frozen gate (`core/promotion_gate.py`, ≥30 RESOLVED per arm) + explicit owner sign-off, always.
2. **Ledger first.** Refuted families are answered from `refuted-families-ledger`; re-screening requires the reopen bar (quote the qualifying evidence verbatim in the debate file).
3. **Both-agree rule.** Any action (new probe, ledger row, promotion dossier, probe-retirement recommendation) requires MATCHING final verdicts from both models after the rebuttal round. Disagreement → parked + documented + surfaced to owner; no action.
4. **Prereg discipline.** Screen preregistrations committed/hashed BEFORE the run; raw artifacts persisted (pipeline process rules, 2026-07-17).
5. **Honesty framing.** Probes of refuted/unconfirmed families are forward measurement, not endorsement; expectation NO-PROMOTE unless evidence surprises. Fragility disclosed up-front in the ledger row so a future bleed is never re-litigated as a surprise.

## Iteration cycle (self-paced via ScheduleWakeup; idle cadence 1800–3600s)

- **S0 Health (every wakeup, cheap):** `data/heartbeat.json` fresh + `paper_trading_profile == MAX_FLOW_BAND` + `is_halted == false` + no `data/risk_incident_latch.json` + probe heartbeats ticking in today's log. Regression → diagnose per CLAUDE.md Gotchas (supervisor env inheritance, incident latch, boot banner = ground truth) and fix BEFORE any research work. Known trap fixed 2026-07-22: the `TradingBot-24x7` schtask carried `--paper-profile aggressive-research`, silently overriding `.env`; the flag is now removed — if the profile regresses, check the task definition first.
- **S1 Funnel triggers:** read `data/promotion_funnel.json`. Any lane with resolved ≥30 → compute frozen-gate metrics → dual-model adjudication → both-agree PROMOTE-WORTHY = owner dossier in `reports/promotion_dossiers/` (promotion still owner-signed); both-agree NO = retirement/continue note + ledger update; split = parked. One lane per iteration.
- **S2 Screen queue:** advance ONE stage of the queued prereg screens (C1 CFTC options-pressure first — hardened prereg per 18_final; C3 quarter-hour imbalance behind it) with independent dual verdicts at the verdict stage. Max one heavy stage per UTC day.
- **S3 Scout (≤1 per UTC day, ONLY if S1/S2 produced no work):** parallel scout — Fable web sweep + `codex exec` scout on the same brief (delta since last sweep only, max 2 candidates each). A candidate enters the prereg queue only if BOTH models rate it screen-worthy AND it passes the ledger reopen check. Most days produce nothing; that is correct behavior, not failure.
- **S4 Record:** one-line iteration record appended to `journal/YYYY-MM-DD.md`. Heavy artifacts numbered under `_workspace/strategy_pipeline/` (next index after 19). Then ScheduleWakeup for the next iteration (long idle interval; shorter only when S1/S2 work is genuinely in flight).

## Codex mechanics (from the proven 18_* run)

- Invoke: `codex exec` (codex-cli, model gpt-5.6-sol). Usage-limit probe first; proceed only on CODEX-OK.
- Every Codex prompt opens with: read `.claude/skills/refuted-families-ledger/SKILL.md` before opining.
- Outputs captured to `_workspace/strategy_pipeline/NN_<stage>_codex.md` (+ `.stderr.txt`).
- Debate shape: shared dossier → independent verdicts (neither model sees the other's before submitting) → one policed rebuttal round each (evidence-only; vocabulary policing per 18_debate) → Fable reconciles → final verdict file.
- Codex unavailable (usage limit/outage): agreement-gated actions are INVALID single-model — park the item, log the outage, retry next iteration. Fable-only work allowed: S0 health, funnel bookkeeping, dossier drafting marked PENDING-CODEX.

## Fleet snapshot at loop creation (2026-07-23 ~19:00Z)

| Lane | State | Resolved | WR | Note |
|---|---|---|---|---|
| rsi2_4h_cfg226 | ACCRUING | 24/30 | 0.67 | nearest to floor (~1.8d) — first S1 adjudication candidate |
| tsmom_20d_1h | ACCRUING | 25/30 | 0.36 | heading to legitimate NO-PROMOTE |
| zfade_4h_cfg365 | ACCRUING | 16/30 | 0.75 | ~6d |
| tsmom_20d_4h | ACCRUING | 9/30 | 0.22 | slow |
| breakout_60d | IDLE | 0/30 | — | no channel breaks yet (by design) |
| unlock_short | IDLE | 0/30 | — | calendar healthy, no qualifying events |
| listing_short | STARVED | 0/30 | — | no actionable shortable listings |
| f1_carry | IDLE | 0/30 | — | structurally idle (edges negative; correct refusal) |
| pullback_ma20_rsi14_4h_v1 | pending activation | 0/30 | — | owner's stated strategy, probe #7 (build in flight) |

## Loop prompt (pass this exact text back to ScheduleWakeup each iteration)

/loop iteration — dual-model strategy loop: read `_workspace/strategy_pipeline/19_dual_model_loop_protocol.md` and execute ONE iteration of its cycle (S0 health → S1 funnel → S2 screen queue → S3 scout if idle → S4 record + reschedule). Both-agree rule and log-only implementation rule are binding; promotion is owner-signed only.
