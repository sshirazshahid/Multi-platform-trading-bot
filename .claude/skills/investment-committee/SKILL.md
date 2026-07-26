---
name: investment-committee
description: Orchestrates the AI investment committee (research brief → hashed prereg → bull/bear → debate → strategy spec packaging → handoff to strategy-evidence-pipeline → risk/paper summary → final verdict → human action). Use when the owner asks for an AI hedge fund workflow, investment committee, seb.ai-style multi-agent debate, or Claude Fable committee run. Does NOT place orders. Live stays OFF. For pure "find/screen/implement strategies" prefer strategy-evidence-pipeline; this skill packages bull/bear debate around a candidate then routes screens there. Refuted-family FAQs → refuted-families-ledger first.
---

# Investment Committee (Orchestrator Skill)

Educational research committee integrated into Trading_Bot. See `docs/AI_INVESTMENT_COMMITTEE.md`.

**Fable = design/session orchestrator only** (`docs/FABLE_OPERATING_DESIGN.md`). Not in the per-trade loop.

## Hard stops

- Never invent data, backtests, or funnel metrics.
- Never enable CONTROLLED_LIVE or withdrawals.
- Never bypass prereg hash / frozen gates.
- Never implement chart/indicator/pullback mass-sweeps that the ledger already STOP'd.
- FINAL VERDICT never submits orders — human action only.

## Trigger + cost rule (binding, added 2026-07-25)

The committee is token-expensive — the single real run (2026-07-25) produced ~15 KB bull + ~18 KB bear + debate + verdict, against ~$420 of capital. It therefore needs a firing rule, not just a workflow:

- Run **only** on a candidate that has already passed the ledger reopen check **and** either carries a committed/content-hashed prereg or is next in the prereg queue. Queued-but-unhashed candidates get their prereg written and hashed **before step 4 (bull/bear) runs** — never after debate.
- **Max one committee run per UTC week.**
- Skip bull/bear entirely when the ledger already refutes the family — answer from the ledger row instead (existing hard stop, restated here as a cost control).
- Record estimated spend in the verdict artifact.

## Agents

| Role | Definition |
|------|------------|
| Research | `.claude/agents/strategy-scout.md` + `prompts/committee/analyst_prompts.md` |
| Bull | `.claude/agents/bull-researcher.md` |
| Bear | `.claude/agents/bear-researcher.md` |
| Debate | `.claude/agents/debate-engine.md` |
| Screen/backtest | **hand off** to `strategy-evidence-pipeline` |
| Audit | `.claude/agents/honesty-auditor.md` (via pipeline) |
| Verdict | `.claude/agents/final-verdict.md` |

Model for Agent calls: `fable` (harness policy).

## Workflow

1. **Clarify** (if unknown): market (crypto perps default), timeframe, objective (screen vs paper soak vs education).
2. **Ledger check** — read `refuted-families-ledger`. REFUTED without reopen bar → answer / REJECT; do not run full committee to re-litigate.
3. **Research brief** — spawn strategy-scout (optional analyst prompts). Write `{NN}_research_brief.md`.
4. **Bull + bear in parallel** — spawn both; write bull/bear artifacts.
5. **Debate** — spawn debate-engine → APPROVE | REVISE | REJECT.
6. **Strategy spec — package, do not generate** — if APPROVE/REVISE: fill `docs/templates/STRATEGY_SPEC_COMMITTEE.md` into `{NN}_strategy_spec.md` **around the candidate's already-hashed prereg**. (Amended 2026-07-25: the one real run had its prereg hashed ~10h BEFORE the committee ran, and that is the order to keep — the committee packages an already-preregistered candidate; it never generates narrative that is tested afterward. A queued-but-unhashed candidate is preregistered and hashed before step 4, per the trigger rule.)
7. **Backtest/screen** — **only** via `strategy-evidence-pipeline` (hash prereg before outcomes; **Stage-0 feasibility gate first** — compute the signal's empirical distribution against the pre-registered threshold grid and count triggers, and if triggers < 30 for EVERY grid cell STOP with INSUFFICIENT_DATA instead of spending the full screen; record the distribution in the prereg artifact. Stopping rule only — it can never turn a NO_GO into a GO and passing it grants nothing. Precedent: VPIN jump-veto 2026-07-25, mean VPIN ≈ 0.127 never reaches θ ∈ [0.55, 0.70], fire% = 0.000; the committee recommended this check and was overridden — `_workspace/strategy_pipeline/36_owner_override_vpin_full_screen.md`). Do not invent a parallel backtester.
8. **Risk summary** — read binding values from `config.py` / `.env`; use `config/risk_committee.yaml` as documentation mirror only.
9. **Paper status** — `data/heartbeat.json`, `data/promotion_funnel.json` if present; else UNKNOWN.
10. **Final verdict** — spawn final-verdict; state HumanActionRequired.
11. **AI Review (added 2026-07-26, owner directive)** — spawn `ai-reviewer` (Opus 5) on the full
    artifact set. It holds APPROVE/REJECT authority for everything PAPER-scope: the brief, both
    theses, the debate, the screen verdict, and any log-only probe integration. Its APPROVE is
    final — no human confirmation required for PAPER. It CANNOT sign
    `docs/CONTROLLED_LIVE_CHECKLIST.md`, enable CONTROLLED_LIVE, override the frozen gate, or
    approve promotion to order flow; those emit `ESCALATE_TO_HUMAN`. The human's one remaining
    authorization is the live signature, at the money line only.
11. **Stop** — report to owner. No orders.

## Partial re-runs

If artifacts exist for the candidate, re-run only stale stages (e.g. debate after revised bull). Mark downstream stale.

## Relation to dual-model loop

S3 scout days may use this skill to package bull/bear/debate before a candidate enters the prereg queue (`19_dual_model_loop_protocol.md`). Screen/install rules unchanged: both-agree + log-only + owner promotion sign-off.
