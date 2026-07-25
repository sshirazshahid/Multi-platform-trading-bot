---
name: investment-committee
description: Orchestrates the AI investment committee (research brief → bull/bear → debate → strategy template → handoff to strategy-evidence-pipeline → risk/paper summary → final verdict → human action). Use when the owner asks for an AI hedge fund workflow, investment committee, seb.ai-style multi-agent debate, or Claude Fable committee run. Does NOT place orders. Live stays OFF. For pure "find/screen/implement strategies" prefer strategy-evidence-pipeline; this skill packages bull/bear debate around a candidate then routes screens there. Refuted-family FAQs → refuted-families-ledger first.
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
6. **Strategy template** — if APPROVE/REVISE: fill `docs/templates/STRATEGY_SPEC_COMMITTEE.md` into `{NN}_strategy_spec.md` / prereg JSON shape.
7. **Backtest/screen** — **only** via `strategy-evidence-pipeline` (hash prereg before outcomes). Do not invent a parallel backtester.
8. **Risk summary** — read binding values from `config.py` / `.env`; use `config/risk_committee.yaml` as documentation mirror only.
9. **Paper status** — `data/heartbeat.json`, `data/promotion_funnel.json` if present; else UNKNOWN.
10. **Final verdict** — spawn final-verdict; state HumanActionRequired.
11. **Stop** — report to owner. No orders.

## Partial re-runs

If artifacts exist for the candidate, re-run only stale stages (e.g. debate after revised bull). Mark downstream stale.

## Relation to dual-model loop

S3 scout days may use this skill to package bull/bear/debate before a candidate enters the prereg queue (`19_dual_model_loop_protocol.md`). Screen/install rules unchanged: both-agree + log-only + owner promotion sign-off.
